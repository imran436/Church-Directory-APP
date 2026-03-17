"""
renderer.py — Render the Jinja2 HTML template with directory data.

Output: landscape 11x8.5" sheets, each with two 5.5x8.5" leaves side by side.
  Sheet 1:   Back Cover (left) | Front Cover (right)
  Sheet 2:   Page 1 (left)     | Page 2 (right)
  Sheet 3:   Page 3 (left)     | Page 4 (right)
  ...
  Sheet 13:  Page 23 (left)    | Page 24 (right)

Total: 13 landscape PDF pages.

PRINTING: Open PDF → Print → Booklet mode (auto-imposes for fold+staple).
If no booklet mode: duplex, flip on short edge, then fold and staple centre.
"""

from __future__ import annotations
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from models import AppConfig, DirectoryPage

logger = logging.getLogger(__name__)
_ASSETS_DIR = Path(__file__).parent / "assets"


def _load_logo(filename: str) -> str:
    path = _ASSETS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    logger.warning("Logo file not found: %s", path)
    return ""


def render(pages: list[DirectoryPage], config: AppConfig) -> str:
    env = Environment(
        loader        = FileSystemLoader(str(_ASSETS_DIR)),
        autoescape    = select_autoescape(["html"]),
        trim_blocks   = True,
        lstrip_blocks = True,
    )
    template = env.get_template("template.html")
    html = template.render(
        pages            = pages,
        config           = config,
        logo_dark        = _load_logo("logo_dark_b64.txt"),
        logo_white       = _load_logo("logo_white_b64.txt"),
        entries_per_page = config.entries_per_page,
    )
    logger.info("Template rendered: %d dir pages → %d sheets, %d chars",
                len(pages), len(pages)//2 + 1, len(html))
    return html
