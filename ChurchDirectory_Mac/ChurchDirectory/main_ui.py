"""
main_ui.py — Main application UI.

Built with tkinter (Python built-in). Runs on the main thread only.
All pipeline work runs in a background thread; progress is received
via a thread-safe queue polled every 100ms.

Windows:
  - SetupDialog:    First-run credential entry + list selection
  - MainWindow:     Progress display, folder picker, run controls
"""

from __future__ import annotations

import logging
import platform
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as msgbox
import tkinter.filedialog as filedialog
import tkinter.ttk as ttk
from pathlib import Path
from typing import Optional

from models import AppConfig, Credentials, ProgressMessage
from errors import (
    CredentialsInvalidError, NetworkError, DirectoryError,
    CredentialsNotFoundError,
)
import auth
import config as cfg
import pc_client
import pipeline

logger = logging.getLogger(__name__)

# ── Colour tokens matching the directory design ───────────────────────────────
BURGUNDY    = "#6B1E2E"
CREAM       = "#FAF7F2"
CREAM_B     = "#EDE0CC"
GOLD        = "#B8944A"
TEXT_MID    = "#4A3020"
TEXT_LIGHT  = "#7A5E48"
WHITE       = "#FFFFFF"
BG_DARK     = "#2C2222"


# ═════════════════════════════════════════════════════════════════════════════
# SETUP DIALOG  — credential entry + list picker
# ═════════════════════════════════════════════════════════════════════════════

