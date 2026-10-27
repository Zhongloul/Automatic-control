import os
import tkinter as tk

HIDDEN_ENV = "KILO_HIDDEN_UI"


def hidden_ui_enabled():
    value = str(os.environ.get(HIDDEN_ENV, "")).strip().lower()
    return value not in ("", "0", "false", "no", "off")


def hide_window_if_testing(window):
    if hidden_ui_enabled():
        window.withdraw()
