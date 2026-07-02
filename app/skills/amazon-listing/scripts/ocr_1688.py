#!/usr/bin/env python3
"""Local, GPU-free OCR for 1688 product detail images.

1688 puts a product's spec table, feature callouts, and size/colour
charts inside the *description images* (long JPEG/WEBP strips), not in
the page HTML. To generate an accurate Amazon title / bullets /
description we OCR those images. This runs fully local with no GPU and
no cloud vision API: `rapidocr-onnxruntime` (ONNX runtime,
CPUExecutionProvider) reads mixed Chinese + English.

Why not a cloud vision model: the detail strips are private supplier
data and there can be a dozen per offer; a local pass keeps them off
third-party services and costs nothing per image.

Usage
-----
  ocr_1688.py IMAGE_OR_DIR [IMAGE_OR_DIR ...] [--json] [--min-conf 0.5]

Prints the recognised text per image (WEBP is converted to PNG in
memory first). With --json, emits {path: [lines]} for a downstream
fill step to fold into the product-info blob.

Requires: rapidocr-onnxruntime, pillow.
"""

import argparse
import json
import os
import sys

# Heavy, optional deps — shipped via the skill's requirements.txt and
# installed per-user, not in the test/CI env. Import once at module load
# so a missing dep yields a clean message rather than a mid-run crash.
try:
    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover - exercised only when deps absent
    np = Image = RapidOCR = None

VALID_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')


def _iter_images(paths):
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for name in sorted(files):
                    if name.lower().endswith(VALID_EXT):
                        yield os.path.join(root, name)
        elif p.lower().endswith(VALID_EXT):
            yield p
        else:
            print(f'skip (not an image): {p}', file=sys.stderr)


def _load_rgb(path):
    """Return a numpy RGB array; converts WEBP/other via Pillow."""
    with Image.open(path) as im:
        return np.array(im.convert('RGB'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('paths', nargs='+', help='image files or directories')
    ap.add_argument(
        '--json', action='store_true', help='emit {path: [lines]} JSON'
    )
    ap.add_argument(
        '--min-conf',
        type=float,
        default=0.5,
        help='drop lines below this confidence (default 0.5)',
    )
    args = ap.parse_args()

    if RapidOCR is None:
        sys.exit(
            'error: rapidocr-onnxruntime + pillow are required '
            '(pip install rapidocr-onnxruntime pillow)'
        )

    ocr = RapidOCR()
    out = {}
    for path in _iter_images(args.paths):
        try:
            img = _load_rgb(path)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f'error reading {path}: {e}', file=sys.stderr)
            continue
        result, _elapsed = ocr(img)
        lines = []
        for entry in result or []:
            # rapidocr returns [box, text, confidence] per line.
            text, conf = entry[1], float(entry[2])
            if conf >= args.min_conf and text.strip():
                lines.append(text.strip())
        out[path] = lines
        if not args.json:
            print(f'\n=== {path} ({len(lines)} lines) ===')
            for ln in lines:
                print(ln)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
