#!/usr/bin/env python3
"""Stage 4 - pause removal and the source->output time map.

This is the highest-risk stage in the pipeline. Every downstream timing (caption
highlights, b-roll windows, graphic beats, SFX) is authored against the original
footage clock and has to be pushed through the map produced here. If the map is
wrong, the whole reel desyncs silently.

Two invariants keep it safe:
  1. Every cut lies strictly inside silence, so no word boundary can ever fall in
     a removed span. resolve() raises rather than returning a wrong answer.
  2. Cuts are snapped to the frame grid, so audio and video trim points agree
     exactly and error cannot accumulate across cuts.
"""
import argparse
import json
import re

FPS = 25
PAUSE_MIN = 0.25          # gaps shorter than this are natural speech rhythm
KEEP_SENTENCE = 0.24      # air left after . ? !
KEEP_CLAUSE = 0.18        # air left after a comma or mid-clause
PUNCH = 0.045             # alternating punch-in that masks each hard cut

# caption chunking
MAX_WORDS, MAX_CHARS = 5, 28
CHUNK_GAP = 0.30
MIN_HOLD = 0.52           # anything shorter reads as a flash; merge it back


def q(v):
    return round(round(v * FPS) / FPS, 4)


def load_words(path):
    d = json.load(open(path))
    w = d.get("words", d) if isinstance(d, dict) else d
    return [x for x in w if x["text"].strip()], d.get("duration")


def find_pauses(words):
    out = []
    for a, b in zip(words, words[1:]):
        gap = b["start"] - a["end"]
        if gap >= PAUSE_MIN:
            sent = bool(re.search(r"[.?!]$", a["text"]))
            out.append({"after": a["text"], "before": b["text"],
                        "at": a["end"], "until": b["start"], "gap": round(gap, 3),
                        "kind": "sentence" if sent else "clause",
                        "keep": KEEP_SENTENCE if sent else KEEP_CLAUSE})
    return out


def plan_cuts(pauses):
    """Split the kept silence evenly either side, so neither the outgoing word's
    tail nor the next word's attack is clipped."""
    cuts = []
    for p in pauses:
        k = p["keep"]
        cs, ce = q(p["at"] + k / 2), q(p["until"] - k / 2)
        if ce - cs >= 1 / FPS:
            cuts.append({"from": cs, "to": ce, "removes": round(ce - cs, 3),
                         "kind": p["kind"], "context": f"{p['after']} | {p['before']}"})
    return cuts


def segments(cuts, duration):
    segs, prev = [], 0.0
    for c in cuts:
        segs.append([prev, c["from"]])
        prev = c["to"]
    segs.append([prev, q(duration)])
    return segs, [q(b - a) for a, b in segs]


def make_resolver(segs, lens):
    def resolve(t):
        for k, (a, b) in enumerate(segs):
            if a - 1e-9 <= t <= b + 1e-9:
                return q(sum(lens[:k]) + (t - a))
        raise ValueError(
            f"time {t:.3f}s falls inside a removed span - a cut was placed over "
            f"speech, which must never happen")
    return resolve


def chunk_captions(words):
    """Rebuild caption groups from REMAPPED times.

    Must not be a shift of the pre-trim chunks: boundaries depend on inter-word
    gaps and those gaps just changed. Regenerating is what eliminates orphan
    one-word flashes.
    """
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        txt = " ".join(x["text"] for x in cur)
        hard = bool(re.search(r"[.?!:]$", w["text"]))
        soft = bool(re.search(r"[,;]$", w["text"]))
        gap = (nxt["o_s"] - w["o_e"]) if nxt else 9
        if (hard or gap >= CHUNK_GAP or len(cur) >= MAX_WORDS
                or (soft and len(cur) >= 3)
                or (nxt and len(txt) + 1 + len(nxt["text"]) > MAX_CHARS)):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    merged = []
    for c in chunks:
        if merged and (c[-1]["o_e"] - c[0]["o_s"]) < MIN_HOLD:
            prev = merged[-1]
            cand = prev + c
            txt = " ".join(x["text"] for x in cand)
            if (len(cand) <= 6 and len(txt) <= 32
                    and (c[0]["o_s"] - prev[-1]["o_e"]) < CHUNK_GAP):
                merged[-1] = cand
                continue
        merged.append(c)

    out = []
    for i, c in enumerate(merged):
        nxt = merged[i + 1][0]["o_s"] if i + 1 < len(merged) else None
        end = c[-1]["o_e"]
        disp = min(nxt - 0.06, end + 0.55) if nxt else end + 0.55
        out.append([{"t": w["text"], "s": w["o_s"], "e": w["o_e"],
                     "out": max(disp, end)} for w in c])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("alignment")
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float)
    a = ap.parse_args()

    words, dur = load_words(a.alignment)
    dur = a.duration or dur
    if not dur:
        raise SystemExit("need --duration (not present in alignment json)")

    pauses = find_pauses(words)
    cuts = plan_cuts(pauses)
    segs, lens = segments(cuts, dur)
    resolve = make_resolver(segs, lens)
    out_dur = q(sum(lens))

    total_pause = sum(p["gap"] for p in pauses)
    print(f"{len(pauses)} pauses >={PAUSE_MIN}s totalling {total_pause:.2f}s "
          f"({total_pause/dur*100:.0f}% of runtime)")
    print(f"{'#':>3} {'context':<34} {'gap':>5} {'keep':>5} {'cut':>14} {'removes':>8}")
    for i, (p, c) in enumerate(zip(pauses, cuts), 1):
        print(f"{i:3d} {c['context'][:34]:<34} {p['gap']:5.2f} {p['keep']:5.2f} "
              f"{c['from']:6.2f}->{c['to']:6.2f} {c['removes']:8.2f}")
    print(f"\n{len(segs)} segments, {len(cuts)} hard cuts -> {out_dur:.2f}s "
          f"(removed {dur-out_dur:.2f}s)")

    for w in words:
        w["o_s"], w["o_e"] = resolve(w["start"]), resolve(w["end"])
    caps = chunk_captions(words)
    print(f"{len(caps)} caption frames on the new timeline")

    json.dump({
        "fps": FPS,
        "source_duration": dur,
        "out_duration": out_dur,
        "removed": round(dur - out_dur, 3),
        "pauses": pauses,
        "cuts": cuts,
        "segments": segs,
        "lengths": lens,
        # alternate the punch so every hard cut changes framing slightly
        "punch": [(i % 2 == 1) for i in range(len(segs))],
        "punch_scale": PUNCH,
        "captions": caps,
        "words": [{"t": w["text"], "s": w["o_s"], "e": w["o_e"],
                   "src_s": w["start"]} for w in words],
    }, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
