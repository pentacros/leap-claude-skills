#!/usr/bin/env python3
"""Stop hook: refuse to finish while the newest build report says FAIL.

The safe-zone gate is the one check that is mechanically decidable, and it has
been shipped broken before. This makes ignoring it impossible rather than a
matter of remembering.

Fails OPEN on every error. A wedged session is worse than a missed gate.
"""
import glob
import json
import os
import sys

GATE_MARKERS = ("HARD GATE", "Hard gate")
FAIL_MARKERS = ("**FAIL**", "FAIL -", ": FAIL")
# Shallow, bounded patterns only. A recursive "**/" glob walks the whole tree
# and hangs the hook when the session's cwd is a large directory.
REPORT_GLOBS = ("output/build-report*.md",
                "*/output/build-report*.md",
                "../output/build-report*.md")


def newest_report():
    found = []
    for pattern in REPORT_GLOBS:
        found.extend(glob.glob(pattern))
    found = [f for f in found if os.path.isfile(f)]
    return max(found, key=os.path.getmtime) if found else None


def main():
    try:
        report = newest_report()
        if not report:
            return                                    # nothing built; nothing to gate
        with open(report, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if not any(m in text for m in GATE_MARKERS):
            return                                    # not a gated report
        if not any(m in text for m in FAIL_MARKERS):
            return                                    # gate passed
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"{report} reports a safe-zone gate FAILURE. Do not hand this reel over. "
                "Find the offending overlay frames in the report, fix the cause (a caption "
                "wider than the safe box splits into two chunks without touching word "
                "timings; a card too low needs the graphics band narrowed), regenerate the "
                "overlays, re-check the PNGs against the safe zones before re-rendering, "
                "then rebuild and re-verify."),
        }))
    except Exception:
        return                                        # fail open, always


if __name__ == "__main__":
    main()
    sys.exit(0)
