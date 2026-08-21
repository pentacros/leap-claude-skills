# Extending a frame with a supplied still

For when footage is the wrong shape for the deliverable (16:9 source, 9:16 reel)
and someone supplies a wider or taller still of the same scene. Cropping alone
forces a heavy upscale; compositing the still buys real estate so the crop can
be bigger and the upscale smaller.

Tool: `scripts/extend_frame.py`. Everything is measured from the inputs.

    python3 extend_frame.py --video IN.mp4 --extension STILL.png --work DIR \
        [--extra N | --extra upscale:1.15] [--out-w 1080 --out-h 1920] \
        [--crop-x subject|centre|PX] [--extend-edge top|bottom] [--no-colour-match]

It prints an alignment report, writes `plate.png`, `feather_mask.png` and
`extend_report.json`, and emits the exact ffmpeg command.

---

## The one thing that decides whether this works

**Is the still an outpaint of THIS frame, or a re-render of the scene?**

An outpaint keeps the camera and set geometry, so a single scale+shift aligns it.
A re-render puts the same furniture at different relative distances — it will
look right to a human and be impossible to align. Measured on one shoot, two
stills of the same room:

| still | regions agreeing | worst-axis spread | per-region corr |
|---|---|---|---|
| outpaint of the frame | 3/4 | 7px | 0.43 – 0.72 |
| re-render of the room | 3/4 | 14px | 0.26 – 0.54 |

On the re-render, hand-picked regions put the origin 115px apart across one
frame. No transform reconciles that. Options: get a true outpaint, or blur the
extended area so the mismatch stops reading as broken architecture.

## Judge alignment by agreement, never by one score

A single correlation number cannot tell "aligned" from "confidently wrong". Use
three or more regions and count how many agree with the median. That is the
whole reason the script picks several.

Corollaries that each cost real time to learn:

- **Match regions must be spread out.** Several patches in one corner
  re-measure the same geometry; their agreement proves nothing.
- **Exclude both vertical edges.** The extended edge is where an outpaint blends
  and stops matching. The opposite edge may be outside the still's coverage
  entirely — a patch there matches noise and silently poisons the solve. This
  single mistake inverted the ranking of two candidate stills.
- **Exclude the mover.** Find it from temporal std across sampled frames
  (background is static). No face detector, works on any footage.
- **Compare like with like.** Spread that sums two axes is not comparable to a
  per-axis tolerance; that mismatch made the warning fire on good solves.
- **Reject outliers.** Raw max-min lets one bad region condemn a good solve.

Absolute scale still moves ~4% with region choice (0.756 auto vs 0.788 hand
-picked on the same pair). Treat the number as approximate and check the join.

## Metrics that will mislead you

- **Row-mean profile across the full width** hides local mismatch. It reported a
  step of 3 where the join was visibly broken.
- **Correlation over a region containing a high-contrast object** is dominated by
  that object; 1–2px on a lamp stem tanks the number while the wall is fine.
- **Edge detectors comparing different rows** in the two images return nonsense.
- **1-D vertical-edge profiles** constrain horizontal shift and scale but barely
  constrain vertical position — two different stills returned the same headroom.
- Region averaging cannot see a thin fringe; see `plate-notes.md`.

When numbers disagree, render the join at the crop width and look at it.

## Geometry

Let `head` = mover's top row, `H` = source height, `headroom` = real rows the
still adds above the frame, `E` = synthesised rows.

    canvas_h = headroom + E + H
    crop_w   = canvas_h * out_w / out_h
    upscale  = out_w / crop_w
    head_out = (head + headroom + E) * out_h / canvas_h

**More headroom is close to free**: a taller canvas means a wider crop, so
upscale *falls* as space grows. On one shoot, going from +0 to +672 rows took
upscale from 1.54x to exactly 1.00x. Use `--extra upscale:1.0` to solve for it.

Also: cropping the top of a source and then bottom-aligning cancels out, so the
subject's position is unchanged and discarded rows cost nothing.

## Colour match

Fit per-channel gain/offset on the overlap, not by eye. Two cautions:

- Fit on the regions that matter. A fit over the whole overlap gave gain 0.76;
  refitting near the join gave 0.44 for a 2-point improvement — implausible, and
  a sign the mismatch is spatially varying rather than a global grade offset.
- If the still is darker on one side and brighter on the other, no single
  gain/offset fixes both. That is another re-render fingerprint.

## Synthesising extra rows

Grow the plate from its own top band **per column**, so vertical features (a
lamp cord, a wall edge) continue as lines instead of smearing. Add a slight
fall-off and blur only the invented band. Report the invented fraction — past
roughly a quarter of frame height it needs judging on real footage.

## Performance

Use a pre-built ramp mask plus `alphamerge` for the feathered join. `geq` over a
full frame is orders of magnitude slower — the same 5s render went from timing
out at 10 minutes to **1.9 seconds**.

## Always tell the reviewer

The extended region is a **frozen still**: no grain, no motion, while the footage
below has both. It passes on a dark static wall and fails on anything textured or
lit that moves. Judge it on a moving clip, never a still.
