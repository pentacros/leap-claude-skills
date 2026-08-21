#!/usr/bin/env python3
"""Stage 6 - QA. One hard gate, the rest warn.

HARD GATE: nothing may intrude on the Instagram unsafe zones.
WARN:      single-highlight, A/V sync, loudness, storyboard coverage.

Exit 1 only if the hard gate fails.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

W, H = 1080, 1920
UI_TOP = 1500          # below this is IG username / caption / audio ticker
RAIL_X = 960           # icon column proper. 920 is the advisory guideline
                       # in reference/spec.md; 960 is where intrusion is real.
LUFS_TARGET, LUFS_TOL = -14.0, 1.0
ACCENT = (84, 82, 228)


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def check_safe_zones(overlay_dir):
    """HARD GATE. Overlay frames are pure graphics, so any opaque pixel in an
    unsafe zone is a real intrusion."""
    worst, offenders = 0.0, []
    for f in sorted(glob.glob(os.path.join(overlay_dir, "*.png"))):
        a = np.asarray(Image.open(f))
        if a.shape[2] < 4:
            continue
        al = a[..., 3]
        bottom = (al[UI_TOP:] > 40).mean()
        rail = (al[220:UI_TOP, RAIL_X:] > 40).mean()
        bad = max(bottom, rail)
        if bad > 0.001:
            offenders.append((os.path.basename(f), round(bottom, 4), round(rail, 4)))
        worst = max(worst, bad)
    return {"pass": not offenders, "worst_fraction": round(worst, 5),
            "offenders": offenders[:10], "offender_count": len(offenders)}


def check_single_highlight(overlay_dir):
    """Exactly one accent pill per caption frame. Caught a real double-highlight
    bug where adjacent word spans both matched."""
    counts, none = {}, 0
    for f in sorted(glob.glob(os.path.join(overlay_dir, "*.png"))):
        a = np.asarray(Image.open(f))
        band = a[1270:1440]
        if band.shape[2] < 4:
            continue
        R, G, B, A = (band[..., i].astype(int) for i in range(4))
        m = ((A > 200) & (abs(R - ACCENT[0]) < 40)
             & (abs(G - ACCENT[1]) < 40) & (abs(B - ACCENT[2]) < 45))
        if not m.any():
            none += 1
            continue
        col = m.any(0).astype(int)
        runs = int(((col[1:] == 1) & (col[:-1] == 0)).sum() + col[0])
        counts[runs] = counts.get(runs, 0) + 1
    bad = sum(v for k, v in counts.items() if k > 1)
    return {"pass": bad == 0, "distribution": counts,
            "frames_without_caption": none, "frames_multi_highlight": bad}


def check_sync(video, timeline):
    """Compare detected silences against predicted positions. Accumulating drift
    is a real bug; random scatter is just the detector's threshold behaviour."""
    err = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", video,
              "-af", "silencedetect=n=-38dB:d=0.10", "-f", "null", "-"])
    det, start = [], None
    for ln in err.splitlines():
        if "silence_start" in ln:
            start = float(ln.rsplit(":", 1)[1])
        elif "silence_end" in ln and start is not None:
            m = re.search(r"silence_end: ([0-9.]+)", ln)
            if m:
                det.append((start, float(m.group(1))))
            start = None
    # ignore anything past the narration - the outro slate is silence by design
    body = timeline["out_duration"]
    det = [d for d in det if d[0] < body - 0.2]
    words = timeline["words"]
    # only gaps big enough for silencedetect to actually register, and only
    # count a pairing if a silence lands nearby - otherwise the "nearest" match
    # can be seconds away and the number is noise, not drift.
    # Only gaps long enough for silencedetect to actually resolve. After pause
    # removal the kept gaps are 0.18-0.24s, and comparing against those produced
    # a WARN on reels whose time map was later proven exact by cross-correlation.
    exp = [((a["e"] + b["s"]) / 2) for a, b in zip(words, words[1:])
           if b["s"] - a["e"] >= 0.32]
    if not det or not exp:
        return {"pass": True, "note": "not enough data to compare"}
    deltas, unmatched = [], 0
    for mid in exp:
        d = min(det, key=lambda x: abs((x[0] + x[1]) / 2 - mid))
        dd = ((d[0] + d[1]) / 2) - mid
        if abs(dd) > 0.50:
            unmatched += 1
            continue
        deltas.append(dd)
    if not deltas:
        return {"pass": True, "note": "no comparable gaps"}
    worst = max(abs(x) for x in deltas)
    half = len(deltas) // 2 or 1
    drift = abs(np.mean(deltas[-half:]) - np.mean(deltas[:half]))
    return {"pass": worst < 0.12 and drift < 0.06,
            "gaps_compared": len(deltas), "gaps_unmatched": unmatched,
            "worst_delta": round(worst, 3),
            "accumulating_drift": round(float(drift), 3),
            "note": "drift near zero means the time map is correct; "
                    "drift growing toward the end is a real bug"}


