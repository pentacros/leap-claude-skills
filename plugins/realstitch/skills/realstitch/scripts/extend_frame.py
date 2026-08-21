#!/usr/bin/env python3
"""Extend a video frame with a supplied still, then crop to a target aspect.

Use when footage is the wrong shape for the deliverable (e.g. 16:9 source, 9:16
reel) and someone supplies a wider or taller still of the same set. Cropping
alone forces a heavy upscale; compositing the still above/around the footage
buys real estate, so the crop can be larger and the upscale smaller.

Nothing about a particular shoot is baked in. Geometry, match regions, subject
position, colour correction and crop are all measured from the inputs.

    python3 extend_frame.py --video IN.mp4 --extension STILL.png --work DIR

Outputs into --work: an alignment report (JSON), the colour-matched plate, a
feather mask, and the exact ffmpeg command to render.
"""
import argparse
import json
import os
import subprocess
import sys

try:
    import numpy as np
    from PIL import Image, ImageFilter
except ImportError as exc:                                  # pragma: no cover
    sys.exit(f"ERROR: missing dependency ({exc}). Install: pip3 install --user numpy pillow")

# ---- tunables, named rather than inline ------------------------------------
PROBE_FRAMES = 5              # frames sampled to separate subject from set
SUBJECT_STD_PCTL = 75         # temporal-std percentile that counts as "moving"
PATCH_COUNT = 4               # match regions; >=3 lets disagreement be detected
PATCH_W_FRAC, PATCH_H_FRAC = 0.22, 0.26
PATCH_GRID = (5, 6)           # rows, cols of candidate positions; denser = more survive
PATCH_SEP_FRAC = 0.18         # min centre separation, fraction of width
EDGE_EXCLUDE_FRAC = 0.15      # skip regions this close to EITHER vertical edge
COARSE_STEP, FINE_STEP = 0.02, 0.002
FINE_SPAN = 0.02
SCALE_LO, SCALE_HI = 0.30, 1.10
CEILING_SRC_FRAC = 0.030      # top fraction of the plate used to grow it upward
CEILING_FALLOFF = 0.80        # brightness multiplier at the very top
CEILING_BLUR_PX = 9
FEATHER_FRAC = 0.045          # of source height, ramped at the footage's top edge
INLIER_TOL_FRAC = 0.010       # of source width; offsets within this agree
MIN_INLIERS = 3               # regions that must agree for a trustworthy solve
MIN_PATCH_PX = 8


# ---- helpers ---------------------------------------------------------------
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:4])}...\n{r.stderr[:400]}")
    return r.stdout


def probe_video(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height",
               "-show_entries", "format=duration", "-of", "csv=p=0", path]).split()
    if not out:
        raise RuntimeError(f"ffprobe returned nothing for {path}")
    w, h = (int(v) for v in out[0].split(",")[:2])
    return w, h, float(out[-1])


def sample_frames(path, duration, work, count=PROBE_FRAMES):
    """Frames spread across the clip, avoiding the very first/last."""
    times = [duration * f for f in np.linspace(0.15, 0.85, count)]
    frames = []
    for i, t in enumerate(times):
        p = os.path.join(work, f"_probe{i}.png")
        run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", path,
             "-frames:v", "1", "-y", p])
        frames.append(np.asarray(Image.open(p).convert("L")).astype(np.float64))
    return frames, times


def grad(a):
    gy, gx = np.gradient(a)
    return np.hypot(gx, gy)


def ncc_map(image, template):
    """Normalised cross-correlation over all valid template positions."""
    H, W = image.shape
    h, w = template.shape
    if h > H or w > W:
        return None
    t = template - template.mean()
    tss = np.sqrt((t ** 2).sum())
    if tss < 1e-9:
        return None
    sh, sw = H + h - 1, W + w - 1
    f_img = np.fft.rfft2(image, s=(sh, sw))
    f_sq = np.fft.rfft2(image ** 2, s=(sh, sw))
    f_tpl = np.fft.rfft2(t[::-1, ::-1], s=(sh, sw))
    f_one = np.fft.rfft2(np.ones((h, w)), s=(sh, sw))
    corr = np.fft.irfft2(f_img * f_tpl, s=(sh, sw))[h - 1:H, w - 1:W]
    s1 = np.fft.irfft2(f_img * f_one, s=(sh, sw))[h - 1:H, w - 1:W]
    s2 = np.fft.irfft2(f_sq * f_one, s=(sh, sw))[h - 1:H, w - 1:W]
    n = h * w
    den = np.sqrt(np.maximum(s2 - s1 ** 2 / n, 1e-9)) * tss
    return corr / den


