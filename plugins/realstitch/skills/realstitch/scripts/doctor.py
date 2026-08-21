#!/usr/bin/env python3
"""Preflight check. Verifies every external dependency Realstitch needs and
prints exact install commands for anything missing. Never installs anything."""
import os
import shutil
import subprocess
import sys

FILTERS = ["chromakey", "despill", "gblur", "alphamerge", "zoompan",
           "colorbalance", "vignette", "loudnorm", "silencedetect", "xfade"]
HN = "/System/Library/Fonts/HelveticaNeue.ttc"


def ffmpeg_filters():
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    return {ln.split()[1] for ln in out.splitlines()
            if len(ln.split()) > 1 and ln[:1] == " "}


def main():
    problems, notes = [], []

    if not shutil.which("ffmpeg"):
        problems.append(("ffmpeg not found", "brew install ffmpeg"))
    else:
        have = ffmpeg_filters()
        if have is None:
            problems.append(("could not query ffmpeg filters", "brew reinstall ffmpeg"))
        else:
            missing = [f for f in FILTERS if f not in have]
            if missing:
                problems.append((f"ffmpeg lacks filters: {', '.join(missing)}",
                                 "brew install ffmpeg   # needs a full build"))
    if not shutil.which("ffprobe"):
        problems.append(("ffprobe not found", "brew install ffmpeg"))

    for mod, pip in (("numpy", "numpy"), ("PIL", "pillow")):
        try:
            __import__(mod)
        except ImportError:
            problems.append((f"python module '{mod}' not installed",
                             f"python3 -m pip install {pip}"))

    if not os.environ.get("ELEVENLABS_API_KEY"):
        problems.append(("ELEVENLABS_API_KEY not set (needed for stage 1)",
                         "export ELEVENLABS_API_KEY='your-key-here'   "
                         "# add to ~/.zshrc to persist"))

    if not os.path.exists(HN):
        notes.append(f"Helvetica Neue not found at {HN} - will fall back to Arial. "
                     "Type will not match brand exactly.")

    if problems:
        print("PREFLIGHT FAILED\n")
        for what, fix in problems:
            print(f"  x {what}")
            print(f"    fix: {fix}\n")
        print("Resolve the above, then re-run. Nothing was installed.")
        return 1

    print("PREFLIGHT OK - ffmpeg filters, python modules and API key all present.")
    for n in notes:
        print(f"  ! {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
