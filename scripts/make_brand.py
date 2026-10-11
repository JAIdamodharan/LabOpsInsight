"""Turn a Canva export (black icon + navy wordmark on a flat pale background) into transparent, tinted brand assets.

Usage, from the project root:
    .venv/bin/python -I scripts/make_brand.py analyst/static/brand/logo_canva_export.png analyst/static/brand

Expects the icon above the wordmark. Needs numpy and Pillow. No purple: the icon is tinted cyan->blue (for dark
backgrounds) or blue->teal (for light backgrounds), and the favicon is a bright glyph on a deep navy tile.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

a = np.asarray(Image.open(src).convert("RGB")).astype(float)
H, W, _ = a.shape
bg_r = a[4, 4, 0]                       # flat background colour (corner pixel)
R = a[..., 0]
split = int(H * 0.608)                  # rows above = icon, below = wordmark

alpha_icon = np.clip((bg_r - R) / bg_r, 0, 1)                # icon strokes are black
alpha_text = np.clip((bg_r - R) / (bg_r - 15.0), 0, 1)       # wordmark is navy #0f1b4d (red channel 15)
alpha = np.where(np.arange(H)[:, None] < split, alpha_icon, alpha_text)
alpha[alpha < 0.03] = 0

BRIGHT = ((0, 212, 255), (77, 141, 255))     # cyan -> blue: high contrast on dark glass
DEEP = ((0, 114, 255), (0, 163, 196))        # blue -> teal: high contrast on light glass
NAVY_TILE = (6, 22, 48)


def rgba(color_fn, mask_rows):
    img = np.zeros((H, W, 4), dtype=np.uint8)
    img[..., :3] = color_fn().astype(np.uint8)
    img[..., 3] = (alpha * 255 * mask_rows[:, None]).astype(np.uint8)
    return Image.fromarray(img, "RGBA")


rows_icon = (np.arange(H) < split).astype(float)
rows_text = 1.0 - rows_icon
ys, xs = np.where(alpha_icon * rows_icon[:, None] > 0.05)
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()


def gradient(pair):
    def fn():
        t = ((np.arange(W)[None, :] - x0) / (x1 - x0) + (np.arange(H)[:, None] - y0) / (y1 - y0)) / 2
        t = np.clip(t, 0, 1)[..., None]
        c1, c2 = np.array(pair[0], float), np.array(pair[1], float)
        return c1 * (1 - t) + c2 * t
    return fn


def solid(hexcolor):
    c = np.array([int(hexcolor[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    return lambda: np.ones((H, W, 3)) * c


icon_bright, icon_deep = rgba(gradient(BRIGHT), rows_icon), rgba(gradient(DEEP), rows_icon)
text_navy, text_light = rgba(solid("#0f1b4d"), rows_text), rgba(solid("#eef4ff"), rows_text)


def crop_pad(im, pad=0.07):
    bb = im.getbbox()
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    p = int(max(w, h) * pad)
    return im.crop((max(0, bb[0] - p), max(0, bb[1] - p), min(W, bb[2] + p), min(H, bb[3] + p)))


def square(im, size):
    s = max(im.size)
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    canvas.paste(im, ((s - im.width) // 2, (s - im.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


square(crop_pad(icon_bright), 512).save(out / "logo-mark.png", optimize=True)          # for dark mode
square(crop_pad(icon_deep), 512).save(out / "logo-mark-light.png", optimize=True)      # for light mode
for name, icon, txt in (("logo.png", icon_deep, text_navy), ("logo-dark.png", icon_bright, text_light)):
    lock = crop_pad(Image.alpha_composite(icon, txt), 0.05)
    lock.resize((900, int(900 * lock.height / lock.width)), Image.LANCZOS).save(out / name, optimize=True)

# favicon / app icon: bright glyph on a deep navy tile (no gradient background, maximum contrast)
glyph = square(crop_pad(Image.fromarray(np.asarray(icon_bright), "RGBA"), 0.0), 1000)


def tile(size, radius_frac):
    base = Image.new("RGBA", (size, size), NAVY_TILE + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius_frac), fill=255)
    base.putalpha(mask)
    inner = int(size * 0.7)
    base.alpha_composite(glyph.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return base


tile(512, 0.22).resize((64, 64), Image.LANCZOS).save(out / "favicon-64.png", optimize=True)
tile(512, 0.22).resize((32, 32), Image.LANCZOS).save(out / "favicon-32.png", optimize=True)
tile(512, 0.0).resize((180, 180), Image.LANCZOS).convert("RGB").save(out / "apple-touch-icon.png", optimize=True)   # iOS applies its own mask
tile(512, 0.22).save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
print("wrote", sorted(p.name for p in out.iterdir()))
