"""Recolor logo.png orange → brand blue and strip white background."""

from pathlib import Path
import colorsys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "logo.png"
OUT_ASSETS = ROOT / "tahmeed" / "assets" / "logo_blue.png"
OUT_ROOT = ROOT / "logo_blue.png"

BLUE = (0x00, 0x77, 0xC5)
BLUE_DARK = (0x00, 0x5E, 0xA3)

# Background removal: fully clear above HARD; soft-fade between SOFT and HARD.
_WHITE_SOFT = 228
_WHITE_HARD = 248


def is_orange(r: int, g: int, b: int, a: int) -> bool:
    if a < 20:
        return False
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    orange_hue = (h <= 0.12) or (h >= 0.95)
    return orange_hue and s >= 0.35 and v >= 0.25 and r > g and r > b


def map_orange_to_blue(r: int, g: int, b: int, a: int) -> tuple[int, int, int, int]:
    _h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    br, bg, bb = BLUE_DARK if (v < 0.55 or r < 200) else BLUE
    bh, bs, bv = colorsys.rgb_to_hsv(br / 255, bg / 255, bb / 255)
    nv = max(0.25, min(1.0, bv * (0.55 + 0.55 * v)))
    ns = min(1.0, bs * (0.85 + 0.15 * s))
    nr, ng, nb = colorsys.hsv_to_rgb(bh, ns, nv)
    return (int(nr * 255), int(ng * 255), int(nb * 255), a)


def strip_white_background(im: Image.Image) -> int:
    """Make paper-white pixels fully transparent; soft-fade near-white fringe."""
    pixels = im.load()
    w, h = im.size
    cleared = 0
    span = float(_WHITE_HARD - _WHITE_SOFT)
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            # Keep saturated brand blues / colored anti-alias fringes.
            if (mx - mn) > 28:
                continue
            if mn >= _WHITE_HARD:
                pixels[x, y] = (255, 255, 255, 0)
                cleared += 1
                continue
            if mn >= _WHITE_SOFT:
                t = (mn - _WHITE_SOFT) / span  # 0 at soft → 1 at hard
                new_a = int(round(a * (1.0 - t)))
                pixels[x, y] = (r, g, b, new_a)
                cleared += 1
    return cleared


def main() -> None:
    im = Image.open(SRC).convert("RGBA")
    pixels = im.load()
    w, h = im.size
    recolored = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if is_orange(r, g, b, a):
                pixels[x, y] = map_orange_to_blue(r, g, b, a)
                recolored += 1
    cleared = strip_white_background(im)
    OUT_ASSETS.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT_ASSETS, "PNG")
    im.save(OUT_ROOT, "PNG")
    print(
        f"saved {OUT_ASSETS} "
        f"({recolored} pixels recolored, {cleared} background pixels cleared/faded)"
    )


if __name__ == "__main__":
    main()
