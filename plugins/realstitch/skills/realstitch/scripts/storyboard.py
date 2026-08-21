#!/usr/bin/env python3
"""Stage 3 - resolve the storyboard against the alignment.

Input is either a CSV/TSV export or a rows JSON. A screenshot is read by the
agent with vision and handed over as rows JSON:

    [{"script_line": "...", "visual": "...", "notes": "..."}, ...]

Script lines are the time anchors. They are fuzzy-matched so truncated cells
(screenshots cut text off), curly-vs-straight quotes and small script drift all
still resolve. Timecodes are never used - they would break the moment pauses are
removed.
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys

MATCH_MIN = 0.55          # below this a row is treated as unresolved
LOW_CONF = 0.75           # reported as shaky but still built

NO_CUTAWAY = re.compile(r"\bna\b|keep the person|as is|no change", re.I)


def norm(s):
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9' ]+", " ", s).split()


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif", ".bmp", ".tiff"}


def die(msg, fix=None):
    print(f"ERROR: {msg}", file=sys.stderr)
    if fix:
        print(f"fix: {fix}", file=sys.stderr)
    sys.exit(1)


def load_rows(path):
    # A screenshot cannot be parsed here - this script has no vision. Reading the
    # table out of an image is the calling agent's job, not Python's. Fail with
    # instructions rather than trying to CSV-decode a PNG.
    if os.path.splitext(path)[1].lower() in IMAGE_EXT:
        die(f"{os.path.basename(path)} is an image - this script cannot read it",
            "the AGENT must transcribe the screenshot first: read the image, then "
            "write a 3-column CSV (script_line,visual,notes) beside it and pass "
            "that instead. Copy each Script Line cell verbatim, and if a cell is "
            "visually truncated write only the part you can actually read - "
            "fuzzy matching handles the rest.")
    return _load_rows_text(path)


def _load_rows_text(path):
    if path.lower().endswith(".json"):
        return json.load(open(path))
    delim = "\t" if path.lower().endswith((".tsv", ".tab")) else ","
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.reader(fh, delimiter=delim):
            cells = [c.strip() for c in r]
            if not any(cells):
                continue
            # tolerate a leading row-number column and a header row
            if cells and re.fullmatch(r"\d+", cells[0] or ""):
                cells = cells[1:]
            if len(cells) >= 2 and cells[0].lower().startswith("script"):
                continue
            rows.append({"script_line": cells[0] if cells else "",
                         "visual": cells[1] if len(cells) > 1 else "",
                         "notes": cells[2] if len(cells) > 2 else ""})
    return rows


def match_line(line, words, from_idx=0):
    """Locate a script line in the word stream.

    Slides a window sized to the line and scores by sequence similarity. Handles
    truncation because a short line simply matches a short window.
    """
    toks = norm(line)
    if not toks:
        return None
    # Storyboard rows are spoken in order, so a row may only match at or after
    # where the previous row ended. Without this, two rows sharing a distinctive
    # word (both these rows contain "STEM") collapse onto the same span and one
    # graphic gets trimmed out of existence.
    words = words[from_idx:]
    if not words:
        return None
    wt = [norm(w["t"])[0] if norm(w["t"]) else "" for w in words]
    n = len(toks)
    best, bi, bs = 0.0, None, n
    # Search a range of window lengths - the spoken form rarely has exactly the
    # same token count as the written line (contractions, numerals read aloud).
    cands = {n, max(2, n - 3), max(2, n - 2), max(2, n - 1), n + 1, n + 2, n + 3}
    for i in range(0, max(1, len(wt) - max(2, n // 2))):
        for span in cands:
            seg = wt[i:i + span]
            if not seg:
                continue
            r = difflib.SequenceMatcher(None, toks, seg).ratio()
            if r > best:
                best, bi, bs = r, i, span
    if bi is None or best < MATCH_MIN:
        return None
    # Use the span that actually scored best. Using the script's token count here
    # instead made rows overshoot their speech - one ran 1.4s past its last word
    # and swallowed the start of the next sentence, and the sequential cursor
    # then propagated that drift to every following row.
    span = min(len(words) - bi, max(2, bs))
    # Pin the end precisely by locating the line's final tokens near that edge.
    tail = [t for t in toks[-3:] if t]
    if tail and span >= len(tail):
        bt, be = 0.0, span
        for end in range(max(len(tail), span - 4), min(len(words) - bi, span + 5) + 1):
            seg = wt[bi + end - len(tail):bi + end]
            if len(seg) != len(tail):
                continue
            r = difflib.SequenceMatcher(None, tail, seg).ratio()
            if r > bt:
                bt, be = r, end
        if bt > 0.55:
            span = be
    return {"start": words[bi]["s"], "end": words[bi + span - 1]["e"],
            "end_idx": from_idx + bi + span,
            "confidence": round(best, 3),
            "first_word": words[bi]["t"], "word_index": from_idx + bi, "span": span}


# Assets the pipeline owns. They must never be offered as generic b-roll -
# matching "money/cash element" to the follow button is worse than failing.
RESERVED = ("follow button", "outrow slate", "outro slate", "question")
# Words that carry no matching signal - almost every storyboard cell has them.
HINT_STOP = {
    "element", "elements", "style", "pop", "up", "sound", "and", "or", "of",
    "the", "a", "an", "in", "on", "with", "add", "show", "put", "maybe", "is",
    "needs", "to", "be", "for", "from", "use", "view", "shot", "clip", "broll",
    "b", "roll", "sfx", "template", "interface", "icon", "video", "animation",
}
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _reserved(name):
    n = name.lower()
    return any(r in n for r in RESERVED)



HINTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "reference", "clip-hints.json")


def clip_hint(path):
    """Measured in-point and crop for a known clip.

    Defaulting to src_in 0.1 and a centre crop is how a globe over North America
    ended up on the line about the UK and Europe. Where a clip is known, use the
    measured numbers; otherwise say so, loudly, in the plan.
    """
    try:
        hints = json.load(open(HINTS_PATH))["hints"]
    except Exception:
        return None
    name = os.path.basename(path).lower()
    for h in hints:
        if h["match"].lower() in name:
            return h
    return None

def find_clip(hint, assets):
    """Pick the b-roll clip whose filename genuinely fits the instruction.

    Returns None rather than guessing. A confident miss is recoverable - the row
    fails loudly and the user fixes it. A wrong match ships silently.
    """
    if not assets or not os.path.isdir(assets):
        return None
    vids = [f for f in os.listdir(assets)
            if f.lower().endswith((".mp4", ".mov", ".m4v")) and not _reserved(f)]
    if not vids:
        return None
    ht = {t for t in norm(hint) if t not in HINT_STOP and len(t) > 2}
    if not ht:
        return None
    scored = []
    for v in vids:
        vt = {t for t in norm(os.path.splitext(v)[0])
              if t not in HINT_STOP and len(t) > 2}
        shared = ht & vt
        # Require a real content word in common. Fuzzy ratios over sorted token
        # strings are noise at this length and used to pass on nothing.
        if shared:
            scored.append((len(shared), sorted(shared), v))
    if not scored:
        return None
    scored.sort(key=lambda r: (-r[0], r[2]))   # deterministic on ties
    return os.path.join(assets, scored[0][2])


def find_images(assets):
    """Every non-reserved image asset, for a logo/seal pop-up beat.

    Filenames don't always name the institution (NYU's file is Crest_1_2C.png),
    so a logo beat uses the images that are present rather than trying to match
    each name individually.
    """
    if not assets or not os.path.isdir(assets):
        return []
    return [os.path.join(assets, f) for f in sorted(os.listdir(assets))
            if f.lower().endswith(IMG_EXT) and not _reserved(f)]


def word_time(words, label, lo=None, hi=None):
    """Find when a specific word is spoken, so a graphic can land on it."""
    t = norm(label)
    if not t:
        return None
    best, bt = 0.0, None
    for w in words:
        if lo is not None and w["s"] < lo - 0.5:
            continue
        if hi is not None and w["s"] > hi + 0.5:
            continue
        wn = norm(w["t"])[0] if norm(w["t"]) else ""
        if not wn:
            continue
        r = difflib.SequenceMatcher(None, t[0], wn).ratio()
        # An acronym is often spoken as the expanded word ("OPT" -> "Optional"),
        # which scores below the ratio threshold. Treat a clean prefix or
        # containment as a strong match instead of dropping to the row start.
        if wn.startswith(t[0]) or t[0].startswith(wn) or t[0] in wn:
            r = max(r, 0.92)
        if r > best:
            best, bt = r, w["s"]
    return bt if best > 0.7 else None


def interpret(row, span, assets, notes_global, words):
    """Map a plain-language Visual instruction to a beat."""
    vis = row.get("visual", "")
    v = vis.lower()
    s, e = span["start"], span["end"]

    if NO_CUTAWAY.search(v):
        return {"type": "hold", "start": s, "end": e,
                "no_cutaway": True, "note": vis}

    # Logo / seal pop-up. Filenames rarely name the institution, so use the
    # images that are actually present.
    if re.search(r"\blogos?\b|\bseal\b|\bcrest\b|\bemblem\b|\buniversit", v):
        imgs = [p for p in find_images(assets)
                if not re.search(r"dollar|money|cash|rupee", os.path.basename(p), re.I)]
        if not imgs:
            die(f"row asks for logos but no image assets were found: {vis!r}",
                "put the logo PNGs in the assets folder")
        step = (e - s) / max(len(imgs), 1)
        return {"type": "logo_popup", "start": s, "end": e, "zone": "top",
                "images": [{"path": p, "at": round(s + i * step, 2)}
                           for i, p in enumerate(imgs)], "note": vis}

    # Cost / money beat. No stock clip needed - this is a graphic plus a hit.
    if re.search(r"\bmoney\b|\bcash\b|\bdollar\b|\bcost\b|\bexpensive\b|\bfees?\b", v):
        money = [p for p in find_images(assets)
                 if re.search(r"dollar|money|cash|rupee|cost", os.path.basename(p), re.I)]
        if money:
            # Land it on the word that motivates it ("expensive"), with a short
            # dwell. Spanning the whole sentence left a dollar sign hanging over
            # unrelated words all the way to "competitive".
            trig = None
            for kw in ("expensive", "cost", "costly", "money", "fees"):
                trig = word_time(words, kw, s, e)
                if trig is not None:
                    break
            st = trig if trig is not None else s
            return {"type": "logo_popup",
                    "start": round(max(s, st - 0.20), 2),
                    # Short dwell: it belongs to "expensive", and holding it
                    # through "and competitive" made it read as decoration.
                    "end": round(min(e + 0.20, st + 1.25), 2), "zone": "top",
                    "images": [{"path": money[0], "at": round(max(s, st - 0.10), 2)}],
                    "note": vis}
        return {"type": "cost_card", "start": s, "end": e, "zone": "top",
                "label": "EXPENSIVE", "note": vis}

    if "bullet" in v:
        # Two ways storyboards write lists: dash rows, or inline after a colon
        # ("bullet points: Budget, career goals and profile strength"). Only
        # supporting dashes silently produced a card with a kicker and no items.
        labels = re.findall(r"[-\u2022*]\s*([A-Za-z][A-Za-z /&]+)", vis)
        if not labels:
            m = re.search(r"bullet\s*points?\s*[:\-]\s*(.+)$", vis, re.I)
            tail = m.group(1) if m else re.sub(r"^.*?[:\-]\s*", "", vis)
            tail = tail.rstrip(". ")
            parts = re.split(r",|\band\b", tail)
            labels = [x.strip() for x in parts if len(x.strip()) > 2]
        items = []
        for lab in labels:
            lab = lab.strip()
            # stagger each row onto the word actually being spoken
            at = word_time(words, lab, s, e)
            items.append({"label": lab.title(), "at": at if at is not None else s})
        if not items:
            die(f"bullet row produced no items: {vis!r}",
                "list them after a colon or as dash rows")
        first = min((i["at"] for i in items), default=s)
        last = max((i["at"] for i in items), default=e)
        return {"type": "bullets",
                "start": round(max(0.0, first - 0.6), 2),
                "end": round(min(e + 0.5, last + 3.0), 2),
                "items": items, "zone": "top", "note": vis}

    if "follow" in v or "ig page" in v or "instagram page" in v:
        return {"type": "follow", "start": max(0.0, s - 0.5), "end": e,
                "y": 300, "note": vis, "zone": "top"}

    if "highlight" in v or "in a box" in v or "highlighted element" in v:
        # Don't depend on quotes surviving: they get stripped by CSV round-trips
        # and mangled into curly quotes by Sheets. Fall back through straight
        # quotes, curly quotes, then a capitalised mid-sentence word, then any
        # word of this line that the alignment actually contains.
        word = None
        # Explicit form first - "highlight: OPT" / "highlight - STEM PROGRAM".
        m = re.search(r"(?:highlight|highlighted|label)\s*[:\-]\s*(.+?)\s*$",
                      vis, re.I)
        if m:
            word = m.group(1).strip().rstrip(".")
        if not word:
            for pat in (r'"([^"]+)"', r"[“”]([^“”]+)[“”]", r"'([^']+)'"):
                q = re.findall(pat, vis)
                if q:
                    word = q[0].strip()
                    break
        if not word:
            # Acronyms and product names are usually shouted (OPT, STEM), so take
            # all-caps tokens before falling back to mid-sentence capitals.
            allcaps = re.findall(r"\b([A-Z]{2,})\b", vis)
            caps = re.findall(r"(?<!^)(?<![.!?]\s)\b([A-Z][a-z]{2,})\b", vis)
            word = (allcaps or caps or [None])[0]
        if not word:
            die(f"cannot tell which word to highlight from: {vis!r}",
                'use "highlight: WORD", or quote it: \'"Leap" in a box\'')
        # A multi-word target ("STEM PROGRAM") may only partly appear in speech
        # ("STEM-designated"); try the phrase, then its first token.
        at = word_time(words, word, s, e)
        if at is None and " " in word:
            at = word_time(words, word.split()[0], s, e)
            word = word.split()[0]
        st = at if at is not None else s
        return {"type": "word_card", "start": round(st - 0.35, 2),
                "end": round(st + 2.2, 2),
                "word": word, "note": vis, "zone": "top"}

    if "qna" in v or "question overlay" in v or ("question" in v and "template" in v):
        return {"type": "sticker", "start": max(0.3, s), "end": e,
                "big_until": min(e, s + 2.5), "note": vis,
                "zone": "top", "persistent": "all over the video" in notes_global}

    clip = find_clip(vis, assets)
    if clip:
        h = clip_hint(clip)
        beat = {"type": "broll", "start": s, "end": e, "clip": clip,
                "src_in": (h["src_in"] if h else 0.1), "note": vis}
        if h:
            beat["crop_x"] = h["crop_x"]
            beat["hint_why"] = h["why"]
        else:
            beat["unhinted"] = ("no measured in-point/crop for this clip - starting "
                                "at 0.1s with a centre crop, which may miss the "
                                "subject of the shot")
        if "sfx" in v or "stamp" in v:
            beat["wants_sfx"] = True
        return beat

    return {"type": "unresolved", "start": s, "end": e, "note": vis}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard", help="CSV/TSV export, or rows JSON from a screenshot")
    ap.add_argument("timeline", help="timeline.json (output-timeline word times)")
    ap.add_argument("--assets")
    ap.add_argument("--analysis", help="analysis.json, for the measured graphics band")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tl = json.load(open(a.timeline))
    words = tl["words"]
    rows = load_rows(a.storyboard)
    if not rows:
        sys.exit("storyboard has no usable rows")

    notes = " ".join(r.get("notes", "") for r in rows).lower()
    beats, resolved, unresolved = [], [], []
    cursor = 0          # rows are spoken in order; never match backwards
    for r in rows:
        span = match_line(r["script_line"], words, cursor)
        rec = dict(r)
        if not span:
            rec["resolved"] = False
            unresolved.append(rec)
            resolved.append(rec)
            continue
        rec.update({"resolved": True, "confidence": span["confidence"],
                    "start": span["start"], "end": span["end"]})
        resolved.append(rec)
        cursor = span.get("end_idx", cursor)
        b = interpret(r, span, a.assets, notes, words)
        if b["type"] == "unresolved":
            rec["resolved"] = False
            rec["why"] = "no clip or graphic matched the Visual instruction"
            unresolved.append(rec)
        b["row"] = r["script_line"][:60]
        if b["type"] == "sticker":
            # The sticker's copy must be THIS video's question. Carrying only the
            # truncated display row is how a reel once shipped a question the
            # storyboard never asked for.
            b["question"] = r["script_line"].strip()
        b["row_end"] = span["end"]
        beats.append(b)

    # Clamp every top-zone graphic to its own spoken row. Bullets were running
    # ~1.9s past "profile strength." and were still on screen under the next
    # sentence's caption.
    for b in beats:
        if b.get("zone") != "top" or b.get("persistent"):
            continue
        row_end = b.get("row_end")
        if row_end and b["end"] > row_end + 0.35:
            b["end"] = round(row_end + 0.35, 2)
            b["clamped_to_row"] = True

    holds = [(b["start"], b["end"]) for b in beats if b.get("no_cutaway")]
    for b in beats:
        if b["type"] == "broll":
            if any(hs < b["end"] and he > b["start"] for hs, he in holds):
                b["conflict"] = "overlaps a 'keep the face' line"

    # --- collision guards -------------------------------------------------
    # 1. Two rows anchoring to the same span. Happens when one row holds
    #    DISPLAYED text (a sticker) that near-duplicates a SPOKEN line - e.g.
    #    "a previous visa rejection" vs "a past visa rejection". Left alone, the
    #    sticker gets buried under the cutaway that shares its span.
    dupes = []
    for i, x in enumerate(beats):
        for y in beats[i + 1:]:
            if abs(x["start"] - y["start"]) < 0.25 and abs(x["end"] - y["end"]) < 0.25:
                dupes.append((x, y))
    for x, y in dupes:
        pair = {x["type"], y["type"]}
        if "broll" in pair and pair & {"sticker", "bullets", "word_card", "follow"}:
            g = x if x["type"] != "broll" else y
            g["conflict"] = ("shares its anchor with a full-frame cutaway - the "
                             "script lines are near-identical, so this graphic "
                             "needs its own anchor or an explicit duration")

    # 2. A persistent sticker should run until the next top-zone graphic, not
    #    just to the end of its own line ("Question Overlay all over the video").
    tops = sorted([b for b in beats if b.get("zone") == "top"
                   and b["type"] != "sticker"], key=lambda b: b["start"])
    for b in beats:
        if b["type"] == "sticker" and b.get("persistent"):
            # "all over the video" means from the top, not from this row's anchor
            b["start"] = 0.3
            nxt = next((t["start"] for t in tops if t["start"] > b["start"]), None)
            b["end"] = round((nxt - 0.3) if nxt else tl["out_duration"], 2)
            b["big_until"] = round(min(b["start"] + 2.5, b["end"] - 0.5), 2)
            b.pop("conflict", None)   # anchor no longer matters once pinned to 0

    # 3a. A top-zone card under a full-frame cutaway is invisible. Pull it clear
    #     rather than letting it silently disappear.
    brolls = [b for b in beats if b["type"] == "broll"]
    for g in [b for b in beats if b.get("zone") == "top"]:
        for br in brolls:
            if g["start"] < br["end"] and g["end"] > br["start"]:
                if g["end"] - br["end"] > br["start"] - g["start"]:
                    g["start"] = round(br["end"] + 0.10, 2)      # start after it
                else:
                    g["end"] = round(br["start"] - 0.10, 2)      # end before it
                g["moved_for_broll"] = os.path.basename(br.get("clip", "broll"))
        if g["end"] - g["start"] < 0.8:
            g["dropped"] = "left under a cutaway with no room"

    # 3. No two top-zone graphics may be on screen at once.
    tz = sorted([b for b in beats if b.get("zone") == "top"], key=lambda b: b["start"])
    MIN_CARD = 0.65
    for x, y in zip(tz, tz[1:]):
        if x["end"] > y["start"]:
            x["end"] = round(y["start"] - 0.15, 2)
            x["trimmed_for"] = y["type"]
            # Trimming must never invert the beat. It silently did, producing a
            # card whose end preceded its start - it rendered nothing at all.
            if x["end"] - x["start"] < MIN_CARD:
                x["dropped"] = (f"collides with the following {y['type']} and "
                                f"cannot be trimmed to a visible length")
    beats[:] = [b for b in beats if not b.get("dropped")
                or b.get("zone") != "top"]

    band = None
    if a.analysis and os.path.exists(a.analysis):
        band = json.load(open(a.analysis)).get("graphics_band")
    if band:
        print(f"graphics band from analysis: y{band[0]}-{band[1]}")
    else:
        print("! no measured graphics band - cards may overlap the speaker")
    plan = {"graphics_band": band,
            "rows": resolved, "beats": [b for b in beats if b["type"] != "hold"],
            "holds": holds,
            "globals": {"persistent_question_overlay": "all over the video" in notes,
                        "subtitles_throughout": "subtitle" in notes},
            "sfx": [{"kind": ("cash" if b.get("wants_sfx") == "cash" else "stamp"),
                     "at": round(b["start"] + 0.40, 2), "gain": 0.85,
                     "for": b["type"]}
                    for b in beats if b.get("wants_sfx")]}

    print(f"{len(resolved)} rows, {len(plan['beats'])} beats, {len(holds)} no-cutaway zones")
    for r in resolved:
        tag = "ok " if r.get("resolved") else "FAIL"
        c = r.get("confidence")
        cs = f"{c:.2f}" if c else "  - "
        print(f"  [{tag}] {cs}  {r['script_line'][:44]:<44} -> {r.get('visual','')[:40]}")
    low = [r for r in resolved if r.get("resolved") and r.get("confidence", 1) < LOW_CONF]
    if low:
        print(f"! {len(low)} rows matched weakly - verify these anchors")

    if unresolved:
        print("\nSTOP - unresolvable storyboard rows:")
        for r in unresolved:
            print(f"  - {r['script_line'][:60]!r}: "
                  f"{r.get('why', 'script line not found in the alignment')}")
        json.dump(plan, open(a.out, "w"), indent=1)
        sys.exit(1)

    json.dump(plan, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