class SetupDialog(tk.Toplevel):
    """
    First-run or credential-recovery dialog.
    Collects App ID and PAT, validates against Planning Center,
    then lets staff pick their member list from a dropdown.

    Blocks the parent window (modal).
    Sets self.result = True on success, False on cancel.
    """

    def __init__(self, parent: tk.Tk, app_config: AppConfig,
                 recovery_mode: bool = False):
        super().__init__(parent)
        self.app_config    = app_config
        self.recovery_mode = recovery_mode
        self.result        = False
        self._credentials: Optional[Credentials] = None
        self._available_lists: list[dict] = []

        title = "Update Credentials" if recovery_mode else "Welcome — Setup Required"
        self.title(title)
        self.resizable(False, False)
        self.grab_set()   # Modal
        self.configure(bg=CREAM)

        self._build_ui()
        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w  = self.winfo_width()
        h  = self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _build_ui(self):
        pad = {"padx": 24, "pady": 8}

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BURGUNDY, height=6)
        header.pack(fill="x")

        tk.Label(self, text="The Gathering Church",
                 bg=CREAM, fg=BURGUNDY,
                 font=("Georgia", 16, "bold")).pack(padx=24, pady=(20, 2))

        subtitle = ("Update your Planning Center credentials."
                    if self.recovery_mode else
                    "Enter your Planning Center credentials to get started.")
        tk.Label(self, text=subtitle,
                 bg=CREAM, fg=TEXT_LIGHT,
                 font=("Arial", 10)).pack(padx=24, pady=(0, 16))

        # ── Form frame ────────────────────────────────────────────────────────
        form = tk.Frame(self, bg=CREAM_B, padx=20, pady=16)
        form.pack(fill="x", padx=24, pady=4)

        def lbl(text):
            tk.Label(form, text=text, bg=CREAM_B, fg=TEXT_MID,
                     font=("Arial", 10, "bold"), anchor="w").pack(fill="x", pady=(8, 2))

        def entry(show=None):
            e = tk.Entry(form, font=("Arial", 11), relief="flat",
                         bg=WHITE, fg=TEXT_MID, insertbackground=BURGUNDY,
                         highlightthickness=1, highlightbackground="#CCC",
                         highlightcolor=BURGUNDY, show=show)
            e.pack(fill="x", ipady=6)
            return e

        lbl("Planning Center App ID")
        self._app_id_var = tk.StringVar()
        app_id_entry = entry()
        app_id_entry.config(textvariable=self._app_id_var)

        lbl("Personal Access Token")
        self._pat_var = tk.StringVar()
        pat_entry = entry(show="•")
        pat_entry.config(textvariable=self._pat_var)

        # Help link
        help_frame = tk.Frame(form, bg=CREAM_B)
        help_frame.pack(fill="x", pady=(6, 0))
        link = tk.Label(help_frame,
                        text="How to get your App ID and Personal Access Token →",
                        bg=CREAM_B, fg=GOLD, font=("Arial", 9, "underline"),
                        cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: self._open_help())

        # ── Status label ──────────────────────────────────────────────────────
        self._status_var = tk.StringVar()
        self._status_lbl = tk.Label(self, textvariable=self._status_var,
                                    bg=CREAM, fg="red",
                                    font=("Arial", 9), wraplength=340)
        self._status_lbl.pack(padx=24, pady=(8, 0))

        # ── List picker (shown after credential validation) ───────────────────
        self._list_frame = tk.Frame(self, bg=CREAM)
        tk.Label(self._list_frame, text="Select your active member list:",
                 bg=CREAM, fg=TEXT_MID,
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=24, pady=(12, 4))

        self._list_var = tk.StringVar()
        self._list_combo = ttk.Combobox(self._list_frame, textvariable=self._list_var,
                                        state="readonly", font=("Arial", 10),
                                        width=44)
        self._list_combo.pack(padx=24, pady=(0, 8))

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg=CREAM)
        btn_frame.pack(fill="x", padx=24, pady=16)

        self._validate_btn = tk.Button(
            btn_frame,
            text="Validate & Connect",
            command=self._on_validate,
            bg=BURGUNDY, fg=WHITE,
            font=("Arial", 11, "bold"),
            relief="flat", cursor="hand2",
            padx=16, pady=8,
        )
        self._validate_btn.pack(side="left")

        self._save_btn = tk.Button(
            btn_frame,
            text="Save & Continue",
            command=self._on_save,
            bg=GOLD, fg=WHITE,
            font=("Arial", 11, "bold"),
            relief="flat", cursor="hand2",
            padx=16, pady=8,
        )
        # Save button shown only after list is loaded

        tk.Button(
            btn_frame,
            text="Cancel",
            command=self._on_cancel,
            bg=CREAM, fg=TEXT_MID,
            font=("Arial", 10),
            relief="flat", cursor="hand2",
            padx=12, pady=8,
        ).pack(side="right")

        tk.Frame(self, bg=BURGUNDY, height=4).pack(fill="x", side="bottom")

    def _open_help(self):
        import webbrowser
        webbrowser.open(
            "https://developer.planning.center/docs/#/overview/authentication"
        )

    def _set_status(self, msg: str, colour: str = "red"):
        self._status_var.set(msg)
        self._status_lbl.configure(fg=colour)
        self.update_idletasks()

    def _on_validate(self):
        app_id = self._app_id_var.get().strip()
        pat    = self._pat_var.get().strip()

        if not app_id or not pat:
            self._set_status("Please enter both your App ID and Personal Access Token.")
            return

        self._validate_btn.configure(text="Validating…", state="disabled")
        self._set_status("Connecting to Planning Center…", TEXT_LIGHT)
        self.update_idletasks()

        creds = Credentials(app_id=app_id, pat=pat)

        try:
            pc_client.validate_credentials(creds)
        except CredentialsInvalidError:
            self._set_status("These credentials could not be verified. "
                             "Please check your App ID and Personal Access Token.")
            self._validate_btn.configure(text="Validate & Connect", state="normal")
            return
        except NetworkError as e:
            self._set_status(e.user_message)
            self._validate_btn.configure(text="Validate & Connect", state="normal")
            return
        except Exception as e:
            self._set_status(f"Unexpected error: {e}")
            self._validate_btn.configure(text="Validate & Connect", state="normal")
            return

        # Credentials valid — fetch lists
        self._set_status("Connected! Loading your lists…", TEXT_LIGHT)
        self.update_idletasks()

        try:
            lists = pc_client.fetch_lists(creds)
        except Exception as e:
            self._set_status(f"Could not load lists: {e}")
            self._validate_btn.configure(text="Validate & Connect", state="normal")
            return

        self._credentials      = creds
        self._available_lists  = lists

        # Populate combobox
        list_names = [f"{l['name']}  (ID: {l['id']})" for l in lists]
        self._list_combo["values"] = list_names
        if list_names:
            self._list_combo.current(0)

        # Show list picker + save button
        self._list_frame.pack(fill="x", before=self._status_lbl)
        self._save_btn.pack(side="left", padx=(8, 0))
        self._validate_btn.configure(text="✓ Connected", state="disabled",
                                     bg=TEXT_LIGHT)
        self._set_status(f"Found {len(lists)} list(s). Select your member list below.",
                         TEXT_LIGHT)

    def _on_save(self):
        if not self._credentials:
            return

        idx = self._list_combo.current()
        if idx < 0 or idx >= len(self._available_lists):
            self._set_status("Please select a list.")
            return

        chosen = self._available_lists[idx]

        # Save credentials to keychain
        used_keychain = auth.save_credentials(
            self.app_config.keychain_service,
            self._credentials,
        )
        if not used_keychain:
            msgbox.showwarning(
                "Security Notice",
                "Your credentials were saved using an encrypted fallback store "
                "(the OS keychain was not available on this machine).\n\n"
                "Your credentials are still protected but consider running on a "
                "machine with keychain access for best security.",
                parent=self,
            )

        # Save list ID to config.local.json
        cfg.save_local({"list_id": chosen["id"]})

        logger.info("Setup complete — list %s (%s) selected", chosen["name"], chosen["id"])
        self.result = True
        self.destroy()

    def _on_cancel(self):
        self.result = False
        self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class MainWindow:
    """
    Primary application window.
    Shows run controls, progress, and status.
    """

    def __init__(self, root: tk.Tk, app_config: AppConfig):
        self.root        = root
        self.app_config  = app_config
        self._progress_q : queue.Queue       = queue.Queue()
        self._cancel_evt : threading.Event   = threading.Event()
        self._running    : bool              = False
        self._output_dir : Optional[Path]    = None

        root.title(f"{app_config.church_name} — Directory Generator")
        root.configure(bg=CREAM)
        root.resizable(False, False)

        self._build_ui()
        self._center()

    def _center(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_ui(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BURGUNDY, height=6).pack(fill="x")

        header = tk.Frame(self.root, bg=BURGUNDY)
        header.pack(fill="x")

        tk.Label(header, text=self.app_config.church_name,
                 bg=BURGUNDY, fg=WHITE,
                 font=("Georgia", 18, "bold"),
                 padx=24, pady=14).pack(side="left")

        # Version label
        tk.Label(header, text="Directory Generator  v1.0",
                 bg=BURGUNDY, fg="#C8A8B0",
                 font=("Arial", 9), padx=16).pack(side="right", pady=14)

        # ── Year indicator ────────────────────────────────────────────────────
        year_frame = tk.Frame(self.root, bg=CREAM_B, padx=24, pady=10)
        year_frame.pack(fill="x")

        tk.Label(year_frame,
                 text=f"Generating:  {self.app_config.directory_year} Directory",
                 bg=CREAM_B, fg=TEXT_MID,
                 font=("Arial", 11, "bold")).pack(side="left")

        tk.Button(year_frame, text="Change Year",
                  command=self._change_year,
                  bg=CREAM_B, fg=TEXT_LIGHT,
                  font=("Arial", 9), relief="flat",
                  cursor="hand2").pack(side="right")

        # ── Status area ───────────────────────────────────────────────────────
        status_frame = tk.Frame(self.root, bg=CREAM, padx=24, pady=20)
        status_frame.pack(fill="x")

        self._status_var = tk.StringVar(value="Ready to generate the directory.")
        tk.Label(status_frame, textvariable=self._status_var,
                 bg=CREAM, fg=TEXT_MID,
                 font=("Arial", 11),
                 wraplength=380, justify="left").pack(anchor="w")

        # Progress bar
        self._progress = ttk.Progressbar(status_frame, mode="indeterminate",
                                          length=380)
        self._progress.pack(pady=(12, 0))

        # Photo sub-progress
        self._photo_var = tk.StringVar(value="")
        tk.Label(status_frame, textvariable=self._photo_var,
                 bg=CREAM, fg=TEXT_LIGHT,
                 font=("Arial", 9)).pack(anchor="w", pady=(4, 0))

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = tk.Frame(self.root, bg=CREAM, padx=24, pady=8)
        btn_frame.pack(fill="x")

        self._run_btn = tk.Button(
            btn_frame,
            text="Generate Directory →",
            command=self._on_generate,
            bg=BURGUNDY, fg=WHITE,
            font=("Arial", 12, "bold"),
            relief="flat", cursor="hand2",
            padx=20, pady=10,
        )
        self._run_btn.pack(side="left")

        self._cancel_btn = tk.Button(
            btn_frame,
            text="Cancel",
            command=self._on_cancel,
            bg=CREAM, fg=TEXT_MID,
            font=("Arial", 10),
            relief="flat", cursor="hand2",
            padx=12, pady=10,
            state="disabled",
        )
        self._cancel_btn.pack(side="left", padx=(8, 0))

        # Settings button
        tk.Button(
            btn_frame,
            text="⚙ Setup",
            command=self._on_setup,
            bg=CREAM, fg=TEXT_LIGHT,
            font=("Arial", 9),
            relief="flat", cursor="hand2",
            padx=8, pady=10,
        ).pack(side="right")

        # ── Footer ────────────────────────────────────────────────────────────
        tk.Frame(self.root, bg=BURGUNDY, height=4).pack(fill="x", side="bottom")
        tk.Label(self.root,
                 text=self.app_config.church_tagline,
                 bg=CREAM, fg=TEXT_LIGHT,
                 font=("Arial", 9, "italic"),
                 pady=6).pack(side="bottom")

        self.root.geometry("440x340")

    # ── Year change ───────────────────────────────────────────────────────────

    def _change_year(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Change Directory Year")
        dialog.configure(bg=CREAM)
        dialog.resizable(False, False)
        dialog.grab_set()

        tk.Label(dialog, text="Directory year:", bg=CREAM, fg=TEXT_MID,
                 font=("Arial", 10)).pack(padx=20, pady=(16, 4))

        year_var = tk.StringVar(value=self.app_config.directory_year)
        entry = tk.Entry(dialog, textvariable=year_var, font=("Arial", 12),
                         width=8, justify="center")
        entry.pack(padx=20, pady=4)

        def save():
            y = year_var.get().strip()
            if len(y) == 4 and y.isdigit():
                self.app_config = cfg.AppConfig(
                    **{**self.app_config.__dict__, "directory_year": y}
                )
                cfg.save_local({"directory_year": y})
                dialog.destroy()
            else:
                msgbox.showwarning("Invalid year", "Please enter a 4-digit year.",
                                   parent=dialog)

        tk.Button(dialog, text="Save", command=save,
                  bg=BURGUNDY, fg=WHITE, font=("Arial", 10, "bold"),
                  relief="flat", padx=12).pack(pady=12)

    # ── Generate ──────────────────────────────────────────────────────────────

    def _on_generate(self):
        # Pick output folder
        folder = filedialog.askdirectory(
            title  = "Choose where to save the directory",
            parent = self.root,
        )
        if not folder:
            return   # User cancelled picker

        self._output_dir = Path(folder)
        self._start_run()

    def _start_run(self):
        self._running = True
        self._cancel_evt.clear()
        self._run_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress.start(12)
        self._photo_var.set("")
        self._status_var.set("Starting…")

        try:
            credentials = auth.get_credentials(self.app_config.keychain_service)
        except CredentialsNotFoundError:
            self._show_setup(recovery_mode=False)
            return

        thread = threading.Thread(
            target=pipeline.run,
            args=(self.app_config, credentials, self._output_dir,
                  self._progress_q, self._cancel_evt),
            daemon=True,
        )
        thread.start()
        self.root.after(100, self._poll_progress)

    def _poll_progress(self):
        """Poll the progress queue every 100ms and update UI."""
        try:
            while True:
                msg: ProgressMessage = self._progress_q.get_nowait()
                self._handle_progress(msg)
        except queue.Empty:
            pass

        if self._running:
            self.root.after(100, self._poll_progress)

    def _handle_progress(self, msg: ProgressMessage):
        if msg.stage == "photos" and msg.total > 0:
            self._photo_var.set(f"Photos: {msg.current} / {msg.total}")
            self._status_var.set(msg.message)

        elif msg.stage == "done":
            self._on_run_complete(msg)

        elif msg.stage == "error":
            self._on_run_error(msg)

        else:
            self._status_var.set(msg.message)
            self._photo_var.set("")

    def _on_run_complete(self, msg: ProgressMessage):
        self._running = False
        self._progress.stop()
        self._run_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._status_var.set("✓  " + msg.message)
        self._photo_var.set("")

        report = msg.result
        detail = (
            f"{report.member_count} members · {report.page_count} pages\n\n"
            "The directory has opened in your browser.\n\n"
            "TO PRINT:\n"
            "  Ctrl+P (Windows) or Cmd+P (Mac)\n"
            "  → Select Booklet under Layout or Finishing\n"
            "  → Print\n"
            "  → Fold stack in half, staple the centre"
        )
        if report.warnings:
            detail += f"\n\n⚠ {len(report.warnings)} warning(s):\n"
            detail += "\n".join(f"  • {w}" for w in report.warnings)

        msgbox.showinfo("Directory Ready", detail, parent=self.root)

    def _on_run_error(self, msg: ProgressMessage):
        self._running = False
        self._progress.stop()
        self._run_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._status_var.set("⚠  " + msg.message)
        self._photo_var.set("")

        # If it's a credential error, offer to re-enter credentials
        if "credentials" in msg.message.lower() or "token" in msg.message.lower():
            if msgbox.askyesno(
                "Credentials Invalid",
                msg.message + "\n\nWould you like to update your credentials now?",
                parent=self.root,
            ):
                self._show_setup(recovery_mode=True)
        else:
            msgbox.showerror("Error", msg.message, parent=self.root)

    def _on_cancel(self):
        if self._running:
            self._cancel_evt.set()
            self._status_var.set("Cancelling…")
            self._cancel_btn.configure(state="disabled")

    def _on_setup(self):
        self._show_setup(recovery_mode=True)

    def _show_setup(self, recovery_mode: bool = False):
        dialog = SetupDialog(self.root, self.app_config, recovery_mode=recovery_mode)
        self.root.wait_window(dialog)
        if dialog.result:
            # Reload config in case list_id changed
            try:
                self.app_config = cfg.load_config()
            except Exception:
                pass
            self._status_var.set("Setup complete — ready to generate.")


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def run_app():
    """Launch the application."""
    root = tk.Tk()

    # Load config
    try:
        app_config = cfg.load_config()
    except Exception as e:
        msgbox.showerror("Configuration Error",
                         f"Could not load configuration:\n\n{e}\n\n"
                         "Please ensure config.json is present and valid.")
        return

    window = MainWindow(root, app_config)

    # First-run check — show setup if credentials missing or list not selected yet
    needs_setup = (
        not auth.credentials_exist(app_config.keychain_service)
        or not app_config.list_id
    )
    if needs_setup:
        dialog = SetupDialog(root, app_config, recovery_mode=False)
        root.wait_window(dialog)
        if not dialog.result:
            return   # User cancelled setup — exit

        # Reload config after setup (list_id now saved)
        try:
            app_config = cfg.load_config()
            window.app_config = app_config
        except Exception:
            pass

    root.mainloop()
