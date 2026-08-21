# Realstitch design spec

Measured values, not preferences. Every number here was derived from real footage and verified in a render. Re-derive only if the shoot setup changes.

## Frame system — 1080×1920 @ 25fps

| Zone | Pixels | Rule |
|---|---|---|
| Top chrome | 0–220 | Watermark only. Nothing critical. |
| **Graphics zone** | 220–740 | Question sticker, follow pill, bullets, CTA. The deliberately empty top. |
| Head top | **≈740** (38.5%) | Subject occupies 61.5% of frame height. |
| Caption baseline | ≈1352 (70%) | Auto-placed: below the chin, above the IG UI. |
| IG UI | 1500–1920 | Username, caption, audio ticker. **Hard gate — nothing here.** |
| Action rail | x > 920 advisory, **x > 960 hard gate** | Like/comment/share/save. Design to 920; 960 is where the icon column genuinely begins and where `verify.py` blocks. |
| Safe box | x60–920, y220–1500 | All critical content lives inside. |

The two rail numbers are deliberate. Leap's own follow-button asset is 831px wide centred (≈x125–955), so a 920 hard gate would reject brand assets as supplied. 920 stays the design guideline; 960 is the line that actually fails a build. The big question sticker is sized **840px** centred (x120–960) to clear it.

Caption block max width **800px**, centred; type auto-shrinks from 78px (floor 52px) to fit rather than wrapping.

Requirement: **subject 60–70% of frame height, top 30–40% empty.** Solve scale and offset from the measured matte to hit it — do not reuse fixed numbers across shoots.

### Framing solve

Given measured head-top `h` and source height `H`, targeting head-top `740` with the frame bottom at `1920`:

```
s  = (1920 - 740) / (H - h)
oy = 740 - h*s
ox = 540 - (subject_center_x * s)
```

Snap scaled dimensions to even numbers. Include the table (it grounds him at a desk); the bottom scrim grades it into a desk edge.

Verified examples:
- Shoot 1: head-top 96 → `scale=2304:1296`, `overlay=-456:624`
- Shoot 2: head-top 110 → `scale=2336:1314`, `overlay=-475:606`

A 14px difference between shoots is why this is solved, not hardcoded.

## Chroma key

Green measured on both shoots: **RGB(108,170,80)** — muddy, low purity, ~20% brighter centre-frame than left edge. That combination makes the key window narrow.

Measured window: usable **0.05–0.08**. At 0.11 the key eats through shirt and face; by 0.14 the subject is gone entirely. Shipped value **0.07 / blend 0.05**.

```
format=rgba,
chromakey=<sampled>:<swept>:0.05,
despill=type=green:mix=0.5:expand=0.3,
gblur=sigma=1.7:planes=8,     # alpha only — feathers the cutout edge
gblur=sigma=0.35:planes=7,    # RGB only — kills aliasing
eq=<grade correction>,
scale=<solved>, setsar=1
```

`planes` is a bitmask; for rgba, alpha is plane 3 → bit 8. `alphaextract` cannot be used — it fails format negotiation in this chain.

Saturation-boosting before keying does **not** widen the window; it moves the green away from the sampled colour and breaks the key. Don't.

Keep the chair. It sits behind his arms and reads naturally.

## Integration

The "pasted cutout" look has two independent causes; fixing one alone is not enough.

1. **Blur cliff.** Background at sigma 26 against a razor-sharp matte edge. Use **sigma 16**. At 13 the background competes with the subject; at 26 it's mush.
2. **Unfeathered matte.** The key produces a hard aliased edge. Feather alpha at sigma 1.7.

Then grade-match. Measured mismatch before correction:

| | subject | background | gap |
|---|---|---|---|
| luma mean | 117.4 | 63.5 | +53.9 |
| contrast (std) | 39.3 | 34.1 | +5.2 |
| saturation proxy | 48.3 | 13.8 | +34.5 |

After correction (`brightness=-0.080 saturation=0.78 contrast=0.93`, background `brightness=-0.02 saturation=0.95`):

