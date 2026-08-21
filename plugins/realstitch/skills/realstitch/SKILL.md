---
name: realstitch
description: Build a Leap Scholar branded 9:16 Instagram reel from green-screen talking-head footage. Runs forced alignment first, then chroma-keys the subject onto a supplied background, removes dead air, and burns in word-highlighted captions plus storyboard-driven b-roll and graphics. Use when the user asks to build/stitch a reel from green screen footage, or invokes /realstitch.
metadata:
  origin: Leap Scholar video pipeline
  outputs: 1080x1920 H.264 MP4, -14 LUFS, plus build report
---

# Realstitch

Turns green-screen talking-head footage into a finished Leap Scholar vertical reel.

The pipeline is **alignment-first**: every downstream timing — captions, b-roll, graphics, pause cuts — is derived from word-level forced alignment, never from hand-typed timecodes. That is what makes the build survive pause removal and re-takes.

Read `reference/HANDOFF.md` first — it lists known bugs in these scripts and the
review style that actually works.

**Default to frames-first**: solve geometry and keying, render single frames, get
them approved, then build. A build is ~10 minutes; a frame is seconds. Run fully
autonomously only when the look is already locked from a previous version, and
still emit review material (proof frame, resolved plan, build report) into `output/`.

## Inputs

Point the skill at a folder. Discover by convention, and state what you found:

| Input | How to find it | Required |
|---|---|---|
| Green-screen footage | Largest video with a dominant green background | yes |
| Background plate | Image or video with "background"/"bg" in the name, or the only still image | if keying |
| Storyboard | Screenshot (`.png`/`.jpg`) of a script/visual table, or a `.csv`/`.tsv` | yes |
| B-roll + brand extras | An assets folder (`Assests`, `Assets`, `assets`, `broll`) | optional |
| Alignment | `*alignment*.json` **only if it was made from this exact footage** — validate it, never assume | optional |

Brand assets (follow button, outro slate, question sticker) ship **inside this skill** at `assets/brand/`. A same-named file in the project's assets folder overrides the bundled one.

## Stage 0 — Preflight

Run `scripts/doctor.py`. It verifies ffmpeg exists with `chromakey`, `despill`, `gblur`, `alphamerge`, `zoompan`, and that Python has numpy + Pillow.

If anything is missing, **stop and print the exact install commands it gives you.** Never auto-install.

Helvetica Neue is a macOS system font at `/System/Library/Fonts/HelveticaNeue.ttc` — not bundled (not redistributable). On a machine without it, fall back to Arial and say so in the report.

## Stage 1 — Forced alignment

This is the foundation. Everything else reads its output.

```
python3 scripts/align.py <footage> --out <folder>/alignment.json
```

- Requires `ELEVENLABS_API_KEY`. If unset, stop with the setup line `doctor.py` prints.
- If no script/transcript exists, it transcribes the audio first, then aligns against that text.

**Never reuse an alignment just because one is sitting in the folder.** An alignment belongs to exactly one audio file. Re-align by default; only pass `--reuse` when you know the JSON was made from *this* file.

Reusing a stale alignment is the worst failure this pipeline can have: the build succeeds, individual frames look fine, and every caption, cut and b-roll beat is anchored to speech that isn't there any more. It has already happened in this project — a folder held a 52.06s render beside an alignment describing a 59.76s cut, including an opening line that had been deleted.

`--reuse` therefore validates before trusting, and **duration is not the test.** Two takes of the same script run to nearly the same length while their internal timing differs completely (2 pauses / 2.24s versus 15 pauses / 10.34s on two real takes here). The real test compares where the audio actually falls silent against where the alignment thinks the gaps are:

| pairing | median offset | verdict |
|---|---|---|
| alignment made from this file | 0.033–0.051s (~1 frame) | accepted |
| a different take, same script, same length | 0.350–0.409s | **blocked** |

Anything over 0.15s is rejected as a different take.

**Filter whitespace tokens.** The response interleaves space tokens with their own timings; roughly half of all tokens are whitespace. If you don't drop them every inter-word gap computes as ~0 and you will detect no pauses at all.

Flag words with high `loss` (low confidence) in the build report with timestamps, then continue. Bad timings mainly shift a caption highlight.

