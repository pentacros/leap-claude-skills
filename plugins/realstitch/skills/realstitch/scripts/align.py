#!/usr/bin/env python3
"""Stage 1 - forced alignment. Everything downstream reads this output.

Extracts audio, transcribes it if no script is supplied, then aligns to get
word-level timings. Emits a normalised JSON that the rest of the pipeline uses.

The output format is deliberately flat and provider-agnostic:
    {"words": [{"text": "...", "start": 0.08, "end": 0.52, "loss": 0.001}, ...],
     "duration": 46.56, "source": "...", "engine": "elevenlabs"}
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

API = "https://api.elevenlabs.io/v1"
# Words whose alignment confidence is worse than this get flagged in the report.
LOSS_WARN = 2.0
# Median offset between real silences and the alignment's gaps. Correct pairings
# measure 0.033-0.048s; wrong takes 0.329-0.378s. See validate_reuse().
SILENCE_ALIGN_MAX = 0.15


def die(msg, fix=None):
    print(f"ERROR: {msg}", file=sys.stderr)
    if fix:
        print(f"fix: {fix}", file=sys.stderr)
    sys.exit(1)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    return float(out)


def extract_audio(video, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest, "-y"],
                   check=True)


def post(url, key, files, data=None):
    try:
        import requests
    except ImportError:
        die("python module 'requests' not installed",
            "python3 -m pip install requests")
    r = requests.post(url, headers={"xi-api-key": key}, files=files,
                      data=data or {}, timeout=600)
    if r.status_code >= 400:
        die(f"ElevenLabs returned {r.status_code}: {r.text[:400]}")
    return r.json()


def transcribe(wav, key):
    with open(wav, "rb") as fh:
        j = post(f"{API}/speech-to-text", key,
                 {"file": ("audio.wav", fh, "audio/wav")},
                 {"model_id": "scribe_v1"})
    text = j.get("text", "").strip()
    if not text:
        die("transcription came back empty")
    return text


def align(wav, text, key):
    with open(wav, "rb") as fh:
        return post(f"{API}/forced-alignment", key,
                    {"file": ("audio.wav", fh, "audio/wav")},
                    {"text": text})


def normalise(raw):
    """Flatten the provider response to our schema.

    CRITICAL: the response interleaves whitespace tokens that carry their own
    start/end times - typically ~half of all tokens. They sit exactly where the
    silence is, so if they are not dropped, every inter-word gap computes as ~0
    and pause detection finds nothing.
    """
    toks = raw.get("words") or raw.get("characters") or []
    words, dropped = [], 0
    for t in toks:
        txt = (t.get("text") or t.get("char") or "")
        if not txt.strip():
            dropped += 1
            continue
        words.append({"text": txt.strip(),
                      "start": round(float(t["start"]), 3),
                      "end": round(float(t["end"]), 3),
                      "loss": float(t.get("loss", 0.0))})
    return words, dropped


def detect_silences(path, thresh=-35, mind=0.30):
    """Substantial silences in the audio itself, independent of any alignment."""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
         f"silencedetect=n={thresh}dB:d={mind}", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    out, start = [], None
    for ln in err.splitlines():
        if "silence_start" in ln:
            try:
                start = float(ln.rsplit(":", 1)[1])
            except ValueError:
                start = None
        elif "silence_end" in ln and start is not None:
            m = re.search(r"silence_end: ([0-9.]+)", ln)
            if m:
                out.append((start, float(m.group(1))))
            start = None
    return out


def validate_reuse(raw, words, footage, dur):
    """An alignment may ONLY be reused for the exact file it was made from.

    Reusing a stale alignment is the worst silent failure in this pipeline: the
    build succeeds, looks fine in stills, and every caption, cut and b-roll beat
    is anchored to speech that is no longer there. Observed in the wild - a reel
    folder held a 52.06s render beside an alignment describing a 59.76s cut,
    complete with an opening line that had been removed.

    Cheap guards, in order of strength.
    """
    if not words:
        die("alignment contains no words")
    span = words[-1]["end"]

    # 1. Our own output records what it was built from - that is authoritative.
    if isinstance(raw, dict) and raw.get("duration"):
        if abs(float(raw["duration"]) - dur) > 0.5:
            die(f"alignment was made from a {float(raw['duration']):.2f}s file but "
                f"{os.path.basename(footage)} is {dur:.2f}s - it is stale",
                "drop --reuse and let stage 1 align this footage properly")
        if raw.get("source") and os.path.basename(raw["source"]) != os.path.basename(footage):
            print(f"! alignment names a different source file "
                  f"({os.path.basename(raw['source'])}) but the duration matches - "
                  f"continuing, verify this is a rename not a different take")

    # 2. Span must at least fit inside the footage.
    if span > dur + 0.3:
        die(f"alignment runs to {span:.2f}s but the footage is only {dur:.2f}s - "
            f"it describes a different (longer) cut",
            "drop --reuse and re-align")
    if span < dur * 0.60:
        die(f"alignment covers only {span:.2f}s of {dur:.2f}s of footage "
            f"({span/dur*100:.0f}%) - it likely belongs to a different take",
            "drop --reuse and re-align")

    # 3. THE ONE THAT MATTERS: does the alignment's silence structure match the
    #    audio's? Duration is a useless fingerprint - two takes of the same
    #    script run to nearly the same length while their internal timing differs
    #    completely (2 pauses / 2.24s vs 15 pauses / 10.34s on real takes here).
    #    Every substantial silence in the audio must sit in a gap the alignment
    #    also believes is silent.
    sil = detect_silences(footage)
    if not sil:
        print("! could not detect silences - falling back to duration checks only")
        print(f"reuse accepted on duration: {span:.2f}s of {dur:.2f}s")
        return
    gaps = [(a["end"], b["start"]) for a, b in zip(words, words[1:])
            if b["start"] - a["end"] >= 0.22]
    if not gaps:
        print("! alignment has no gaps to compare - accepted on duration only")
        return
    # For each real silence, how far is the nearest gap the alignment believes in?
    # Measured on four real pairings: correct ones land at 0.033-0.048s (about one
    # frame); wrong takes at 0.329-0.378s. A ~7x separation, so 0.15s sits in open
    # space. (Counting non-overlapping silences only separates 2x - too tight.)
    offs = sorted(min(abs((g[0] + g[1]) / 2 - (s[0] + s[1]) / 2) for g in gaps)
                  for s in sil)
    median = offs[len(offs) // 2]
    if median > SILENCE_ALIGN_MAX:
        die(f"the audio's silences sit a median {median:.3f}s away from where this "
            f"alignment says the gaps are (correct pairings land under "
            f"{SILENCE_ALIGN_MAX}s) - this alignment is for a DIFFERENT TAKE, even "
            f"though the durations are similar ({span:.2f}s vs {dur:.2f}s)",
            "drop --reuse; a stale alignment desyncs every caption, cut and beat")
    print(f"reuse validated: {len(sil)} audio silences sit a median {median:.3f}s "
          f"from the alignment's gaps, span {span:.2f}s of {dur:.2f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("footage")
    ap.add_argument("--out", required=True)
    ap.add_argument("--script", help="path to the exact script text, if you have it")
    ap.add_argument("--reuse", help="existing alignment json to normalise instead of calling the API")
    a = ap.parse_args()

    if a.reuse:
        raw = json.load(open(a.reuse))
        words, dropped = normalise(raw)
        validate_reuse(raw, words, a.footage, probe_duration(a.footage))
        engine = "reused"
    else:
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            die("ELEVENLABS_API_KEY not set",
                "export ELEVENLABS_API_KEY='your-key-here'")
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "a.wav")
            extract_audio(a.footage, wav)
            if a.script:
                text = open(a.script).read().strip()
                src = "supplied script"
            else:
                text = transcribe(wav, key)
                src = "auto-transcribed"
            print(f"aligning against {src} ({len(text.split())} words)")
            raw = align(wav, text, key)
        words, dropped = normalise(raw)
        engine = "elevenlabs"

    if not words:
        die("alignment produced no words")

    suspect = [w for w in words if w["loss"] > LOSS_WARN]
    out = {"engine": engine,
           "source": os.path.abspath(a.footage),
           "duration": probe_duration(a.footage),
           "words": words,
           "low_confidence": [{"text": w["text"], "at": w["start"],
                               "loss": round(w["loss"], 2)} for w in suspect]}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1)

    print(f"{len(words)} words ({dropped} whitespace tokens dropped) "
          f"spanning {words[0]['start']:.2f}-{words[-1]['end']:.2f}s "
          f"of {out['duration']:.2f}s")
    if suspect:
        print(f"! {len(suspect)} low-confidence words - carry these into the build report:")
        for w in suspect[:12]:
            print(f"    {w['start']:6.2f}s  {w['text']!r}  loss={w['loss']:.1f}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
