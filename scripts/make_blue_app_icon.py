"""Recolor app_icon.png orange horse → brand blue; rebuild app.ico for taskbar/desktop."""

from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tahmeed" / "assets" / "app_icon.png"
OUT_PNG = ROOT / "tahmeed" / "assets" / "app_icon.png"
OUT_ICO = ROOT / "tahmeed" / "assets" / "app.ico"

BLUE = (0x00, 0x77, 0xC5)
BLUE_DARK = (0x00, 0x5E, 0xA3)

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def is_orange(r: int, g: int, b: int, a: int) -> bool:
    if a < 20:
        return False
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    orange_hue = (h <= 0.12) or (h >= 0.95)
    return orange_hue and s >= 0.30 and v >= 0.20 and r > g and r > b


def map_orange_to_blue(r: int, g: int, b: int, a: int) -> tuple[int, int, int, int]:
    _h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    br, bg, bb = BLUE_DARK if (v < 0.55 or max(r, g, b) < 200) else BLUE
    bh, bs, bv = colorsys.rgb_to_hsv(br / 255, bg / 255, bb / 255)
    nv = max(0.22, min(1.0, bv * (0.55 + 0.55 * v)))
    ns = min(1.0, bs * (0.85 + 0.15 * s))
    nr, ng, nb = colorsys.hsv_to_rgb(bh, ns, nv)
    return (int(nr * 255), int(ng * 255), int(nb * 255), a)


def recolor(im: Image.Image) -> tuple[Image.Image, int]:
    out = im.convert("RGBA").copy()
    pixels = out.load()
    w, h = out.size
    count = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if is_orange(r, g, b, a):
                pixels[x, y] = map_orange_to_blue(r, g, b, a)
                count += 1
    return out, count


def save_ico(im: Image.Image, path: Path) -> None:
    icons = [im.resize(size, Image.Resampling.LANCZOS) for size in ICO_SIZES]
    icons[0].save(
        path,
        format="ICO",
        sizes=[(icon.width, icon.height) for icon in icons],
        append_images=icons[1:],
    )


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"Missing source icon: {SRC}")
    im = Image.open(SRC)
    blue_im, n = recolor(im)
    blue_im.save(OUT_PNG, "PNG")
    save_ico(blue_im, OUT_ICO)
    print(f"saved {OUT_PNG} ({n} orange pixels recolored)")
    print(f"saved {OUT_ICO}")


if __name__ == "__main__":
    main()