def find_subject(frames):
    """Locate the mover. Background is static, so temporal std isolates it.

    Returns (x0, x1, head_row). Works on any footage without a face detector.
    """
    stack = np.stack(frames)
    std = stack.std(axis=0)
    col = std.mean(axis=0)
    thr = np.percentile(col, SUBJECT_STD_PCTL)
    cols = np.where(col > thr)[0]
    if len(cols) == 0:
        h, w = frames[0].shape
        return 0, w, 0
    x0, x1 = int(cols.min()), int(cols.max())
    row = std[:, x0:x1].mean(axis=1)
    row_thr = row.max() * 0.25
    rows = np.where(row > row_thr)[0]
    head = int(rows.min()) if len(rows) else 0
    return x0, x1, head


def pick_patches(frame, subject_x0, subject_x1, extend_edge="top"):
    """Textured regions that avoid the mover AND the edge being extended.

    Two properties matter more than raw texture. Regions must be spatially
    SPREAD, or several patches re-measure the same geometry and their agreement
    proves nothing. And regions must avoid the edge the still extends past,
    because that is where an outpaint blends and stops matching the source.
    """
    h, w = frame.shape
    pw, ph = int(w * PATCH_W_FRAC), int(h * PATCH_H_FRAC)
    energy = grad(frame)
    rows, cols = PATCH_GRID
    edge_limit = int(h * EDGE_EXCLUDE_FRAC)
    cands = []
    for r in range(rows):
        for c in range(cols):
            y = int(r * (h - ph) / max(rows - 1, 1))
            x = int(c * (w - pw) / max(cols - 1, 1))
            if not (x + pw <= subject_x0 or x >= subject_x1):
                continue                                   # overlaps the mover
            # Exclude BOTH vertical edges. The extended edge is where an
            # outpaint blends and stops matching; the opposite edge is where the
            # still may simply not reach, and a patch outside its coverage
            # matches noise and silently poisons the solve.
            if y < edge_limit or y + ph > h - edge_limit:
                continue
            cands.append((float(energy[y:y + ph, x:x + pw].mean()), y, y + ph, x, x + pw))
    if not cands:
        raise RuntimeError("no match regions clear of the subject and the extended edge - "
                           "relax EDGE_EXCLUDE_FRAC or SUBJECT_STD_PCTL")
    cands.sort(reverse=True)
    min_sep = w * PATCH_SEP_FRAC
    chosen = []
    for _, y0, y1, x0, x1 in cands:
        cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
        if all(((cx - (px0 + px1) / 2) ** 2 + (cy - (py0 + py1) / 2) ** 2) ** 0.5 >= min_sep
               for py0, py1, px0, px1 in chosen):
            chosen.append((y0, y1, x0, x1))
        if len(chosen) == PATCH_COUNT:
            break
    if len(chosen) < 3:
        chosen = [tuple(c[1:]) for c in cands[:PATCH_COUNT]]
        print("NOTE could not spread match regions; agreement will be weak evidence")
    return chosen


