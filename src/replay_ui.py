import copy
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .scene import list_markers, list_scene_ids
from .session_store import load_events, read_meta, save_events, write_meta
from .session_trim import BOUNDARY_TYPES, trim_events, trim_frames
from .theme import ACCENT, BG, DANGER, FG, FIELD, MUTED, PANEL, SELECT_FG, WARN
from .ui_common import hide_window_if_testing

FONT = "Microsoft YaHei UI"

CHECKPOINT_TYPES = ("sync", "wait_visual", "wait_seconds", "branch")
SOURCE_LABELS = {"scene": "场景标记", "image": "参考图"}
SOURCE_VALUES = {label: value for value, label in SOURCE_LABELS.items()}


def event_detail(event):
    kind = str(event.get("type") or "")
    if kind in ("key_down", "key_up"):
        return f"key={event.get('key', '')}"
    if kind in ("mouse_down", "mouse_up", "click"):
        pos = event.get("pos") or []
        detail = f"button={event.get('button', 'left')}"
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            detail += f" pos=[{float(pos[0]):.3f},{float(pos[1]):.3f}]"
        return detail
    if kind == "mouse_move":
        pos = event.get("pos") or []
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            return f"pos=[{float(pos[0]):.3f},{float(pos[1]):.3f}]"
        return ""
    if kind == "checkpoint":
        detail = f"name={event.get('name', 'sync')}"
        if event.get("source"):
            detail += f" source={event.get('source')}"
        if event.get("scene"):
            detail += f" scene={event.get('scene')}"
        if event.get("seconds"):
            detail += f" seconds={event.get('seconds')}"
        if event.get("ref"):
            detail += f" ref={Path(str(event.get('ref'))).name}"
        if event.get("threshold") is not None:
            detail += f" threshold={event.get('threshold')}"
        if str(event.get("name") or "").lower() == "branch":
            cases = event.get("cases") or []
            detail += f" cases={len(cases)}"
            default = event.get("default") or {}
            if isinstance(default, dict):
                goto = default.get("goto") if isinstance(default.get("goto"), dict) else default
                if goto.get("session"):
                    detail += f" default={goto.get('session')}"
        return detail
    return ""


def session_choices(folder):
    folder = Path(folder)
    result = []
    try:
        for path in folder.parent.iterdir():
            if path.is_dir() and (path / "events.jsonl").is_file() and path.resolve() != folder.resolve():
                result.append(path.name)
    except OSError:
        return []
    return sorted(result)


def target_session_path(folder, name):
    path = Path(str(name))
    if not path.is_absolute():
        path = Path(folder).parent / path
    return path


def target_session_valid(folder, name):
    path = target_session_path(folder, name)
    return path.is_dir() and (path / "events.jsonl").is_file()


def validate_branch_data(folder, data):
    if not isinstance(data, dict):
        return "分支配置为空"
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return "分支断点至少需要一个条件"
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            return f"第 {index} 个分支条件格式错误"
        goto = case.get("goto") if isinstance(case.get("goto"), dict) else case
        session = str(goto.get("session") or "").strip()
        if not session:
            return f"第 {index} 个分支条件缺少目标会话"
        if not target_session_valid(folder, session):
            return f"第 {index} 个分支目标会话不存在: {session}"
        source = str(case.get("source") or "").lower()
        if source == "scene":
            if not str(case.get("scene") or "").strip():
                return f"第 {index} 个分支条件缺少场景 ID"
        else:
            ref = str(case.get("ref") or "").strip()
            if not ref:
                return f"第 {index} 个分支条件缺少参考图"
            path = Path(ref)
            if not path.is_absolute():
                path = Path(folder) / path
            if not path.is_file():
                return f"第 {index} 个分支参考图不存在: {ref}"
            try:
                threshold = float(case.get("threshold", 0.1))
            except (TypeError, ValueError):
                return f"第 {index} 个分支阈值无效"
            if not (0.0001 <= threshold <= 1.0):
                return f"第 {index} 个分支阈值超出范围"
    default = data.get("default")
    if isinstance(default, dict):
        goto = default.get("goto") if isinstance(default.get("goto"), dict) else default
        session = str(goto.get("session") or "").strip()
        if session and not target_session_valid(folder, session):
            return f"默认分支目标会话不存在: {session}"
    try:
        timeout = float(data.get("timeout_s", 15.0))
    except (TypeError, ValueError):
        return "分支等待超时必须是数字"
    if timeout <= 0:
        return "分支等待超时必须大于 0"
    return ""


