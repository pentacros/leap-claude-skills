#!/usr/bin/env python3
"""Stage 5 - drive ffmpeg. Each stage writes an intermediate so a re-run skips
completed work. Emits the exact commands it runs, so a failure is debuggable.

Non-obvious things encoded here, each of which silently ruins a render:
  * setpts=PTS-STARTPTS on every seeked input, or overlay never matches PTS and
    composites nothing at all.
  * gblur=planes=8 to feather alpha (alphaextract fails format negotiation).
  * explicit tv/bt709 tagging, or RGB/PNG inputs yield deprecated yuvj420p.
  * hard cuts + alternating punch, never xfade on a face.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

W, H, FPS = 1080, 1920, 25


def run(cmd, label):
    print(f"[{label}]")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed at stage: {label}")



def cached(path):
    """True only if `path` is a COMPLETE, playable intermediate.

    Caching on os.path.exists alone is unsafe: a render killed part-way (timeout,
    Ctrl-C, full disk) leaves a truncated file with no moov atom, and every later
    run happily "resumes" from it and fails downstream with a confusing error.
    Probing costs milliseconds and turns that into a clean rebuild.
    """
    if not os.path.exists(path) or os.path.getsize(path) < 4096:
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return r.returncode == 0 and float(r.stdout.strip()) > 0.1
    except ValueError:
        return False


def scrims(path):
    """Top scrim for type legibility, bottom scrim to grade the table into a
    desk edge and ground the frame."""
    y = np.arange(H)
    top = np.clip(1 - (y / 760), 0, 1) ** 1.5 * 0.60
    bot = np.clip((y - 1690) / 230, 0, 1) ** 1.2 * 0.72
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = (np.clip(top + bot, 0, 1)[:, None] * 255).astype(np.uint8)
    Image.fromarray(a, "RGBA").save(path)


def broll_scrim(path):
    y = np.arange(H)
    bot = np.clip((y - 1130) / 500, 0, 1) ** 1.4 * 0.62
    a = np.zeros((H, W, 4), np.uint8)
    a[..., 3] = (bot[:, None] * 255).astype(np.uint8)
    Image.fromarray(a, "RGBA").save(path)


def build_plate(footage, bg, an, work):
    out = os.path.join(work, "01_plate.mp4")
    if cached(out):
        return out
    sc = os.path.join(work, "scrim.png")
    scrims(sc)
    g = an.get("grade", {})
    cor = g.get("correction", {"brightness": -0.08, "saturation": 0.78, "contrast": 0.93})
    bgp = g.get("bg", {"blur_sigma": 16, "brightness": -0.02, "saturation": 0.95})
    feather = g.get("alpha_feather", 1.7)
    soften = g.get("rgb_soften", 0.35)
    sw, sh = an["framing"]["scale"]
    ox, oy = an["framing"]["overlay"]
    key = an["green"]["key"]
    sim, bl = an["key"]["similarity"], an["key"]["blend"]

    fc = (
        f"[1:v]scale={int(W*1.19)}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},gblur=sigma={bgp['blur_sigma']},"
        f"eq=brightness={bgp['brightness']}:saturation={bgp['saturation']},setsar=1[bg];"
        f"[0:v]format=rgba,chromakey={key}:{sim}:{bl},"
        f"despill=type=green:mix=0.5:expand=0.3,"
        f"gblur=sigma={feather}:planes=8,gblur=sigma={soften}:planes=7,"
        f"eq=brightness={cor['brightness']}:saturation={cor['saturation']}"
        f":contrast={cor['contrast']},scale={sw}:{sh},setsar=1[fg];"
        f"[bg][fg]overlay=x={ox}:y={oy}:shortest=1:format=auto[c];"
        f"[c][2:v]overlay=0:0:format=auto,vignette=PI/5,format=yuv420p[v]"
    )
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", footage,
         "-loop", "1", "-i", bg, "-i", sc, "-filter_complex", fc,
         "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-crf", "16",
         "-preset", "veryfast", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
         out, "-y"], "plate")
    return out



def build_vertical_plate(footage, an, work):
    """Landscape source -> 9:16, with a headroom band above the subject.

    A 1920x1080 source has no content above the speaker's head (measured y58 of
    1080 here), so no 9:16 crop can produce the empty top the design needs. This
    crops to 9:16, then slides the image DOWN and fills the revealed strip with a
    heavily blurred, darkened stretch of the wall from just above her head.

    Deliberately NOT a cover-scaled blur of the whole frame - that renders a
    ghostly second face in the band. And the sharp layer's top edge is ramped, or
    the blurred/sharp boundary reads as a hard line across the wall.
    """
    out = os.path.join(work, "01_plate.mp4")
    if cached(out):
        return out
    v = an["vertical"]
    cw, ch, cx = v["crop_w"], v["crop_h"], v["crop_x"]
    shift = v["shift_y"]
    ramp = os.path.join(work, "seam_ramp.png")
    if not os.path.exists(ramp):
        y = np.arange(H)
        a = np.clip(y / float(v.get("seam", 80)), 0, 1)
        Image.fromarray(np.repeat((a[:, None] * 255).astype(np.uint8), W, axis=1),
                        "L").save(ramp)
    # The band is stretched to FULL height and used as the base. An earlier
    # version laid a color=black source underneath - that source is infinite and
    # overlay had no shortest=1, so the render never terminated: it kept emitting
    # black frames past the end of the audio and the file grew without bound.
    # Nothing needs a synthetic base here; the band plus the sharp layer already
    # cover every pixel.
    fc = (
        f"[0:v]crop={cw}:{ch}:{cx}:0,scale={W}:{H}:flags=lanczos,split=2[sharp][top];"
        f"[top]crop={W}:56:0:0,scale={W}:{H}:flags=bicubic,"
        f"gblur=sigma=32,eq=brightness=-0.11:saturation=0.80[band];"
        f"[1:v]format=gray[msk];[sharp][msk]alphamerge[shm];"
        f"[band][shm]overlay=x=0:y={shift}:shortest=1:format=auto,format=yuv420p[v]"
    )
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", footage, "-i", ramp,
         "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
         "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", out, "-y"], "vertical plate")
    return out


def build_trim(plate, tl, work):
    """Hard cuts with an alternating punch-in. Applied to the plate only, before
    overlays, so captions and cards stay put."""
    out = os.path.join(work, "02_trim.mp4")
    if cached(out):
        return out
    segs, punch = tl["segments"], tl["punch"]
    n = len(segs)
    z = 1 + tl["punch_scale"]
    pw, ph = int(round(W * z / 2) * 2), int(round(H * z / 2) * 2)
    # re-centre the punch on the face so the subject does not shift
    cx, cy = int((pw - W) * 0.5), int((ph - H) * 0.47)
    PUNCH = f"scale={pw}:{ph}:flags=lanczos,crop={W}:{H}:{cx}:{cy}"

    p = ["[0:v]split=%d%s" % (n, "".join(f"[s{i}]" for i in range(n))),
         "[0:a]asplit=%d%s" % (n, "".join(f"[t{i}]" for i in range(n)))]
    for i, (a, b) in enumerate(segs):
        pv = f",{PUNCH}" if punch[i] else ""
        p.append(f"[s{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS,fps={FPS}{pv},setsar=1[v{i}]")
        p.append(f"[t{i}]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]")
    p.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
    p.append("[vc]format=yuv420p[vout]")
    p.append("[ac]anull[aout]")
    fp = os.path.join(work, "trim.filter")
    open(fp, "w").write(";\n".join(p))
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", plate,
         "-/filter_complex", fp, "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", out, "-y"], "trim + punch")
    return out


def build_broll(beats, work):
    """16:9 -> full-bleed 9:16, cropped on the region of interest, slow push."""
    sc = os.path.join(work, "br_scrim.png")
    broll_scrim(sc)
    made = []
    for i, b in enumerate(beats):
        if b["type"] != "broll":
            continue
        out = os.path.join(work, f"br{i}.mp4")
        dur = round(b["end"] - b["start"], 2)
        fr = max(1, int(dur * FPS))
        cx = b.get("crop_x", 656)
        fc = (f"[0:v]crop=608:1080:{cx}:0,scale=1188:2112:flags=lanczos,"
              f"zoompan=z='1+0.05*on/{fr}':d=1:x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
              f"eq=saturation=0.92:contrast=1.02:brightness=-0.02,setsar=1,"
              f"setpts=PTS-STARTPTS[v];[v][1:v]overlay=0:0:format=auto,format=yuv420p[o]")
        if not cached(out):
            run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", str(b["src_in"]),
                 "-t", str(dur), "-i", b["clip"], "-i", sc,
                 "-filter_complex", fc, "-map", "[o]", "-an",
                 "-c:v", "libx264", "-crf", "15", "-preset", "fast",
                 "-r", str(FPS), out, "-y"], f"b-roll {i}")
        made.append((out, b))
    return made


def synth_sfx(kind, work):
    """Render a short impact to WAV. Beats can ask for a sound without shipping
    an audio asset, so the pipeline generates one rather than failing."""
    import wave
    import numpy as np
    sr = 48000
    path = os.path.join(work, f"sfx_{kind}.wav")
    if os.path.exists(path):
        return path
    if kind == "cash":
        # Bright two-tone chime with a fast attack - reads as money/cost.
        dur = 0.42
        t = np.arange(int(sr * dur)) / sr
        x = (np.sin(2 * np.pi * 1180 * t) * np.exp(-t * 9) * 0.6 +
             np.sin(2 * np.pi * 1760 * t) * np.exp(-t * 11) * 0.4 +
             np.sin(2 * np.pi * 2640 * t) * np.exp(-t * 16) * 0.2)
        cn = int(sr * 0.012)
        noise = np.random.RandomState(11).randn(cn)
        x[:cn] += np.diff(np.concatenate([[0], noise])) * 0.25
    else:
        # Low thump plus mechanical click - a stamp landing.
        dur = 0.34
        t = np.arange(int(sr * dur)) / sr
        f = 130 * np.exp(-t * 9) + 48
        x = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 13) * 0.85
        cn = int(sr * 0.028)
        noise = np.random.RandomState(7).randn(cn)
        click = np.diff(np.concatenate([[0], noise])) * \
            np.exp(-np.arange(cn) / sr * 150)
        x[:cn] += click / max(np.abs(click).max(), 1e-9) * 0.42
    x = x / max(np.abs(x).max(), 1e-9) * 0.72
    st = np.stack([x, x], 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    return path


def resolve_sfx(sfx, work):
    """Turn plan sfx entries into real files. Accepts a supplied path or a kind."""
    out = []
    for s in sfx:
        f = s.get("file")
        if f and os.path.exists(f):
            out.append({**s, "file": f})
            continue
        kind = s.get("kind") or "stamp"
        out.append({**s, "file": synth_sfx(kind, work)})
    return out


def build_composite(trim, brolls, ovdir, sfx, work):
    out = os.path.join(work, "05_composite.mp4")
    ins = ["-i", trim]
    for p, _ in brolls:
        ins += ["-i", p]
    ins += ["-framerate", str(FPS), "-i", os.path.join(ovdir, "%05d.png")]
    ov_idx = 1 + len(brolls)
    parts, cur = [], "[0:v]"
    for k, (_, b) in enumerate(brolls, start=1):
        parts.append(f"[{k}:v]setpts=PTS-STARTPTS+{b['start']}/TB[b{k}]")
    for k, (_, b) in enumerate(brolls, start=1):
        nxt = f"[x{k}]"
        parts.append(f"{cur}[b{k}]overlay=0:0:eof_action=pass:"
                     f"enable='between(t,{b['start']},{b['end']})'{nxt}")
        cur = nxt
    parts.append(f"{cur}[{ov_idx}:v]overlay=0:0:eof_action=pass:format=auto,"
                 f"format=yuv420p[v]")
    amap = ["-map", "0:a"]
    if sfx:
        for s in sfx:
            ins += ["-i", s["file"]]
        mixes = []
        for j, s in enumerate(sfx):
            idx = ov_idx + 1 + j
            ms = int(s["at"] * 1000)
            parts.append(f"[{idx}:a]adelay={ms}|{ms},volume={s.get('gain',0.85)}[s{j}]")
            mixes.append(f"[s{j}]")
        parts.append("[0:a]" + "".join(mixes) +
                     f"amix=inputs={len(sfx)+1}:duration=first:normalize=0[a]")
        amap = ["-map", "[a]"]
    run(["ffmpeg", "-hide_banner", "-v", "error"] + ins +
        ["-filter_complex", ";".join(parts), "-map", "[v]"] + amap +
        ["-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", out, "-y"], "composite")
    return out


def build_outro(comp, slate, dur, work):
    out = os.path.join(work, "06_joined.mp4")
    fc = (f"[0:v]setsar=1[v0];[0:a]afade=t=out:st={max(0,dur-0.30):.2f}:d=0.30[a0];"
          f"[1:v]scale={W}:{H}:flags=lanczos,fps={FPS},setsar=1,format=yuv420p[v1];"
          f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:1.54[a1];"
          f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", comp, "-i", slate,
         "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", out, "-y"], "outro")
    return out


def master(src, dest):
    """Two-pass loudnorm, then tag the range explicitly."""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af",
         "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = json.loads(err[err.index("{"):err.rindex("}") + 1])
    af = ("loudnorm=I=-14:TP=-1.0:LRA=11"
          f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
          f":offset={m['target_offset']}:linear=true")
    run(["ffmpeg", "-hide_banner", "-v", "error", "-i", src, "-af", af,
         "-c:v", "libx264", "-crf", "17", "-preset", "medium",
         "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
         "-color_range", "tv", "-colorspace", "bt709",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-movflags", "+faststart", dest, "-y"], "master")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--footage", required=True)
    ap.add_argument("--background")
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--overlays", required=True)
    ap.add_argument("--brand", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    an = json.load(open(a.analysis))
    tl = json.load(open(a.timeline))
    plan = json.load(open(a.plan))
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)

    if an.get("is_green_screen") and a.background:
        plate = build_plate(a.footage, a.background, an, a.work)
    elif an.get("vertical"):
        print("[plate] landscape source - converting to 9:16 with a headroom band")
        plate = build_vertical_plate(a.footage, an, a.work)
    else:
        print("[plate] not green screen - using footage as shot")
        plate = a.footage

    trim = build_trim(plate, tl, a.work)
    brolls = build_broll(plan.get("beats", []), a.work)
    comp = build_composite(trim, brolls, a.overlays,
                           resolve_sfx(plan.get("sfx", []), a.work), a.work)
    slate = os.path.join(a.brand, "outro-slate.mov")
    joined = build_outro(comp, slate, tl["out_duration"], a.work) \
        if os.path.exists(slate) else comp
    master(joined, a.out)
    print(f"delivered {a.out}")


if __name__ == "__main__":
    main()
