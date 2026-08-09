"""
Convert source artwork into the WebP files the site's tiles and banners want.

Written for the game icons (#234), which have to sit beside the eighteen topic
icons from #223 without looking like a different product. Those eighteen are
the specification, and they were measured rather than assumed:

    400 x 400, RGB, 20.9-36.1 KB (mean 29.1)

`quality 82, method 6` reproduces that band on this artwork. `method=6` is the
slowest and smallest setting; these are build-time assets, so the seconds are
free and the bytes are not.

Usage:
    python reports/scripts/to_webp.py --tile     in.jpg src_dir/ out_dir/
    python reports/scripts/to_webp.py --banner   in.jpg out.webp
    python reports/scripts/to_webp.py --width N  in.jpg out.webp

Three shapes, because the buttons they feed are three different jobs:

* ``--tile``    400 x 400. The square game icons. A source that is not square
  is centre-cropped to square first.
* ``--banner``  1600 x 400, the 4:1 shape #234 specified for #237's wide
  button. Centre-crops to 4:1 first.
* ``--width N`` N wide, **height derived from the source** so nothing is
  cropped at all.

That last mode is the one to reach for when a source's own ratio is worth
keeping, and it is what `read_a_text` needed: the artwork came back at 2.36:1,
and a centre-crop to #234's nominal 4:1 cut through the printing press's feet.
#234's real constraint is that the button and the image share a ratio so that
nothing is lost -- which a 2.36:1 pair satisfies exactly as well as a 4:1 one.
Naming a width rather than a full size keeps that reproducible: re-run it on a
re-drawn banner of some third ratio and it still does the right thing.

Cropping is always from the **centre**, and always in preference to stretching.
A squashed icon is obvious at a glance; a tighter frame usually is not. But a
crop still discards image, so check a cropped result on the page rather than
trusting it -- #234 asks for that anyway, since the tiles are seen at about
8.5rem with their lower third under a caption scrim.

Requires Pillow (see requirements.txt in this folder).
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

# Measured from static/img/topics -- see the module docstring.
SHAPES = {"tile": (400, 400), "banner": (1600, 400)}

DEFAULT_QUALITY = 82
METHOD = 6

# Extensions worth trying in a directory sweep. The *content* is what decides:
# the #234 artwork arrived named .jpg but was PNG data, which Pillow handles
# without being told, so this list only has to be generous.
SOURCE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def target_size(im, shape=None, width=None):
    """The (w, h) this image should be written at.

    With `shape`, a fixed size from SHAPES. With `width`, that width and a
    height scaled from the source, so the result keeps the source's ratio.
    """
    if shape:
        return SHAPES[shape]
    source_w, source_h = im.size
    return width, max(1, round(width * source_h / source_w))


def crop_to_ratio(im, ratio):
    """Centre-crop `im` to the given width/height ratio. A no-op if it matches."""
    width, height = im.size
    if abs(width / height - ratio) < 0.001:
        return im
    if width / height > ratio:          # too wide -- trim the sides
        new_width = round(height * ratio)
        left = (width - new_width) // 2
        return im.crop((left, 0, left + new_width, height))
    new_height = round(width / ratio)   # too tall -- trim top and bottom
    top = (height - new_height) // 2
    return im.crop((0, top, width, top + new_height))


def convert(src, dest, shape=None, width=None, quality=DEFAULT_QUALITY):
    """Write `src` to `dest` as WebP. Returns (size, bytes_written, cropped)."""
    with Image.open(src) as im:
        # RGB, not RGBA: the topic icons carry no alpha and the tile paints its
        # own background, so an alpha channel would be bytes doing nothing.
        im = im.convert("RGB")
        size = target_size(im, shape, width)
        before = im.size
        im = crop_to_ratio(im, size[0] / size[1])
        cropped = im.size != before
        im = im.resize(size, Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "WEBP", quality=quality, method=METHOD)
    return size, dest.stat().st_size, cropped


def main():
    parser = argparse.ArgumentParser(
        description="Convert artwork to the site's WebP tiles and banners.")
    shape = parser.add_mutually_exclusive_group(required=True)
    shape.add_argument("--tile", action="store_const", const="tile",
                       dest="shape", help="400x400 square icon")
    shape.add_argument("--banner", action="store_const", const="banner",
                       dest="shape", help="1600x400 wide banner (4:1)")
    shape.add_argument("--width", type=int,
                       help="this width, height from the source -- never crops")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                        help=f"WebP quality (default {DEFAULT_QUALITY})")
    parser.add_argument("src", type=Path, help="source file or directory")
    parser.add_argument("dest", type=Path, help="output file or directory")
    args = parser.parse_args()

    if args.src.is_dir():
        sources = sorted(p for p in args.src.iterdir()
                         if p.suffix.lower() in SOURCE_SUFFIXES)
        pairs = [(p, args.dest / (p.stem + ".webp")) for p in sources]
    else:
        pairs = [(args.src, args.dest)]

    if not pairs:
        print(f"nothing to convert in {args.src}")
        return 1

    for src, dest in pairs:
        size, written, cropped = convert(
            src, dest, args.shape, args.width, args.quality)
        note = "  (centre-cropped)" if cropped else ""
        print(f"{src.name} -> {dest}  {size[0]}x{size[1]}  "
              f"{written / 1024:.1f} KB{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
