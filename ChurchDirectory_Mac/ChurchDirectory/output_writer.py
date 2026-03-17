"""
output_writer.py — Write output files and open the output folder.

Responsibilities:
- Write PDF to staff-chosen folder
- Write structured run log (no PII — counts and timestamps only)
- Prune old run logs (keep max_run_logs most recent)
- Open output folder in OS file manager
- Clean up temporary photo directory
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from models import RunReport
from errors import OutputWriteError

logger = logging.getLogger(__name__)


def _open_file(path: Path) -> None:
    """Open a file in the OS default application (browser for HTML)."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        logger.warning("Could not open file: %s", e)


def _open_folder(path: Path) -> None:
    """Open a folder in the OS default file manager."""
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif system == "Windows":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        logger.warning("Could not open output folder: %s", e)


def _prune_logs(log_dir: Path, max_logs: int) -> None:
    """Delete oldest run logs keeping only max_logs most recent."""
    logs = sorted(log_dir.glob("run_log_*.txt"))
    while len(logs) > max_logs:
        oldest = logs.pop(0)
        try:
            oldest.unlink()
            logger.debug("Pruned old log: %s", oldest.name)
        except Exception:
            pass


def _format_run_log(report: RunReport) -> str:
    """
    Format a human-readable run log.
    IMPORTANT: Contains NO member personal data (names, addresses, etc.)
    Only counts, timestamps, and anonymised warnings.
    """
    lines = [
        "=" * 60,
        f"  The Gathering Church — Directory Generator",
        f"  Run Log",
        "=" * 60,
        "",
        f"  Timestamp:        {report.timestamp}",
        f"  Duration:         {report.duration_seconds:.1f} seconds",
        f"  Output:           {report.output_path}",
        "",
        "-" * 60,
        "  MEMBER STATISTICS",
        "-" * 60,
        f"  Active members fetched:   {report.member_count}",
        f"  Directory groups:         {report.group_count}",
        f"  Households:               {report.processing.households}",
        f"  Individual entries:       {report.processing.individuals}",
        f"  Members with no address:  {report.processing.no_address}",
        f"  Directory pages:          {report.page_count}",
        "",
        "-" * 60,
        "  PHOTO STATISTICS",
        "-" * 60,
        f"  Photos downloaded:        {report.photo_successes}",
        f"  Placeholders generated:   {report.photo_failures}",
        "",
    ]

    if report.validation.warning_count > 0:
        lines += [
            "-" * 60,
            f"  VALIDATION WARNINGS ({report.validation.warning_count})",
            "-" * 60,
        ]
        for w in report.validation.warnings:
            lines.append(f"  [{w.field}] {w.person_name}: {w.message}")
        lines.append("")

    if report.warnings:
        lines += [
            "-" * 60,
            "  RUN WARNINGS",
            "-" * 60,
        ]
        for w in report.warnings:
            lines.append(f"  ! {w}")
        lines.append("")

    if report.errors:
        lines += [
            "-" * 60,
            "  ERRORS",
            "-" * 60,
        ]
        for e in report.errors:
            lines.append(f"  ERROR: {e}")
        lines.append("")

    lines += [
        "=" * 60,
        "  Run complete.",
        "=" * 60,
    ]

    return "\n".join(lines)


def write_output(
    pdf_path:     Path,
    output_dir:   Path,
    report:       RunReport,
    max_run_logs: int = 10,
    open_folder:  bool = True,
) -> Path:
    """
    Write the PDF and run log to the output directory.

    Args:
        pdf_path:     Source PDF file (from pdf_generator)
        output_dir:   Staff-chosen destination folder
        report:       Run statistics
        max_run_logs: Maximum number of run logs to retain
        open_folder:  Whether to open the folder after writing

    Returns:
        Path to the written PDF.

    Raises:
        OutputWriteError if writing fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── HTML file ─────────────────────────────────────────────────────────────
    dest_pdf = output_dir / pdf_path.name
    if pdf_path.resolve() != dest_pdf.resolve():
        # File is in a temp location — copy it over
        try:
            shutil.copy2(str(pdf_path), str(dest_pdf))
            logger.info("HTML written to %s", dest_pdf)
        except OSError as e:
            raise OutputWriteError(str(output_dir), str(e)) from e
    else:
        logger.info("HTML already at destination: %s", dest_pdf)

    # ── Run log ───────────────────────────────────────────────────────────────
    log_dir = output_dir / "run_logs"
    log_dir.mkdir(exist_ok=True)

    ts       = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_name = f"run_log_{ts}.txt"
    log_path = log_dir / log_name

    try:
        log_path.write_text(_format_run_log(report), encoding="utf-8")
        logger.info("Run log written to %s", log_path)
    except OSError as e:
        logger.warning("Could not write run log: %s", e)

    # ── Prune old logs ────────────────────────────────────────────────────────
    _prune_logs(log_dir, max_run_logs)

    # ── Open folder ───────────────────────────────────────────────────────────
    if open_folder:
        _open_file(dest_pdf)    # Open HTML in browser
        _open_folder(output_dir)  # Also open folder so they can find it

    return dest_pdf


def cleanup_temp(temp_dir: str) -> None:
    """Delete the temporary photo directory. Fails silently."""
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.debug("Temp directory cleaned up: %s", temp_dir)
    except Exception as e:
        logger.warning("Could not clean up temp directory %s: %s", temp_dir, e)
