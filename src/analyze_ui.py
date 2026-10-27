import copy
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from .analyzer import analyze_session, apply_suggestions
from .keyframes import load_reference
from .ui_common import hide_window_if_testing

BG = "#14161b"
PANEL = "#1d2027"
FIELD = "#11141a"
FG = "#e8e8e8"
MUTED = "#93a0b4"
ACCENT = "#0fb98a"
WARN = "#ffcc66"
FONT = "Microsoft YaHei UI"


class AnalyzerWindow(tk.Toplevel):
    def __init__(self, master, config, session_folder, on_saved=None):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title(f"自动断点建议 - {Path(session_folder).name}")
        self.configure(bg=BG)
        self.folder = Path(session_folder)
        self.app_config = config
        self.on_saved = on_saved
        self.suggestions = []
        self._vars = []
        self._photos = []
        analyzer_cfg = (config or {}).get("analyzer") or {}
        try:
            gap_default = float(analyzer_cfg.get("min_gap_s", 1.5))
        except (TypeError, ValueError):
            gap_default = 1.5
        self.min_gap_var = tk.StringVar(value=f"{gap_default:g}")
        width = min(980, self.winfo_screenwidth() - 120)
        height = min(700, self.winfo_screenheight() - 120)
        self.geometry(f"{width}x{height}")
        self.minsize(860, 560)
        self._build_ui()
        self.transient(master)
        self.grab_set()
        self.after(60, self._run_analysis)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = ttk.Frame(self, padding=(14, 10, 14, 6))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="自动断点建议", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text=(
                "分析录制时保存的画面（4 fps）。在长时间空闲且画面明显变化处生成 wait_visual 断点："
                "回放时等画面恢复到记录状态，再立即执行下一动作。阈值越小越严格，越大越宽松。"
            ),
            style="Muted.TLabel",
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        options = ttk.Frame(head, style="Panel.TFrame")
        options.grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(options, text="最小空闲(s)", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(
            options,
            from_=0.1,
            to=600.0,
            increment=0.1,
            width=7,
            textvariable=self.min_gap_var,
        ).pack(side="left", padx=(4, 12))
        ttk.Button(options, text="重新分析", command=self._reanalyze).pack(side="left")

        body = ttk.Frame(self, padding=(14, 4, 14, 8))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="正在分析会话...")
        tk.Label(
            body,
            textvariable=self.status_var,
            bg=BG,
            fg=WARN,
            anchor="w",
            justify="left",
            wraplength=900,
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        list_frame = ttk.Frame(body, style="Panel.TFrame", padding=8)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(list_frame, bg=PANEL, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = tk.Frame(self.canvas, bg=PANEL)
        self._window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._window_id, width=event.width),
        )
        self.bind("<MouseWheel>", self._on_mousewheel)

        footer = ttk.Frame(self, padding=(14, 0, 14, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        footer.columnconfigure(2, weight=1)
        footer.columnconfigure(3, weight=1)
        ttk.Button(footer, text="全选", command=lambda: self._set_all(True)).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(footer, text="全不选", command=lambda: self._set_all(False)).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self.write_button = ttk.Button(
            footer, text="写入会话", style="Accent.TButton", command=self._write
        )
        self.write_button.grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(footer, text="关闭", command=self._close).grid(
            row=0, column=3, sticky="ew", padx=(4, 0)
        )

    def _on_mousewheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    def _analysis_config(self):
        try:
            gap = max(0.1, float(self.min_gap_var.get()))
        except ValueError:
            gap = 1.5
            self.min_gap_var.set("1.5")
        config = copy.deepcopy(self.app_config or {})
        config.setdefault("analyzer", {})["min_gap_s"] = gap
        return config

    def _reanalyze(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.suggestions = []
        self._vars = []
        self._photos = []
        self.write_button.configure(state="normal")
        self._run_analysis()

    def _run_analysis(self):
        self.status_var.set("正在分析会话...")
        self.write_button.configure(state="disabled")
        self.update_idletasks()
        try:
            result = analyze_session(self.folder, self._analysis_config())
        except Exception as exc:
            self.status_var.set(f"分析失败: {exc}")
            self.write_button.configure(state="disabled")
            return
        self.suggestions = result.get("suggestions") or []
        reason = str(result.get("reason") or "")
        if not self.suggestions:
            text = reason or "没有发现需要同步的长空闲段"
            self.status_var.set(text)
            self.write_button.configure(state="disabled")
            return
        self.write_button.configure(state="normal")
        self.status_var.set(
            f"共 {len(self.suggestions)} 条建议（最小空闲 {self.min_gap_var.get()}s，"
            f"基于 {result.get('frame_count', 0)} 帧画面），默认全部勾选。"
        )
        for suggestion in self.suggestions:
            self._add_row(suggestion)

    def _add_row(self, suggestion):
        row = tk.Frame(self.inner, bg=FIELD, padx=8, pady=6)
        row.pack(fill="x", pady=4, padx=2)
        variable = tk.BooleanVar(value=True)
        self._vars.append(variable)
        check = tk.Checkbutton(
            row,
            variable=variable,
            bg=FIELD,
            activebackground=FIELD,
            highlightthickness=0,
            bd=0,
        )
        check.grid(row=0, column=0, rowspan=2, sticky="n")
        photo = self._make_photo(suggestion.get("ref"))
        thumb = tk.Label(row, image=photo, bg=FIELD)
        if photo is not None:
            self._photos.append(photo)
        thumb.grid(row=0, column=1, rowspan=2, padx=(6, 10))
        note = str(suggestion.get("note") or "")
        title = (
            f"t={float(suggestion.get('t', 0.0)):.2f}s · 阈值 {float(suggestion.get('threshold', 0.1)):.2f}"
        )
        tk.Label(
            row,
            text=title,
            bg=FIELD,
            fg=ACCENT,
            anchor="w",
            justify="left",
            font=(FONT, 9, "bold"),
        ).grid(row=0, column=2, sticky="w")
        tk.Label(
            row,
            text=note,
            bg=FIELD,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=520,
            font=(FONT, 9),
        ).grid(row=1, column=2, sticky="w")
        row.columnconfigure(2, weight=1)

    def _make_photo(self, ref):
        image = load_reference(self.folder, ref)
        if image is None:
            return None
        height = 64
        width = max(1, int(image.shape[1] * height / image.shape[0]))
        image = Image.fromarray(image[:, :, ::-1].copy())
        image = image.resize((width, height), Image.LANCZOS)
        return ImageTk.PhotoImage(image)

    def _set_all(self, value):
        for variable in self._vars:
            variable.set(bool(value))

    def _write(self):
        selected = [
            suggestion
            for suggestion, variable in zip(self.suggestions, self._vars)
            if variable.get()
        ]
        if not selected:
            messagebox.showinfo("提示", "请至少勾选一条建议", parent=self)
            return
        try:
            added = apply_suggestions(self.folder, selected)
        except Exception as exc:
            messagebox.showerror("写入失败", str(exc), parent=self)
            return
        messagebox.showinfo("已写入", f"已写入 {added} 个断点", parent=self)
        if self.on_saved is not None:
            self.on_saved()
        self._close()

    def _close(self):
        self.grab_release()
        self.destroy()