class CheckpointEditorWindow(tk.Toplevel):
    def __init__(self, master, root, session_folder, on_saved=None):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title(f"编辑会话 - {Path(session_folder).name}")
        self.configure(bg=BG)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1160, screen_w - 100)
        height = min(780, screen_h - 120)
        self.geometry(f"{width}x{height}")
        self.minsize(980, 650)
        self.folder = Path(session_folder)
        self.on_saved = on_saved
        self.events = load_events(self.folder)
        self.events.sort(key=lambda event: float(event.get("t", 0.0)))
        self.dirty = False
        self._checkpoint_refs = []
        self._selected_event = None
        self._form_mode = "event"
        self.trim_head_var = tk.BooleanVar(value=True)
        self.trim_tail_var = tk.BooleanVar(value=True)
        meta = read_meta(self.folder)
        self._frame_start = 0.0
        self._frame_end = float(meta.get("duration", 0.0) or 0.0)
        if self._frame_end <= 0:
            self._frame_end = max(
                (float(event.get("t", 0.0) or 0.0) for event in self.events),
                default=0.0,
            )
        self._frames_need_trim = False
        self._undo_stack = []
        self._redo_stack = []
        self._saved_state = self._dirty_signature()
        self._auto_job = None
        self._suppress_auto = False
        self.scene_choices = [
            scene_id for scene_id in list_scene_ids(root) if list_markers(root, scene_id)
        ]
        self.branch_data = {}

        self._build_ui()
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-z>", self._undo)
        self.bind("<Control-y>", self._redo)
        self.bind("<Control-Shift-Z>", self._redo)

    def _dark_check(self, parent, text, variable):
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            bg=PANEL,
            fg=FG,
            activebackground=PANEL,
            activeforeground=FG,
            selectcolor=FIELD,
            highlightthickness=0,
            bd=0,
            font=(FONT, 9),
        )

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="编辑会话：时间、断点与裁剪", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            head,
            text="左侧可选择任意事件修改时间或删除；顶部可手动去头/去尾（保存后生效）；右侧编辑同步断点。新增断点建议比目标事件晚 0.001~0.05 秒。Ctrl+Z 撤销，Ctrl+Y 重做。",
            style="Muted.TLabel",
            wraplength=1080,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        trim_bar = ttk.Frame(self, style="Panel.TFrame", padding=(12, 8))
        trim_bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
        ttk.Label(trim_bar, text="裁剪", style="Section.TLabel").pack(side="left")
        self._dark_check(trim_bar, "去头", self.trim_head_var).pack(side="left", padx=(10, 4))
        self._dark_check(trim_bar, "去尾", self.trim_tail_var).pack(side="left", padx=(0, 12))
        ttk.Button(trim_bar, text="应用裁剪", command=self._apply_trim).pack(side="left")
        self.trim_summary_var = tk.StringVar(value="")
        tk.Label(
            trim_bar,
            textvariable=self.trim_summary_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            font=(FONT, 9),
        ).pack(side="left", padx=(14, 0), fill="x", expand=True)

        main = ttk.Frame(self, padding=(12, 4, 12, 12))
        main.grid(row=2, column=0, sticky="nsew")
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main, style="Panel.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="时间线事件（含录制开始/结束）", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        list_frame = ttk.Frame(left, style="Panel.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.event_list = tk.Listbox(
            list_frame,
            width=56,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Consolas", 9),
        )
        self.event_list.grid(row=0, column=0, sticky="nsew")
        event_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.event_list.yview)
        event_scroll.grid(row=0, column=1, sticky="ns")
        self.event_list.configure(yscrollcommand=event_scroll.set)
        self.event_list.bind("<<ListboxSelect>>", self._on_event_select)
        self.event_time_var = tk.StringVar(value="")

        right = ttk.Frame(main, style="Panel.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        ttk.Label(right, text="断点列表", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        checkpoint_frame = ttk.Frame(right, style="Panel.TFrame")
        checkpoint_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 8))
        checkpoint_frame.columnconfigure(0, weight=1)
        checkpoint_frame.rowconfigure(0, weight=1)
        self.checkpoint_list = tk.Listbox(
            checkpoint_frame,
            height=8,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=(FONT, 9),
        )
        self.checkpoint_list.grid(row=0, column=0, sticky="nsew")
        checkpoint_scroll = ttk.Scrollbar(checkpoint_frame, orient="vertical", command=self.checkpoint_list.yview)
        checkpoint_scroll.grid(row=0, column=1, sticky="ns")
        self.checkpoint_list.configure(yscrollcommand=checkpoint_scroll.set)
        self.checkpoint_list.bind("<<ListboxSelect>>", self._on_checkpoint_select)

        self.selected_var = tk.StringVar(value="未选择事件")
        tk.Label(
            right,
            textvariable=self.selected_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=540,
            font=(FONT, 9),
        ).grid(row=2, column=0, sticky="w", pady=(0, 4))

        form = ttk.Frame(right, style="Panel.TFrame")
        form.grid(row=3, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="时间(s)", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self.time_var = tk.StringVar(value="0.000")
        self.time_entry = ttk.Entry(form, textvariable=self.time_var)
        self.time_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.time_entry.bind("<Return>", self._auto_apply_time)
        self.time_entry.bind("<FocusOut>", self._auto_apply_time)

        self.type_label = ttk.Label(form, text="类型", style="Panel.TLabel")
        self.type_label.grid(row=1, column=0, sticky="w", pady=3)
        self.type_var = tk.StringVar(value="sync")
        self.type_combo = ttk.Combobox(
            form,
            textvariable=self.type_var,
            values=CHECKPOINT_TYPES,
            state="readonly",
        )
        self.type_combo.grid(row=1, column=1, sticky="ew", pady=3)
        self.type_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_type_changed())

        self.source_label = ttk.Label(form, text="来源", style="Panel.TLabel")
        self.source_label.grid(row=2, column=0, sticky="w", pady=3)
        self.source_var = tk.StringVar(value=SOURCE_LABELS["image"])
        self.source_combo = ttk.Combobox(
            form,
            textvariable=self.source_var,
            values=list(SOURCE_LABELS.values()),
            state="readonly",
        )
        self.source_combo.grid(row=2, column=1, sticky="ew", pady=3)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_type_changed())

        self.scene_label = ttk.Label(form, text="场景 ID", style="Panel.TLabel")
        self.scene_label.grid(row=3, column=0, sticky="w", pady=3)
        self.scene_var = tk.StringVar(value="")
        self.scene_combo = ttk.Combobox(form, textvariable=self.scene_var, values=self.scene_choices, state="normal")
        self.scene_combo.grid(row=3, column=1, sticky="ew", pady=3)
        self.scene_combo.bind("<<ComboboxSelected>>", lambda _event: self._schedule_checkpoint_apply())

        self.seconds_label = ttk.Label(form, text="等待秒数", style="Panel.TLabel")
        self.seconds_label.grid(row=4, column=0, sticky="w", pady=3)
        self.seconds_var = tk.StringVar(value="")
        self.seconds_entry = ttk.Entry(form, textvariable=self.seconds_var)
        self.seconds_entry.grid(row=4, column=1, sticky="ew", pady=3)
        self.seconds_entry.bind("<Return>", self._schedule_checkpoint_apply)
        self.seconds_entry.bind("<FocusOut>", self._schedule_checkpoint_apply)

        self.ref_label = ttk.Label(form, text="参考图", style="Panel.TLabel")
        self.ref_label.grid(row=5, column=0, sticky="w", pady=3)
        self.ref_row = ttk.Frame(form, style="Panel.TFrame")
        self.ref_row.grid(row=5, column=1, sticky="ew", pady=3)
        self.ref_row.columnconfigure(0, weight=1)
        self.ref_var = tk.StringVar(value="")
        self.ref_entry = ttk.Entry(self.ref_row, textvariable=self.ref_var)
        self.ref_entry.grid(row=0, column=0, sticky="ew")
        self.ref_entry.bind("<Return>", self._schedule_checkpoint_apply)
        self.ref_entry.bind("<FocusOut>", self._schedule_checkpoint_apply)
        self.ref_button = ttk.Button(self.ref_row, text="选择", command=self._choose_reference)
        self.ref_button.grid(row=0, column=1, padx=(6, 0))

        self.threshold_label = ttk.Label(form, text="匹配阈值", style="Panel.TLabel")
        self.threshold_label.grid(row=6, column=0, sticky="w", pady=3)
        self.threshold_var = tk.StringVar(value="0.10")
        self.threshold_entry = ttk.Entry(form, textvariable=self.threshold_var)
        self.threshold_entry.grid(row=6, column=1, sticky="ew", pady=3)
        self.threshold_entry.bind("<Return>", self._schedule_checkpoint_apply)
        self.threshold_entry.bind("<FocusOut>", self._schedule_checkpoint_apply)

        self.threshold_hint = tk.Label(
            form,
            text="匹配阈值越小越严格，越大越宽松；匹配不上就调大，匹配过早/误判就调小。",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            font=(FONT, 8),
        )
        self.threshold_hint.grid(row=7, column=0, columnspan=2, sticky="w")

        self.branch_label = ttk.Label(form, text="分支配置", style="Panel.TLabel")
        self.branch_label.grid(row=8, column=0, sticky="w", pady=3)
        self.branch_row = ttk.Frame(form, style="Panel.TFrame")
        self.branch_row.grid(row=8, column=1, sticky="ew", pady=3)
        self.branch_row.columnconfigure(0, weight=1)
        self.branch_summary_var = tk.StringVar(value="未配置")
        tk.Label(
            self.branch_row,
            textvariable=self.branch_summary_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="ew")
        self.branch_button = ttk.Button(
            self.branch_row, text="编辑分支...", command=self._open_branch_editor
        )
        self.branch_button.grid(row=0, column=1, padx=(6, 0))

        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.add_button = ttk.Button(
            buttons, text="新建断点", style="Accent.TButton", command=self._on_add_button
        )
        self.add_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.delete_button = ttk.Button(buttons, text="删除选中", command=self._delete_event)
        self.delete_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(buttons, text="保存会话", command=self._save_file).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        tk.Label(
            right,
            text="时间、断点条件修改会自动更新左侧列表；“保存会话”才写入 events.jsonl。",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=520,
            font=(FONT, 8),
        ).grid(row=5, column=0, sticky="w", pady=(8, 0))

        self.status_var = tk.StringVar(value="修改会自动更新左侧列表；点“保存会话”写入文件，原文件备份为 events.jsonl.bak")
        tk.Label(
            right,
            textvariable=self.status_var,
            bg=PANEL,
            fg=WARN,
            anchor="w",
            justify="left",
            wraplength=520,
            font=(FONT, 9),
        ).grid(row=6, column=0, sticky="w", pady=(8, 0))
        self._update_field_state()

    def _source(self):
        return SOURCE_VALUES.get(self.source_var.get(), "image")

    def _update_field_state(self):
        has_selection = self._selected_event is not None and any(
            candidate is self._selected_event for candidate in self.events
        )
        self.delete_button.configure(state="normal" if has_selection else "disabled")
        checkpoint_widgets = (
            self.type_label,
            self.type_combo,
            self.source_label,
            self.source_combo,
            self.scene_label,
            self.scene_combo,
            self.seconds_label,
            self.seconds_entry,
            self.ref_label,
            self.ref_row,
            self.threshold_label,
            self.threshold_entry,
            self.threshold_hint,
            self.branch_label,
            self.branch_row,
        )
        if self._form_mode != "checkpoint":
            for widget in checkpoint_widgets:
                widget.grid_remove()
            self.add_button.configure(text="新建断点")
            return

        self.type_label.grid()
        self.type_combo.grid()
        self.add_button.configure(text="新建断点")
        kind = self.type_var.get()
        branch = kind == "branch"
        visual = kind == "wait_visual"
        source = self._source()
        show_source = visual
        show_scene = visual and source == "scene"
        show_seconds = kind == "wait_seconds"
        show_image = visual and source == "image"

        for widget in (self.source_label, self.source_combo):
            if show_source:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.scene_label, self.scene_combo):
            if show_scene:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.seconds_label, self.seconds_entry):
            if show_seconds:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.ref_label, self.ref_row):
            if show_image:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.threshold_label, self.threshold_entry, self.threshold_hint):
            if show_image:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.branch_label, self.branch_row):
            if branch:
                widget.grid()
            else:
                widget.grid_remove()

        self.source_combo.configure(state="readonly")
        self.scene_combo.configure(state="normal")
        self.seconds_entry.configure(state="normal")
        self.ref_entry.configure(state="normal")
        self.ref_button.configure(state="normal")
        self.threshold_entry.configure(state="normal")

    def _relative_ref(self, path):
        candidate = Path(path)
        try:
            return candidate.resolve().relative_to(self.folder.resolve()).as_posix()
        except ValueError:
            return str(candidate)

    def _choose_reference(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择参考画面",
            initialdir=str(self.folder),
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if path:
            self.ref_var.set(self._relative_ref(path))
            self._schedule_checkpoint_apply()

    def _dirty_signature(self):
        return (
            copy.deepcopy(self.events),
            round(self._frame_start, 4),
            round(self._frame_end, 4),
            bool(self._frames_need_trim),
        )

    def _push_undo(self):
        self._undo_stack.append(
            {
                "events": copy.deepcopy(self.events),
                "frame_start": self._frame_start,
                "frame_end": self._frame_end,
                "frames_need_trim": self._frames_need_trim,
                "branch_data": copy.deepcopy(self.branch_data),
                "form_mode": self._form_mode,
            }
        )
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore_undo_state(self, state):
        self.events = copy.deepcopy(state.get("events") or [])
        self._frame_start = float(state.get("frame_start", 0.0))
        self._frame_end = float(state.get("frame_end", 0.0))
        self._frames_need_trim = bool(state.get("frames_need_trim", False))
        self.branch_data = copy.deepcopy(state.get("branch_data") or {})
        self._form_mode = str(state.get("form_mode") or "event")
        self._selected_event = None
        self.selected_var.set("未选择事件")
        self.time_var.set("0.000")
        self.event_list.selection_clear(0, "end")
        self.checkpoint_list.selection_clear(0, "end")
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._update_branch_summary()
        self._update_field_state()
        self.dirty = self._dirty_signature() != self._saved_state

    def _undo(self, _event=None):
        if not self._undo_stack:
            self.status_var.set("没有可撤销的修改")
            return "break"
        self._redo_stack.append(
            {
                "events": copy.deepcopy(self.events),
                "frame_start": self._frame_start,
                "frame_end": self._frame_end,
                "frames_need_trim": self._frames_need_trim,
                "branch_data": copy.deepcopy(self.branch_data),
                "form_mode": self._form_mode,
            }
        )
        self._restore_undo_state(self._undo_stack.pop())
        self.status_var.set(f"已撤销（Ctrl+Z），还可撤销 {len(self._undo_stack)} 步")
        return "break"

    def _redo(self, _event=None):
        if not self._redo_stack:
            self.status_var.set("没有可重做的修改")
            return "break"
        self._undo_stack.append(
            {
                "events": copy.deepcopy(self.events),
                "frame_start": self._frame_start,
                "frame_end": self._frame_end,
                "frames_need_trim": self._frames_need_trim,
                "branch_data": copy.deepcopy(self.branch_data),
                "form_mode": self._form_mode,
            }
        )
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._restore_undo_state(self._redo_stack.pop())
        self.status_var.set(f"已重做（Ctrl+Y），还可重做 {len(self._redo_stack)} 步")
        return "break"

    def _timeline_duration(self):
        duration = max(0.0, self._frame_end - self._frame_start)
        if self.events:
            duration = max(
                duration,
                max(float(event.get("t", 0.0) or 0.0) for event in self.events),
            )
        return duration

    def _refresh_event_list(self):
        self.event_list.delete(0, "end")
        duration = self._timeline_duration()
        meta = read_meta(self.folder)
        started = str(meta.get("created_at") or "").strip()
        finished = str(meta.get("finished_at") or "").strip()
        start_text = "录制开始" + (f" {started}" if started else "")
        end_text = "录制结束" + (f" {finished}" if finished else "")
        self.event_list.insert("end", f"{0.0:8.4f}  [{start_text}]")
        for event in self.events:
            detail = event_detail(event)
            self.event_list.insert(
                "end",
                f"{float(event.get('t', 0.0)):8.4f}  {str(event.get('type') or ''):<12} {detail}",
            )
        self.event_list.insert("end", f"{duration:8.4f}  [{end_text}]")
        self.event_list.itemconfig(0, foreground=MUTED)
        self.event_list.itemconfig("end", foreground=MUTED)
        self._update_trim_summary()

    def _refresh_checkpoint_list(self):
        self.checkpoint_list.delete(0, "end")
        self._checkpoint_refs = [
            event for event in self.events if str(event.get("type") or "").lower() == "checkpoint"
        ]
        for event in self._checkpoint_refs:
            detail = event_detail(event)
            self.checkpoint_list.insert("end", f"{float(event.get('t', 0.0)):8.4f}  {detail}")

    def _on_event_select(self, _event=None):
        selection = self.event_list.curselection()
        if not selection:
            return
        row = int(selection[0])
        if row == 0:
            self._selected_event = None
            self._form_mode = "event"
            self.checkpoint_list.selection_clear(0, "end")
            self.selected_var.set("录制开始")
            self._update_field_state()
            self.status_var.set("录制开始（0.0000s）")
            return
        if row > len(self.events):
            duration = self._timeline_duration()
            self._selected_event = None
            self._form_mode = "event"
            self.checkpoint_list.selection_clear(0, "end")
            self.selected_var.set("录制结束")
            self._update_field_state()
            self.status_var.set(f"录制结束（{duration:.4f}s）")
            return
        event = self.events[row - 1]
        self._selected_event = event
        self.selected_var.set(
            f"选中事件: {float(event.get('t', 0.0)):.4f}s · {str(event.get('type') or '')} {event_detail(event)}"
        )
        self.time_var.set(f"{float(event.get('t', 0.0)):.4f}")
        if str(event.get("type") or "").lower() == "checkpoint":
            self._select_checkpoint_event(event)
        else:
            self._form_mode = "event"
            self.checkpoint_list.selection_clear(0, "end")
            self._update_field_state()
            self.status_var.set("已选择普通事件：修改时间后按回车或点其他位置会自动更新。")

    def _on_checkpoint_select(self, _event=None):
        selection = self.checkpoint_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._checkpoint_refs):
            event = self._checkpoint_refs[index]
            self._form_mode = "checkpoint"
            self._selected_event = event
            self._populate_form(event)
            self._select_event_object(event)
            self.selected_var.set(
                f"选中断点: {float(event.get('t', 0.0)):.4f}s · {event_detail(event)}"
            )

    def _select_checkpoint_event(self, event):
        try:
            index = self._checkpoint_refs.index(event)
        except ValueError:
            return
        self._form_mode = "checkpoint"
        self.checkpoint_list.selection_clear(0, "end")
        self.checkpoint_list.selection_set(index)
        self.checkpoint_list.see(index)
        self._selected_event = event
        self._populate_form(event)
        self.selected_var.set(
            f"选中断点: {float(event.get('t', 0.0)):.4f}s · {event_detail(event)}"
        )

    def _select_event_object(self, event):
        for index, candidate in enumerate(self.events):
            if candidate is event:
                list_index = index + 1
                self.event_list.selection_clear(0, "end")
                self.event_list.selection_set(list_index)
                self.event_list.see(max(0, list_index - 2))
                self._selected_event = event
                self.selected_var.set(
                    f"选中事件: {float(event.get('t', 0.0)):.4f}s · {event_detail(event)}"
                )
                return
        self._selected_event = None
        self.selected_var.set("未选择事件")

    def _populate_form(self, event):
        if self._auto_job is not None:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
            self._auto_job = None
        self._suppress_auto = True
        try:
            self._form_mode = "checkpoint"
            self.time_var.set(f"{float(event.get('t', 0.0)):.4f}")
            name = str(event.get("name") or "sync").lower()
            if name == "branch":
                self.type_var.set("branch")
                self.branch_data = copy.deepcopy(event)
                self._update_branch_summary()
            elif name in ("wait_scene", "wait_state", "wait_visual"):
                source = str(event.get("source") or "").lower()
                if name == "wait_scene" or source == "scene" or (not event.get("ref") and event.get("scene")):
                    source = "scene"
                else:
                    source = "image"
                self.type_var.set("wait_visual")
                self.source_var.set(SOURCE_LABELS[source])
            else:
                if name not in CHECKPOINT_TYPES:
                    name = "sync"
                self.type_var.set(name)
            if self.type_var.get() != "branch":
                self.branch_data = {}
                self._update_branch_summary()
            self.scene_var.set(str(event.get("scene") or ""))
            seconds = event.get("seconds")
            self.seconds_var.set("" if seconds is None else str(seconds))
            self.ref_var.set(str(event.get("ref") or ""))
            threshold = event.get("threshold")
            self.threshold_var.set("0.10" if threshold is None else str(threshold))
            self._update_field_state()
        finally:
            self._suppress_auto = False

    def _update_branch_summary(self):
        cases = self.branch_data.get("cases") if isinstance(self.branch_data, dict) else None
        cases = cases if isinstance(cases, list) else []
        default = ""
        if isinstance(self.branch_data, dict):
            value = self.branch_data.get("default")
            if isinstance(value, dict):
                goto = value.get("goto") if isinstance(value.get("goto"), dict) else value
                default = str(goto.get("session") or "")
        text = f"{len(cases)} 个条件"
        if default:
            text += f" · 默认 {default}"
        self.branch_summary_var.set(text)

    def _open_branch_editor(self):
        try:
            if getattr(self, "_branch_window", None) is not None and self._branch_window.winfo_exists():
                self._branch_window.lift()
                return
        except tk.TclError:
            self._branch_window = None
        self._branch_window = BranchEditorWindow(
            self,
            self.folder,
            self.scene_choices,
            self.branch_data,
            on_save=self._on_branch_saved,
        )

    def _on_branch_saved(self, data):
        self.branch_data = copy.deepcopy(data)
        self._update_branch_summary()
        self._schedule_checkpoint_apply()

    def _on_type_changed(self):
        self._update_field_state()
        self._schedule_checkpoint_apply()

    def _schedule_checkpoint_apply(self, _event=None):
        if self._suppress_auto or self._form_mode != "checkpoint":
            return
        if self._selected_checkpoint() is None:
            return
        if self._auto_job is not None:
            try:
                self.after_cancel(self._auto_job)
            except tk.TclError:
                pass
        self._auto_job = self.after(150, self._auto_apply_checkpoint)

    def _auto_apply_time(self, _event=None):
        if self._suppress_auto:
            return
        event = self._selected_event
        if event is None or not any(candidate is event for candidate in self.events):
            return
        try:
            time_value = float(self.time_var.get())
        except ValueError:
            self.status_var.set("时间无效，未更新")
            return
        if time_value < 0:
            self.status_var.set("时间不能为负数")
            return
        if str(event.get("type") or "").lower() == "checkpoint":
            self._auto_apply_checkpoint()
            return
        if abs(float(event.get("t", 0.0) or 0.0) - round(time_value, 4)) < 1e-9:
            return
        self._push_undo()
        event["t"] = round(time_value, 4)
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._select_event_object(event)
        self.time_var.set(f"{event['t']:.4f}")
        self.status_var.set("事件时间已自动更新（保存会话后写入文件）")

    def _auto_apply_checkpoint(self):
        self._auto_job = None
        if self._suppress_auto or self._form_mode != "checkpoint":
            return
        current = self._selected_checkpoint()
        if current is None:
            return
        event, error = self._build_checkpoint()
        if event is None:
            self.status_var.set(f"断点配置未更新: {error}")
            return
        if current == event:
            return
        self._push_undo()
        current.clear()
        current.update(event)
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._select_checkpoint_event(current)
        self.status_var.set("断点修改已自动更新（保存会话后写入文件）")

    def _selected_checkpoint(self):
        selection = self.checkpoint_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self._checkpoint_refs):
            return self._checkpoint_refs[index]
        return None

    def _build_checkpoint(self):
        try:
            time_value = float(self.time_var.get())
        except ValueError:
            return None, "时间必须是数字"
        if time_value < 0:
            return None, "时间不能为负数"
        kind = str(self.type_var.get() or "sync")
        if kind not in CHECKPOINT_TYPES:
            return None, "断点类型无效"
        event = {"t": round(time_value, 4), "type": "checkpoint", "name": kind}
        if kind == "wait_visual":
            source = self._source()
            event["source"] = source
            if source == "scene":
                scene_id = self.scene_var.get().strip()
                if not scene_id:
                    return None, "wait_visual（场景标记）需要填写场景 ID"
                event["scene"] = scene_id
            else:
                ref = self.ref_var.get().strip()
                if not ref:
                    return None, "wait_visual（参考图）需要选择参考图"
                resolved = Path(ref)
                if not resolved.is_absolute():
                    resolved = self.folder / resolved
                if not resolved.is_file():
                    return None, f"参考图不存在: {ref}"
                try:
                    threshold = float(self.threshold_var.get())
                except ValueError:
                    return None, "匹配阈值必须是数字"
                if not (0.0001 <= threshold <= 1.0):
                    return None, "匹配阈值范围 0.0001 ~ 1.0"
                event["ref"] = ref
                event["threshold"] = round(threshold, 4)
                event["roi"] = [0.0, 0.0, 1.0, 1.0]
        elif kind == "wait_seconds":
            try:
                seconds = float(self.seconds_var.get())
            except ValueError:
                return None, "等待秒数必须是数字"
            if seconds <= 0:
                return None, "等待秒数必须大于 0"
            event["seconds"] = round(seconds, 3)
        elif kind == "branch":
            error = validate_branch_data(self.folder, self.branch_data)
            if error:
                return None, error
            event["cases"] = copy.deepcopy(self.branch_data.get("cases") or [])
            event["timeout_s"] = round(float(self.branch_data.get("timeout_s", 15.0)), 3)
            default = self.branch_data.get("default")
            if isinstance(default, dict):
                event["default"] = copy.deepcopy(default)
        return event, ""

    def _on_add_button(self):
        if self._form_mode != "checkpoint":
            self._form_mode = "checkpoint"
            self.type_var.set("sync")
            self.branch_data = {}
            self.scene_var.set("")
            self.seconds_var.set("")
            self.ref_var.set("")
            self.threshold_var.set("0.10")
            self._update_branch_summary()
            self._update_field_state()
        self._add_checkpoint()

    def _add_checkpoint(self):
        self._form_mode = "checkpoint"
        event, error = self._build_checkpoint()
        if event is None:
            messagebox.showerror("断点无效", error, parent=self)
            return
        self._push_undo()
        self.events.append(event)
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._select_checkpoint_event(event)
        self._select_event_object(event)
        self.status_var.set("已添加断点（尚未保存）")

    def _apply_checkpoint(self):
        self._form_mode = "checkpoint"
        current = self._selected_checkpoint()
        if current is None:
            messagebox.showinfo("提示", "请先在断点列表中选择一个断点", parent=self)
            return
        event, error = self._build_checkpoint()
        if event is None:
            messagebox.showerror("断点无效", error, parent=self)
            return
        self._push_undo()
        current.clear()
        current.update(event)
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._select_checkpoint_event(current)
        self._select_event_object(current)
        self.status_var.set("已修改断点（尚未保存）")

    def _update_selected_time(self):
        event = self._selected_event
        if event is None or not any(candidate is event for candidate in self.events):
            messagebox.showinfo("提示", "请先在左侧时间线或断点列表选择一个事件", parent=self)
            return
        try:
            time_value = float(self.time_var.get())
        except ValueError:
            messagebox.showerror("时间无效", "时间必须是数字", parent=self)
            return
        if time_value < 0:
            messagebox.showerror("时间无效", "时间不能为负数", parent=self)
            return
        self._push_undo()
        event["t"] = round(time_value, 4)
        self._form_mode = (
            "checkpoint"
            if str(event.get("type") or "").lower() == "checkpoint"
            else "event"
        )
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._select_event_object(event)
        if str(event.get("type") or "").lower() == "checkpoint":
            self._select_checkpoint_event(event)
        else:
            self.checkpoint_list.selection_clear(0, "end")
            self._update_field_state()
        self.status_var.set("已修改事件时间（尚未保存）")

    def _delete_event(self):
        event = self._selected_event
        if event is None or not any(candidate is event for candidate in self.events):
            messagebox.showinfo("提示", "请先选择一个事件", parent=self)
            return
        if not messagebox.askyesno("删除事件", "确定删除选中的事件？", parent=self):
            return
        self._push_undo()
        self.events.remove(event)
        self._selected_event = None
        self._form_mode = "event"
        self.selected_var.set("未选择事件")
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._update_field_state()
        self.status_var.set("已删除事件（尚未保存）")

    def _apply_trim(self):
        head = bool(self.trim_head_var.get())
        tail = bool(self.trim_tail_var.get())
        if not head and not tail:
            messagebox.showinfo("裁剪", "请至少勾选“去头”或“去尾”", parent=self)
            return
        total = max(0.0, self._frame_end - self._frame_start)
        max_event = max((float(event.get("t", 0.0) or 0.0) for event in self.events), default=0.0)
        if total <= 0 or max_event > total:
            total = max(total, max_event)
        result = trim_events(
            self.events,
            trim_head=head,
            trim_tail=tail,
            total_elapsed=total,
        )
        if not result["trimmed"]:
            self.status_var.set("没有可裁剪的内容（可能没有键鼠操作）")
            return
        self._push_undo()
        self._frame_start += float(result["start"])
        self._frame_end = self._frame_start + float(result["duration"])
        self._frames_need_trim = True
        self.events = result["events"]
        self._selected_event = None
        self._form_mode = "event"
        self.selected_var.set("未选择事件")
        self.dirty = True
        self._refresh_event_list()
        self._refresh_checkpoint_list()
        self._update_field_state()
        self.event_list.selection_clear(0, "end")
        self.checkpoint_list.selection_clear(0, "end")
        self.status_var.set(
            f"已裁剪（仅当前列表）：去头 {result['start']:.2f}s，"
            f"去尾 {total - result['end']:.2f}s，剩余 {result['duration']:.2f}s；点“保存会话”后生效"
        )

    def _update_trim_summary(self):
        actions = [
            float(event.get("t", 0.0) or 0.0)
            for event in self.events
            if str(event.get("type") or "").lower() in BOUNDARY_TYPES
        ]
        if not actions:
            self.trim_summary_var.set("没有键鼠操作，无法按首尾裁剪")
            return
        total = self._timeline_duration()
        self.trim_summary_var.set(
            f"首操作 {min(actions):.2f}s · 末操作 {max(actions):.2f}s · "
            f"事件 {len(self.events)} · 时长 {total:.2f}s"
        )

    def _validate_all(self):
        for event in self.events:
            try:
                time_value = float(event.get("t", 0.0))
            except (TypeError, ValueError):
                return f"事件时间无效: {event}"
            if time_value < 0:
                return f"事件时间为负数: {event}"
        for event in self._checkpoint_refs:
            try:
                time_value = float(event.get("t", 0.0))
            except (TypeError, ValueError):
                return f"断点时间无效: {event}"
            if time_value < 0:
                return f"断点时间为负数: {event}"
            kind = str(event.get("name") or "sync").lower()
            if kind == "branch":
                error = validate_branch_data(self.folder, event)
                if error:
                    return f"分支断点配置错误: {error}"
            elif kind == "wait_visual":
                source = str(event.get("source") or "").lower()
                if source not in ("scene", "image"):
                    source = "scene" if event.get("scene") else "image"
                if source == "scene":
                    if not event.get("scene"):
                        return f"wait_visual 缺少场景: {event}"
                else:
                    ref = str(event.get("ref") or "").strip()
                    if not ref:
                        return f"wait_visual 缺少参考图: {event}"
                    path = Path(ref)
                    if not path.is_absolute():
                        path = self.folder / path
                    if not path.is_file():
                        return f"wait_visual 参考图不存在: {ref}"
                    try:
                        threshold = float(event.get("threshold", 0.1))
                    except (TypeError, ValueError):
                        return f"wait_visual 阈值无效: {event}"
                    if not (0.0001 <= threshold <= 1.0):
                        return f"wait_visual 阈值超出范围: {event}"
            elif kind == "wait_scene":
                if not event.get("scene"):
                    return f"wait_scene 缺少场景: {event}"
            elif kind == "wait_seconds":
                if float(event.get("seconds", 0) or 0) <= 0:
                    return f"wait_seconds 缺少有效秒数: {event}"
            elif kind == "wait_state":
                ref = str(event.get("ref") or "").strip()
                if not ref:
                    return f"wait_state 缺少参考图: {event}"
                path = Path(ref)
                if not path.is_absolute():
                    path = self.folder / path
                if not path.is_file():
                    return f"wait_state 参考图不存在: {ref}"
                try:
                    threshold = float(event.get("threshold", 0.1))
                except (TypeError, ValueError):
                    return f"wait_state 阈值无效: {event}"
                if not (0.0001 <= threshold <= 1.0):
                    return f"wait_state 阈值超出范围: {event}"
            elif kind != "sync":
                return f"断点类型无效: {event}"
        return ""

    def _save_file(self, quiet=False):
        error = self._validate_all()
        if error:
            messagebox.showerror("会话配置错误", error, parent=self)
            return False
        self.events.sort(key=lambda item: float(item.get("t", 0.0)))
        try:
            save_events(self.folder, self.events)
            meta = read_meta(self.folder)
            max_time = max((float(event.get("t", 0.0)) for event in self.events), default=0.0)
            if self._frames_need_trim:
                duration = max(self._frame_end - self._frame_start, max_time)
                frame_count = trim_frames(
                    self.folder,
                    self._frame_start,
                    self._frame_start + duration,
                )
                meta["frame_count"] = frame_count
                self._frame_start = 0.0
                self._frame_end = duration
                self._frames_need_trim = False
            else:
                duration = max(float(meta.get("duration", 0.0) or 0.0), max_time)
                self._frame_start = 0.0
                self._frame_end = duration
            if not self.events:
                duration = 0.0
            meta["event_count"] = len(self.events)
            meta["duration"] = round(duration, 3)
            write_meta(self.folder, meta)
        except Exception as exc:
            messagebox.showerror("写入失败", str(exc), parent=self)
            return False
        self.dirty = False
        self._saved_state = self._dirty_signature()
        self._update_trim_summary()
        if not quiet:
            self.status_var.set("已保存到会话文件，原文件备份为 events.jsonl.bak")
        if self.on_saved is not None:
            self.on_saved()
        return True

    def _on_close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel(
                "未写入",
                "修改尚未保存到会话文件。\n\n是 = 保存并关闭\n否 = 直接关闭\n取消 = 留下",
                parent=self,
            )
            if answer is None:
                return
            if answer:
                self._save_file()
                if self.dirty:
                    return
        self.grab_release()
        self.destroy()