def solve_alignment(frame, still, patches):
    """Scale + offset placing the footage inside the still.

    Returns dict with scale, origin, per-patch correlation and the spread
    between patches. Spread is the honesty check: a low-spread solve means
    independent regions agree, which a single correlation score cannot tell you.
    """
    still_g = grad(still)

    def evaluate(scale):
        found = {}
        for (y0, y1, x0, x1) in patches:
            tw = max(MIN_PATCH_PX, int((x1 - x0) * scale))
            th = max(MIN_PATCH_PX, int((y1 - y0) * scale))
            tpl = np.asarray(Image.fromarray(frame[y0:y1, x0:x1]).resize((tw, th), Image.LANCZOS))
            m = ncc_map(still_g, grad(tpl))
            if m is None:
                return None
            idx = np.unravel_index(np.argmax(m), m.shape)
            found[(y0, x0)] = (float(m[idx]), idx[1] - x0 * scale, idx[0] - y0 * scale)
        return found

    def score(found):
        """Consensus offset with outlier rejection.

        A raw max-min spread lets one bad region condemn a good solve, and a
        single correlation score cannot reveal disagreement at all. Counting
        regions that agree with the median separates "confidently wrong" from
        "actually aligned".
        """
        dxs = np.array([v[1] for v in found.values()])
        dys = np.array([v[2] for v in found.values()])
        mx, my = float(np.median(dxs)), float(np.median(dys))
        tol = frame.shape[1] * INLIER_TOL_FRAC
        keep = (np.abs(dxs - mx) <= tol) & (np.abs(dys - my) <= tol)
        # spread is the WORST single-axis disagreement among agreeing regions,
        # so it is directly comparable to the tolerance (summing axes made the
        # warning fire even on a good solve)
        if keep.sum() >= 2:
            mx, my = float(dxs[keep].mean()), float(dys[keep].mean())
            spread = float(max(dxs[keep].max() - dxs[keep].min(),
                               dys[keep].max() - dys[keep].min()))
        else:
            spread = float(max(dxs.max() - dxs.min(), dys.max() - dys.min()))
        return spread, mx, my, int(keep.sum())

    def sweep(values):
        best = None
        for s in values:
            found = evaluate(float(s))
            if found is None:
                continue
            spread, dx, dy, inliers = score(found)
            total = sum(v[0] for v in found.values())
            # inliers first: agreement between independent regions is the only
            # evidence that a lock is real rather than merely confident
            inlier_corr = sum(v[0] for v in found.values()) if inliers == len(found) else total
            rank = (inliers, -spread, inlier_corr)
            if best is None or rank > best[0]:
                best = (rank, float(s), dx, dy, spread, total, found, inliers)
        return best

    coarse = sweep(np.arange(SCALE_LO, SCALE_HI, COARSE_STEP))
    if coarse is None:
        raise RuntimeError("alignment failed at every scale - is the still the same scene?")
    fine = sweep(np.arange(max(SCALE_LO, coarse[1] - FINE_SPAN),
                          min(SCALE_HI, coarse[1] + FINE_SPAN), FINE_STEP)) or coarse
    _, scale, dx, dy, spread, total, found, inliers = fine
    return {"scale": scale, "origin": [dx, dy], "spread_px": spread,
            "inliers": inliers, "regions": len(found), "corr_total": total,
            "corr_per_patch": {f"{k[1]},{k[0]}": round(v[0], 3) for k, v in found.items()},
            "patches": [list(p) for p in patches]}


def colour_match(still_up, frame_rgb, patches, origin, scale):
    """Per-channel gain/offset fitted on the overlap, so the join does not step."""
    px, py = -origin[0] / scale, -origin[1] / scale
    src, dst = [], []
    for (y0, y1, x0, x1) in patches:
        sy, sx = int(round(y0 - py)), int(round(x0 - px))
        block = still_up[sy:sy + (y1 - y0), sx:sx + (x1 - x0)]
        target = frame_rgb[y0:y1, x0:x1]
        if block.shape == target.shape:
            src.append(block.reshape(-1, 3))
            dst.append(target.reshape(-1, 3))
    if not src:
        return np.ones(3), np.zeros(3)
    src, dst = np.concatenate(src), np.concatenate(dst)
    gain, off = np.ones(3), np.zeros(3)
    for c in range(3):
        A = np.vstack([src[:, c], np.ones(len(src))]).T
        gain[c], off[c] = np.linalg.lstsq(A, dst[:, c], rcond=None)[0]
    return gain, off


