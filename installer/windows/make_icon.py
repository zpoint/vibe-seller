"""Generate vibe-seller.ico from the shared brand mark.

Draws the same design as frontend/public/vibe-seller.svg (an indigo
rounded square with a white "V") and writes a multi-resolution
Windows .ico used by the installer, Start-Menu entry, and tray. Keeping
one design in both places is what makes the web favicon and the Windows
icon consistent.

Run from the repo (Pillow required — it's a dependency already):
    python installer/windows/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

INDIGO = (99, 102, 241, 255)
WHITE = (255, 255, 255, 255)
OUT = Path(__file__).parent / 'vibe-seller.ico'
SIZES = [16, 32, 48, 64, 128, 256]


def _render(size: int) -> Image.Image:
    # Supersample 4x then downscale for smooth edges at small sizes.
    scale = 4
    big = size * scale
    img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = big / 256.0  # design is authored on a 256 grid

    d.rounded_rectangle(
        [0, 0, big - 1, big - 1], radius=int(56 * u), fill=INDIGO
    )
    # The "V": left-top -> bottom-center -> right-top.
    d.line(
        [(84 * u, 84 * u), (128 * u, 172 * u), (172 * u, 84 * u)],
        fill=WHITE,
        width=int(24 * u),
        joint='curve',
    )
    # Round the stroke ends (PIL lines are flat-capped) with dots.
    r = 12 * u
    for cx, cy in [(84 * u, 84 * u), (172 * u, 84 * u), (128 * u, 172 * u)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    # Save the high-res render; Pillow embeds each requested size by
    # downscaling from it (so every frame is crisp, not an upscaled 16px).
    base = _render(256)
    base.save(OUT, format='ICO', sizes=[(s, s) for s in SIZES])
    print(f'wrote {OUT} ({", ".join(str(s) for s in SIZES)})')  # noqa: T201


if __name__ == '__main__':
    main()