class BranchEditorWindow(tk.Toplevel):
    def __init__(self, master, folder, scene_choices, data, on_save):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title("编辑条件分支")
        self.configure(bg=BG)
        self.geometry("860x620")
        self.minsize(780, 560)
        self.folder = Path(folder)
        self.scene_choices = list(scene_choices)
        self.on_save = on_save
        self.cases = copy.deepcopy((data or {}).get("cases") or [])
        self.session_names = session_choices(self.folder)
        self.editing_index = None
        self._build_ui(data or {})
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._refresh_case_list()
        self._update_source_state()
        self._update_target_hint()

    def _build_ui(self, data):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = ttk.Frame(self, padding=(12, 10, 12, 4))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="条件分支编辑", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            head,
            text="按顺序检查条件，命中后切换到目标会话并从其开头继续；超时可走默认路径。每条结果路径请单独录制会话。阈值越小越严格，越大越宽松。",
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        top = ttk.Frame(self, padding=(12, 4, 12, 4), style="Panel.TFrame")
        top.grid(row=1, column=0, sticky="ew")
        ttk.Label(top, text="超时(s)", style="Panel.TLabel").pack(side="left")
        self.timeout_var = tk.StringVar(value=str(data.get("timeout_s", 15.0)))
        ttk.Entry(top, textvariable=self.timeout_var, width=8).pack(side="left", padx=(4, 18))
        ttk.Label(top, text="默认目标（超时）", style="Panel.TLabel").pack(side="left")
        default_session = ""
        default = data.get("default")
        if isinstance(default, dict):
            goto = default.get("goto") if isinstance(default.get("goto"), dict) else default
            default_session = str(goto.get("session") or "")
        self.default_var = tk.StringVar(value=default_session or "（不设置）")
        ttk.Combobox(
            top,
            textvariable=self.default_var,
            values=["（不设置）"] + self.session_names,
            state="readonly",
            width=30,
        ).pack(side="left", padx=(4, 0))

        body = ttk.Frame(self, padding=(12, 4, 12, 4))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="条件列表", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        list_frame = ttk.Frame(left, style="Panel.TFrame")
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.case_list = tk.Listbox(
            list_frame,
            width=48,
            height=12,
            bg=FIELD,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=(FONT, 9),
        )
        self.case_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.case_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.case_list.configure(yscrollcommand=scroll.set)
        self.case_list.bind("<<ListboxSelect>>", self._on_case_select)
        self.case_hint_var = tk.StringVar(value="")
        tk.Label(
            left,
            textvariable=self.case_hint_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=360,
            font=(FONT, 8),
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

        right = ttk.Frame(body, style="Panel.TFrame", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="来源", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self.source_var = tk.StringVar(value=SOURCE_LABELS["scene"])
        self.source_combo = ttk.Combobox(
            right,
            textvariable=self.source_var,
            values=list(SOURCE_LABELS.values()),
            state="readonly",
        )
        self.source_combo.grid(row=0, column=1, sticky="ew", pady=3)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_source_state())

        self.scene_label = ttk.Label(right, text="场景 ID", style="Panel.TLabel")
        self.scene_label.grid(row=1, column=0, sticky="w", pady=3)
        self.scene_var = tk.StringVar(value="")
        self.scene_combo = ttk.Combobox(right, textvariable=self.scene_var, values=self.scene_choices)
        self.scene_combo.grid(row=1, column=1, sticky="ew", pady=3)

        self.ref_label = ttk.Label(right, text="参考图", style="Panel.TLabel")
        self.ref_label.grid(row=2, column=0, sticky="w", pady=3)
        ref_row = ttk.Frame(right, style="Panel.TFrame")
        ref_row.grid(row=2, column=1, sticky="ew", pady=3)
        ref_row.columnconfigure(0, weight=1)
        self.ref_var = tk.StringVar(value="")
        self.ref_entry = ttk.Entry(ref_row, textvariable=self.ref_var)
        self.ref_entry.grid(row=0, column=0, sticky="ew")
        self.ref_button = ttk.Button(ref_row, text="选择", command=self._choose_reference)
        self.ref_button.grid(row=0, column=1, padx=(6, 0))

        self.threshold_label = ttk.Label(right, text="匹配阈值", style="Panel.TLabel")
        self.threshold_label.grid(row=3, column=0, sticky="w", pady=3)
        self.threshold_var = tk.StringVar(value="0.10")
        self.threshold_entry = ttk.Entry(right, textvariable=self.threshold_var)
        self.threshold_entry.grid(row=3, column=1, sticky="ew", pady=3)

        self.threshold_hint = tk.Label(
            right,
            text="阈值越小越严格，越大越宽松；匹配不上调大，匹配过早调小。",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            font=(FONT, 8),
        )
        self.threshold_hint.grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Label(right, text="目标会话", style="Panel.TLabel").grid(row=5, column=0, sticky="w", pady=3)
        self.target_var = tk.StringVar(value="")
        self.target_combo = ttk.Combobox(
            right,
            textvariable=self.target_var,
            values=self.session_names,
            state="readonly",
        )
        self.target_combo.grid(row=5, column=1, sticky="ew", pady=3)
        self.target_hint_var = tk.StringVar(value="")
        self.target_hint = tk.Label(
            right,
            textvariable=self.target_hint_var,
            bg=PANEL,
            fg=WARN,
            anchor="w",
            justify="left",
            wraplength=360,
            font=(FONT, 8),
        )
        self.target_hint.grid(row=6, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)
        self.add_button = ttk.Button(
            buttons, text="添加条件", style="Accent.TButton", command=self._add_case
        )
        self.add_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="更新选中", command=self._update_case).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(buttons, text="删除选中", command=self._delete_case).grid(
            row=0, column=2, sticky="ew", padx=(4, 0)
        )
        ttk.Button(buttons, text="清空输入", command=self._clear_editor).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )

        footer = ttk.Frame(self, padding=(12, 4, 12, 12))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        self.save_button = ttk.Button(
            footer, text="保存分支", style="Accent.TButton", command=self._save
        )
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(footer, text="取消", command=self._cancel).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        self.status_var = tk.StringVar(value="条件按顺序检查，命中后切换会话。")
        tk.Label(
            self,
            textvariable=self.status_var,
            bg=BG,
            fg=WARN,
            anchor="w",
            justify="left",
            wraplength=820,
            font=(FONT, 9),
        ).grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))

    def _source(self):
        return SOURCE_VALUES.get(self.source_var.get(), "scene")

    def _update_source_state(self):
        scene = self._source() == "scene"
        for widget in (self.scene_label, self.scene_combo):
            if scene:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (
            self.ref_label,
            self.ref_entry,
            self.ref_button,
            self.threshold_label,
            self.threshold_entry,
            self.threshold_hint,
        ):
            if scene:
                widget.grid_remove()
            else:
                widget.grid()

    def _refresh_case_list(self):
        self.case_list.delete(0, "end")
        for index, case in enumerate(self.cases, 1):
            self.case_list.insert("end", f"{index}. {self._format_case(case)}")
        if self.cases:
            self.case_hint_var.set(f"共 {len(self.cases)} 个条件，按顺序检查，命中第一个即切换会话。")
        else:
            self.case_hint_var.set(
                "条件列表为空：填写右侧的来源、场景/参考图和目标会话，再点“添加条件”。"
            )

    def _update_target_hint(self):
        if self.session_names:
            self.target_hint.configure(fg=MUTED)
            self.target_hint_var.set("目标会话必须是 sessions/ 下另一个已有事件的会话。")
        else:
            self.target_hint.configure(fg=WARN)
            self.target_hint_var.set(
                "没有其他会话可跳转：请先分别录制成功/失败路径，再回来配置分支。"
            )
            self.add_button.configure(state="disabled")
            self.save_button.configure(state="disabled")

    def _format_case(self, case):
        goto = case.get("goto") if isinstance(case.get("goto"), dict) else case
        session = str(goto.get("session") or "")
        if str(case.get("source") or "").lower() == "scene":
            return f"场景 {case.get('scene', '')} → {session}"
        ref = Path(str(case.get("ref") or "")).name
        return f"参考图 {ref}（阈值 {case.get('threshold', 0.1)}）→ {session}"

    def _on_case_select(self, _event=None):
        selection = self.case_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if not (0 <= index < len(self.cases)):
            return
        case = self.cases[index]
        self.editing_index = index
        source = str(case.get("source") or "scene").lower()
        self.source_var.set(SOURCE_LABELS.get(source, SOURCE_LABELS["scene"]))
        self.scene_var.set(str(case.get("scene") or ""))
        self.ref_var.set(str(case.get("ref") or ""))
        self.threshold_var.set(str(case.get("threshold", 0.10)))
        goto = case.get("goto") if isinstance(case.get("goto"), dict) else case
        self.target_var.set(str(goto.get("session") or ""))
        self._update_source_state()

    def _choose_reference(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择参考画面",
            initialdir=str(self.folder),
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        candidate = Path(path)
        try:
            self.ref_var.set(candidate.resolve().relative_to(self.folder.resolve()).as_posix())
        except ValueError:
            self.ref_var.set(str(candidate))

    def _read_case(self):
        session = self.target_var.get().strip()
        if not session:
            return None, "请选择目标会话"
        if not target_session_valid(self.folder, session):
            return None, f"目标会话不存在: {session}"
        source = self._source()
        if source == "scene":
            scene = self.scene_var.get().strip()
            if not scene:
                return None, "请填写场景 ID"
            return {"source": "scene", "scene": scene, "goto": {"session": session}}, ""
        ref = self.ref_var.get().strip()
        if not ref:
            return None, "请选择参考图"
        path = Path(ref)
        if not path.is_absolute():
            path = self.folder / path
        if not path.is_file():
            return None, f"参考图不存在: {ref}"
        try:
            threshold = float(self.threshold_var.get())
        except ValueError:
            return None, "匹配阈值必须是数字"
        if not (0.0001 <= threshold <= 1.0):
            return None, "匹配阈值范围 0.0001 ~ 1.0"
        return (
            {
                "source": "image",
                "ref": ref,
                "threshold": round(threshold, 4),
                "roi": [0.0, 0.0, 1.0, 1.0],
                "goto": {"session": session},
            },
            "",
        )

    def _add_case(self):
        case, error = self._read_case()
        if case is None:
            messagebox.showerror("条件无效", error, parent=self)
            return
        self.cases.append(case)
        self.editing_index = None
        self._refresh_case_list()
        self.status_var.set("已添加条件（尚未保存）")

    def _update_case(self):
        if self.editing_index is None or not (0 <= self.editing_index < len(self.cases)):
            messagebox.showinfo("提示", "请先在条件列表中选择一项", parent=self)
            return
        case, error = self._read_case()
        if case is None:
            messagebox.showerror("条件无效", error, parent=self)
            return
        self.cases[self.editing_index] = case
        self._refresh_case_list()
        self.status_var.set("已更新条件（尚未保存）")

    def _delete_case(self):
        if self.editing_index is None or not (0 <= self.editing_index < len(self.cases)):
            messagebox.showinfo("提示", "请先在条件列表中选择一项", parent=self)
            return
        del self.cases[self.editing_index]
        self.editing_index = None
        self._clear_editor()
        self.status_var.set("已删除条件（尚未保存）")

    def _clear_editor(self):
        self.editing_index = None
        self.case_list.selection_clear(0, "end")
        self.scene_var.set("")
        self.ref_var.set("")
        self.threshold_var.set("0.10")
        self.target_var.set("")

    def _save(self):
        if not self.cases:
            messagebox.showerror("分支无效", "至少需要一个条件", parent=self)
            return
        try:
            timeout = float(self.timeout_var.get())
        except ValueError:
            messagebox.showerror("分支无效", "超时必须是数字", parent=self)
            return
        if timeout <= 0:
            messagebox.showerror("分支无效", "超时必须大于 0", parent=self)
            return
        default = self.default_var.get().strip()
        if default == "（不设置）":
            default = ""
        data = {"cases": copy.deepcopy(self.cases), "timeout_s": round(timeout, 3)}
        if default:
            if not target_session_valid(self.folder, default):
                messagebox.showerror("分支无效", f"默认目标会话不存在: {default}", parent=self)
                return
            data["default"] = {"goto": {"session": default}}
        error = validate_branch_data(self.folder, data)
        if error:
            messagebox.showerror("分支无效", error, parent=self)
            return
        self.on_save(copy.deepcopy(data))
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.grab_release()
        self.destroy()