## Stage 2 — Analyse the footage

```
python3 scripts/analyze.py <footage> <background> --out <work>/analysis.json
```

Measures, per run — never assume:

1. **Green colour** — samples the dominant green across several frames.
2. **Key window** — sweeps similarity and reports the usable range plus the chosen value. Picks the strongest value *before* the subject starts breaking up. Report the window width; a narrow one means the shoot was lit poorly.
3. **Subject geometry** — head-top, horizontal extent and table line from the matte, across the clip.
4. **Framing solve** — scale and offset so the subject fills **60–70% of frame height with the top 30–40% empty**. See `reference/spec.md`.
5. **Grade match** — subject vs background luma, contrast and saturation, and the correction needed.

If green coverage is low, this is not green-screen footage: **skip keying, keep the real background**, and run everything else.

## Stage 3 — Plan the timeline

```
python3 scripts/timeline.py <alignment> --out <work>/timeline.json
```

Detect pauses as gaps between consecutive real words, threshold **≥0.25s**. Pause removal is **always on**.

Keep silence by context, split evenly on both sides so nothing sounds clipped:

| After | Keep |
|---|---|
| Sentence end (`.` `?` `!`) | 0.24s |
| Comma or mid-clause | 0.18s |

Cut span is `[word_end + keep/2, next_start - keep/2]`, **snapped to the frame grid** (`round(t*fps)/fps`). Video cuts must land on frames or audio and video trim points disagree and the error compounds. Skip any cut under one frame.

Then build the source→output time map and push **every** downstream timing through it:

```
out_t = sum(lengths of prior segments) + (t - segment_start)
```

Because every cut sits strictly inside silence, no word boundary can fall in a removed span, so the map is total. If it isn't, the script raises rather than silently desyncing.

**Regenerate caption chunks from the remapped times** — do not shift the old chunks. Chunk boundaries depend on inter-word gaps and those gaps just changed. This is what removes sub-0.5s orphan flashes.

## Stage 4 — Resolve the storyboard

```
python3 scripts/storyboard.py <storyboard> <work>/timeline.json --out <work>/plan.json
```

**Must run after stage 3.** Beats anchor on the *output* timeline, so `timeline.json` has to exist first — passing `alignment.json` here is a schema mismatch and will fail.

**The storyboard is usually a screenshot, and `storyboard.py` cannot read images — it has no vision.** You must transcribe it yourself first: read the screenshot, then write a 3-column CSV (`script_line,visual,notes`) beside it and pass that. Copy each Script Line verbatim; where a cell is visually truncated write only the readable part, since matching is fuzzy. The script rejects image input with these instructions rather than crashing.

The storyboard is a 3-column table:

- **Script Line** — the spoken text. This is the time anchor.
- **Visual / Storyboard** — the beat, in the user's own words.
- **Notes** — global directives (e.g. "Question Overlay all over the video", "Subtitles through out the video").

Rules:

- **Anchor by words, never timecodes.** Resolve each Script Line against the timeline's word list by fuzzy match — tolerate truncation (screenshots cut cells off), curly vs straight quotes, and small script drift. Report a confidence per row.
- **Interpret the Visual column freely.** Map phrasing to beats: "b-roll with an sfx" → cutaway + impact sound; "bullet points" → numbered card; "highlighted element in a box" → accent pill on that word; "Show IG page … follow icon" → IG page b-roll + follow button.
- **`NA, keep the persons face as is` is an instruction, not a blank.** Those lines are no-cutaway zones. Never place b-roll or full-frame cards over them.
- **If a row cannot be resolved, stop.** List the unresolvable rows and why. Do not silently ship a different video.

B-roll quantity and placement come from the storyboard, not from a target percentage.

## Stage 5 — Render

`scripts/build.py` drives ffmpeg in stages. Each writes an intermediate so a re-run can skip completed work.

1. **Plate** — background scaled to cover, blurred, graded; subject keyed, despilled, alpha-feathered, grade-corrected, composited; scrims; vignette.
2. **Trim** — split into kept segments, hard-cut concat, with an **alternating 4.5% punch-in** re-centred on the face.
3. **Overlays** — `overlay.py` generates a PNG per frame: captions plus storyboard graphics.
4. **Composite** — b-roll cutaways (full-bleed 9:16, crop auto-centred on the region of interest, slow 5% push), then overlays, then SFX.
5. **Outro** — append the bundled slate (always).
6. **Master** — two-pass loudnorm to **−14 LUFS / −1 dBTP**, `yuv420p`, tagged `-color_range tv -colorspace bt709`.

