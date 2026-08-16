"""Render the PWA icons from icon.svg.template.

Writes the two SVG variants plus an HTML harness per size. The PNGs are produced
by screenshotting that harness in headless Chrome, because neither Pillow nor
cairosvg is installed and adding a native image dependency to rasterise four
flat shapes is not a trade worth making.

    python web/ui/icon-src/build_icons.py

Then screenshot each harness listed on stdout into the matching PNG path.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "public" / "icons"

# Blue is the real tracker, amber is synthetic. They must not be confusable at
# icon size — see the note in icon.svg.template.
VARIANTS = {
    "local": {"FG": "#F2F5FB", "BG": "#2F6AE0"},
    "demo": {"FG": "#1B1408", "BG": "#E0AA4A"},
}
SIZES = (192, 512)


def main() -> int:
    template = (HERE / "icon.svg.template").read_text(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    harnesses = []

    for variant, colours in VARIANTS.items():
        svg = template
        for key, value in colours.items():
            svg = svg.replace("{{%s}}" % key, value)
        svg_path = OUT / f"icon-{variant}.svg"
        svg_path.write_text(svg, encoding="utf-8")

        for size in SIZES:
            # margin:0 and an exact-size body so the screenshot needs no
            # cropping; a stray scrollbar would shift the mark off-centre.
            html = (
                "<!doctype html><meta charset=utf-8>"
                "<style>html,body{margin:0;padding:0;overflow:hidden;"
                f"width:{size}px;height:{size}px}}"
                f"svg{{display:block;width:{size}px;height:{size}px}}</style>"
                + svg)
            harness = OUT / f"_harness-{variant}-{size}.html"
            harness.write_text(html, encoding="utf-8")
            harnesses.append((harness, OUT / f"icon-{variant}-{size}.png", size))

    print(f"wrote {len(VARIANTS)} svg + {len(harnesses)} harnesses to {OUT}")
    for harness, png, size in harnesses:
        print(f"  {size:>3}px  {harness.name}  ->  {png.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
