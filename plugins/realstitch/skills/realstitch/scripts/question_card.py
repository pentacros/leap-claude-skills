#!/usr/bin/env python3
"""Render the IG-style question sticker from text, at render time.

Every other card in this pipeline is drawn, not bitmapped, which is why they
always carry the right copy. The question sticker was the one exception - a PNG
with text baked into pixels - and that is exactly how a reel once shipped with a
question the storyboard never asked for.

Geometry is measured from the reference sticker, not guessed:
card 1368x619, corner radius 110, header bar inset 19 / top 22, height 180.

    python3 question_card.py --text "Is the US a good option?" --out card.png
"""
import argparse
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("ERROR: pillow missing. pip3 install --user --break-system-packages pillow")

# ---- measured design constants ---------------------------------------------
CARD_W, CARD_H = 1368, 619
RADIUS = 110
BAR_TOP, BAR_H, BAR_INSET = 22, 180, 19
BAR_FILL = (27, 27, 28)
CARD_FILL = (253, 253, 253)
HEADER_COLOUR = (255, 255, 255)
BODY_COLOUR = (17, 17, 17)
HEADER_TEXT = "Ask me a question"
HEADER_SIZE = 68
BODY_SIZE = 62
BODY_PITCH_RATIO = 74 / 62          # measured line pitch relative to size
BODY_TOP_GAP = 60                   # bar bottom -> first baseline block
SIDE_PAD = 70                       # text inset; must clear the widest measured line (1220px at 62)
MIN_BODY_SIZE = 34
SUPERSAMPLE = 4

FONT_CANDIDATES = (
    ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
    ("/Library/Fonts/Arial Unicode.ttf", None),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
)


def load_font(size):
    for path, index in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return (ImageFont.truetype(path, size, index=index) if index is not None
                        else ImageFont.truetype(path, size))
            except OSError:
                continue
    raise RuntimeError("no usable bold font found; add one to FONT_CANDIDATES")


def wrap(draw, text, font, max_w):
    """Balanced wrap: minimise raggedness, not just fill each line greedily.

    Greedy wrapping leaves orphans - a long line followed by one short word -
    which reads badly on a card. This costs each line by its leftover space
    squared, so the breaks distribute evenly, closer to how a designer sets them.
    """
    words = text.split()
    if not words:
        return [""]
    n = len(words)
    width = [draw.textlength(w, font=font) for w in words]
    space = draw.textlength(" ", font=font)

    def line_w(i, j):
        return sum(width[i:j]) + space * (j - i - 1)

    INF = float("inf")
    cost = [0.0] + [INF] * n
    split = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            w = line_w(i, j)
            if w > max_w and j - i > 1:
                break                                  # any earlier start is wider
            # Penalise the LAST line too. Exempting it is right for paragraphs
            # but on a centred card it is what leaves a one-word orphan.
            slack = (max_w - w) ** 2
            if cost[i] + slack < cost[j]:
                cost[j], split[j] = cost[i] + slack, i
    lines, j = [], n
    while j > 0:
        i = split[j]
        lines.append(" ".join(words[i:j]))
        j = i
    return lines[::-1]


def fit_body(draw, text, max_w, max_h, ss):
    """Largest size at which the wrapped text fits the box, floor at MIN_BODY_SIZE.

    Measure and wrap in SUPERSAMPLED units. Mixing base-size fonts with a
    supersampled width silently makes everything "fit" on one line.
    """
    size = BODY_SIZE
    while size > MIN_BODY_SIZE:
        font = load_font(size * ss)
        lines = wrap(draw, text, font, max_w)
        if len(lines) * round(size * BODY_PITCH_RATIO) * ss <= max_h:
            return font, lines, size
        size -= 2
    font = load_font(size * ss)
    return font, wrap(draw, text, font, max_w), size


def render(text, header=HEADER_TEXT, out="question-sticker.png"):
    ss = SUPERSAMPLE
    W, H = CARD_W * ss, CARD_H * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=RADIUS * ss, fill=CARD_FILL + (255,))

    bar = (BAR_INSET * ss, BAR_TOP * ss, (CARD_W - BAR_INSET) * ss, (BAR_TOP + BAR_H) * ss)
    inner_r = max(RADIUS - BAR_INSET, 8) * ss
    d.rounded_rectangle(bar, radius=inner_r, fill=BAR_FILL + (255,))
    # square off the bar's bottom corners so it reads as a header band, not a pill
    d.rectangle((bar[0], bar[3] - inner_r, bar[2], bar[3]), fill=BAR_FILL + (255,))

    hf = load_font(HEADER_SIZE * ss)
    d.text(((bar[0] + bar[2]) / 2, (bar[1] + bar[3]) / 2), header,
           font=hf, fill=HEADER_COLOUR + (255,), anchor="mm")

    max_w = (CARD_W - 2 * SIDE_PAD) * ss
    max_h = (CARD_H - BAR_TOP - BAR_H - BODY_TOP_GAP - 40) * ss
    bf, lines, size = fit_body(d, text, max_w, max_h, ss)
    pitch = round(size * BODY_PITCH_RATIO) * ss
    block_h = len(lines) * pitch
    top = (BAR_TOP + BAR_H) * ss
    y = top + ((CARD_H * ss - top) - block_h) / 2 + pitch / 2
    for line in lines:
        d.text((W / 2, y), line, font=bf, fill=BODY_COLOUR + (255,), anchor="mm")
        y += pitch

    img = img.resize((CARD_W, CARD_H), Image.LANCZOS)
    img.save(out)
    return {"out": out, "size": [CARD_W, CARD_H], "body_size": size,
            "lines": lines, "pitch": round(size * BODY_PITCH_RATIO)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", required=True, help="the question, verbatim from the storyboard")
    ap.add_argument("--header", default=HEADER_TEXT)
    ap.add_argument("--out", default="question-sticker.png")
    a = ap.parse_args()
    if not a.text.strip():
        sys.exit("ERROR: --text is empty; the sticker must carry the storyboard's question")
    r = render(a.text.strip(), a.header, a.out)
    print(f"wrote {r['out']} {r['size'][0]}x{r['size'][1]}  body {r['body_size']}px "
          f"pitch {r['pitch']}  {len(r['lines'])} lines")
    for ln in r["lines"]:
        print(f"   | {ln}")


if __name__ == "__main__":
    main()
