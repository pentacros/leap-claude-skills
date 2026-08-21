#!/usr/bin/env python3
"""Generate the caption + graphics layer as an RGBA PNG per frame.

Done in PIL rather than ffmpeg drawtext because the caption rule needs a filled
pill behind exactly one word with per-word timing, which drawtext cannot express.
"""
import argparse
import glob
import json
import os

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 25
ACCENT = (84, 82, 228, 255)          # #5452e4 - Leap indigo, used everywhere
ON_ACCENT = (255, 255, 255, 255)     # white on the pill: ~5.5:1 contrast
WHITE = (255, 255, 255, 255)
CAP_MAXW = 760                       # 2*(920-540): centred, clears the action rail
CAP_SIZE, CAP_MIN = 78, 52

HN = "/System/Library/Fonts/HelveticaNeue.ttc"
ARIAL = "/Library/Fonts/Arial Unicode.ttf"
BOLD, MED = 1, 10


def font(sz, idx=BOLD):
    if os.path.exists(HN):
        return ImageFont.truetype(HN, sz, index=idx)
    return ImageFont.truetype(ARIAL, sz)


def ensure_follow_frames(brand):
    """The bundled follow button is ProRes 4444 with a real alpha channel. Extract
    it to an RGBA PNG sequence once, cropped to the pill, and cache it."""
    d = os.path.join(brand, "follow")
    have = sorted(glob.glob(os.path.join(d, "*.png")))
    if have:
        return have
    mov = os.path.join(brand, "follow-button.mov")
    if not os.path.exists(mov):
        return []
    os.makedirs(d, exist_ok=True)
    # find the pill's bounding box from one frame, then crop the whole clip to it
    import subprocess
    import tempfile
    import numpy as np
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "probe.png")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", "2", "-i", mov,
                        "-frames:v", "1", "-pix_fmt", "rgba", p, "-y"], check=True)
        al = np.asarray(Image.open(p))[..., 3]
        ys = np.where(al.sum(1) > 0)[0]
        xs = np.where(al.sum(0) > 0)[0]
        if not len(ys) or not len(xs):
            return []
        x0, y0 = max(0, int(xs.min()) - 8), max(0, int(ys.min()) - 8)
        cw, ch = int(xs.max() - xs.min()) + 16, int(ys.max() - ys.min()) + 16
    subprocess.run(["ffmpeg", "-v", "error", "-i", mov,
                    "-vf", f"crop={cw}:{ch}:{x0}:{y0},fps={FPS}",
                    "-pix_fmt", "rgba", os.path.join(d, "f_%03d.png"), "-y"],
                   check=True)
    return sorted(glob.glob(os.path.join(d, "*.png")))


def ease(p):
    return 1 - (1 - max(0.0, min(1.0, p))) ** 3


def env(t, a, b, d=0.24):
    """Fade envelope with in/out ramps."""
    if t < a or t >= b:
        return 0.0
    return min(1.0, (t - a) / d, (b - t) / d)



SAFE_CENTRED_W = 760          # x60..920 around centre x=540 -> 2*(920-540)


def fit_font(d, text, start_px, weight=None, pad=0, floor=48):
    """Largest font size at which `text` (plus padding) clears the action rail.

    Cards used to render at a fixed size, so a long label like "STEM PROGRAM"
    overflowed x=920 and tripped the safe-zone hard gate.
    """
    sz = start_px
    while sz > floor:
        f = font(sz) if weight is None else font(sz, weight)
        if d.textlength(text, font=f) + 2 * pad <= SAFE_CENTRED_W:
            return f, sz
        sz -= 2
    return (font(sz) if weight is None else font(sz, weight)), sz

