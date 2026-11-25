#!/usr/bin/env python3
"""Write the apps' brand assets: the app icon (Icon Composer `.icon`) and the iOS launch screen.

The mark is the tickr.bot site's: a teal price line that dips, bounces and breaks out to a dot,
on a dark rounded tile. One description here produces every file, so the macOS and iOS icons and
the launch screen never drift apart:

- `apps/macos/Tickr/AppIcon.icon` and `apps/ios/Tickr/AppIcon.icon`: the layered icon
  (one look in every appearance; the system adds Liquid Glass and the tinted rendering).
- `apps/ios/Tickr/Assets.xcassets`: `LaunchBackground` (the window background, so the launch
  screen hands over to the first screen without a flash) and `LaunchMark` (the mark on its tile).

Colours come from `apps/shared/Sources/TickrDesign/Palette.swift`. The in-app mark
(`BrandMark` in TickrDesign) draws the same tile and line from the same shares. Files are committed;
`tests/test_brand_assets.py` fails when they differ from this script's output.
Usage: make_brand_assets.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PALETTE = REPO / "apps/shared/Sources/TickrDesign/Palette.swift"
ICON_DIRS = (REPO / "apps/macos/Tickr/AppIcon.icon", REPO / "apps/ios/Tickr/AppIcon.icon")
IOS_ASSETS = REPO / "apps/ios/Tickr/Assets.xcassets"

# Icon canvas in points (Icon Composer draws on 1024 x 1024).
CANVAS = 1024
# The mark as the tickr.bot site draws it: a price line on a 24-unit grid that dips, bounces and
# breaks out to a dot, inside a 40-unit tile. GRID is the line's grid, TILE the tile's side.
GRID = 24
TILE = 40
LINE = ((3, 15), (8, 11), (11, 14), (20, 5))
# Line width and dot radius on the grid.
LINE_WIDTH_UNITS = 2.4
DOT_RADIUS_UNITS = 1.6
# The same as shares of the tile's side: the grid sits centred in the tile. BrandMark.points,
# lineWidth and dotDiameter in apps/shared/Sources/TickrDesign/BrandMark.swift match these.
INSET = (TILE - GRID) / 2 / TILE
POINTS = tuple((INSET + x / TILE, INSET + y / TILE) for x, y in LINE)
LINE_WIDTH = LINE_WIDTH_UNITS / TILE
DOT_RADIUS = DOT_RADIUS_UNITS / TILE
# The tile's corner radius (12 of 40) and edge width (1 of 40), as shares of its side.
TILE_RADIUS = 12 / TILE
TILE_EDGE = 1 / TILE
# The icon's soft glow behind the line: its radius and its opacity at the centre (the site's halo).
GLOW_RADIUS = 0.42
GLOW_OPACITY = 0.35
# Launch-screen mark size in points (the image is drawn at this size, centred).
LAUNCH_MARK = 160

# A role is a Color, or a Swatch when other code needs its raw values (Palette.swift).
COLOR = re.compile(r"static let (\w+) = (?:Color|Swatch)"
                   r"\(light: 0x([0-9A-F]{6}), dark: 0x([0-9A-F]{6})\)")


def palette() -> dict[str, tuple[str, str]]:
    """Each role's light and dark hex value from Palette.swift."""
    return {name: (light, dark) for name, light, dark in COLOR.findall(PALETTE.read_text())}


def pt(share: float, size: float = CANVAS) -> str:
    return f"{share * size:.1f}".rstrip("0").rstrip(".")


def line_path(size: float) -> str:
    return "M" + " L".join(f"{pt(x, size)},{pt(y, size)}" for x, y in POINTS)


def svg(body: str, size: float = CANVAS, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{pt(1, size)}" height="{pt(1, size)}" '
            f'viewBox="0 0 {pt(1, size)} {pt(1, size)}">{defs}{body}</svg>\n')


def mark_centre() -> tuple[float, float]:
    """The middle of the line's bounding box, where the glow sits."""
    xs, ys = [x for x, _ in POINTS], [y for _, y in POINTS]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def layers(colors: dict[str, tuple[str, str]], size: float = CANVAS) -> dict[str, str]:
    """The mark's three icon layers as SVG: the glow, the line and the dot, all in the brand teal."""
    teal = colors["brandLine"][0]
    last_x, last_y = POINTS[-1]
    cx, cy = mark_centre()
    glow = (f'<radialGradient id="g" cx="{pt(cx, size)}" cy="{pt(cy, size)}" r="{pt(GLOW_RADIUS, size)}" '
            'gradientUnits="userSpaceOnUse">'
            f'<stop offset="0" stop-color="#{teal}" stop-opacity="{GLOW_OPACITY}"/>'
            f'<stop offset="1" stop-color="#{teal}" stop-opacity="0"/></radialGradient>')
    stroke = (f'stroke-width="{pt(LINE_WIDTH, size)}" stroke-linecap="round" '
              f'stroke-linejoin="round" fill="none"')
    return {
        "glow": svg(f'<rect width="{pt(1, size)}" height="{pt(1, size)}" fill="url(#g)"/>', size, glow),
        "line": svg(f'<path d="{line_path(size)}" stroke="#{teal}" {stroke}/>', size),
        "dot": svg(f'<circle cx="{pt(last_x, size)}" cy="{pt(last_y, size)}" '
                   f'r="{pt(DOT_RADIUS, size)}" fill="#{teal}"/>', size),
    }


