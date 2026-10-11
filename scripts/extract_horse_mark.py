"""Crop the login horse from logo_blue.png into a transparent horse_mark.png."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tahmeed" / "assets" / "logo_blue.png"
OUT = ROOT / "tahmeed" / "assets" / "horse_mark.png"

# Horse lives in the upper band of the full wordmark (above TAHMEED text).
_CROP = (100, 60, 1130, 400)


def extract(im: Image.Image) -> Image.Image:
    horse = im.convert("RGBA").crop(_CROP)
    px = horse.load()
    w, h = horse.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            chroma = mx - mn
            # Keep brand blues.
            if chroma > 30 and b > r + 15 and b > g + 8:
                continue
            # Keep bright white / light eye & swoosh details.
            if chroma < 55 and mx >= 180:
                continue
            # Drop dark ink, mid greys, and leftover canvas.
            px[x, y] = (0, 0, 0, 0)
    bbox = horse.getbbox()
    if bbox is None:
        raise SystemExit("No horse pixels found in logo crop")
    return horse.crop(bbox)


def main() -> None:
    if not SRC.is_file():
        raise SystemExit(f"Missing {SRC}")
    out = extract(Image.open(SRC))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "PNG", optimize=True)
    print(f"saved {OUT} {out.size}")


if __name__ == "__main__":
    main()
