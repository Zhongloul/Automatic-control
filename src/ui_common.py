import os
import sys
import tkinter as tk
from pathlib import Path

HIDDEN_ENV = "KILO_HIDDEN_UI"


def hidden_ui_enabled():
    value = str(os.environ.get(HIDDEN_ENV, "")).strip().lower()
    return value not in ("", "0", "false", "no", "off")


def icon_path():
    candidates = []
    if getattr(sys, "frozen", False):
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "assets" / "app.ico")
        candidates.append(Path(sys.executable).resolve().parent / "assets" / "app.ico")
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "assets" / "app.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def apply_window_icon(window):
    path = icon_path()
    if path is None:
        return
    try:
        window.iconbitmap(str(path))
    except (tk.TclError, OSError):
        pass


def hide_window_if_testing(window):
    apply_window_icon(window)
    if hidden_ui_enabled():
        window.withdraw()