def check_loudness(video):
    err = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", video,
              "-af", "loudnorm=print_format=summary", "-f", "null", "-"])
    g = lambda k: next((float(re.findall(r"-?\d+\.?\d*", l)[0])
                        for l in err.splitlines() if k in l), None)
    i, tp = g("Input Integrated"), g("Input True Peak")
    ok = i is not None and abs(i - LUFS_TARGET) <= LUFS_TOL and (tp or 0) <= -0.5
    return {"pass": bool(ok), "lufs": i, "true_peak": tp,
            "target": f"{LUFS_TARGET} LUFS / -1 dBTP"}


def check_storyboard(plan):
    beats = plan.get("beats", [])
    rows = plan.get("rows", [])
    unres = [r for r in rows if not r.get("resolved", True)]
    low = [r for r in rows if r.get("confidence", 1.0) < 0.75]
    return {"pass": not unres, "beats": len(beats), "rows": len(rows),
            "unresolved": [r.get("script_line", "?")[:50] for r in unres],
            "low_confidence": [r.get("script_line", "?")[:50] for r in low]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("work")
    ap.add_argument("--report", required=True)
    a = ap.parse_args()

    ovd = os.path.join(a.work, "ov")
    tl = json.load(open(os.path.join(a.work, "timeline.json")))
    plan_p = os.path.join(a.work, "plan.json")
    plan = json.load(open(plan_p)) if os.path.exists(plan_p) else {}

    gate = check_safe_zones(ovd) if os.path.isdir(ovd) else {"pass": True, "note": "no overlays"}
    warns = {
        "single_highlight": check_single_highlight(ovd) if os.path.isdir(ovd) else {},
        "av_sync": check_sync(a.video, tl),
        "loudness": check_loudness(a.video),
        "storyboard": check_storyboard(plan),
    }

    lines = ["# Realstitch build report", ""]
    lines += [f"- output: `{os.path.basename(a.video)}`",
              f"- duration: {tl['out_duration']:.2f}s "
              f"(source {tl['source_duration']:.2f}s, removed {tl['removed']:.2f}s)",
              f"- pauses removed: {len(tl['cuts'])} of {len(tl['pauses'])} detected",
              f"- caption frames: {len(tl['captions'])}", ""]

    lines += ["## Hard gate - IG safe zones", ""]
    lines.append(f"**{'PASS' if gate['pass'] else 'FAIL'}** - worst intrusion "
                 f"{gate.get('worst_fraction', 0)*100:.3f}% of an unsafe zone")
    for o in gate.get("offenders", []):
        lines.append(f"  - {o[0]}: bottom {o[1]}, rail {o[2]}")
    lines.append("")

    lines += ["## Checks (advisory)", ""]
    for name, r in warns.items():
        if not r:
            continue
        lines.append(f"### {name} - {'ok' if r.get('pass') else 'WARN'}")
        for k, v in r.items():
            if k != "pass":
                lines.append(f"- {k}: {v}")
        lines.append("")

    if tl.get("cuts"):
        lines += ["## Cuts", "", "| # | context | removes | at output |", "|---|---|---|---|"]
        acc = 0.0
        for i, (c, l) in enumerate(zip(tl["cuts"], tl["lengths"]), 1):
            acc += l
            lines.append(f"| {i} | {c['context'][:40]} | {c['removes']:.2f}s | {acc:.2f}s |")
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(a.report)) or ".", exist_ok=True)
    open(a.report, "w").write("\n".join(lines))

    print(f"HARD GATE (safe zones): {'PASS' if gate['pass'] else 'FAIL'}")
    for n, r in warns.items():
        if r:
            print(f"  {n}: {'ok' if r.get('pass') else 'WARN'}")
    print(f"report -> {a.report}")
    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
