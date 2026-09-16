#!/usr/bin/env python3
"""Render a terminal-style demo GIF from a REAL captured gauntlet run.

Run with Pillow installed (a tooling dependency, deliberately not a project
one -- the package itself must stay small):

    pip install Pillow
    python tools/make_demo_gif.py

The source is `experiments/007-m0-gate-passed/console.txt`, which is the
verbatim output of Actions run 35149486157. Nothing here invents a number:
if the demo is wrong, the experiment record is wrong, and both are in git.

That constraint is the point. A project whose whole argument is "do not
trust a reassuring-looking output" should not ship a hand-drawn screenshot
of a result it never produced.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "experiments" / "007-m0-gate-passed" / "console.txt"
OUT = ROOT / "docs" / "assets" / "demo.gif"

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SIZE = 14
COLS, ROWS = 122, 30
PAD = 16
CHROME = 26  # title bar

BG = "#11161d"
BAR = "#1b222c"
FG = "#c8d3e0"
DIM = "#5d6b7d"
CYAN = "#4ec9d9"
GREEN = "#5ec27a"
AMBER = "#d9a441"
RED = "#e06c68"
VIOLET = "#a78bfa"


def colour(line: str) -> str:
    """Colour by meaning, using only what the line actually says."""
    if line.startswith("---") or line.startswith("$ ") or line.startswith("  ("):
        return DIM
    if "GATE: PASS" in line:
        return GREEN
    if "GATE: FAIL" in line:
        return RED
    if "[sentinel]" in line:
        return VIOLET
    if "[GATED" in line:
        return AMBER
    if "<- frontier" in line or "<- report this one" in line:
        return GREEN
    if re.match(r"^(task|fingerprint|models|variants|mode|runs|median|top-1)", line):
        return CYAN
    if line.strip().startswith(("median tau", "top-1 stability")):
        return CYAN
    return FG


def bolded(line: str) -> bool:
    return "GATE:" in line or "<- frontier" in line or line.startswith("--- ")


def load_lines() -> list[str]:
    raw = SOURCE.read_text(encoding="utf-8").splitlines()
    # Keep the command, drop the provenance comment lines beneath it (they
    # are in the caption instead), then everything from the header down.
    out, skipping = [], False
    for ln in raw:
        if ln.startswith("  (GitHub Actions"):
            skipping = True
            continue
        if skipping and (ln.startswith("  ") or not ln.strip()):
            if ln.strip():
                continue
            skipping = False
            continue
        skipping = False
        out.append(ln[:COLS])
    return out


def render(lines: list[str], font, bold, upto: int, width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, width, CHROME], fill=BAR)
    for i, c in enumerate(("#e06c68", "#d9a441", "#5ec27a")):
        d.ellipse([PAD + i * 18, 9, PAD + i * 18 + 9, 18], fill=c)
    d.text((width // 2 - 96, 6), "gauntlet — the M0 gate, live", font=font, fill=DIM)

    visible = lines[max(0, upto - ROWS):upto]
    y = CHROME + PAD // 2
    for ln in visible:
        d.text((PAD, y), ln, font=bold if bolded(ln) else font, fill=colour(ln))
        y += 17
    # cursor
    if upto < len(lines):
        d.rectangle([PAD, y + 3, PAD + 8, y + 15], fill=DIM)
    return img


def main() -> None:
    font = ImageFont.truetype(FONT, SIZE)
    bold = ImageFont.truetype(BOLD, SIZE)
    lines = load_lines()

    width = int(COLS * font.getlength("M")) + PAD * 2
    height = CHROME + PAD + ROWS * 17

    # Two lines per frame: the output is dense and a per-line reveal makes a
    # 2.5 MB GIF nobody will wait to load.
    steps = list(range(2, len(lines) + 1, 2)) + [len(lines)]
    frames = [render(lines, font, bold, n, width, height) for n in steps]

    # A flat palette quantised once and shared by every frame. Terminal text
    # is a handful of colours, so 16 is plenty and the file shrinks roughly
    # tenfold against full RGB.
    master = frames[-1].quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    frames = [f.quantize(palette=master, dither=Image.Dither.NONE)
              for f in frames]

    # One long final frame rather than many identical ones.
    durations = [110] * (len(frames) - 1) + [4000]
    frames[0].save(
        OUT, save_all=True, append_images=frames[1:], loop=0,
        duration=durations, optimize=True, disposal=1,
    )
    kb = OUT.stat().st_size / 1024
    print(f"{OUT.relative_to(ROOT)}  {width}x{height}  "
          f"{len(frames)} frames  {kb:.0f} KB  from {len(lines)} real lines")


if __name__ == "__main__":
    main()
