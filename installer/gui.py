#!/usr/bin/env python3
"""
Techteia Audio Converter — GUI
Friendly audio conversion for everyone.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import io

import customtkinter as ctk

# Suppress console windows spawned by ffmpeg/ffprobe on Windows
_NO_WINDOW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32" else {}
)

# ── Locate bundled FFmpeg before importing convert ─────────────────────────────

def _setup_ffmpeg_path() -> None:
    """
    When running as a PyInstaller bundle, prepend the bundled ffmpeg/ directory
    to PATH so all subprocess calls in convert.py find it automatically.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
        candidates = [base / "ffmpeg", base / "_internal" / "ffmpeg"]
    else:
        base = Path(__file__).parent
        candidates = [base / "ffmpeg"]

    for d in candidates:
        if d.is_dir() and (d / "ffmpeg.exe").exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return

_setup_ffmpeg_path()

def _check_ffmpeg() -> bool:
    """Return True if ffmpeg is callable, show a friendly error and exit if not."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, check=True, **_NO_WINDOW,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Show error via a minimal Tk window before the main app launches
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "FFmpeg Not Found",
            "Techteia Audio Converter could not find FFmpeg.\n\n"
            "If you installed the app using the installer, please\n"
            "uninstall and reinstall — the installer may be corrupt.\n\n"
            "If you are running from source, place ffmpeg.exe and\n"
            "ffprobe.exe in a subfolder called  ffmpeg/  next to gui.py.",
        )
        root.destroy()
        return False

if not _check_ffmpeg():
    sys.exit(1)

from convert import (   # noqa: E402  (import after PATH is set)
    AUDIO_EXTENSIONS,
    CODEC_MAP,
    LOSSLESS_FORMATS,
    discover_audio_files,
    get_audio_properties,
    build_ffmpeg_command,
    resolve_output_path,
)

# ── Constants ──────────────────────────────────────────────────────────────────

APP_NAME    = "Techteia Audio Converter"
APP_VERSION = "1.0.8"
WIN_W, WIN_H = 740, 820
FORMAT_OPTIONS  = sorted(CODEC_MAP.keys())
DEFAULT_FORMAT  = "mp3"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

BRAND_BLUE   = "#1a6faf"
BRAND_HOVER  = "#155a8a"
BRAND_HEADER = "#d0e8f8"
GREEN  = "#27ae60"
RED    = "#c0392b"
BLUE   = "#2980b9"


# ── Conversion worker ──────────────────────────────────────────────────────────

class ConversionWorker(threading.Thread):
    """
    Runs the full conversion loop in a background thread.
    Posts update tuples to `update_queue` for the UI to consume.

    Message types:
        ('log',              text)
        ('total_known',      total)
        ('file_start',       idx, total, filename)
        ('file_progress',    pct_0_to_100)
        ('file_indeterminate',)         — duration unknown, show spinner
        ('overall_progress', idx, total)
        ('done',             stats_dict)
        ('error',            text)
    """

    def __init__(
        self,
        source_dir: str,
        output_dir: str,
        output_format: str,
        recursive: bool,
        update_queue: queue.Queue,
    ):
        super().__init__(daemon=True)
        self.source_dir    = Path(source_dir)
        self.output_dir    = Path(output_dir)
        self.output_format = output_format
        self.recursive     = recursive
        self.q             = update_queue
        self._cancel       = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _put(self, kind, *args):
        self.q.put((kind, *args))
        if kind == "log" and hasattr(self, "_log_file"):
            self._log_file.write(args[0] + "\n")
            self._log_file.flush()

    # ── Thread entry ───────────────────────────────────────────────────────────

    def run(self):
        try:
            self._run()
        except Exception as exc:
            self._put("error", str(exc))

    def _run(self):
        from datetime import datetime
        log_dir = self.output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"conversion_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        self._log_file = open(log_path, "w", encoding="utf-8")
        self._put("log", f"Log: {log_path}")
        try:
            self._do_run()
        finally:
            self._log_file.close()

    def _do_run(self):
        self._put("log", f"Scanning  {self.source_dir} ...")
        files = discover_audio_files(self.source_dir, recursive=self.recursive)

        if not files:
            self._put("log", "No supported audio files found.")
            self._put("done", {"converted": 0, "copied": 0, "failed": 0,
                               "total": 0, "failed_files": []})
            return

        total      = len(files)
        output_ext = f".{self.output_format}"
        self._put("log", f"Found {total} audio file(s). Starting conversion...")
        self._put("total_known", total)

        stats = {"converted": 0, "copied": 0, "failed": 0,
                 "total": total, "failed_files": []}

        for idx, source_file in enumerate(files, 1):
            if self._cancel.is_set():
                self._put("log", "⚠  Cancelled by user.")
                stats["total"] = idx - 1
                break

            self._put("file_start", idx, total, source_file.name)

            if source_file.suffix.lower() == output_ext:
                ok = self._do_copy(source_file)
                label = "↓  Copied" if ok else "✗  Failed"
                if ok:
                    stats["copied"] += 1
                else:
                    stats["failed"] += 1
                    stats["failed_files"].append(source_file.name)
            else:
                ok = self._do_convert(source_file)
                label = "✓  Converted" if ok else "✗  Failed"
                if ok:
                    stats["converted"] += 1
                else:
                    stats["failed"] += 1
                    stats["failed_files"].append(source_file.name)

            self._put("log", f"  [{idx}/{total}]  {label}:  {source_file.name}")
            self._put("overall_progress", idx, total)

        self._put("done", stats)

    # ── Copy ───────────────────────────────────────────────────────────────────

    def _do_copy(self, source_file: Path) -> bool:
        try:
            output_path = resolve_output_path(
                source_file, self.source_dir, self.output_dir,
                source_file.suffix.lower()
            )
            shutil.copy2(str(source_file), str(output_path))
            self._put("file_progress", 100)
            return True
        except Exception as exc:
            self._put("log", f"    Copy error: {exc}")
            return False

    # ── Convert ────────────────────────────────────────────────────────────────

    def _do_convert(self, source_file: Path) -> bool:
        try:
            output_path = resolve_output_path(
                source_file, self.source_dir, self.output_dir,
                f".{self.output_format}"
            )
            audio_props     = get_audio_properties(source_file)
            is_lossless     = self.output_format in LOSSLESS_FORMATS
            effective_br    = None if is_lossless else audio_props.get("bit_rate")
            duration_s      = audio_props.get("duration") or 0.0

            cmd = build_ffmpeg_command(
                source_file, output_path, self.output_format,
                "0", effective_br,
                audio_props["sample_rate"], audio_props["channels"],
            )
            # Insert progress pipe before the output path (last element)
            cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats", cmd[-1]]

            if duration_s > 0:
                self._put("file_progress", 0)
            else:
                self._put("file_indeterminate")

            stderr_buf = io.StringIO()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                **_NO_WINDOW,
            )

            # Drain stderr in a background thread to prevent pipe-buffer deadlock.
            # FFmpeg writes verbose encoding output to stderr; if nobody reads it
            # the 64 KB OS buffer fills up and FFmpeg blocks, freezing the app.
            def _drain_stderr():
                for ln in proc.stderr:
                    stderr_buf.write(ln)

            stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
            stderr_thread.start()

            # Stream progress from stdout
            for line in proc.stdout:
                if self._cancel.is_set():
                    proc.terminate()
                    proc.wait()
                    return False
                line = line.strip()
                if line.startswith("out_time_ms=") and duration_s > 0:
                    try:
                        ms  = int(line.split("=")[1])
                        pct = min(99, int(ms / (duration_s * 1_000_000) * 100))
                        self._put("file_progress", pct)
                    except (ValueError, ZeroDivisionError):
                        pass

            proc.wait()
            stderr_thread.join(timeout=5)

            stderr_text = stderr_buf.getvalue().strip()
            if proc.returncode == 0:
                self._put("file_progress", 100)
                if hasattr(self, "_log_file") and stderr_text:
                    self._log_file.write(f"[ffmpeg stderr]\n{stderr_text}\n\n")
                    self._log_file.flush()
                return True

            err = stderr_text[-300:]
            self._put("log", f"    FFmpeg error: {err}")
            return False

        except Exception as exc:
            self._put("log", f"    Exception: {exc}")
            return False


# ── Main application ───────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.minsize(580, 700)
        self.resizable(True, True)

        self._worker: ConversionWorker | None = None
        self._queue:  queue.Queue = queue.Queue()
        self._running = False

        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, corner_radius=0, fg_color=BRAND_BLUE)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text=APP_NAME,
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="white",
        ).grid(row=0, column=0, padx=24, pady=(18, 2))

        ctk.CTkLabel(
            hdr, text="Convert your music and audio files — quickly and easily",
            font=ctk.CTkFont(size=13),
            text_color=BRAND_HEADER,
        ).grid(row=1, column=0, padx=24, pady=(0, 16))

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        P = dict(padx=24, pady=8)

        # ── Input folder ───────────────────────────────────────────────────────
        self._section(scroll, "📁  Input Folder", row=0)

        f_in = ctk.CTkFrame(scroll, fg_color="transparent")
        f_in.grid(row=1, column=0, sticky="ew", **P)
        f_in.grid_columnconfigure(0, weight=1)

        self.input_var = ctk.StringVar()
        ctk.CTkEntry(
            f_in, textvariable=self.input_var, height=40,
            placeholder_text="Select the folder containing your audio files…",
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            f_in, text="Browse…", width=110, height=40,
            font=ctk.CTkFont(size=13),
            command=self._browse_input,
        ).grid(row=0, column=1)

        self.recursive_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            scroll, text="Include subfolders  (recommended)",
            variable=self.recursive_var,
            font=ctk.CTkFont(size=13),
        ).grid(row=2, column=0, sticky="w", padx=32, pady=(0, 4))

        # ── Output folder ──────────────────────────────────────────────────────
        self._section(scroll, "📂  Output Folder", row=3)

        f_out = ctk.CTkFrame(scroll, fg_color="transparent")
        f_out.grid(row=4, column=0, sticky="ew", **P)
        f_out.grid_columnconfigure(0, weight=1)

        self.output_var = ctk.StringVar()
        ctk.CTkEntry(
            f_out, textvariable=self.output_var, height=40,
            placeholder_text="Select where to save the converted files…",
            font=ctk.CTkFont(size=13),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            f_out, text="Browse…", width=110, height=40,
            font=ctk.CTkFont(size=13),
            command=self._browse_output,
        ).grid(row=0, column=1)

        # ── Output format ──────────────────────────────────────────────────────
        self._section(scroll, "🎵  Output Format", row=5)

        f_fmt = ctk.CTkFrame(scroll, fg_color="transparent")
        f_fmt.grid(row=6, column=0, sticky="w", **P)

        self.format_var = ctk.StringVar(value=DEFAULT_FORMAT.upper())
        ctk.CTkOptionMenu(
            f_fmt,
            values=[f.upper() for f in FORMAT_OPTIONS],
            variable=self.format_var,
            width=160, height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0)

        ctk.CTkLabel(
            f_fmt,
            text="   Files already in this format will be copied as-is.",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).grid(row=0, column=1)

        # ── Action buttons ─────────────────────────────────────────────────────
        f_btn = ctk.CTkFrame(scroll, fg_color="transparent")
        f_btn.grid(row=7, column=0, sticky="ew", padx=24, pady=18)
        f_btn.grid_columnconfigure(0, weight=1)

        self.convert_btn = ctk.CTkButton(
            f_btn, text="▶   Convert", height=56,
            font=ctk.CTkFont(size=20, weight="bold"),
            fg_color=BRAND_BLUE, hover_color=BRAND_HOVER,
            command=self._start_conversion,
        )
        self.convert_btn.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.cancel_btn = ctk.CTkButton(
            f_btn, text="✕  Cancel", width=130, height=56,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=RED, hover_color="#922b21",
            state="disabled",
            command=self._cancel_conversion,
        )
        self.cancel_btn.grid(row=0, column=1)

        # ── Progress ───────────────────────────────────────────────────────────
        self._section(scroll, "⏳  Progress", row=8)

        self.current_file_lbl = ctk.CTkLabel(
            scroll, text="", font=ctk.CTkFont(size=13),
            text_color="gray", anchor="w",
        )
        self.current_file_lbl.grid(row=9, column=0, sticky="ew", padx=28, pady=(0, 6))

        f_prog = ctk.CTkFrame(scroll, fg_color="transparent")
        f_prog.grid(row=10, column=0, sticky="ew", padx=24, pady=4)
        f_prog.grid_columnconfigure(1, weight=1)

        for lbl_text, attr, row in [("File:", "file_pb", 0), ("Total:", "total_pb", 1)]:
            ctk.CTkLabel(
                f_prog, text=lbl_text, font=ctk.CTkFont(size=13),
                width=55, anchor="e",
            ).grid(row=row, column=0, padx=(0, 10), pady=4)
            pb = ctk.CTkProgressBar(f_prog, height=20, corner_radius=8)
            pb.grid(row=row, column=1, sticky="ew", pady=4)
            pb.set(0)
            setattr(self, attr, pb)

        self.total_lbl = ctk.CTkLabel(
            scroll, text="", font=ctk.CTkFont(size=12), text_color="gray",
        )
        self.total_lbl.grid(row=11, column=0, pady=(0, 4))

        # ── Log ────────────────────────────────────────────────────────────────
        self._section(scroll, "📋  Log", row=12)

        self.log_box = ctk.CTkTextbox(
            scroll, height=200,
            font=ctk.CTkFont(family="Courier New", size=12),
            state="disabled",
        )
        self.log_box.grid(row=13, column=0, sticky="ew", padx=24, pady=(0, 8))

        # ── Summary (hidden until done) ────────────────────────────────────────
        self.summary_frame = ctk.CTkFrame(
            scroll, corner_radius=12,
            fg_color=("#f0f7ff", "#f0f7ff"),
            border_width=1, border_color="#b3d4f0",
        )
        self.summary_frame.grid(row=14, column=0, sticky="ew", padx=24, pady=(0, 28))
        self.summary_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.summary_frame.grid_remove()

        self._sum_converted = self._stat_cell(self.summary_frame, "✓", "Converted", GREEN, 0)
        self._sum_copied    = self._stat_cell(self.summary_frame, "↓", "Copied",    BLUE,  1)
        self._sum_failed    = self._stat_cell(self.summary_frame, "✗", "Failed",    RED,   2)

    def _section(self, parent, text: str, row: int):
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).grid(row=row, column=0, sticky="ew", padx=24, pady=(14, 2))

    def _stat_cell(self, parent, icon: str, label: str, color: str, col: int):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=0, column=col, padx=10, pady=16)
        num = ctk.CTkLabel(f, text="0",
                           font=ctk.CTkFont(size=36, weight="bold"),
                           text_color=color)
        num.pack()
        ctk.CTkLabel(f, text=f"{icon}  {label}",
                     font=ctk.CTkFont(size=13),
                     text_color=color).pack()
        return num

    # ── Browse callbacks ───────────────────────────────────────────────────────

    def _browse_input(self):
        path = filedialog.askdirectory(title="Select Input Folder")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(str(Path(path).parent / "output"))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_var.set(path)

    # ── Conversion lifecycle ───────────────────────────────────────────────────

    def _start_conversion(self):
        src = self.input_var.get().strip()
        dst = self.output_var.get().strip()
        fmt = self.format_var.get().lower()

        if not src:
            messagebox.showerror("Missing Input", "Please select an input folder.")
            return
        if not Path(src).is_dir():
            messagebox.showerror("Folder Not Found",
                                 f"Input folder does not exist:\n{src}")
            return
        if not dst:
            messagebox.showerror("Missing Output", "Please select an output folder.")
            return
        try:
            Path(dst).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Output Error",
                                 f"Cannot create output folder:\n{exc}")
            return

        self._set_running(True)
        self._clear_log()
        self.summary_frame.grid_remove()
        self.file_pb.configure(mode="determinate")
        self.file_pb.set(0)
        self.total_pb.set(0)
        self.total_lbl.configure(text="")
        self.current_file_lbl.configure(text="")

        self._queue = queue.Queue()
        self._worker = ConversionWorker(
            src, dst, fmt, self.recursive_var.get(), self._queue
        )
        self._worker.start()
        self.after(100, self._poll_queue)

    def _cancel_conversion(self):
        if self._worker:
            self._worker.cancel()
        self.cancel_btn.configure(state="disabled")

    # ── Queue polling ──────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                self._handle(self._queue.get_nowait())
        except queue.Empty:
            pass
        if self._running:
            self.after(100, self._poll_queue)

    def _handle(self, msg):
        kind = msg[0]

        if kind == "log":
            self._log(msg[1])

        elif kind == "total_known":
            self._log(f"Processing {msg[1]} file(s)…")

        elif kind == "file_start":
            _, idx, total, name = msg
            self.current_file_lbl.configure(
                text=f"Converting file {idx} of {total}:  {name}"
            )
            self.file_pb.configure(mode="determinate")
            self.file_pb.set(0)

        elif kind == "file_indeterminate":
            self.file_pb.configure(mode="indeterminate")
            self.file_pb.start()

        elif kind == "file_progress":
            self.file_pb.stop()
            self.file_pb.configure(mode="determinate")
            self.file_pb.set(msg[1] / 100)

        elif kind == "overall_progress":
            _, idx, total = msg
            self.total_pb.set(idx / total)
            self.total_lbl.configure(text=f"{idx} of {total} files complete")

        elif kind == "error":
            self._log(f"ERROR: {msg[1]}")
            messagebox.showerror("Unexpected Error", msg[1])
            self._set_running(False)

        elif kind == "done":
            self._on_done(msg[1])

    # ── Completion ─────────────────────────────────────────────────────────────

    def _on_done(self, stats: dict):
        self.file_pb.stop()
        self.file_pb.configure(mode="determinate")
        self.file_pb.set(1)
        self.total_pb.set(1)

        converted = stats.get("converted", 0)
        copied    = stats.get("copied",    0)
        failed    = stats.get("failed",    0)
        total     = stats.get("total",     0)

        self.current_file_lbl.configure(text="All done!")
        self.total_lbl.configure(text=f"{total} of {total} files complete")

        self._sum_converted.configure(text=str(converted))
        self._sum_copied.configure(text=str(copied))
        self._sum_failed.configure(text=str(failed))
        self.summary_frame.grid()

        self._log("─" * 52)
        self._log(
            f"Summary:  ✓ {converted} converted   "
            f"↓ {copied} copied   ✗ {failed} failed"
        )
        if stats.get("failed_files"):
            self._log("Failed files:")
            for name in stats["failed_files"]:
                self._log(f"  • {name}")

        self._set_running(False)

        if failed == 0:
            messagebox.showinfo(
                "All Done! 🎉",
                f"Conversion complete!\n\n"
                f"✓  Converted : {converted}\n"
                f"↓  Copied    : {copied}\n"
                f"✗  Failed    : {failed}",
            )
        else:
            messagebox.showwarning(
                "Done — with some errors",
                f"Finished, but {failed} file(s) could not be converted.\n\n"
                f"✓  Converted : {converted}\n"
                f"↓  Copied    : {copied}\n"
                f"✗  Failed    : {failed}\n\n"
                f"Check the log for details.",
            )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        state_on  = "normal"   if running else "disabled"
        state_off = "disabled" if running else "normal"
        self.convert_btn.configure(state=state_off)
        self.cancel_btn.configure(state=state_on)

    def _log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
