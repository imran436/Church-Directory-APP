"""
main.py — Entry point for the Church Directory Generator.

Responsibilities:
  - Configure logging
  - Launch the UI

Usage:
  python main.py
  (or double-click the PyInstaller bundle)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def _configure_logging():
    """Set up file + console logging."""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "app.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main():
    _configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Church Directory Generator starting …")

    try:
        from main_ui import run_app
        run_app()
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        try:
            import tkinter.messagebox as mb
            mb.showerror("Fatal Error",
                         f"The application encountered a fatal error and must close.\n\n{e}")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