class Overlay:
    def __init__(self, plan, timeline, brand):
        self.caps = timeline["captions"]
        self.dur = timeline["out_duration"]
        self.beats = plan.get("beats", [])
        self.broll = [(b["start"], b["end"]) for b in self.beats
                      if b["type"] == "broll"]
        self.brand = brand
        self.follow = ensure_follow_frames(brand)
        if not self.follow and any(b["type"] == "follow" for b in self.beats):
            raise SystemExit(
                "storyboard asks for a follow beat but no follow button asset was "
                f"found in {brand} (expected follow-button.mov or follow/*.png). "
                "Refusing to silently drop the beat.")
        sp = os.path.join(brand, "question-sticker.png")
        self.sticker = Image.open(sp).convert("RGBA") if os.path.exists(sp) else None
        if self.sticker:
            # Crop to the CARD, not the halo. getbbox() keeps every non-zero
            # pixel, and this asset carries a soft shadow 1491px wide around a
            # 1025px card - sizing to that made the visible sticker ~30% smaller
            # than intended. A small threshold keeps a tight shadow, drops the halo.
            a = self.sticker.split()[3]
            solid = a.point(lambda v: 255 if v > 40 else 0)
            self.sticker = self.sticker.crop(solid.getbbox() or a.getbbox())
        self.cap_y = timeline.get("caption_y", 1352)
        # The graphics band is measured, not assumed. Hardcoding it to the
        # green-screen layout (head-top 740) put every card on the speaker's head
        # in tighter-framed footage.
        band = (plan.get("graphics_band") or timeline.get("graphics_band")
                or [220, 760])
        self.band_top, self.band_bot = int(band[0]), int(band[1])
        self.band_h = max(60, self.band_bot - self.band_top)
        self.band_mid = self.band_top + self.band_h // 2

    def in_broll(self, t):
        return any(a <= t < b for a, b in self.broll)

    # ---- captions -------------------------------------------------------
    def caption(self, img, t):
        for c in self.caps:
            if not (c[0]["s"] - 0.10 <= t < c[0].get("out", c[-1]["e"] + 0.55)):
                continue
            words = [w["t"] for w in c]
            d = ImageDraw.Draw(img, "RGBA")
            size = CAP_SIZE
            while size > CAP_MIN:
                f = font(size)
                sp = int(size * 0.30)
                tw = sum(d.textlength(x, font=f) for x in words) + sp * (len(words) - 1)
                # the pill around the active word adds padding on both sides, and
                # the shadow another 2px; budget for them or the pill overruns.
                if tw + 2 * int(size * 0.19) + 2 <= CAP_MAXW:
                    break
                size -= 2
            f = font(size)
            sp = int(size * 0.30)
            ws = [d.textlength(x, font=f) for x in words]
            asc, desc = f.getmetrics()
            pop = ease((t - c[0]["s"] + 0.10) / 0.16)
            x = (W - (sum(ws) + sp * (len(words) - 1))) / 2
            cy = self.cap_y + (1 - pop) * 14

            # exactly ONE active word: the last one whose start has passed, held
            # until the next begins. Testing each span independently would let
            # adjacent words both match and double-highlight.
            act = -1
            for k, w in enumerate(c):
                if t >= w["s"] - 0.03:
                    act = k

            for k, (word, wd) in enumerate(zip(words, ws)):
                if k == act:
                    px, py = int(size * 0.19), int(size * 0.13)
                    d.rounded_rectangle(
                        [x - px, cy - asc * 0.76 - py, x + wd + px, cy + desc * 0.5 + py],
                        radius=int(size * 0.26), fill=ACCENT)
                    d.text((x, cy), word, font=f, fill=ON_ACCENT, anchor="ls")
                else:
                    d.text((x + 2, cy + 3), word, font=f, fill=(0, 0, 0, 120), anchor="ls")
                    d.text((x, cy), word, font=f, fill=WHITE, anchor="ls")
                x += wd + sp
            return

    # ---- graphics -------------------------------------------------------
    def sticker_beat(self, img, t, b):
        if self.sticker is None or self.in_broll(t):
            return
        ar = self.sticker.width / self.sticker.height
        # big: take the full safe width and hang it off the head-clearance line.
        # Fitting to band height alone capped it at ~588px and read as too small.
        # It may rise above band_top - that region is only light IG chrome, and a
        # decorative sticker there is fine; what matters is clearing the subject.
        big_w = SAFE_CENTRED_W
        big_y = max(70, self.band_bot - int(big_w / ar))
        # corner: parked flush to the left margin at the top of the band, so it
        # reads as pinned rather than floating mid-air
        cor_w = min(360, big_w)
        cor_y = self.band_top
        tw_a, tw_b = b["big_until"], b["big_until"] + 0.44
        if t < b["start"]:
            return
        if t < tw_a:
            a, w, y, cx = env(t, b["start"], tw_a + 0.05, 0.30), big_w, big_y, None
        elif t < tw_b:
            p = ease((t - tw_a) / 0.44)
            a = 1.0
            w = big_w + (cor_w - big_w) * p
            y = big_y + (cor_y - big_y) * p
            cx = 60 + ((W - w) / 2 - 60) * (1 - p)
        elif t < b["end"]:
            a, w, y, cx = env(t, tw_b, b["end"], 0.25), cor_w, cor_y, 60
        else:
            return
        if a <= 0:
            return
        w = int(w)
        h = int(w * self.sticker.height / self.sticker.width)
        x = int((W - w) / 2 if cx is None else cx)
        s = self.sticker.resize((w, h), Image.LANCZOS)
        if a < 1:
            s.putalpha(s.getchannel("A").point(lambda v: int(v * a)))
        img.alpha_composite(s, (x, int(y)))

    def bullets(self, img, t, b):
        a = env(t, b["start"], b["end"], 0.28)
        if a <= 0:
            return
        d = ImageDraw.Draw(img, "RGBA")
        items = b["items"]
        kicker = b.get("kicker", "")
        # Fit rows inside the measured band. Fixed y-values (452/554/656) sat on
        # the speaker whenever the framing was tighter than the green-screen setup.
        top = self.band_top + 12
        avail = self.band_h - 24
        if kicker:
            self._track(d, (W / 2, top + 26), kicker.upper(), font(31, MED),
                        (255, 255, 255, int(180 * a)), 5)
            top += 52
            avail -= 52
        n = max(len(items), 1)
        step = min(102, int(avail / n))
        fsz = max(38, min(64, int(step * 0.62)))
        dia = max(30, int(step * 0.52))
        rows_h = step * n
        y0 = top + max(0, (avail - rows_h) // 2) + int(step * 0.72)
        for i, it in enumerate(items):
            ra = env(t, it["at"], b["end"], 0.26)
            if ra <= 0:
                continue
            p = ease((t - it["at"]) / 0.26)
            y = y0 + i * step
            dx = (1 - p) * 22
            d.ellipse([150 + dx, y - dia * 0.82, 150 + dia + dx, y + dia * 0.18],
                      fill=ACCENT[:3] + (int(255 * ra),))
            d.text((150 + dia / 2 + dx, y - dia * 0.32), str(i + 1),
                   font=font(max(20, int(dia * 0.60))),
                   fill=(255, 255, 255, int(255 * ra)), anchor="mm")
            d.text((150 + dia + 28 + dx, y), it["label"], font=font(fsz),
                   fill=(255, 255, 255, int(255 * ra)), anchor="ls")

    def word_card(self, img, t, b):
        a = env(t, b["start"], b["end"], 0.26)
        if a <= 0:
            return
        d = ImageDraw.Draw(img, "RGBA")
        if b.get("kicker"):
            self._track(d, (W / 2, self.band_top + 44), b["kicker"].upper(),
                        font(36, MED), (255, 255, 255, int(190 * a)), 8)
        p = ease((t - b["start"]) / 0.34)
        sc = 0.92 + 0.08 * p
        f, base = fit_font(d, b["word"], 104, pad=34)
        sz = max(48, int(base * sc))
        f = font(sz)
        wd = d.textlength(b["word"], font=f)
        asc, desc = f.getmetrics()
        cy = self.band_bot - int(desc * 0.4) - 26
        d.rounded_rectangle([(W - wd) / 2 - 34, cy - asc * 0.74 - 20,
                             (W + wd) / 2 + 34, cy + desc * 0.4 + 20],
                            radius=30, fill=ACCENT[:3] + (int(255 * a),))
        d.text((W / 2, cy), b["word"], font=f,
               fill=(255, 255, 255, int(255 * a)), anchor="ms")

    def follow_beat(self, img, t, b):
        if not self.follow:
            return
        i = int((t - b["start"]) * FPS)
        if i < 0 or i >= len(self.follow):
            return
        p = Image.open(self.follow[i]).convert("RGBA")
        if p.width > SAFE_CENTRED_W:          # asset is 880 wide; safe box is 760
            p = p.resize((SAFE_CENTRED_W,
                          int(p.height * SAFE_CENTRED_W / p.width)), Image.LANCZOS)
        fy = b.get("y")
        if fy is None or fy + p.height > self.band_bot:
            fy = self.band_top + max(0, (self.band_h - p.height) // 2)
        img.alpha_composite(p, (int((W - p.width) / 2), int(fy)))

    @staticmethod
    def _track(d, xy, txt, f, fill, track):
        x, y = xy
        w = sum(d.textlength(c, font=f) + track for c in txt) - track
        x -= w / 2
        for c in txt:
            d.text((x, y), c, font=f, fill=fill, anchor="ls")
            x += d.textlength(c, font=f) + track

    _LOGO_CACHE = {}

    def _logo(self, path):
        """Load a logo trimmed to its own alpha bbox.

        Source seals carry wildly different amounts of transparent padding
        (Harvard's is 3840x2160 with the mark small and centred), so scaling the
        raw file makes some render far smaller than others in the same row.
        """
        if path in self._LOGO_CACHE:
            return self._LOGO_CACHE[path]
        try:
            im = Image.open(path).convert("RGBA")
        except Exception:
            self._LOGO_CACHE[path] = None
            return None
        bb = im.split()[3].getbbox()
        if bb:
            im = im.crop(bb)
        self._LOGO_CACHE[path] = im
        return im

    def logo_popup(self, img, t, b):
        """Institution seals popping in one after another, in the top zone."""
        a = env(t, b["start"], b["end"], 0.26)
        if a <= 0:
            return
        imgs = b.get("images", [])
        if not imgs:
            return
        gap = 34
        n = max(len(imgs), 1)
        box_w = int((SAFE_CENTRED_W - gap * (n - 1)) / n)
        # cap by the band height too, or logos overflow onto the subject
        box = min(box_w, self.band_h - 24)
        total = len(imgs) * box + (len(imgs) - 1) * gap
        x0 = (W - total) / 2
        cy = self.band_mid
        for i, item in enumerate(imgs):
            ia = env(t, item["at"], b["end"], 0.20)
            if ia <= 0:
                continue
            p = ease(min(1.0, max(0.0, (t - item["at"]) / 0.28)))
            sc = 0.72 + 0.28 * p        # pop in
            logo = self._logo(item["path"])
            if logo is None:
                continue
            side = int(box * sc)
            # normalise by visual area, not bounding box, so wide and square
            # marks carry the same weight in the row
            k = (side * side * 0.82 / max(logo.width * logo.height, 1)) ** 0.5
            logo = logo.resize((max(1, int(logo.width * k)),
                                max(1, int(logo.height * k))), Image.LANCZOS)
            if ia < 1:
                logo.putalpha(logo.getchannel("A").point(
                    lambda v: int(v * ia)))
            px = int(x0 + i * (box + gap) + (box - logo.width) / 2)
            py = int(cy - logo.height / 2 + (1 - p) * 18)
            img.alpha_composite(logo, (px, py))

    def cost_card(self, img, t, b):
        """Cost/expense beat: no stock clip, just an accent card that lands hard."""
        a = env(t, b["start"], b["end"], 0.24)
        if a <= 0:
            return
        d = ImageDraw.Draw(img, "RGBA")
        p = ease(min(1.0, max(0.0, (t - b["start"]) / 0.30)))
        label = b.get("label", "EXPENSIVE")
        f, base = fit_font(d, label, 96, pad=40)
        sz = max(48, int(base * (0.90 + 0.10 * p)))
        f = font(sz)
        wd = d.textlength(label, font=f)
        asc, desc = f.getmetrics()
        cy = self.band_bot - int(desc * 0.4) - 26
        pad = 40
        d.rounded_rectangle([(W - wd) / 2 - pad, cy - asc * 0.74 - 22,
                             (W + wd) / 2 + pad, cy + desc * 0.4 + 22],
                            radius=30, fill=ACCENT[:3] + (int(255 * a),))
        d.text((W / 2, cy), label, font=f,
               fill=(255, 255, 255, int(255 * a)), anchor="ms")


    # Every beat type must have a renderer. A type with no branch produces no
    # pixels and no error - the graphic simply never appears, which is one of the
    # documented failure modes. Fail loudly instead.
    RENDERERS = {"sticker": "sticker_beat", "bullets": "bullets",
                 "word_card": "word_card", "follow": "follow_beat",
                 "logo_popup": "logo_popup", "cost_card": "cost_card"}
    # Beats consumed elsewhere in the pipeline, not drawn as overlays.
    NON_OVERLAY = {"broll", "hold"}

    def frame(self, t):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for b in self.beats:
            if not (b["start"] - 0.4 <= t < b["end"] + 0.4):
                continue
            k = b["type"]
            if k in self.NON_OVERLAY:
                continue
            fn = self.RENDERERS.get(k)
            if fn is None:
                raise SystemExit(
                    f"ERROR: no overlay renderer for beat type {k!r} - it would "
                    f"silently never appear. Add one to overlay.py.")
            getattr(self, fn)(img, t, b)
        self.caption(img, t)
        return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("plan")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    tl = json.load(open(a.timeline))
    plan = json.load(open(a.plan))
    ov = Overlay(plan, tl, a.brand)
    os.makedirs(a.out, exist_ok=True)
    n = int(round(tl["out_duration"] * FPS))
    for i in range(n):
        ov.frame(i / FPS).save(os.path.join(a.out, f"{i:05d}.png"))
    print(f"{n} overlay frames -> {a.out}")


if __name__ == "__main__":
    main()
