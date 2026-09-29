"""Window-scoped Auto Approve helper for the Codex desktop UI on Windows."""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

try:
    from pywinauto import Desktop
except ImportError:  # Shown cleanly when Start is pressed.
    Desktop = None


# Codex can run standalone, in ChatGPT, or as the Codex pane in VS Code.
ALLOWED_PROCESSES = {"codex.exe", "chatgpt.exe", "code.exe"}
BUTTON_TEXT = "Allow once"
DENY_TEXT = "Deny"
COPILOT_BUTTON_TEXT = "Allow"
COPILOT_SKIP_TEXT = "Skip"
COPILOT_KEEP_TEXT = "Keep"
COPILOT_UNDO_TEXT = "Undo"
POLL_SECONDS = 0.45


def process_name(pid: int) -> str:
    """Return the executable basename for a PID without another dependency."""
    try:
        import ctypes
        from ctypes import wintypes

        query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(
            query_limited_information, False, pid
        )
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            )
            return os.path.basename(buffer.value).lower() if ok else ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


class AutoApproveApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Codex Auto Approve")
        self.root.geometry("600x410")
        self.root.minsize(520, 340)

        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.approval_count = 0

        self.status = tk.StringVar(value="Stopped")
        self.count = tk.StringVar(value="Approvals this run: 0")
        self.always_on_top = tk.BooleanVar(value=False)
        self.mode = tk.StringVar(value="Codex")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Codex Auto Approve", font=("Segoe UI", 17, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            outer,
            text=(
                "Clicks only an exact ‘Allow once’ button inside Codex, ChatGPT, "
                "or the VS Code Codex pane. It does not move your mouse or "
                "activate the window."
            ),
            wraplength=550,
        ).pack(anchor="w", pady=(5, 14))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        ttk.Label(controls, text="Mode:").pack(side="left")
        self.codex_mode = ttk.Radiobutton(
            controls, text="Codex", variable=self.mode, value="Codex"
        )
        self.codex_mode.pack(side="left", padx=(6, 0))
        self.copilot_mode = ttk.Radiobutton(
            controls, text="Copilot", variable=self.mode, value="Copilot"
        )
        self.copilot_mode.pack(side="left", padx=(6, 14))
        self.start_button = ttk.Button(controls, text="Start", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            controls,
            text="Always on top",
            variable=self.always_on_top,
            command=lambda: self.root.attributes("-topmost", self.always_on_top.get()),
        ).pack(side="right")

        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(14, 8))
        ttk.Label(status_row, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(
            side="left"
        )
        ttk.Label(status_row, textvariable=self.count).pack(side="right")

        self.log = tk.Text(
            outer,
            height=12,
            state="disabled",
            wrap="word",
            font=("Consolas", 9),
        )
        self.log.pack(fill="both", expand=True)
        self._log("Ready. Press Start to monitor Codex.")

    def start(self) -> None:
        if Desktop is None:
            messagebox.showerror(
                "Missing package",
                "Install the required package first:\n\npy -m pip install -r requirements.txt",
            )
            return
        if self.worker and self.worker.is_alive():
            return

        self.approval_count = 0
        self.count.set("Approvals this run: 0")
        self.stop_event.clear()
        self.active_mode = self.mode.get()
        self.codex_mode.configure(state="disabled")
        self.copilot_mode.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Running")
        self._log(f"Started in {self.active_mode} mode.")
        self.worker = threading.Thread(target=self._monitor, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.codex_mode.configure(state="normal")
        self.copilot_mode.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Stopped")
        self._log("Stopped.")

    def close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def _monitor(self) -> None:
        recently_invoked: dict[str, float] = {}
        last_error = ""

        while not self.stop_event.is_set():
            now = time.monotonic()
            recently_invoked = {
                key: timestamp
                for key, timestamp in recently_invoked.items()
                if now - timestamp < 4.0
            }
            try:
                for window in Desktop(backend="uia").windows():
                    if self.stop_event.is_set():
                        break
                    pid = window.element_info.process_id
                    executable = process_name(pid)
                    if self.active_mode == "Copilot":
                        if executable != "code.exe":
                            continue
                        button_text = COPILOT_BUTTON_TEXT
                        context_text = COPILOT_SKIP_TEXT

                        skip_labels = [
                            item
                            for item in window.descendants(title=COPILOT_SKIP_TEXT)
                            if item.is_visible()
                        ]
                        undo_labels = [
                            item
                            for item in window.descendants(title=COPILOT_UNDO_TEXT)
                            if item.is_visible()
                        ]
                        for button in window.descendants(control_type="Button"):
                            try:
                                name = button.element_info.name or ""
                                if name == COPILOT_KEEP_TEXT or name.startswith(
                                    f"{COPILOT_KEEP_TEXT} ("
                                ):
                                    action_text = COPILOT_KEEP_TEXT
                                elif name == COPILOT_BUTTON_TEXT or name.startswith(
                                    f"{COPILOT_BUTTON_TEXT} ("
                                ):
                                    action_text = COPILOT_BUTTON_TEXT
                                else:
                                    continue
                                if not button.is_visible() or not button.is_enabled():
                                    continue

                                rect = button.rectangle()
                                window_rect = window.rectangle()
                                if (
                                    rect.bottom <= window_rect.top
                                    or rect.top >= window_rect.bottom
                                    or rect.right <= window_rect.left
                                    or rect.left >= window_rect.right
                                ):
                                    continue

                                if name in {
                                    COPILOT_BUTTON_TEXT,
                                    COPILOT_KEEP_TEXT,
                                }:
                                    context_labels = (
                                        undo_labels
                                        if action_text == COPILOT_KEEP_TEXT
                                        else skip_labels
                                    )
                                    has_nearby_context = any(
                                        0 < context.rectangle().left - rect.right < 180
                                        and abs(
                                            (
                                                context.rectangle().top
                                                + context.rectangle().bottom
                                            )
                                            - (rect.top + rect.bottom)
                                        )
                                        < max(80, rect.height())
                                        for context in context_labels
                                    )
                                    if not has_nearby_context:
                                        continue

                                key = (
                                    f"{pid}:{rect.left}:{rect.top}:"
                                    f"{rect.right}:{rect.bottom}"
                                )
                                if key in recently_invoked:
                                    continue

                                button.invoke()
                                recently_invoked[key] = time.monotonic()
                                self.events.put(("copilot_approved", (pid, action_text)))
                            except Exception:
                                continue
                        continue
                    else:
                        if executable not in ALLOWED_PROCESSES:
                            continue
                        button_text = BUTTON_TEXT
                        context_text = DENY_TEXT

                    # Chromium sometimes exposes the visible label as a Text child
                    # of an unnamed Button. Find the label, verify the nearby Deny
                    # label that identifies a Codex approval card, then invoke its
                    # nearest Button ancestor.
                    labels = window.descendants(title=button_text)
                    deny_labels = [
                        item
                        for item in window.descendants(title=context_text)
                        if item.is_visible()
                    ]
                    for label in labels:
                        if not label.is_visible():
                            continue
                        try:
                            rect = label.rectangle()
                            if self.active_mode == "Copilot":
                                has_nearby_deny = any(
                                    0 < deny.rectangle().left - rect.right < 180
                                    and abs(
                                        (deny.rectangle().top + deny.rectangle().bottom)
                                        - (rect.top + rect.bottom)
                                    )
                                    < max(80, rect.height())
                                    for deny in deny_labels
                                )
                            else:
                                has_nearby_deny = any(
                                    abs(
                                        (deny.rectangle().left + deny.rectangle().right)
                                        - (rect.left + rect.right)
                                    )
                                    < max(160, rect.width())
                                    and 0 < rect.top - deny.rectangle().bottom < 180
                                    for deny in deny_labels
                                )
                            if not has_nearby_deny:
                                continue

                            button = label
                            for _ in range(5):
                                if button.element_info.control_type == "Button":
                                    break
                                button = button.parent()
                            if button.element_info.control_type != "Button":
                                continue
                            if not button.is_visible() or not button.is_enabled():
                                continue

                            rect = button.rectangle()
                            key = f"{pid}:{rect.left}:{rect.top}:{rect.right}:{rect.bottom}"
                        except Exception:
                            continue
                        if key in recently_invoked:
                            continue

                        # UIA InvokePattern operates directly on this button. It does
                        # not move the cursor, click coordinates, or focus the window.
                        button.invoke()
                        recently_invoked[key] = time.monotonic()
                        self.events.put(("approved", pid))
                last_error = ""
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error != last_error:
                    self.events.put(("error", error))
                    last_error = error

            self.stop_event.wait(POLL_SECONDS)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "approved":
                    self.approval_count += 1
                    self.count.set(f"Approvals this run: {self.approval_count}")
                    approved_text = (
                        COPILOT_BUTTON_TEXT
                        if self.active_mode == "Copilot"
                        else BUTTON_TEXT
                    )
                    self._log(f"Approved ‘{approved_text}’ in process {value}.")
                elif kind == "copilot_approved":
                    pid, action_text = value
                    self.approval_count += 1
                    self.count.set(f"Approvals this run: {self.approval_count}")
                    self._log(f"Approved ‘{action_text}’ in process {pid}.")
                elif kind == "error":
                    self._log(f"Monitor error: {value}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    AutoApproveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
