#!/usr/bin/env python3
"""Render the arena's fault injector from a font glyph.

    pip install Pillow
    python tools/make_dragon_png.py

The first dragon was hand-drawn as SVG paths and was, accurately, called
badly shaped. Drawing a creature with bezier curves and no eyes on the
result is a bad use of anyone's afternoon, and the arena had three
rendering bugs to prove it.

So the dragon is a glyph: U+1F409, rendered from **Noto Color Emoji**,
which ships with this container at
`/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`. Noto Color Emoji is
licensed under the SIL Open Font License 1.1, whose terms cover the font
software; the OFL says explicitly that the requirement does not apply to
documents created *using* the font, which is what a rendered glyph is.

Regenerating it is a committed script rather than a binary somebody once
dropped in, on the same terms as `demo.gif`: if the asset is wrong, the
generator is wrong, and both are in git.

Noto Color Emoji is a CBDT bitmap font with a single 109px strike, so the
glyph is rendered at its native size and scaled up once for high-DPI
screens. Scaling a bitmap is lossy, which is why it happens here, in a
reviewable step, rather than in the browser at whatever size the layout
happens to want.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")
OUT = ROOT / "src" / "agent_gauntlet" / "ui" / "dragon.png"

GLYPH = "\U0001F409"      # DRAGON
NATIVE = 109              # the font's only bitmap strike
SCALE = 2                 # one upscale, for high-DPI


def render() -> Image.Image:
    font = ImageFont.truetype(str(FONT), NATIVE)
    canvas = Image.new("RGBA", (NATIVE * 2, NATIVE * 2), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text(
        (NATIVE, NATIVE), GLYPH, font=font, anchor="mm", embedded_color=True,
    )
    # Cropped to the ink, so the arena positions a dragon rather than a
    # box with a dragon somewhere inside it.
    box = canvas.getbbox()
    if box is None:
        raise SystemExit("the glyph rendered empty -- is this a colour emoji font?")
    glyph = canvas.crop(box)
    return glyph.resize(
        (glyph.width * SCALE, glyph.height * SCALE), Image.LANCZOS)


if __name__ == "__main__":
    if not FONT.exists():
        raise SystemExit(
            f"{FONT} not found. Install fonts-noto-color-emoji, or point FONT "
            f"at another colour emoji font."
        )
    image = render()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(f"wrote {OUT}  ({image.width}x{image.height})")
