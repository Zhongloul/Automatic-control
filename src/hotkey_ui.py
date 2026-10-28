import tkinter as tk
from tkinter import messagebox, ttk

from .theme import ACCENT, BG, FG, FIELD, MUTED, PANEL, SELECT_FG
from .ui_common import hide_window_if_testing

FONT = "Microsoft YaHei UI"

HOTKEY_FIELDS = (
    ("toggle", "自动操作开始/继续"),
    ("quit", "停止当前任务"),
    ("debug", "叠加层开关"),
    ("record_toggle", "录制开始/停止"),
    ("record_stop", "停止录制并保存"),
    ("record_checkpoint", "插入同步断点"),
)
HOTKEY_VALUES = ["无"] + [f"F{index}" for index in range(1, 13)]


class HotkeySettingsWindow(tk.Toplevel):
    def __init__(self, master, hotkeys, on_save):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title("热键设置")
        self.configure(bg=BG)
        self.geometry("520x420")
        self.minsize(460, 380)
        self.on_save = on_save
        self.vars = {}
        self._build_ui(hotkeys or {})
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _build_ui(self, hotkeys):
        self.columnconfigure(0, weight=1)
        head = ttk.Frame(self, padding=(14, 12, 14, 6))
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="程序热键设置", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        tk.Label(
            head,
            text="热键会被程序占用，因此不会被录制。修改后立即保存；录制、回放或任务运行中不能修改。",
            bg=BG,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=480,
            font=(FONT, 9),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        form = ttk.Frame(self, padding=(14, 6, 14, 6), style="Panel.TFrame")
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        for index, (key, label) in enumerate(HOTKEY_FIELDS):
            ttk.Label(form, text=label, style="Panel.TLabel").grid(
                row=index, column=0, sticky="w", pady=4
            )
            value = str(hotkeys.get(key) or "").strip().upper()
            if not value:
                value = "无"
            if value not in HOTKEY_VALUES:
                value = HOTKEY_VALUES[min(index + 1, len(HOTKEY_VALUES) - 1)]
            variable = tk.StringVar(value=value)
            self.vars[key] = variable
            combo = ttk.Combobox(
                form,
                textvariable=variable,
                values=HOTKEY_VALUES,
                state="readonly",
                width=10,
            )
            combo.grid(row=index, column=1, sticky="w", pady=4)

        self.status_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=480,
            font=(FONT, 9),
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 0))

        footer = ttk.Frame(self, padding=(14, 6, 14, 12))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        ttk.Button(footer, text="保存", style="Accent.TButton", command=self._save).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(footer, text="取消", command=self._cancel).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    def _collect(self):
        result = {}
        for key, _label in HOTKEY_FIELDS:
            raw = self.vars[key].get().strip()
            value = "" if raw == "无" else raw.lower()
            if value and value not in (item.lower() for item in HOTKEY_VALUES[1:]):
                return None, "热键必须是 F1~F12 或“无”"
            result[key] = value
        used = [value for value in result.values() if value]
        if len(set(used)) != len(used):
            return None, "热键不能重复，请为每个功能设置不同的键"
        return result, ""

    def _save(self):
        result, error = self._collect()
        if result is None:
            self.status_var.set(error)
            return
        try:
            self.on_save(result)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.grab_release()
        self.destroy()
