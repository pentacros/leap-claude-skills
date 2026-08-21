# Realstitch

Builds a Leap Scholar branded 9:16 Instagram reel from green-screen talking-head footage.

Forced alignment runs first, and everything else — captions, b-roll, graphics, pause cuts — is derived from word timings rather than hand-typed timecodes. That's what lets the build survive pause removal and re-takes.

## Install

Copy the `realstitch` folder into your skills directory:

```bash
cp -R realstitch ~/.claude/skills/
```

Then in Claude Code, run `/realstitch` on a folder of footage.

## Prerequisites

```bash
brew install ffmpeg
```

```bash
python3 -m pip install numpy pillow requests
```

```bash
export ELEVENLABS_API_KEY='your-key-here'
```

Add the export to `~/.zshrc` so it persists. Check everything at once:

```bash
python3 ~/.claude/skills/realstitch/scripts/doctor.py
```

It verifies ffmpeg has the required filters (`chromakey`, `despill`, `gblur`, `zoompan`, `loudnorm`), that the Python modules are importable, and that the API key is set. It never installs anything — it prints the exact command for whatever is missing.

**Fonts:** the brand uses Helvetica Neue, a macOS system font. It isn't bundled (not redistributable). On a machine without it the build falls back to Arial and says so in the report.

## What to put in the folder

```
MyReel/
  green screen.mp4          # the footage
  background.jpg            # plate to composite behind the subject
  storyboard.png            # screenshot of your script/visual table (or .csv)
  Assets/                   # b-roll clips
```

Names are matched loosely; the skill reports what it picked up.

### Storyboard

A three-column table — screenshot from Sheets is fine, CSV is better because screenshots truncate long cells:

| Script Line | Visual / Storyboard | Notes |
|---|---|---|
| A question many students ask while planning to study abroad is, | NA, keep the persons face as is. | Question Overlay all over the video |
| "What impact does a past visa rejection have on future visa applications | Visa rejection symbol/element/b-roll with an sfx | Subtitles through out the video |
| but a previous visa rejection does not permanently affect your chances, | Show Visa Interview cabin view, visa officer taking interview of someone. | |

**Alignment is never reused blindly.** Drop new footage in a folder that still has an old alignment file and the skill re-aligns rather than trusting it. If you do force reuse, it checks that the audio's silences fall where the alignment says they do, and refuses when they don't — durations alone can't tell two takes apart.

- **Script Line** is the time anchor. It's fuzzy-matched against the alignment, so truncation and small typos are fine.
- **Visual** is read in plain language — write it however you normally would.
- `NA, keep the persons face as is` is an instruction, not a blank: those lines become no-cutaway zones.
- If a row can't be resolved, the build stops and tells you which one and why.

## Output

Everything lands in `output/`, auto-versioned so nothing is overwritten:

```
output/
  MyReel v1.mp4         # 1080x1920, 25fps, -14 LUFS
  build-report.md       # pauses removed, cuts, b-roll, QA results
  plan.json             # the resolved storyboard
  proof.png             # one composited frame
```

The run is autonomous — it doesn't stop for approval. Review the report and proof frame afterwards and re-run with an override if you want the blur, framing or a placement changed; completed stages are cached so a re-run is fast.

## Quality gates

**Blocks delivery:** anything intruding on Instagram's UI zones — the bottom strip where the username and caption sit, or the right-hand action rail.

**Reported as warnings:** exactly one highlighted word per caption frame; A/V sync after pause removal; loudness on target; every storyboard row honoured.

## What's locked

Design decisions are fixed so reels stay consistent. Details in `reference/spec.md`.

- Subject fills 60–70% of frame height, top 30–40% deliberately empty for graphics
- Captions max 5 words on one line, exactly one word highlighted at a time, white on a `#5452e4` pill
- `#5452e4` is the single accent everywhere
- Type is `'Helvetica Neue', Helvetica, Arial` and nothing else
- B-roll is always vertical and full-bleed — never letterboxed, never blurred filler bars
- 9:16 only

## Stages

| Stage | Script | Does |
|---|---|---|
| 0 | `doctor.py` | dependency preflight |
| 1 | `align.py` | transcribe if needed, then forced-align to word timings |
| 2 | `analyze.py` | sample the green, find the safe key window, solve framing, measure the grade match |
| 3 | `storyboard.py` | resolve the storyboard against the alignment |
| 4 | `timeline.py` | detect pauses, plan cuts, build the time map, chunk captions |
| 5 | `overlay.py` + `build.py` | render caption/graphic frames, then drive ffmpeg |
| 6 | `verify.py` | QA and build report |

Each stage writes an intermediate, so a re-run skips work already done.
