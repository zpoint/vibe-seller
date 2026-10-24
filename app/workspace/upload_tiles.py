"""Readable tiles for tall uploaded images (supplier detail pages).

A supplier's product "detail page" is usually ONE image, ~750-1200 px
wide and 20,000-40,000 px tall: the spec table, the materials, the
dimensions and every feature callout stacked top to bottom. The agent's
image reader scales an image to fit a fixed pixel budget, so a page like
that arrives as a thin strip in which no text survives -- and the agent,
seeing only the product's shape and colours, writes the listing from the
SKU name and its own guesses. One listing went live under the wrong
product category while the page named the right one in large type.

So a tall upload is cut into tiles the reader can actually read, in
order, with a small overlap so a line of text cut at a tile edge appears
whole in one of the two tiles. The tiles live in a hidden directory
beside the upload (``uploads/.tiles/<name>/``), are made once, and are
kept across retries with the rest of ``uploads/``.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TILE_DIR = '.tiles'
# An image is "tall" when the reader would shrink its text to nothing:
# much taller than wide, and tall in absolute terms (a portrait product
# photo is 1:1.5 and reads fine).
TALL_RATIO = 2.5
TALL_MIN_HEIGHT = 2500
# Tile geometry, in OUTPUT pixels: wide enough for small spec-table type,
# short enough that the reader does not shrink it again.
TILE_WIDTH = 1000
TILE_HEIGHT = 1400
OVERLAP = 80
_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}


def tiles_for(path: Path) -> list[Path]:
    """The readable tiles of `path`, made on first call; [] if not tall.

    Never raises: a file Pillow cannot open, or Pillow missing, is
    simply not tiled and the agent gets the original path as before.
    """
    if path.suffix.lower() not in _IMAGE_EXTS:
        return []
    out_dir = path.parent / TILE_DIR / path.stem
    existing = sorted(out_dir.glob('part*.jpg')) if out_dir.is_dir() else []
    if existing and out_dir.stat().st_mtime >= path.stat().st_mtime:
        return existing
    try:
        from PIL import Image  # noqa: PLC0415 -- optional, heavy

        Image.MAX_IMAGE_PIXELS = None  # a 1200x36000 page is legitimate
        with Image.open(path) as im:
            w, h = im.size
            if h < TALL_MIN_HEIGHT or h < TALL_RATIO * w:
                return []
            scale = min(1.0, TILE_WIDTH / w)
            step = int((TILE_HEIGHT - OVERLAP) / scale)
            span = int(TILE_HEIGHT / scale)
            rgb = im.convert('RGB')
            out_dir.mkdir(parents=True, exist_ok=True)
            for old in existing:
                old.unlink(missing_ok=True)
            tiles = []
            for n, top in enumerate(range(0, h, step), start=1):
                box = (0, top, w, min(h, top + span))
                tile = rgb.crop(box)
                if scale < 1.0:
                    tile = tile.resize((
                        round(tile.width * scale),
                        round(tile.height * scale),
                    ))
                dest = out_dir / f'part{n:02d}.jpg'
                tile.save(dest, quality=82)
                tiles.append(dest)
                if box[3] >= h:
                    break
            return tiles
    except Exception:  # noqa: BLE001 -- a bad upload must not break the prompt
        logger.warning('could not tile %s', path, exc_info=True)
        return []
