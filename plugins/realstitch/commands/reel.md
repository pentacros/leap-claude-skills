---
description: Build a 9:16 reel from talking-head footage, frames-first
disable-model-invocation: false
---

Build a vertical reel from the footage in the given folder (default: the current
directory). Follow the `realstitch` skill, with one override that matters more
than anything else in it:

**Render frames and get them approved before building.** A build is ~10 minutes
per variant; a frame is seconds. Do not spend the ten minutes before the look is
agreed.

Run in this order:

1. **Preflight.** `doctor.py`, then confirm `requests` is importable — doctor.py
   does not check for it and `align.py` dies without it.
2. **Align first.** Forced alignment on this take's own audio. Never reuse
   timings from another take, even of the same script: takes drift, and the
   drift is not a constant offset you can shift.
3. **Measure, never inherit.** Chroma key, letterbox extent and subject geometry
   come from these frames. Sanity-check `head_top` — a value near 0 or a
   full-width `x0-x1` means the detector failed.
4. **Frames for approval.** Composite single frames covering each element type
   (sticker, logos, word card, bullets, follow) plus a plain caption. Name every
   knob and its value. Change one variable at a time.
5. **Wait for approval.** Then build, verify the delivered file, write SRTs.

Before handing anything over, read `reference/HANDOFF.md` in the skill. It lists
seven known bugs in these scripts with the workaround for each, and the keying
metrics that actually catch flicker.

State what you found and what you assumed. If a check is untestable, say so
rather than reporting it as passing.
