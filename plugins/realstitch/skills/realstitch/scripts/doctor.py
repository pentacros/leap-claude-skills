#!/usr/bin/env python3
"""Preflight check. Verifies every external dependency and prints an exact,
copy-pasteable install command for anything missing.

Does not install anything itself - a script should not silently change a machine.
It prints `--install-cmd` so a caller can run the whole fix in one line after the
user agrees.
"""
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

    # 'requests' was missing from this list, so preflight passed and then stage 1
    # (align.py) died with an ImportError. Every module the pipeline imports
    # belongs here.
    for mod, pip in (("numpy", "numpy"), ("PIL", "pillow"), ("requests", "requests")):
        try:
            __import__(mod)
        except ImportError:
            problems.append((f"python module '{mod}' not installed",
                             f"pip3 install --user --break-system-packages {pip}"))

    import envfile
    if not envfile.load("ELEVENLABS_API_KEY"):
        problems.append(("ELEVENLABS_API_KEY not set - stage 1 (forced alignment) "
                         "cannot run without it",
                         "Open the .env file in the project folder and paste the key "
                         "after ELEVENLABS_API_KEY= .\n"
                         "         Ask Aniket (aniket.rajput@leapfinance.com) for it.\n"
                         "         It stays on this machine. Never commit it."))

    if not os.path.exists(HN):
        notes.append(f"Helvetica Neue not found at {HN} - will fall back to Arial. "
                     "Type will not match brand exactly.")

    if problems:
        print("PREFLIGHT FAILED\n")
        for what, fix in problems:
            print(f"  x {what}")
            print(f"    fix: {fix}\n")
        installs = [c for _, c in problems if c.startswith(("brew ", "pip3 "))]
        if installs:
            brew = sorted({c for c in installs if c.startswith("brew ")})
            pips = sorted({c.split()[-1] for c in installs if c.startswith("pip3 ")})
            one = []
            if brew:
                one.append("brew install ffmpeg")
            if pips:
                one.append("pip3 install --user --break-system-packages " + " ".join(pips))
            print("--install-cmd: " + " && ".join(one))
            print("\nNothing was installed. Run that command, then re-run this check.")
        else:
            print("\nNothing to install - resolve the items above, then re-run this check.")
        return 1

    print("PREFLIGHT OK - ffmpeg filters, python modules and API key all present.")
    for n in notes:
        print(f"  ! {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
