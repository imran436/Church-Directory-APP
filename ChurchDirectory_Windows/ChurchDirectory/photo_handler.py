"""
photo_handler.py — Generate initials placeholder images for members without photos.

Called after validation when person.photo_path is None.
Uses Pillow to draw a coloured circle with two-letter monogram.
Colours are deterministic per person (based on name hash) matching the directory palette.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from models import Person

logger = logging.getLogger(__name__)

# Palette of background colours that work well against white initials
# Drawn from the directory design tokens
_PALETTE = [
    "#6B1E2E",  # burgundy
    "#8B2D3F",  # burgundy-mid
    "#5C3D2E",  # warm brown
    "#3D4A6B",  # slate blue
    "#6B3A2A",  # dark sienna
    "#7A4A3A",  # sienna
    "#2D4A3E",  # dark teal
    "#4A3A6B",  # muted purple
    "#3A5A4A",  # forest green
    "#6B4A2D",  # amber brown
]


def _pick_colour(name: str) -> str:
    """Deterministically pick a palette colour based on the person's name."""
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    hex_colour = hex_colour.lstrip("#")
    return tuple(int(hex_colour[i:i+2], 16) for i in (0, 2, 4))


def generate_placeholder(person: Person, output_dir: str, size: int = 200) -> Optional[str]:
    """
    Generate a circular initials placeholder PNG for a person.

    Args:
        person:      The Person record (uses first_name, last_name, id)
        output_dir:  Directory to write the PNG into
        size:        Image size in pixels (square)

    Returns:
        Path to the generated PNG, or None if Pillow is unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.error("Pillow not installed — cannot generate initials placeholders.")
        return None

    # Compute initials
    first_initial = (person.first_name or "?")[0].upper()
    last_initial  = (person.last_name  or "?")[0].upper()
    initials      = f"{first_initial}{last_initial}"

    # Colour
    bg_hex = _pick_colour(f"{person.last_name}{person.first_name}")
    bg_rgb = _hex_to_rgb(bg_hex)

    # Create image with transparent background
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw filled circle
    draw.ellipse([0, 0, size - 1, size - 1], fill=(*bg_rgb, 255))

    # Draw initials text
    font_size = int(size * 0.38)
    try:
        # Try to use a system font
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except (OSError, AttributeError):
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except (OSError, AttributeError):
            font = ImageFont.load_default()

    # Centre the text
    bbox   = draw.textbbox((0, 0), initials, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]

    draw.text((x, y), initials, fill=(255, 255, 255, 255), font=font)

    # Save
    safe_last  = "".join(c for c in (person.last_name  or "x").lower() if c.isalnum())
    safe_first = "".join(c for c in (person.first_name or "x").lower() if c.isalnum())
    filename   = f"placeholder_{safe_last}_{safe_first}_{person.id}.png"
    out_path   = os.path.join(output_dir, filename)

    img.save(out_path, "PNG")
    return out_path


def ensure_photos(people: list[Person], photo_dir: str) -> tuple[int, int]:
    """
    For every person without a photo, generate an initials placeholder.
    Mutates person.photo_path and person.has_photo in place.

    Returns:
        (real_photos, placeholders) counts
    """
    real_photos  = 0
    placeholders = 0

    for person in people:
        if person.photo_path and os.path.exists(person.photo_path):
            person.has_photo = True
            real_photos += 1
        else:
            # Generate placeholder
            path = generate_placeholder(person, photo_dir)
            person.photo_path = path
            person.has_photo  = False
            placeholders += 1

    logger.info("Photos: %d real, %d placeholders generated", real_photos, placeholders)
    return real_photos, placeholders