def tile(colors: dict[str, tuple[str, str]], size: float) -> str:
    """The whole mark on its tile (the launch screen's image): as BrandMark draws it in the apps."""
    top, bottom, edge = (colors[k][0] for k in ("brandTileTop", "brandTileBottom", "brandTileEdge"))
    half = TILE_EDGE / 2
    defs = ('<linearGradient id="t" x1="0.33" y1="0" x2="0.67" y2="1">'
            f'<stop offset="0" stop-color="#{top}"/><stop offset="1" stop-color="#{bottom}"/>'
            '</linearGradient>')
    body = (f'<rect x="{pt(half, size)}" y="{pt(half, size)}" width="{pt(1 - TILE_EDGE, size)}" '
            f'height="{pt(1 - TILE_EDGE, size)}" rx="{pt(TILE_RADIUS - half, size)}" fill="url(#t)" '
            f'stroke="#{edge}" stroke-width="{pt(TILE_EDGE, size)}"/>')
    small = layers(colors, size)
    body += "".join(re.sub(r"</?svg[^>]*>\n?", "", small[k]) for k in ("line", "dot"))
    return svg(body, size, defs)


def srgb(hex_value: str) -> str:
    channels = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return "srgb:" + ",".join(f"{c:.5f}" for c in [*channels, 1])


def xcode_json(value: object) -> str:
    """JSON the way Xcode writes asset files: `"key" : value`, 2-space indent."""
    return json.dumps(value, indent=2, separators=(",", " : ")) + "\n"


def group(name: str, image: str, glass: bool, shadow: str, translucent: bool) -> dict:
    return {
        "layers": [{"glass": glass, "image-name": f"{image}.svg", "name": name}],
        "name": name,
        "shadow": {"kind": shadow, "opacity": 0.5},
        "specular": glass,
        "translucency": {"enabled": translucent, "value": 0.5},
    }


def icon_json(colors: dict[str, tuple[str, str]]) -> str:
    # The tile is the brand's and the same in every appearance: the system lightens it towards
    # the top (the site's tile gradient) and makes the tinted rendering from it.
    return xcode_json({
        "fill": {"automatic-gradient": srgb(colors["brandTileTop"][0])},
        # Front to back.
        "groups": [
            group("Dot", "dot", True, "layer-color", False),
            group("Line", "line", True, "layer-color", False),
            group("Glow", "glow", False, "none", True),
        ],
        "supported-platforms": {"squares": "shared"},
    })


def colorset(light: str, dark: str) -> str:
    def entry(hex_value: str, appearance: str | None) -> dict:
        r, g, b = (f"0x{hex_value[i:i + 2]}" for i in (0, 2, 4))
        item: dict = {"color": {"color-space": "srgb",
                                "components": {"alpha": "1.000", "blue": b, "green": g, "red": r}},
                      "idiom": "universal"}
        if appearance:
            item["appearances"] = [{"appearance": "luminosity", "value": appearance}]
        return item
    return xcode_json({"colors": [entry(light, None), entry(dark, "dark")],
                       "info": {"author": "xcode", "version": 1}})


def outputs() -> dict[Path, str]:
    """Every generated file and its content."""
    colors = palette()
    files: dict[Path, str] = {}
    for icon in ICON_DIRS:
        files[icon / "icon.json"] = icon_json(colors)
        for name, content in layers(colors).items():
            files[icon / "Assets" / f"{name}.svg"] = content
    mark = tile(colors, LAUNCH_MARK)
    info = {"info": {"author": "xcode", "version": 1}}
    files[IOS_ASSETS / "Contents.json"] = xcode_json(info)
    files[IOS_ASSETS / "LaunchBackground.colorset/Contents.json"] = colorset(*colors["background"])
    files[IOS_ASSETS / "LaunchMark.imageset/LaunchMark.svg"] = mark
    files[IOS_ASSETS / "LaunchMark.imageset/Contents.json"] = xcode_json({
        "images": [{"filename": "LaunchMark.svg", "idiom": "universal"}],
        **info,
        "properties": {"preserves-vector-representation": True},
    })
    return files


def main() -> int:
    for icon in ICON_DIRS:
        shutil.rmtree(icon, ignore_errors=True)
    for path, content in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(path.relative_to(REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
