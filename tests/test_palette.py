"""Enforce the visual rules: no purple anywhere, and bright, high-contrast colours in both light and dark mode."""
import colorsys
import re
from pathlib import Path

import pytest
from PIL import Image

from analyst.server import STATIC

CSS = (STATIC / "style.css").read_text()
JS = (STATIC / "app.js").read_text()
HTML = (STATIC / "index.html").read_text()


def hexrgb(h):
    h = h.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(c):
    f = lambda v: (v / 255 / 12.92) if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, c)
    return .2126 * r + .7152 * g + .0722 * b


def ratio(a, b):
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + .05) / (lo + .05)


def hue_sat(rgb):
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    return h * 360, s, l


def is_purple(rgb):
    h, s, l = hue_sat(rgb)
    return 255 <= h <= 322 and s > 0.15 and 0.08 < l < 0.95


def block(start):
    i = CSS.index(start)
    return CSS[i:CSS.index("}", i)]


def tokens(blk):
    return dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{3,6})\b", blk))


DARK = tokens(block(":root{\n  color-scheme:dark"))
LIGHT = tokens(block(":root[data-theme=light]{"))
MEDIA = tokens(block("@media (prefers-color-scheme:light){:root:not([data-theme]){"))
CARD_DARK = tuple(round(255 * .075 + v * .925) for v in hexrgb(DARK["bg"]))     # glass (7.5% white) over the page background
CARD_LIGHT = tuple(round(255 * .62 + v * .38) for v in hexrgb(LIGHT["bg"]))
CHARTS = [f"c{i}" for i in range(1, 13)]


def test_no_purple_hex_or_rgb_colours_in_css_js_or_html():
    offenders = []
    for name, text in (("style.css", CSS), ("app.js", JS), ("index.html", HTML)):
        for m in re.finditer(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", text):
            if is_purple(hexrgb(m.group())):
                offenders.append((name, m.group()))
        for m in re.finditer(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", text):
            if is_purple(tuple(int(x) for x in m.groups())):
                offenders.append((name, m.group()))
        for w in re.findall(r"violet|purple|lavender|indigo|magenta", text, re.I):
            offenders.append((name, w))
    assert not offenders, offenders


def test_there_is_no_gradient_accent_left():
    for sel in (".btn.primary{", ".tabs .ind{", "#pal li[aria-selected=true]{", ".prog>i{"):
        rule = CSS[CSS.index(sel):CSS.index("}", CSS.index(sel))]
        assert "gradient" not in rule, sel


@pytest.mark.parametrize("name", sorted(p.name for p in (STATIC / "brand").glob("*.png") if p.name != "logo_canva_export.png"))
def test_brand_images_contain_no_purple(name):
    im = Image.open(STATIC / "brand" / name).convert("RGBA").resize((128, 128))
    px = [p for p in im.get_flattened_data() if p[3] > 128] if hasattr(im, 'get_flattened_data') else [p for p in im.getdata() if p[3] > 128]
    assert px, name
    purple = sum(is_purple(p[:3]) for p in px) / len(px)
    assert purple < 0.02, (name, purple)


@pytest.mark.parametrize("pal,card,floor,label", [(DARK, CARD_DARK, 4.5, "dark"), (LIGHT, CARD_LIGHT, 3.0, "light")])
def test_chart_colours_have_enough_contrast_on_their_glass_cards(pal, card, floor, label):
    low = {k: round(ratio(hexrgb(pal[k]), card), 2) for k in CHARTS if ratio(hexrgb(pal[k]), card) < floor}
    assert not low, f"{label}: chart colours under {floor}:1 -> {low}"


@pytest.mark.parametrize("pal,card,label", [(DARK, CARD_DARK, "dark"), (LIGHT, CARD_LIGHT, "light")])
def test_text_and_status_colours_pass_wcag(pal, card, label):
    for k, floor in (("text", 7), ("mute", 4.5), ("accent-t", 4.5), ("ok", 4.0), ("err", 4.5), ("warn", 3.5)):
        assert ratio(hexrgb(pal[k]), card) >= floor, (label, k, ratio(hexrgb(pal[k]), card))
    assert ratio(hexrgb(pal["on-accent"]), hexrgb(pal["accent"])) >= 4.5, label      # label on the solid accent (buttons, active tab)


@pytest.mark.parametrize("pal,label,floor", [(DARK, "dark", 60), (LIGHT, "light", 40)])   # light colours must be dark enough for 3:1, which leaves less room between them
def test_first_eight_series_colours_are_clearly_distinguishable(pal, label, floor):
    cols = [hexrgb(pal[k]) for k in CHARTS[:8]]
    for i in range(8):
        for j in range(i + 1, 8):
            d = sum((a - b) ** 2 for a, b in zip(cols[i], cols[j])) ** .5
            assert d >= floor, (label, CHARTS[i], CHARTS[j], round(d))


def test_dark_chart_colours_are_vivid():
    for k in CHARTS:
        h, s, l = hue_sat(hexrgb(DARK[k]))
        assert s >= 0.9 and 0.4 <= l <= 0.78, (k, DARK[k], round(s, 2), round(l, 2))      # saturated, not pastel and not muddy


def test_os_light_mode_media_block_matches_the_explicit_light_theme():
    assert MEDIA == LIGHT


def test_chart_defaults_make_marks_thick_and_tooltips_high_contrast():
    assert "D.elements.line.borderWidth = 3.5" in JS and "T.backgroundColor = 'rgba(4,10,22,.96)'" in JS and "id: 'glow'" in JS
    assert "PALETTE" not in JS and "css(`--c${i + 1}`)" in JS
