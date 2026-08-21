#!/usr/bin/env python3
"""Emit an SRT sidecar from the planned timeline.

Two things to understand about the timings here:

1. Times come from the OUTPUT timeline (post pause-removal), so the file matches
   the delivered reel, not the raw footage. Using source times would leave the
   subtitles drifting further out of sync with every cut that was removed.

2. Subtitle grouping is deliberately NOT the 5-word burned-in caption rhythm.
   Those exist to be read one beat at a time under a karaoke highlight; a
   subtitle file is read as text, so it follows normal practice - up to two
   lines, ~42 characters each, broken on sentence and clause boundaries.
   Pass --match-captions if you want it to mirror the on-screen chunks exactly.
"""
import argparse
import json
import re

MAX_LINE = 42          # characters per line, standard readable width
MAX_LINES = 2
MIN_DUR = 0.7
MAX_DUR = 6.0


def ts(t):
    if t < 0:
        t = 0.0
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


def wrap(text):
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) > MAX_LINE and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines[:MAX_LINES] if len(lines) <= MAX_LINES else \
        [" ".join(lines[:-1]), lines[-1]]


def group(words):
    """Group words into subtitle cues on clause boundaries and length."""
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["t"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        dur = cur[-1]["e"] - cur[0]["s"]
        hard = bool(re.search(r"[.?!]$", w["t"]))
        soft = bool(re.search(r"[,;:]$", w["t"]))
        gap = (nxt["s"] - w["e"]) if nxt else 9
        over = nxt and len(text) + 1 + len(nxt["t"]) > MAX_LINE * MAX_LINES
        if hard or over or dur >= MAX_DUR or gap >= 0.45 or (soft and dur >= 2.2):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    return cues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--out", required=True)
    ap.add_argument("--match-captions", action="store_true",
                    help="mirror the 5-word on-screen chunks instead of "
                         "regrouping for readability")
    a = ap.parse_args()

    tl = json.load(open(a.timeline))
    if a.match_captions:
        cues = [[dict(t=w["t"], s=w["s"], e=w["e"]) for w in c]
                for c in tl["captions"]]
    else:
        cues = group(tl["words"])

    out, n = [], 0
    for i, c in enumerate(cues):
        start, end = c[0]["s"], c[-1]["e"]
        # Don't let a cue flash; extend into the following gap when there is room.
        if end - start < MIN_DUR:
            nxt = cues[i + 1][0]["s"] if i + 1 < len(cues) else end + MIN_DUR
            end = min(nxt - 0.05, start + MIN_DUR)
        n += 1
        text = "\n".join(wrap(" ".join(w["t"] for w in c)))
        out.append(f"{n}\n{ts(start)} --> {ts(end)}\n{text}\n")

    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    dur = tl.get("out_duration")
    print(f"wrote {a.out}: {n} cues over {dur}s "
          f"({'caption-matched' if a.match_captions else 'readability-grouped'})")


if __name__ == "__main__":
    main()