def grow_upward(plate, extra):
    """Add `extra` rows above the plate from its own top band, per column.

    Per-column (not per-pixel-block) so vertical features - a lamp cord, a wall
    edge - continue as lines rather than smearing sideways.
    """
    if extra <= 0:
        return plate
    src_rows = max(2, int(plate.shape[0] * CEILING_SRC_FRAC))
    col = plate[:src_rows].mean(axis=0)
    ramp = np.linspace(CEILING_FALLOFF, 1.0, extra)[:, None, None]
    ext = np.repeat(col[None, :, :], extra, axis=0) * ramp
    grown = Image.fromarray(np.clip(np.vstack([ext, plate]), 0, 255).astype(np.uint8))
    band_h = min(extra + src_rows, grown.height)
    band = grown.crop((0, 0, grown.width, band_h)).filter(
        ImageFilter.GaussianBlur(CEILING_BLUR_PX))
    grown.paste(band, (0, 0))
    return np.asarray(grown).astype(np.float64)


def extra_for_upscale(headroom, src_h, out_w, out_h, target):
    """Rows to synthesise so the crop reaches `target` upscale (1.0 = native)."""
    aspect = out_w / out_h
    for extra in range(0, out_h * 2):
        canvas_h = headroom + src_h + extra
        crop_w = canvas_h * aspect
        if out_w / crop_w <= target:
            return extra
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--extension", required=True, help="still of the same scene, wider/taller")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-w", type=int, default=1080)
    ap.add_argument("--out-h", type=int, default=1920)
    ap.add_argument("--extra", default="0",
                    help="rows to synthesise above: N, or 'upscale:1.15' to hit a factor")
    ap.add_argument("--crop-x", default="subject",
                    help="'subject' (default), 'centre', or a pixel value")
    ap.add_argument("--no-colour-match", action="store_true")
    ap.add_argument("--extend-edge", choices=["top", "bottom"], default="top",
                    help="which edge the still extends past; that zone is excluded "
                         "from match regions")
    a = ap.parse_args()

    for p in (a.video, a.extension):
        if not os.path.exists(p):
            sys.exit(f"ERROR: not found: {p}")
    os.makedirs(a.work, exist_ok=True)

    vw, vh, dur = probe_video(a.video)
    frames, times = sample_frames(a.video, dur, a.work)
    sx0, sx1, head = find_subject(frames)
    patches = pick_patches(frames[len(frames) // 2], sx0, sx1, a.extend_edge)
    print(f"source {vw}x{vh} {dur:.2f}s | subject x{sx0}-{sx1} head row {head}")
    print(f"match regions (auto, clear of subject): {patches}")

    still = Image.open(a.extension).convert("RGB")
    align = solve_alignment(frames[len(frames) // 2],
                            np.asarray(still.convert("L")).astype(np.float64), patches)
    scale, (dx, dy) = align["scale"], align["origin"]
    headroom = int(round(dy / scale))
    warn = align["inliers"] < MIN_INLIERS or align["spread_px"] > vw * INLIER_TOL_FRAC
    print(f"scale {scale:.3f} origin ({dx:.1f},{dy:.1f}) | "
          f"{align['inliers']}/{align['regions']} regions agree within "
          f"{vw * INLIER_TOL_FRAC:.0f}px, spread {align['spread_px']:.1f}px")
    print(f"per-region correlation: {align['corr_per_patch']}")
    print(f"real headroom above the frame: {headroom}px")
    if warn:
        print(f"WARNING only {align['inliers']}/{align['regions']} regions agree - the still "
              f"is probably a RE-RENDER of the scene, not an extension of THIS frame, so its "
              f"geometry differs and no scale+shift will line it up. Either source a true "
              f"outpaint of this frame, or blur the extended area so the mismatch stops "
              f"reading as broken architecture.")

    inv = 1.0 / scale
    up = still.resize((int(round(still.width * inv)), int(round(still.height * inv))),
                      Image.LANCZOS)
    plate = np.asarray(up).astype(np.float64)
    frame_rgb = np.asarray(Image.open(os.path.join(a.work, "_probe2.png")).convert("RGB")
                           ).astype(np.float64)
    if a.no_colour_match:
        gain, off = np.ones(3), np.zeros(3)
    else:
        gain, off = colour_match(plate, frame_rgb, patches, [dx, dy], scale)
        print(f"colour match gain {gain.round(3)} offset {off.round(1)}")
    plate = np.clip(plate * gain + off, 0, 255)

    if a.extra.startswith("upscale:"):
        extra = extra_for_upscale(headroom, vh, a.out_w, a.out_h, float(a.extra.split(":")[1]))
    else:
        extra = int(a.extra)
    plate = grow_upward(plate, extra)
    plate_path = os.path.join(a.work, "plate.png")
    Image.fromarray(plate.astype(np.uint8)).save(plate_path)

    canvas_h = headroom + extra + vh
    crop_w = int(round(canvas_h * a.out_w / a.out_h))
    if crop_w > vw:
        print(f"NOTE crop width {crop_w} exceeds source width {vw}; clamping")
        crop_w = vw
    if a.crop_x == "subject":
        cx = (sx0 + sx1) // 2
    elif a.crop_x == "centre":
        cx = vw // 2
    else:
        cx = int(a.crop_x)
    crop_x = max(0, min(vw - crop_w, cx - crop_w // 2))
    video_y = headroom + extra
    plate_x = int(round(-dx * inv))
    head_out = (head + video_y) * a.out_h / canvas_h

    feather = max(1, int(vh * FEATHER_FRAC))
    mask = np.full((vh, vw), 255, dtype=np.uint8)
    mask[:feather, :] = np.linspace(0, 255, feather).astype(np.uint8)[:, None]
    mask_path = os.path.join(a.work, "feather_mask.png")
    Image.fromarray(mask, "L").save(mask_path)

    report = {"source": {"w": vw, "h": vh, "duration": dur},
              "subject": {"x0": sx0, "x1": sx1, "head_row": head},
              "alignment": align, "alignment_suspect": bool(warn),
              "headroom_px": headroom, "extra_rows": extra,
              "colour_match": {"gain": gain.tolist(), "offset": off.tolist()},
              "canvas": [vw, canvas_h], "crop": [crop_w, canvas_h, crop_x],
              "upscale": round(a.out_w / crop_w, 3),
              "head_row_out": round(head_out, 1),
              "invented_fraction": round(extra / canvas_h, 3),
              "plate": plate_path, "mask": mask_path,
              "plate_x": plate_x, "video_y": video_y, "feather_px": feather}
    with open(os.path.join(a.work, "extend_report.json"), "w") as fh:
        json.dump(report, fh, indent=1)

    print(f"\ncanvas {vw}x{canvas_h} | crop {crop_w}x{canvas_h} at x{crop_x} "
          f"-> {a.out_w}x{a.out_h} ({report['upscale']}x) | head row out {head_out:.0f} "
          f"| invented {report['invented_fraction']:.0%}")
    print("\n# render (alphamerge, NOT geq - geq on a full frame is orders of magnitude slower)")
    print(f"""ffmpeg -nostdin -y -i {a.video!r} -loop 1 -i {plate_path!r} -i {mask_path!r} \\
 -filter_complex "color=c=black:s={vw}x{canvas_h}:r=25[base];[1:v]setsar=1[top];\\
[base][top]overlay=x={plate_x}:y=0[bg];[0:v]format=rgba,setsar=1[v0];[2:v]format=gray[m];\\
[v0][m]alphamerge[fg];[bg][fg]overlay=x=0:y={video_y}:shortest=1:format=auto,\\
crop={crop_w}:{canvas_h}:{crop_x}:0,scale={a.out_w}:{a.out_h},setsar=1,format=yuv420p[v]" \\
 -map "[v]" -map 0:a -c:v libx264 -crf 16 -preset veryfast -c:a aac -b:a 192k \\
 -color_range tv -colorspace bt709 OUT.mp4""")


if __name__ == "__main__":
    main()
