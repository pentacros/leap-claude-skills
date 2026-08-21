# Handoff — what a run of this actually costs you

Written from a working session that built five reels. Everything here was learned
by getting it wrong first. Read this before trusting `SKILL.md`'s happy path.

## Working style: show frames, then render

`SKILL.md` describes an autonomous end-to-end run. In practice the person
reviewing cares most about how the subject sits in the frame, and iterates on
**stills**. The pattern that worked:

1. Solve geometry and keying, render **single frames**, hand them over.
2. Change one variable at a time and re-render frames (blur ladder, scale
   ladder, background options). Name the knob and its value on each.
3. Only build after the frame is approved.
4. Verify on the **delivered file**, not on intermediates.

A full build is ~10 minutes per variant. A frame is seconds. Do not spend the
ten minutes before the look is agreed.

## Known bugs in these scripts (found, documented, NOT fixed)

| where | symptom | workaround used |
|---|---|---|
| ~~`doctor.py`~~ | ~~preflight passes, then `align.py` dies — `requests` missing from the module check~~ | **FIXED in 1.3.0** — `requests` is checked, and it prints a one-line `--install-cmd` |
| `analyze.py` | omits `graphics_band` on green-screen sources → `storyboard.py` warns "cards may overlap the speaker", which is the "element on his head" defect | patch `analysis.json`: `[220, round(head_top_out) - 28]` |
| `analyze.py` | head detector fails on letterboxed/vignetted sources: returns `head_top` near 0 and a full-width `x0-x1`, then solves a badly shrunken frame | sanity-check `head_top`; measure from the alpha and patch `analysis.json` |
| `build.py` | plate stage hardcodes `despill expand=0.3` and `vignette=PI/5` — the pink whites and crushed table edge that got rejected | pre-render `01_plate.mp4` into the work dir; `build.py` caches it and skips the stage. **Requires `--background` to be passed**, or it takes the "not green screen" branch and composites over the raw source |
| `overlay.py` | caption shrink loop floors at `CAP_MIN = 52` and then renders anyway, overrunning the x920 action rail and failing the hard gate | split the offending caption chunks in `timeline.json` (word timings unaffected), or raise the floor |
| `overlay.py` | `logo_popup` centres on `band_mid` and sizes a single logo to the band height — a lone dollar sign rendered 565px, mid-gap | narrow `graphics_band`; it drives every element's size and position |
| `overlay.py` | bullets cap at `step<=102`, `font<=64` | project-local copy with the caps raised; swap only the bullets-beat frames |

`overlay.py` is a **separate invocation**. `build.py` only reads the frame
directory — skip it and the build dies at composite.

## Keying: measure partial alpha, not holes

The expensive lesson. A key that looks safe by hole-count can be badly wrong:

- Count **partial** alpha (roughly 5–250), not fully transparent pixels. A
  hole-count metric cannot see flicker, which is the artefact that shows on
  screen.
- Measure **frame-to-frame churn** in the same region across consecutive frames.
- Always compare against the **raw source** as a control — a talking, gesturing
  subject produces churn on its own. Good output churns *less* than the source.

Real numbers from one shoot, same footage, only similarity changed:

| region | similarity 0.075 | similarity 0.05 |
|---|---|---|
| face | 16.4% partial, 1.0% flicker | 0.3% / 0.2% |
| shirt | **76.3% partial, 4.2% flicker** | 0.0% / 0.0% |
| arms | 35.5% / 6.7% | 0.0% / 0.0% |

A sweep whose pass/fail threshold was `alpha < 128` scored 0.075 as clean. It
was not. The garment is usually the binding constraint, not the face.

## Other measured findings

See `plate-notes.md` (despill `expand` tints whites; despill does NOT redden
skin — colour correction does; region averaging cannot see an edge fringe; crop
the letterbox from the alpha not the luma; never apply the full grade match;
vignette belongs on the background, not the composite) and
`frame-extension.md` (extending a frame with a supplied still).

## Brand facts

- Accent is **`#5452e4`** (indigo). Any note claiming amber is wrong.
- `assets/brand/question-sticker.png` has **one specific question baked in**. It
  is not generic — using it unchanged renders copy the storyboard never asked
  for. Supply a per-project sticker via a project-local `--brand` directory.
- Captions sit on the chest by necessity; the "nothing touches the subject" rule
  is about *graphics*.
- Delivery: 25fps, `yuv420p`, tagged `-color_range tv -colorspace bt709`, audio
  −14 LUFS / −1 dBTP.

## Verification that means something

- `av_sync: ok` is **not** a pass when the detail says "not enough data to
  compare" — kept gaps below `silencedetect` resolution make it untested. Listen
  to the cuts.
- Pre-check overlay PNGs against the safe zones *before* spending render time.
- Confirm the right plate reached the output by sampling a frame, not by reading
  the log.
- Beware metrics whose value depends on the background (e.g. counting bright
  "ink" pixels reads differently on a dark wall than a light one).

## Performance

`geq` over a full frame is orders of magnitude slower than a pre-built ramp mask
plus `alphamerge` — 10 minutes versus 1.9 seconds for the same 5s render.