| | gap | was |
|---|---|---|
| luma | **+20.1** | +53.9 |
| contrast | **−0.2** | +5.2 |
| saturation | **+22.2** | +34.5 |

Target ~+20 luma (he *should* be brighter — he's the subject), contrast matched, saturation gap roughly halved. Measure and solve; don't reuse the numbers against a different background.

Plus: top scrim (0→760, 60% black, for type legibility), bottom scrim (1690→1920, 72%, grades the table), `vignette=PI/5`.

## Captions

- Max **5 words**, one line, ≤~28 chars.
- Exactly **one** word highlighted at any instant. Compute a single active index — the last word whose start has passed — then hold it until the next begins. Testing each word's span independently lets adjacent words both match and double-highlights.
- White text; active word gets a `#5452e4` filled pill with **white** text on it.
- Chunk on: sentence punctuation, gap ≥0.30s, 5 words, comma after ≥3 words, or char limit.
- Merge any chunk holding under 0.52s into its predecessor when the merge stays legal (≤6 words, ≤32 chars, gap <0.30s). This removes orphan flashes like `is:` and `chances,`.
- Extend each chunk's out-point into the following gap: `min(next_start - 0.06, end + 0.55)`.

Typical result: ~32 frames averaging ~1.2s.

## Pause removal

Threshold **≥0.25s** between consecutive real words. Always on.

Keep 0.24s after a sentence, 0.18s otherwise, split evenly either side. Cut span snapped to the frame grid. ~0.12s is the floor before speech sounds choppy.

Measured on real takes:
- Shoot 1: 2 pauses, 2.24s (5%) — a tight take.
- Shoot 2: 15 pauses, 10.34s (**22%**) — trimmed to 4.48s, 46.56s → 39.12s.

### Masking the cuts

Every cut leaves a visual jump. Measured subject-region motion across the 15 cuts of shoot 2: **7.8 to 20.4** (normal adjacent-frame motion during speech is 3–8). All of them show.

**Use an alternating punch-in**, re-centred on the face so the subject doesn't shift:

```
scale=1128:2006:flags=lanczos, crop=1080:1920:24:40    # 4.5% in
```

Toggle A/B at each cut. Apply to the **plate only**, before overlays, so captions and cards stay put. Head-top shifts ~7px between states — imperceptible.

**Cross-dissolves are rejected.** A 3-frame dissolve softened the worst cut by only 17% and produced a visible double-exposure ghost of two faces. Longer dissolves make the ghost last longer. Wrong tool for a face.

## B-roll

Always vertical, always full-bleed. No letterboxing, no blurred filler bars — that was the single most obvious flaw in the pre-skill version.

16:9 → 9:16: crop `608:1080` at an x offset chosen by detecting the region of interest, then `scale=1080:1920:flags=lanczos` and a slow 5% `zoompan` push. Plus a bottom scrim for caption contrast.

Sync content beats to the word, not the clip: for a stamp landing at source `t_contact` that must hit output word time `w`, the source in-point is `t_contact - (w - broll_start)`.

## Audio

- Master to **−14 LUFS / −1 dBTP** (Instagram normalises to ~−14; the pre-skill cut shipped at −23.8, about 10 LU too quiet).
- Two-pass `loudnorm` with measured values and `linear=true`.
- Hard-cutting audio inside silence is inaudible — no crossfade needed.
- Stamp impact SFX: 130→48Hz sweep with fast decay plus a 28ms filtered noise transient, ~0.34s.

## Delivery

```
-c:v libx264 -crf 17 -preset medium -profile:v high -level 4.2 -pix_fmt yuv420p
-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -r 25
-c:a aac -b:a 192k -ar 48000 -ac 2 -movflags +faststart
```

Tag the range explicitly. RGB and PNG inputs otherwise yield deprecated `yuvj420p`, which crushes blacks on players that ignore the tag.

`-preset slow` on a 40s 1080×1920 render exceeds a 2-minute command budget — run long encodes in the background or use `medium`.
