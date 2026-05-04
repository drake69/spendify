#!/usr/bin/env python3
"""
create_icon.py — Generate the Spendif.ai app icon as .icns

Design:
    • Rounded rectangle background with a teal/green vertical gradient
      (#00B894 top → #00CEC9 bottom)
    • White "S" letter centred, bold
    • Small "€" sign in the bottom-right quadrant, in a lighter colour
      (#81ECEC) to create a subtle brand accent

Output:
    packaging/macos/spendifai.icns   (next to this script)

Requires:
    Pillow  (pip install Pillow)

Falls back to PNG-only output on non-macOS systems where iconutil is absent.
Exits with code 1 and prints install instructions if Pillow is not available.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Pillow availability check ─────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print(
        "Error: Pillow is required to generate the icon.\n"
        "Install it with:\n"
        "    pip install Pillow\n"
        "or, if using uv inside the project:\n"
        "    uv pip install Pillow\n"
        "After installation, re-run this script.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────
# All sizes required by Apple's iconutil / .icns format
ICON_SIZES: list[int] = [16, 32, 64, 128, 256, 512, 1024]

# Colour palette
COLOUR_TOP    = (0x00, 0xB8, 0x94, 0xFF)   # #00B894 — rich teal (top of gradient)
COLOUR_BOTTOM = (0x00, 0xCE, 0xC9, 0xFF)   # #00CEC9 — lighter cyan (bottom)
COLOUR_WHITE  = (0xFF, 0xFF, 0xFF, 0xFF)   # pure white for "S"
COLOUR_ACCENT = (0x81, 0xEC, 0xEC, 0xCC)   # #81ECEC 80% opacity — "€" accent

# Output paths (relative to this script's directory)
_SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_ICNS = _SCRIPT_DIR / "spendifai.icns"
OUTPUT_PNG  = _SCRIPT_DIR / "spendifai_1024.png"  # fallback for non-macOS


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _lerp_colour(c1: tuple[int, int, int, int], c2: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    """Linear interpolation between two RGBA colours."""
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(4))  # type: ignore[return-value]


def _rounded_rect_mask(size: int, radius_fraction: float = 0.22) -> Image.Image:
    """
    Return an RGBA image that is a white rounded rectangle on a transparent
    background.  radius_fraction is the corner radius as a fraction of size.
    """
    radius = round(size * radius_fraction)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    return mask


def _vertical_gradient(size: int, colour_top: tuple, colour_bottom: tuple) -> Image.Image:
    """
    Return an RGBA image of a smooth vertical gradient from colour_top to
    colour_bottom, shaped as a rounded rectangle.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = img.load()

    # Draw gradient row by row
    for y in range(size):
        t = y / (size - 1) if size > 1 else 0.0
        colour = _lerp_colour(colour_top, colour_bottom, t)
        for x in range(size):
            pixels[x, y] = colour  # type: ignore[index]

    # Apply rounded-rect mask: keep gradient only inside the rounded shape
    mask = _rounded_rect_mask(size)
    img.putalpha(mask)
    return img


