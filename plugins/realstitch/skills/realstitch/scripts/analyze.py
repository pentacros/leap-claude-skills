#!/usr/bin/env python3
"""Stage 2 - measure the footage. Nothing here is assumed; it is all sampled
from the actual frames, because a green screen shifts between shoots.

Produces: sampled green, safe key window, subject geometry, the framing solve
that hits "subject 60-70% / top 30-40% empty", and the grade correction needed
to sit the subject in the supplied background.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

W_OUT, H_OUT = 1080, 1920
# Minimum gap between the lowest graphic and the top of the head.
HEAD_CLEARANCE = 28
HEAD_TARGET = 740          # y of head-top in the output frame (38.5% empty above)
BG_BLUR = 16               # sigma. 26 caused the pasted-cutout look; 13 competes.
LUMA_TARGET_GAP = 20.0     # subject should read brighter than the room, but only ~20


STILL_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic"}


def frames(video, times, vf=None, pix="rgb24"):
    """Grab frames at the given times. Also accepts a still image.

    A still has no timeline, so seeking into it yields nothing and ffmpeg writes
    no file. Backgrounds are routinely stills, so detect that and read frame 0.
    """
    still = os.path.splitext(video)[1].lower() in STILL_EXT
    out = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            p = os.path.join(td, f"f{i}.png")
            cmd = ["ffmpeg", "-v", "error"]
            if not still:
                cmd += ["-ss", str(t)]
            cmd += ["-i", video, "-frames:v", "1"]
            if vf:
                cmd += ["-vf", vf]
            cmd += ["-pix_fmt", pix, p, "-y"]
            subprocess.run(cmd, check=True)
            if not os.path.exists(p):
                raise RuntimeError(
                    f"ffmpeg produced no frame from {os.path.basename(video)} at "
                    f"t={t}s - is it a still, or shorter than {t}s?")
            out.append(np.asarray(Image.open(p)).copy())
    return out


def probe(video):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True).stdout.split()
    wh = o[0].split(",")
    return int(wh[0]), int(wh[1]), float(o[-1])


def sample_green(video, dur):
    """Dominant green across the clip, plus how uneven the lighting is."""
    ts = [dur * f for f in (0.05, 0.25, 0.5, 0.75, 0.95)]
    means, cov, bands = [], [], []
    for a in frames(video, ts):
        a = a.astype(int)
        R, G, B = a[..., 0], a[..., 1], a[..., 2]
        m = (G > R * 1.25) & (G > B * 1.25) & (G > 60)
        cov.append(m.mean())
        if m.any():
            means.append(a[m].mean(0))
        h, w = m.shape
        row = []
        for x0, x1 in ((0, w // 3), (w // 3, 2 * w // 3), (2 * w // 3, w)):
            sub = a[:h // 2, x0:x1].reshape(-1, 3)
            g = sub[(sub[:, 1] > sub[:, 0] * 1.25) & (sub[:, 1] > sub[:, 2] * 1.25)]
            row.append(float(g[:, 1].mean()) if len(g) else 0.0)
        bands.append(row)
    rgb = np.mean(means, 0)
    b = np.mean(bands, 0)
    spread = (b.max() - b.min()) / max(b.max(), 1)
    return (f"0x{int(rgb[0]):02X}{int(rgb[1]):02X}{int(rgb[2]):02X}",
            [round(float(v), 1) for v in rgb], float(np.mean(cov)), round(float(spread), 3))


def head_top_asshot(video, dur):
    """Top of the subject's head in as-shot (non-keyed) footage.

    Needed because the graphics band is defined by where the subject STARTS, not
    by a fixed y. Hardcoding the band to the green-screen layout put every card
    on the speaker's head in tighter-framed footage.

    Hair is far darker than a studio wall, so the topmost row with a run of dark
    pixels in the central columns is the head. Sampled across the clip and the
    MINIMUM taken, so the band stays clear even when he sits up.
    """
    ts = [round(dur * f, 2) for f in
          (0.03, 0.12, 0.22, 0.32, 0.42, 0.52, 0.62, 0.72, 0.82, 0.92)]
    tops = []
    for t in ts:
        try:
            a = frames(video, [t])[0].astype(float)
        except Exception:
            continue
        h, w, _ = a.shape
        lum = a.mean(2)
        c = lum[:, int(w * 0.28):int(w * 0.72)]
        # skip the top 10%: many phone/camera exports have a dark vignette there
        # which otherwise reads as "head at row 0".
        for y in range(int(h * 0.10), int(h * 0.60)):
            if (c[y] < 85).mean() > 0.06:
                tops.append(y)
                break
    if not tops:
        return None
    return min(tops)


def sweep_key(video, dur, key):
    """Find the usable similarity range.

    Coverage should land near (1 - green_fraction). Too low and green survives;
    push too far and the key eats the subject and coverage collapses.
    """
    t = dur * 0.5
    rows = []
    for sim in (0.03, 0.05, 0.07, 0.09, 0.11, 0.14, 0.18):
        a = frames(video, [t],
                   vf=f"format=rgba,chromakey={key}:{sim}:0.02", pix="rgba")[0]
        rows.append((sim, float((a[..., 3] > 200).mean())))
    best = max(rows, key=lambda r: r[1] if r[1] > 0.12 else -1)
    ref = best[1]
    ok = [s for s, c in rows if c > 0.12 and abs(c - ref) / ref < 0.20]
    # NEVER take the top of the window. Coverage is still ~intact one step before
    # collapse while the subject has already gone semi-transparent - rendered at
    # 0.09 on this footage the background bled visibly through shirt and face,
    # even though coverage looked fine. Back off one sweep step (and stay off the
    # ceiling) so the choice sits inside the window, not on its edge.
    if ok:
        top = max(ok)
        safe = [s for s in ok if s < top] or [top]
        chosen = max(safe)
        if len(ok) == 1:
            chosen = round(top * 0.8, 3)   # single-point window: back off hard
    else:
        chosen = 0.07
    return {"table": [[s, round(c, 4)] for s, c in rows],
            "window": [min(ok), max(ok)] if ok else None,
            "similarity": chosen, "blend": 0.05,
            "margin_from_collapse": round(max(ok) - chosen, 3) if ok else None,
            "narrow": bool(ok and (max(ok) - min(ok)) < 0.05)}


def subject_geometry(video, dur, key, sim, blend):
    """Head-top, horizontal extent and table line, from the matte itself."""
    ts = [dur * f for f in (0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95)]
    heads, x0s, x1s, tables = [], [], [], []
    for a in frames(video, ts,
                    vf=f"format=rgba,chromakey={key}:{sim}:{blend}", pix="rgba"):
        op = a[..., 3] > 128
        h, w = op.shape
        above = op[:int(h * 0.93)]           # ignore the table band
        rows = np.where(above.sum(1) > w * 0.010)[0]
        cols = np.where(above.sum(0) > int(h * 0.93) * 0.010)[0]
        if not len(rows) or not len(cols):
            continue
        heads.append(int(rows.min()))
        x0s.append(int(cols.min()))
        # right edge: ignore isolated speckle at the extreme frame edge
        prof = above.sum(0)
        solid = np.where(prof > int(h * 0.93) * 0.02)[0]
        x1s.append(int(solid.max()) if len(solid) else int(cols.max()))
        tables.append(next((y for y in range(int(h * 0.85), h) if op[y].mean() > 0.60), h))
    return {"head_top": int(min(heads)), "x0": int(min(x0s)), "x1": int(max(x1s)),
            "table_top": int(min(tables)),
            "head_top_range": [int(min(heads)), int(max(heads))]}


def solve_framing(sw, sh, geo):
    """Scale/offset so head-top lands at HEAD_TARGET and the source bottom at 1920."""
    h = geo["head_top"]
    s = (H_OUT - HEAD_TARGET) / (sh - h)
    sw2 = int(round(sw * s / 2) * 2)
    sh2 = int(round(sh * s / 2) * 2)
    s_eff = sh2 / sh
    oy = int(round(HEAD_TARGET - h * s_eff))
    cx = (geo["x0"] + geo["x1"]) / 2
    ox = int(round(W_OUT / 2 - cx * s_eff))
    subj_h = H_OUT - (h * s_eff + oy)
    return {"scale": [sw2, sh2], "overlay": [ox, oy],
            "head_top_out": round(h * s_eff + oy, 1),
            "subject_pct": round(subj_h / H_OUT * 100, 1),
            "empty_top_pct": round((h * s_eff + oy) / H_OUT * 100, 1)}


def lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def grade_match(video, bg, dur, key, sim, blend, fr):
    """Measure subject vs background, then solve a correction.

    The pasted-on look is mostly luma + saturation mismatch. Aim for the subject
    ~20 luma brighter than the room (he is the subject) with contrast matched.
    """
    sc = f"{fr['scale'][0]}:{fr['scale'][1]}"
    ox, oy = fr["overlay"]
    fg = frames(video, [dur * 0.5], pix="rgba", vf=(
        f"format=rgba,chromakey={key}:{sim}:{blend},"
        f"despill=type=green:mix=0.5:expand=0.3,scale={sc}"))[0]
    canvas = np.zeros((H_OUT, W_OUT, 4), np.uint8)
    fh, fw = fg.shape[:2]
    y0, x0 = max(0, oy), max(0, ox)
    sy, sx = max(0, -oy), max(0, -ox)
    hh = min(H_OUT - y0, fh - sy)
    ww = min(W_OUT - x0, fw - sx)
    canvas[y0:y0 + hh, x0:x0 + ww] = fg[sy:sy + hh, sx:sx + ww]

    bgi = frames(bg, [0], vf=(
        f"scale={int(W_OUT*1.19)}:{H_OUT}:force_original_aspect_ratio=increase,"
        f"crop={W_OUT}:{H_OUT},gblur=sigma={BG_BLUR}"))[0].astype(float)

    al = canvas[..., 3]
    m = al > 240
    if m.sum() < 5000:
        return {"error": "subject mask too small to measure"}
    ys = np.where(m.any(1))[0]
    ring = np.zeros_like(m)
    ring[ys.min():ys.max(), :] = True
    ring &= al < 10
    sl, bl = lum(canvas[..., :3].astype(float))[m], lum(bgi)[ring]
    sr, br = canvas[..., :3].astype(float)[m], bgi[ring]
    ssat = float(np.mean(sr.max(1) - sr.min(1)))
    bsat = float(np.mean(br.max(1) - br.min(1)))

    # brightness is an additive offset in eq (-1..1 maps to -255..255)
    bright = round(max(-0.30, min(0.0,
                   -((sl.mean() - bl.mean()) - LUMA_TARGET_GAP) / 255.0)), 3)
    sat = round(max(0.55, min(1.0, (bsat + (ssat - bsat) * 0.62) / max(ssat, 1))), 3)
    con = round(max(0.85, min(1.05, float(bl.std() / max(sl.std(), 1)))), 3)
    return {"measured": {"subject_luma": round(float(sl.mean()), 1),
                         "bg_luma": round(float(bl.mean()), 1),
                         "luma_gap": round(float(sl.mean() - bl.mean()), 1),
                         "contrast_gap": round(float(sl.std() - bl.std()), 1),
                         "sat_gap": round(ssat - bsat, 1)},
            "correction": {"brightness": bright, "saturation": sat, "contrast": con},
            "bg": {"blur_sigma": BG_BLUR, "brightness": -0.02, "saturation": 0.95},
            "alpha_feather": 1.7, "rgb_soften": 0.35}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("footage")
    ap.add_argument("background", nargs="?")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    sw, sh, dur = probe(a.footage)
    key, rgb, cov, spread = sample_green(a.footage, dur)
    res = {"source": {"w": sw, "h": sh, "duration": dur},
           "green": {"key": key, "rgb": rgb, "coverage": round(cov, 3),
                     "lighting_spread": spread}}
    print(f"source {sw}x{sh} {dur:.2f}s")
    print(f"green {key} rgb={rgb} coverage={cov*100:.1f}% lighting spread={spread*100:.0f}%")

    if cov < 0.25:
        res["is_green_screen"] = False
        ht = head_top_asshot(a.footage, dur)
        if ht is None:
            print("! could not locate the subject's head - graphics band falls back "
                  "to the default and MAY overlap the speaker")
            ht = 740
        band_top, band_bot = 220, ht - HEAD_CLEARANCE
        res["head_top"] = ht
        res["graphics_band"] = [band_top, band_bot]
        print(f"head-top {ht} ({ht/H_OUT*100:.1f}%) -> graphics band y{band_top}-{band_bot} "
              f"({band_bot-band_top}px usable)")
        if band_bot - band_top < 150:
            print("! very little headroom: cards will be small. Shoot with more space "
                  "above the head for stronger graphics.")
        print("! green coverage low - NOT green screen. Skip keying, keep the real "
              "background, run the rest of the pipeline.")
        json.dump(res, open(a.out, "w"), indent=1)
        return

    res["is_green_screen"] = True
    sk = sweep_key(a.footage, dur, key)
    res["key"] = sk
    win = sk["window"]
    print(f"key window {win} -> similarity {sk['similarity']} blend {sk['blend']}"
          + ("   ! NARROW - footage lit unevenly" if sk["narrow"] else ""))

    geo = subject_geometry(a.footage, dur, key, sk["similarity"], sk["blend"])
    res["geometry"] = geo
    print(f"subject head-top {geo['head_top']} (range {geo['head_top_range']}) "
          f"x[{geo['x0']}-{geo['x1']}] table {geo['table_top']}")

    fr = solve_framing(sw, sh, geo)
    res["framing"] = fr
    ok = 60 <= fr["subject_pct"] <= 70 and 30 <= fr["empty_top_pct"] <= 40
    print(f"framing scale={fr['scale']} overlay={fr['overlay']} -> subject "
          f"{fr['subject_pct']}% / empty top {fr['empty_top_pct']}%  "
          f"{'OK' if ok else '! outside the 60-70/30-40 spec'}")

    if a.background and os.path.exists(a.background):
        res["grade"] = grade_match(a.footage, a.background, dur, key,
                                   sk["similarity"], sk["blend"], fr)
        g = res["grade"]
        if "measured" in g:
            m = g["measured"]
            print(f"grade: luma gap {m['luma_gap']:+.1f} contrast {m['contrast_gap']:+.1f} "
                  f"sat {m['sat_gap']:+.1f} -> correction {g['correction']}")
    else:
        print("! no background supplied - keying to transparent only")

    json.dump(res, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
