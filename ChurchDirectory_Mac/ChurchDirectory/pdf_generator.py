"""
pdf_generator.py — Saves the directory as an HTML file and opens it in the browser.

No PDF library needed. The browser is already on every computer.
Staff print directly from the browser: Ctrl+P → Booklet → Print.
"""

from __future__ import annotations
import logging, os, platform, subprocess
from pathlib import Path
from errors import PDFRenderError

logger = logging.getLogger(__name__)


def generate(html: str, output_path: Path) -> Path:
    """Write HTML to disk and open it in the default browser."""
    out = output_path.with_suffix('.html')
    try:
        out.write_text(html, encoding='utf-8')
        logger.info("Saved: %s", out)
    except OSError as e:
        raise PDFRenderError(f"Could not write output: {e}") from e

    try:
        system = platform.system()
        if system == 'Darwin':
            subprocess.Popen(['open', str(out)])
        elif system == 'Windows':
            os.startfile(str(out))
        else:
            subprocess.Popen(['xdg-open', str(out)])
    except Exception as e:
        logger.warning("Could not open browser automatically: %s", e)

    return out
