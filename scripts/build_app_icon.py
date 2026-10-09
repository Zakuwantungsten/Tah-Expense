"""Build sharp multi-size app.ico + app_icon.png from a high-res master tile.

Usage:
  python scripts/build_app_icon.py [path/to/master.png|jpg]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tahmeed" / "assets"
OUT_PNG = ASSETS / "app_icon.png"
OUT_ICO = ASSETS / "app.ico"
OUT_CIRCLE = ASSETS / "splash_mark.png"

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _squircle_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def prepare_tile(src: Image.Image, size: int = 1024) -> Image.Image:
    """Fit master into a clean opaque squircle PNG (no outer drop-shadow canvas)."""
    im = src.convert("RGBA")
    # Trim near-white margin so the tile fills the icon.
    bg = Image.new("RGB", im.size, (240, 242, 245))
    bg.paste(im.convert("RGB"), mask=im.split()[-1] if im.mode == "RGBA" else None)
    # Slight inset crop to drop floating canvas shadow if present.
    w, h = im.size
    inset = int(min(w, h) * 0.06)
    im = im.crop((inset, inset, w - inset, h - inset))
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    mask = _squircle_mask(size)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0))
    out.putalpha(mask)
    return out


def circle_mark(tile: Image.Image, size: int = 512) -> Image.Image:
    """Circular brand mark for the splash disk (soft edge)."""
    sq = tile.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, size - 3, size - 3), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.6))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(sq, (0, 0))
    out.putalpha(mask)
    return out


def save_ico(tile: Image.Image, path: Path) -> None:
    # Pillow's ICO writer builds frames from ``sizes=`` on a single master image.
    master = tile.convert("RGBA")
    if master.size != (256, 256):
        master = master.resize((256, 256), Image.Resampling.LANCZOS)
    master.save(path, format="ICO", sizes=list(ICO_SIZES))


def main() -> None:
    default = (
        Path(r"C:\Users\Tungsten\.cursor\projects\c-Users-Tungsten-Desktop-tahmeed-expense")
        / "assets"
        / "tahmeed_app_icon_3d.jpg"
    )
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    if not src_path.is_file():
        src_path = ASSETS / "app_icon.png"
    if not src_path.is_file():
        raise SystemExit(f"No master icon found at {src_path}")

    master = Image.open(src_path)
    tile = prepare_tile(master, 1024)
    ASSETS.mkdir(parents=True, exist_ok=True)
    tile.save(OUT_PNG, "PNG", optimize=True)
    save_ico(tile, OUT_ICO)
    circle_mark(tile, 512).save(OUT_CIRCLE, "PNG", optimize=True)

    import struct

    raw = OUT_ICO.read_bytes()
    _reserved, itype, count = struct.unpack_from("<HHH", raw, 0)
    ico_wh: list[int] = []
    pos = 6
    for _ in range(count):
        w, h = struct.unpack_from("<BB", raw, pos)[:2]
        ico_wh.append(256 if w == 0 else w)
        pos += 16
    print(f"saved {OUT_PNG} {tile.size}")
    print(f"saved {OUT_ICO} sizes={sorted(ico_wh)} type={itype}")
    print(f"saved {OUT_CIRCLE}")
    if 256 not in ico_wh:
        raise SystemExit("ICO missing 256px frame — desktop icons will look blurry")


if __name__ == "__main__":
    main()
