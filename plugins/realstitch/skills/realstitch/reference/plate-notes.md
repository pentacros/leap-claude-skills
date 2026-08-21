# Plate notes — measured findings, not defaults

Everything here was arrived at by rendering and measuring, usually after getting it
wrong first. Each entry says how it was found so it can be re-checked rather than
trusted.

---

## despill `expand` is what tints whites pink

**The single most expensive lesson in this pipeline so far.**

`despill` builds a spill map from how much green *exceeds* red and blue. A true
neutral has G == R == B, so its spill is zero and despill correctly leaves it alone.
`expand` then **dilates that map spatially**, so pixels merely *adjacent* to spilled
areas get treated as spilled — even when they have no spill of their own. A white
shirt or table sitting next to keyed green gets caught by proximity. Green is pulled
down, red and blue are not, and the neutral goes magenta.

Measured on Gargi V5, against no-despill as ground truth (drop in G relative to the
R/B midpoint):

| setting | shirt | table L | table R | green px removed |
|---|---|---|---|---|
| `mix=0.50 expand=0.30` | −4 | −18 | **−35** | all (0 left) |
| `mix=0.50 expand=0` | 0 | **0** | −3 | all (0 left) |
| `expand=0.30` + `red=-0.5:blue=-0.5` | −1.5 | −8 | −16 | all |

**`expand=0` costs nothing.** Green removal is identical (0 px remaining either way);
only the pink disappears. Default to `expand=0` unless a specific shot proves it
needs expansion.

The `red` / `blue` scale params (default 0) redistribute the removed spill into those
channels and halve the tint, but they don't beat simply not expanding.

## despill does NOT redden skin — colour correction does

A long detour on V5 blamed despill for turning skin red (face hue 26 → 6). That was
wrong, and the error was **comparing measurements taken from different face regions**.
Like-for-like on the same region, global despill and a luma-gated despill give
identical skin (hue 18 both). The red came from `colorbalance rm=+0.06` plus
`saturation 1.08` that happened to be in the chain at the time.

Consequence: a luma-gated `maskedmerge` despill rig was built to solve a problem that
only existed because of the colour correction. **Pick one sample region per
measurement and hold it fixed across the whole comparison.**

## Region averaging cannot detect an edge fringe

A green rim on hair is 1–3px wide. Averaging hue over a 70×60 box drowns ~36 rim
pixels in surrounding brown and reports "no green". Two ways this misled:

- Measuring the **shirt** for spill: a garment at 19% saturation has meaningless hue.
- Measuring a **region mean** for a rim: reports the mass, not the edge.

To find a fringe, **count pixels in a hue/sat window and map their positions**, or
render the mask and look at it. A magenta-marked overlay of every matching pixel
found the rim in one pass after four failed measurements.

## Letterbox: crop deeper than the black bar

Gargi V5 had a 66px pure-black bar at the top — but keyable green did not start until
**y=124**. Rows 92–124 are a dark-green vignette ramp: too dark for the key to catch,
not black enough to register as a bar. Cropping at 66 leaves a dark band across the
top of every composite.

**Find the crop line from the alpha, not the luma:** key the frame, then scan rows for
the first that is ≥95% transparent. A pure-black row scan is off by ~58px here.

Better still, for a green-screen source: **fill the bar region with the sampled key
colour** (`drawbox=...:color=<green>:t=fill`) rather than cropping. The frame stays
1080×1920, the head position is unchanged, no rescale is needed, and the filled band
keys out cleanly.

## The head detector fails on letterboxed sources

`analyze.py` returned `head_top 0`, `x[0-1079]` on V5 because the black bar and
vignette are not green, so the matte read them as subject — and the entire framing
solve was built on that. Filling the bar with key colour (above) fixed it: 678,
matching a manual measurement of 686.

**Sanity-check `head_top` against the frame before trusting the framing solve.** A
value of 0, or a full-width `x0-x1`, means the detector failed.

## `analyze.py` omits `graphics_band` on green-screen sources

It emits the band for non-green footage only. On a green-screen source the framing
solve *moves* the subject, so the source `head_top` is not the output head position —
and the band is left unset. `storyboard.py` then warns `no measured graphics band —
cards may overlap the speaker`, which is exactly the "element on his head" defect
reported on V2/V3.

Derive it from the solve's own output:

```
graphics_band = [220, round(framing.head_top_out) - 28]
```

## `doctor.py` does not check for `requests`

Preflight passes, then stage 1 (`align.py`) dies with
`ERROR: python module 'requests' not installed`. Add `requests` to the preflight
module list.

## Grade matching: closing the full luma gap reads as underexposure

`analyze.py` computed `brightness -0.232` for V5 (subject luma 156 vs background 77),
which pulled the face from val 83.5 to **45.1** — visibly darker than the background
in places. The measured gap assumes the subject should match the room, but a
studio-lit presenter legitimately *is* brighter than a dim room.

Take roughly **40% of the computed pull** and lift the background to close the rest
from the other side. Verify on a rendered frame, not on the numbers alone.

## Vignette belongs on the environment, not the composite

`vignette=PI/5` applied after compositing crushed the bottom of the subject's table
from (188,166,141) to (95,71,69) — a 47% loss, reading as a dirty band along the
bottom edge. Options, in order of preference: apply the vignette to the **background
only** before compositing, weaken it, or drop it.

Note that *cropping* the band away does not help — the vignette simply darkens
whatever becomes the new bottom edge.

## Making the subject's table reach the frame edges

A framing solve that scales the foreground below 1080 wide (e.g. `1026` at `x=27`)
leaves a strip of background down each side, so a full-width table stops short of the
edges. To have it touch: scale so the layer is **≥1080 wide**, centre it with a
negative x, and let the bottom overflow.

Scaling up and moving the subject down pull in opposite directions on the frame
metrics — larger fills more, lower adds headroom — so the graphics band gets
*roomier* as the subject grows. Nothing is lost by going bigger and lower.

## zsh: brace every variable before a colon

`eq=brightness=$2:saturation=$3:contrast=$4` silently mangles into
`brightness=-0.090st=0.92` — zsh reads `$3:contrast` as a parameter-expansion
modifier. Always `${3}`. Same trap as `$OF:linear`.
