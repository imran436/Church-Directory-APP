"""
pipeline.py — Pipeline orchestrator.

Runs in a background worker thread.
Coordinates all stages and reports progress to the UI via a thread-safe Queue.

Stages:
  1. auth       — retrieve credentials
  2. fetch      — download member data + photos concurrently
  3. validate   — normalise and validate records
  4. photos     — generate initials placeholders for members without photos
  5. process    — sort, group, paginate
  6. render     — Jinja2 HTML template
  7. pdf        — generate PDF via WeasyPrint/Playwright
  8. output     — write to disk, log, open folder
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from models import (
    AppConfig, Credentials, ProgressMessage, RunReport,
    ValidationReport, ProcessingReport,
)
from errors import DirectoryError, CredentialsNotFoundError
import auth
import pc_client
import validator
import processor
import renderer
import output_writer

logger = logging.getLogger(__name__)


def _progress(q: queue.Queue, stage: str, message: str,
              current: int = 0, total: int = 0) -> None:
    """Put a progress message on the UI queue."""
    q.put(ProgressMessage(stage=stage, message=message,
                          current=current, total=total))


def run(
    config:       AppConfig,
    credentials:  Credentials,
    output_dir:   Path,
    progress_q:   queue.Queue,
    cancel_event: threading.Event,
) -> None:
    """
    Execute the full directory generation pipeline.

    Designed to run in a background thread.
    Posts ProgressMessage objects to progress_q throughout.
    Posts a final message with stage='done' or stage='error'.

    Args:
        config:       Application configuration
        credentials:  Planning Center App ID + PAT
        output_dir:   Where to write the final PDF
        progress_q:   Thread-safe queue for UI progress updates
        cancel_event: Set by UI to request cancellation
    """
    start_time   = time.time()
    temp_dir_obj = None
    warnings     = []
    errors       = []

    def check_cancel():
        if cancel_event.is_set():
            raise DirectoryError("Run cancelled by user.", "Directory generation was cancelled.")

    try:
        # ── Stage 1: Auth ─────────────────────────────────────────────────────
        _progress(progress_q, "auth", "Connecting to Planning Center…")
        check_cancel()
        # Credentials already validated at setup — just confirm they're present
        logger.info("Pipeline starting for list %s", config.list_id)

        # ── Stage 2: Fetch members ────────────────────────────────────────────
        _progress(progress_q, "fetch", "Downloading member list…")
        check_cancel()

        raw_people = pc_client.fetch_members(
            credentials = credentials,
            list_id     = config.list_id,
        )
        check_cancel()

        # ── Stage 3: Validate + normalise ─────────────────────────────────────
        _progress(progress_q, "process", "Processing member data…")
        people, val_report = validator.validate_and_normalise(
            raw_people,
            use_goes_by_name=config.use_goes_by_name,
        )
        if val_report.warning_count:
            warnings.append(
                f"{val_report.warning_count} data quality warning(s) — see run log for details"
            )

        # ── Stage 4: Count photos (avatar URLs already on person.photo_path) ───
        real_photos       = sum(1 for p in people if p.photo_path)
        placeholder_count = sum(1 for p in people if not p.photo_path)
        if placeholder_count > 0:
            warnings.append(f"{placeholder_count} member(s) had no photo")
        check_cancel()

        # ── Stage 5: Sort, group, paginate ────────────────────────────────────
        pages, groups, proc_report = processor.process(
            people,
            entries_per_page = config.entries_per_page,
            fuzzy_threshold  = config.fuzzy_match_threshold,
        )
        if proc_report.no_address:
            warnings.append(
                f"{proc_report.no_address} member(s) have no address and are listed individually"
            )
        check_cancel()

        # ── Stage 6: Render HTML template ─────────────────────────────────────
        _progress(progress_q, "render", "Building directory layout…")
        html = renderer.render(pages=pages, config=config)
        check_cancel()

        # ── Stage 7: Write output ─────────────────────────────────────────────
        _progress(progress_q, "output", "Saving directory…")

        ts       = datetime.now().strftime("%Y-%m-%d")
        out_name = config.output_filename_format.format(date=ts)
        out_name = Path(out_name).with_suffix('.html').name
        dest_file = output_dir / out_name
        dest_file.write_text(html, encoding='utf-8')

        # ── Stage 8: Write run log + open folder ──────────────────────────────
        _progress(progress_q, "saving", "Saving run log…")

        duration = time.time() - start_time
        report = RunReport(
            timestamp        = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            member_count     = len(people),
            group_count      = proc_report.total_groups,
            page_count       = len(pages),
            photo_successes  = real_photos,
            photo_failures   = placeholder_count,
            validation       = val_report,
            processing       = proc_report,
            output_path      = str(dest_file),
            duration_seconds = duration,
            warnings         = warnings,
            errors           = errors,
        )

        output_writer.write_output(
            pdf_path     = dest_file,
            output_dir   = output_dir,
            report       = report,
            max_run_logs = config.max_run_logs,
            open_folder  = True,
        )

        # ── Done ──────────────────────────────────────────────────────────────
        progress_q.put(ProgressMessage(
            stage   = "done",
            message = f"Directory ready — {len(people)} active members across {len(pages)} pages.",
            result  = report,
        ))

        logger.info("Pipeline complete in %.1fs — file: %s", duration, dest_file)

    except DirectoryError as e:
        logger.error("Pipeline error: %s", e)
        progress_q.put(ProgressMessage(
            stage   = "error",
            message = e.user_message,
            error   = str(e),
        ))

    except Exception as e:
        logger.exception("Unexpected pipeline error")
        progress_q.put(ProgressMessage(
            stage   = "error",
            message = "An unexpected error occurred. Please check the run log.",
            error   = str(e),
        ))

    finally:
        pass  # Nothing to clean up — no temp directory used