def _find_system_font(bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Attempt to locate a system font with good Latin coverage.
    Priority: SF Pro (macOS) → Helvetica Neue → Arial → Pillow default.
    """
    candidates_bold = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/SFPro.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Linux fallback
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    candidates_regular = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    candidates = candidates_bold if bold else candidates_regular
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=100)
            except Exception:
                continue
    # Absolute last resort: Pillow's built-in bitmap font (no TrueType)
    return ImageFont.load_default()


def draw_icon(size: int) -> Image.Image:
    """
    Draw the complete Spendif.ai icon at the given pixel size.

    Composition:
        Layer 1: vertical teal gradient rounded rectangle (background)
        Layer 2: "S" white letter, centred, ~55% of icon height
        Layer 3: "€" accent, bottom-right quadrant, ~25% of icon height
    """
    img = _vertical_gradient(size, COLOUR_TOP, COLOUR_BOTTOM)
    draw = ImageDraw.Draw(img)

    # ── "S" letter ────────────────────────────────────────────────────────────
    s_font_size = round(size * 0.58)
    try:
        font_s = _find_system_font(bold=True)
        # Re-load at the correct pixel size
        for path in [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            if Path(path).exists():
                try:
                    font_s = ImageFont.truetype(path, size=s_font_size)
                    break
                except Exception:
                    continue
    except Exception:
        font_s = ImageFont.load_default()

    # Measure and centre "S"
    bbox_s = draw.textbbox((0, 0), "S", font=font_s)
    text_w = bbox_s[2] - bbox_s[0]
    text_h = bbox_s[3] - bbox_s[1]
    # Visual centring: shift up slightly to compensate for descender space
    x_s = (size - text_w) / 2 - bbox_s[0]
    y_s = (size - text_h) / 2 - bbox_s[1] - size * 0.04
    draw.text((x_s, y_s), "S", font=font_s, fill=COLOUR_WHITE)

    # ── "€" accent ────────────────────────────────────────────────────────────
    euro_font_size = round(size * 0.24)
    try:
        for path in [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            if Path(path).exists():
                try:
                    font_euro = ImageFont.truetype(path, size=euro_font_size)
                    break
                except Exception:
                    continue
        else:
            font_euro = ImageFont.load_default()
    except Exception:
        font_euro = ImageFont.load_default()

    bbox_e = draw.textbbox((0, 0), "€", font=font_euro)
    ew = bbox_e[2] - bbox_e[0]
    eh = bbox_e[3] - bbox_e[1]
    # Bottom-right quadrant: centre of that quadrant minus half the glyph
    x_e = (size * 0.75) - ew / 2 - bbox_e[0]
    y_e = (size * 0.72) - eh / 2 - bbox_e[1]
    draw.text((x_e, y_e), "€", font=font_euro, fill=COLOUR_ACCENT)

    return img


# ── iconutil helpers ──────────────────────────────────────────────────────────

def _iconset_name_for_size(size: int) -> str:
    """
    Return the iconset filename for a given size.
    Apple's convention: icon_NxN.png for 1x and icon_NxN@2x.png for 2x (retina).
    We generate the 2x variant for sizes <= 512 (so 512@2x = 1024 px).
    """
    if size <= 512:
        return f"icon_{size}x{size}.png"
    # 1024 px is the @2x representation of the 512x512 slot
    return "icon_512x512@2x.png"


def build_icns(output_path: Path) -> None:
    """
    Render icons at all required sizes, place them in a temporary .iconset
    directory, then call iconutil to produce the final .icns file.
    """
    iconutil = shutil.which("iconutil")
    if not iconutil:
        warn_msg = (
            "iconutil not found (non-macOS system?). "
            f"Saving 1024px PNG to {OUTPUT_PNG} instead."
        )
        print(f"Warning: {warn_msg}", file=sys.stderr)
        img = draw_icon(1024)
        img.save(str(OUTPUT_PNG), format="PNG")
        print(f"Saved: {OUTPUT_PNG}")
        return

    with tempfile.TemporaryDirectory(suffix=".iconset") as iconset_dir:
        iconset_path = Path(iconset_dir)

        for size in ICON_SIZES:
            img = draw_icon(size)
            filename = _iconset_name_for_size(size)
            dest = iconset_path / filename
            img.save(str(dest), format="PNG")
            print(f"  Rendered {size}x{size} → {filename}")

        # iconutil -c icns <iconset_dir> -o <output.icns>
        result = subprocess.run(
            [iconutil, "-c", "icns", str(iconset_path), "-o", str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"iconutil error:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

    print(f"Icon saved: {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("Spendif.ai icon generator")
    print(f"Output: {OUTPUT_ICNS}")
    print(f"Sizes:  {ICON_SIZES}")
    print()
    build_icns(OUTPUT_ICNS)


if __name__ == "__main__":
    main()