Critical details that are easy to get wrong — all of them cost a wasted render if missed:

- **`setpts=PTS-STARTPTS` on every seeked input.** A frame seeked to t=24s carries PTS 24 while a still background sits at PTS 0, so `overlay` never matches them and silently composites nothing.
- **`alphaextract` fails format negotiation.** To feather the matte use `gblur=sigma=1.7:planes=8` — plane bit 8 is alpha only.
- **Never let RGB/PNG inputs decide the range.** They produce deprecated `yuvj420p`; tag the master explicitly.
- **Cross-dissolves are banned on the talking head.** They ghost two faces together and read as a glitch. Use the punch-in.

## Stage 6 — Verify, then report

```
python3 scripts/verify.py <final> <work> --report output/build-report.md
```

**Hard gate — blocks delivery:** nothing may intrude on the IG unsafe zones (bottom 1500px+, right of x=920).

**Run and report, warn only:**
- Exactly one highlighted word per caption frame (a real bug caught this way once).
- A/V sync: compare detected silences to predicted positions. Non-accumulating scatter is detector jitter; drift that grows toward the end is a real bug.
- Loudness on target.
- Every storyboard row honoured.

Write `output/` containing the reel, `build-report.md`, `plan.json`, and one proof frame. Then tell the user what shipped, what warned, and what you decided — including anything you departed from.

## Locked design

Do not re-derive these. Full detail in `reference/spec.md`.

- **Frame** 1080×1920 @ 25fps. Head-top ≈ y740. Safe box x60–920, y220–1500.
- **Graphics zone** the empty top 30–40%. Nothing may touch the subject.
- **Captions** max **5 words, one line**, exactly **one** word highlighted at a time — white text, `#5452e4` filled pill, white text on the pill. Auto-placed below the chin and above the IG UI.
- **Accent** `#5452e4` everywhere: caption pill, bullet numbers, highlight boxes, CTA.
- **Type** `'Helvetica Neue', Helvetica, Arial`. Nothing else.
- **B-roll** always vertical, always full-bleed. Never letterboxed, never blurred filler bars.
- **Output** 9:16 only, into `output/`, auto-versioned so nothing is overwritten.

## Failure modes worth knowing

| Symptom | Cause |
|---|---|
| Subject looks pasted on | Background blur too strong, and/or matte edge unfeathered. Reduce blur, feather alpha, grade-match. |
| Composite is just background | Missing `setpts` — PTS mismatch, overlay silently no-ops. |
| Two words highlighted | Active-word test allowing adjacent spans to overlap. Pick a single index. |
| No pauses detected | Whitespace tokens not filtered from the alignment. |
| Ghosted double face at a cut | A cross-dissolve. Replace with the punch-in. |
| Captions drift after trimming | Old chunks shifted instead of regenerated from remapped word times. |
| Reel is subtly desynced throughout | A stale alignment was reused. Durations matching proves nothing; compare silence positions. |
| A graphic never appears | An asset path or a start-time constant that silently no-ops. Assert the beat produced pixels; never let a missing asset pass quietly. |
| Sticker buried under a cutaway | Two storyboard rows resolved to the same anchor — a row holding *displayed* text near-duplicating a *spoken* line. `storyboard.py` flags this; persistent overlays get pinned to t=0 instead. |

### The duplicate-anchor trap

Real example from the reference storyboard: one row's Script Line is the **sticker text** ("a *previous* visa rejection") and another is the **spoken restatement** ("a *past* visa rejection"). They differ by one word, so both fuzzy-match the same span — and the sticker ends up scheduled underneath the full-frame cutaway that shares it.

The rule: a Script Line describing on-screen text is not a spoken anchor. Where the Notes column says something persists ("all over the video"), pin it to the start of the video and run it until the next top-zone graphic, rather than trusting its row anchor.

Only one top-zone graphic may be on screen at a time; overlapping beats get trimmed and the trim is reported.
