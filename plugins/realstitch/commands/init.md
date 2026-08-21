---
description: Scaffold a reel project folder and report what's still missing
disable-model-invocation: false
---

Set up a new reel project. Run the skill's `scripts/init_project.py` against the
folder the user named, or the current directory if they didn't name one.

It creates `Asset/`, `work/`, `output/`, links `brand/` to the bundled Leap
assets, writes a `storyboard.csv` template and a `.env.example`, then reports
what is present and what is missing.

Relay its report, then be explicit about the split:

**Shipped with the plugin — the user supplies nothing:**
- the Leap outro slate
- the Leap follow-button animation
- the question sticker, which is now **drawn** from the storyboard's first line
  rather than being a fixed image

**The user must supply:**
- their talking-head footage
- a background image, but only if the footage is green screen
- their own `storyboard.csv` rows, replacing the template
- an `ELEVENLABS_API_KEY` in the environment
- any per-topic b-roll or logos into `Asset/`, since those cannot be defaults

If anything is missing, say exactly which and stop — do not start a build against
a template storyboard. If everything is present, offer to run `/realstitch:reel`.

Never copy the brand assets; `brand/` is a symlink on purpose, they are ~25MB.
