import copy
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import cv2
from PIL import Image, ImageTk

from src.config import load_config, project_root, save_config
from src.engine import AutomationEngine
from src.hotkeys import HotkeyWatcher
from src.imageio import imwrite
from src.map_graph import DungeonRouter, MapGraph, TransitionExecutor
from src.overlay import draw_overlay
from src.recorder import InputRecorder
from src.replay import ReplayPlayer
from src.replay_ui import CheckpointEditorWindow
from src.scene import SceneRecognizer, list_markers, list_scene_ids
from src.scene_ui import SceneManagerWindow
from src.session_store import delete_session, list_sessions, load_events, rename_session, sessions_dir
from src.theme import (
    ACCENT,
    ACCENT_ACTIVE,
    BG,
    CANVAS_BG,
    DANGER,
    FG,
    FIELD,
    LIST_BG,
    LIST_FG,
    MUTED,
    PANEL,
    SELECT_FG,
    WARN,
)
from src.ui_common import hide_window_if_testing
from src.version import get_version
from src.window import list_windows, set_dpi_awareness

FONT = "Microsoft YaHei UI"

STEP_INFO = [
    {"title": "状态条1", "mode": "rect", "path": ("bars", "hp", "roi"),
     "help": "在截图上拖拽框住 状态条1，尽量只框住填充部分。"},
    {"title": "状态条2", "mode": "rect", "path": ("bars", "mp", "roi"),
     "help": "拖拽框住 状态条2；没有状态条可以跳过此步。"},
    {"title": "快捷栏", "mode": "rect", "path": ("skills", "bar_roi"),
     "help": "拖拽框住整条快捷栏，之后按格数自动切分。"},
    {"title": "主体位置", "mode": "point", "path": ("player", "anchor"),
     "help": "单击主体身体中心，作为距离计算的参考点。"},
    {"title": "目标模板", "mode": "rect", "path": None, "template": True,
     "help": "拖拽框住一个目标精灵，确认后保存；可重复框选多个动作/朝向。"},
]

TASK_DEFS = [
    {
        "id": "combat",
        "title": "智能操作（单窗口）",
        "desc": "实时识别目标，自动移动、接近、执行快捷操作，状态不足时恢复，受击时后撤。适合在单个窗口内重复执行操作。",
        "steps": "使用步骤：锁定目标窗口 → 校准 状态条1/快捷栏/主体位置 → 抓目标模板 → 点击“开始任务”并在倒计时内切回目标窗口。",
        "implemented": True,
    },
    {
        "id": "route",
        "title": "流程导航",
        "desc": "识别当前场景，按配置的跳转流程执行按键或点击，等待加载画面并确认目标场景。适合固定路线、场景切换和流程跳转。",
        "steps": "使用步骤：先在“场景 / 流程管理”里添加场景标记并配置跳转 → 选择目标场景 → 点击“开始任务”。流程任务需要关闭“仅观察”。",
        "implemented": True,
    },
    {
        "id": "dungeon_loop",
        "title": "循环流程（开发中）",
        "desc": "清理目标 → 寻找出口 → 流程跳转 → 结算 → 自动重开。还需要目标清空判定、出口/结算识别和自动重开逻辑，当前版本未完成。",
        "steps": "当前不可用。可先用“自动操作”清理目标，再用“流程导航”前往下一场景。",
        "implemented": False,
    },
    {
        "id": "replay",
        "title": "录制回放",
        "desc": "录制人类操作时间线并自动重放，支持加载/切图同步和手动断点，适合固定跑图和采集路线。",
        "steps": "使用步骤：开始录制 → 手动操作（切图后按 F5 插入同步断点）→ F7 停止 → 选择会话 → 自动加断点 → 设置速度/循环 → 开始回放。",
        "implemented": True,
    },
]


BUTTON_LABELS = {"left": "左键", "right": "右键", "middle": "中键"}


def format_replay_event(event):
    kind = str(event.get("type") or "")
    key = str(event.get("key") or "").upper()
    button = BUTTON_LABELS.get(str(event.get("button") or "").lower(), str(event.get("button") or "左键"))
    pos = event.get("pos")
    pos_text = ""
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        pos_text = f" ({float(pos[0]) * 100:.0f}%, {float(pos[1]) * 100:.0f}%)"
    if kind == "key_down":
        return f"按下 {key}"
    if kind == "key_up":
        return f"松开 {key}"
    if kind == "mouse_down":
        return f"按下{button}{pos_text}"
    if kind == "mouse_up":
        return f"松开{button}{pos_text}"
    if kind == "click":
        return f"点击{button}{pos_text}"
    if kind == "mouse_move":
        return f"移动鼠标{pos_text}"
    if kind == "checkpoint":
        name = str(event.get("name") or "sync")
        text = f"断点 {name}"
        if event.get("scene"):
            text += f" → {event['scene']}"
        if event.get("seconds"):
            text += f" · {event['seconds']}s"
        if event.get("ref"):
            text += f" · {Path(str(event['ref'])).name}"
        return text
    return kind


def dark_checkbutton(parent, text, variable, command=None):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bg=PANEL,
        fg=FG,
        activebackground=PANEL,
        activeforeground=FG,
        selectcolor=FIELD,
        highlightthickness=0,
        bd=0,
        font=(FONT, 9),
    )


def dict_set(config, path, value):
    node = config
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def dict_get(config, path):
    node = config
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


class CalibrationWindow(tk.Toplevel):
    def __init__(self, master, config, root, frame, config_path, on_saved=None):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title("校准工具 - 框选 UI 与抓取模板")
        self.configure(bg=BG)
        self.resizable(False, False)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.canvas_width = min(850, max(480, screen_w - 470))
        self.canvas_height = min(640, max(330, screen_h - 250))
        self.geometry(f"{self.canvas_width + 340}x{self.canvas_height + 170}")
        self.config = copy.deepcopy(config)
        self.root = Path(root)
        self.frame = frame
        self.frame_height, self.frame_width = frame.shape[:2]
        self.config_path = Path(config_path)
        self.on_saved = on_saved
        self.step_index = 0
        self.sel_start = None
        self.sel_end = None
        self.sel_point = None
        self.dragging = False
        self.dirty = False
        self.photo = None
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.display_width = 1
        self.display_height = 1
        self.syncing = False

        self._build_ui()
        self.update()
        self._load_image()
        self._draw_saved()
        self._sync_step()
        self.transient(master)
        self.grab_set()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        canvas_frame = tk.Frame(self, bg=CANVAS_BG)
        canvas_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.canvas = tk.Canvas(
            canvas_frame,
            width=self.canvas_width,
            height=self.canvas_height,
            bg=CANVAS_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        panel = ttk.Frame(self, style="Panel.TFrame", padding=14)
        panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(6, weight=1)

        ttk.Label(panel, text="校准步骤", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")

        self.step_list = tk.Listbox(
            panel,
            height=5,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=(FONT, 10),
        )
        for step in STEP_INFO:
            self.step_list.insert("end", step["title"])
        self.step_list.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        self.step_list.bind("<<ListboxSelect>>", self._on_step_select)

        self.instruction_var = tk.StringVar()
        ttk.Label(
            panel,
            textvariable=self.instruction_var,
            style="Panel.TLabel",
            wraplength=300,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.status_var = tk.StringVar(value="拖拽或单击截屏进行框选")
        ttk.Label(panel, textvariable=self.status_var, style="Panel.TLabel", wraplength=300, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        self.confirm_button = ttk.Button(panel, text="确认并下一步", style="Accent.TButton", command=self._confirm)
        self.confirm_button.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self.skip_button = ttk.Button(panel, text="跳过此步", command=self._skip)
        self.skip_button.grid(row=5, column=0, sticky="ew", padx=(0, 3))

        ttk.Button(panel, text="重新选择", command=self._clear_selection).grid(row=5, column=1, sticky="ew", padx=(3, 0))

        ttk.Button(panel, text="保存配置并关闭", style="Accent.TButton", command=self._save_and_close).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(12, 6)
        )
        ttk.Button(panel, text="关闭（不保存）", command=self._close).grid(row=8, column=0, columnspan=2, sticky="ew")

        ttk.Label(
            panel,
            text="提示：模板图片按当前分辨率保存，更换分辨率后需要重新抓取。",
            style="Panel.TLabel",
            wraplength=300,
            justify="left",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _load_image(self):
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        self.scale = min(canvas_w / self.frame_width, canvas_h / self.frame_height, 1.0)
        self.display_width = max(1, int(self.frame_width * self.scale))
        self.display_height = max(1, int(self.frame_height * self.scale))
        self.offset_x = (canvas_w - self.display_width) // 2
        self.offset_y = (canvas_h - self.display_height) // 2
        if self.scale < 1.0:
            small = cv2.resize(self.frame, (self.display_width, self.display_height), interpolation=cv2.INTER_AREA)
        else:
            small = self.frame
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("all")
        self.canvas.create_image(self.offset_x, self.offset_y, image=self.photo, anchor="nw")

    def _to_frame(self, x, y):
        fx = (x - self.offset_x) / self.scale
        fy = (y - self.offset_y) / self.scale
        fx = min(max(fx, 0.0), self.frame_width - 1.0)
        fy = min(max(fy, 0.0), self.frame_height - 1.0)
        return fx, fy

    def _to_canvas(self, x, y):
        return self.offset_x + x * self.scale, self.offset_y + y * self.scale

    def _inside_image(self, x, y):
        return (
            self.offset_x <= x <= self.offset_x + self.display_width
            and self.offset_y <= y <= self.offset_y + self.display_height
        )

    def _current_mode(self):
        return STEP_INFO[self.step_index]["mode"]

    def _selection_rect(self):
        if self.sel_start is None or self.sel_end is None:
            return None
        x1, x2 = sorted((self.sel_start[0], self.sel_end[0]))
        y1, y2 = sorted((self.sel_start[1], self.sel_end[1]))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _selection_valid(self):
        if self._current_mode() == "point":
            return self.sel_point is not None
        return self._selection_rect() is not None

    def _on_press(self, event):
        if not self._inside_image(event.x, event.y):
            return
        if self._current_mode() == "point":
            self.sel_point = self._to_frame(event.x, event.y)
            self._redraw_selection()
            return
        self.sel_start = self._to_frame(event.x, event.y)
        self.sel_end = self.sel_start
        self.dragging = True
        self._redraw_selection()

    def _on_drag(self, event):
        if not self.dragging:
            return
        self.sel_end = self._to_frame(event.x, event.y)
        self._redraw_selection()

    def _on_release(self, event):
        if not self.dragging:
            return
        self.sel_end = self._to_frame(event.x, event.y)
        self.dragging = False
        self._redraw_selection()

    def _clear_selection(self):
        self.sel_start = None
        self.sel_end = None
        self.sel_point = None
        self.dragging = False
        self._redraw_selection()

    def _redraw_selection(self):
        self.canvas.delete("sel")
        if self._current_mode() == "point":
            if self.sel_point:
                cx, cy = self._to_canvas(*self.sel_point)
                radius = 11
                self.canvas.create_line(cx - radius, cy, cx + radius, cy, fill="#00e676", width=2, tags="sel")
                self.canvas.create_line(cx, cy - radius, cx, cy + radius, fill="#00e676", width=2, tags="sel")
                self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, outline="#00e676", width=2, tags="sel")
        else:
            rect = self._selection_rect()
            if rect:
                x1, y1, x2, y2 = rect
                c1 = self._to_canvas(x1, y1)
                c2 = self._to_canvas(x2, y2)
                self.canvas.create_rectangle(c1[0], c1[1], c2[0], c2[1], outline="#00e676", width=2, tags="sel")
                self.canvas.create_text(
                    c1[0] + 4,
                    max(12, c1[1] - 10),
                    text=f"{x2 - x1} x {y2 - y1}",
                    fill="#00e676",
                    anchor="w",
                    font=(FONT, 9),
                    tags="sel",
                )
        self.confirm_button.configure(state="normal" if self._selection_valid() else "disabled")

    def _draw_saved(self):
        self.canvas.delete("saved")
        specs = [
        (("bars", "hp", "roi"), "#ffd166", "状态1"),
        (("bars", "mp", "roi"), "#4dabf7", "状态2"),
            (("skills", "bar_roi"), "#51cf66", "SKILLS"),
        ]
        for path, color, label in specs:
            roi = dict_get(self.config, path)
            if not roi or len(roi) != 4:
                continue
            x, y, width, height = roi
            c1 = self._to_canvas(x * self.frame_width, y * self.frame_height)
            c2 = self._to_canvas((x + width) * self.frame_width, (y + height) * self.frame_height)
            self.canvas.create_rectangle(c1[0], c1[1], c2[0], c2[1], outline=color, dash=(4, 3), tags="saved")
            self.canvas.create_text(c1[0] + 2, max(10, c1[1] - 8), text=label, fill=color, anchor="w", font=(FONT, 8), tags="saved")
        anchor = self.config["player"].get("anchor")
        if anchor and len(anchor) == 2:
            cx, cy = self._to_canvas(anchor[0] * self.frame_width, anchor[1] * self.frame_height)
            self.canvas.create_line(cx - 7, cy - 7, cx + 7, cy + 7, fill="#ff8787", dash=(3, 2), tags="saved")
            self.canvas.create_line(cx - 7, cy + 7, cx + 7, cy - 7, fill="#ff8787", dash=(3, 2), tags="saved")

    def _sync_step(self):
        step = STEP_INFO[self.step_index]
        self.instruction_var.set(f"{self.step_index + 1}/5  {step['title']}\n{step['help']}")
        self.syncing = True
        self.step_list.selection_clear(0, "end")
        self.step_list.selection_set(self.step_index)
        self.step_list.activate(self.step_index)
        self.syncing = False
        self.confirm_button.configure(text="保存模板" if step.get("template") else "确认并下一步")
        self.skip_button.configure(state="disabled" if step.get("template") else "normal")
        self._redraw_selection()
        self._draw_saved()

    def _on_step_select(self, _event=None):
        if self.syncing:
            return
        selection = self.step_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index == self.step_index:
            return
        self.step_index = index
        self._clear_selection()
        self._sync_step()

    def _next_step(self):
        if self.step_index < len(STEP_INFO) - 1:
            self.step_index += 1
        self._clear_selection()
        self._sync_step()

    def _skip(self):
        self._next_step()

    def _confirm(self):
        if not self._selection_valid():
            return
        step = STEP_INFO[self.step_index]
        if step.get("template"):
            rect = self._selection_rect()
            x1, y1, x2, y2 = rect
            crop = self.frame[y1:y2, x1:x2]
            if crop.size == 0:
                return
            folder = self.root / "templates" / "enemy"
            folder.mkdir(parents=True, exist_ok=True)
            index = 1
            while (folder / f"enemy_{index}.png").exists():
                index += 1
            path = folder / f"enemy_{index}.png"
            imwrite(path, crop)
            self.dirty = True
            self.status_var.set(f"已保存目标模板 {path.name}，可继续框选下一个目标。")
            self._clear_selection()
            return

        if step["mode"] == "point":
            px, py = self.sel_point
            anchor = [round(px / self.frame_width, 5), round(py / self.frame_height, 5)]
            self.config["player"]["anchor"] = anchor
            self.status_var.set(f"已记录主体锚点 {anchor}")
        else:
            x1, y1, x2, y2 = self._selection_rect()
            roi = [
                round(x1 / self.frame_width, 5),
                round(y1 / self.frame_height, 5),
                round((x2 - x1) / self.frame_width, 5),
                round((y2 - y1) / self.frame_height, 5),
            ]
            dict_set(self.config, step["path"], roi)
            self.status_var.set(f"已记录 {step['title']}: {roi}")
        self.dirty = True
        self._draw_saved()
        self._next_step()

    def _save_and_close(self):
        save_config(self.config_path, self.config)
        self.dirty = False
        if self.on_saved is not None:
            self.on_saved()
        self.grab_release()
        self.destroy()

    def _close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel("未保存", "有未保存的校准修改，是否保存？", parent=self)
            if answer is None:
                return
            if answer:
                self._save_and_close()
                return
        self.grab_release()
        self.destroy()


class AutomationApp(tk.Tk):
    def __init__(self):
        super().__init__()
        hide_window_if_testing(self)
        self.root = project_root()
        self.config_path = self.root / "config.json"
        self.config = load_config(self.config_path)
        self.log_queue = queue.Queue()
        self._log_handle = self._open_log_file()
        self.engine = None
        self.hotkeys = HotkeyWatcher()
        self.overlay_enabled = True
        self._preview_photo = None
        self._last_frame_ref = None
        self._last_canvas_size = (0, 0)
        self._force_preview = True
        self._last_preview_at = 0.0
        self._last_replay_event_key = None
        self._scene_manager = None
        self._checkpoint_editor = None
        self._analyzer_window = None
        self._task_thread = None
        self._task_abort = threading.Event()
        self._task_running = False
        self._task_phase = ""
        self._task_message = ""
        self._shown_task_message = ""
        self._last_task_refresh = 0.0
        self.current_task = "combat"
        self._task_refreshing = False
        self._task_list_signature = None
        self._goal_values_signature = None
        self.selected_session = None
        self.recorder = None
        self._record_after_id = None
        self._record_pending = False
        self._record_countdown_end = 0.0
        self._replay_abort = threading.Event()
        self._replay_thread = None
        self._replay_running = False
        self._replay_progress = ""
        self._replay_message = ""
        self._record_message = ""
        self._session_infos = []
        self._session_events = []
        self._replay_current = None
        self._replay_highlight_index = None
        self._replay_session_path = None
        self._replay_display_events = []
        self._replay_display_name = ""
        self._replay_branch_note = ""
        self._pending_replay_session = None
        self._replay_restore_pending = False
        self._last_replay_ui = 0.0

        self.app_version = get_version()
        self.title(f"自动化控制台 v{self.app_version}")
        self.configure(bg=BG)
        width, height = 1320, 820
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(1100, 680)

        self._build_styles()
        self._init_vars()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._create_engine()
        self._log(f"界面已启动 v{self.app_version}")
        self.after(90, self._tick)
        self.after(100, self._poll_hotkeys)

    def _open_log_file(self):
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"gui_{time.strftime('%Y%m%d_%H%M%S')}.log"
        return open(path, "a", encoding="utf-8")

    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=(FONT, 9))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure("Panel.TLabel", background=PANEL, foreground=MUTED, font=(FONT, 9))
        style.configure("Value.TLabel", background=PANEL, foreground=ACCENT, font=("Consolas", 10, "bold"))
        style.configure("Section.TLabel", background=PANEL, foreground=FG, font=(FONT, 10, "bold"))
        style.configure("TaskTitle.TLabel", background=PANEL, foreground=FG, font=(FONT, 13, "bold"))
        style.configure("Title.TLabel", background=BG, foreground=FG, font=(FONT, 14, "bold"))
        style.configure("TButton", font=(FONT, 9), padding=(10, 5))
        style.configure("Accent.TButton", font=(FONT, 9, "bold"), padding=(10, 7), background=ACCENT, foreground=SELECT_FG)
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE), ("disabled", "#3a4c46")])
        style.configure("TCheckbutton", background=PANEL, foreground=FG, font=(FONT, 9))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure(
            "Treeview",
            background=FIELD,
            fieldbackground=FIELD,
            foreground=FG,
            rowheight=24,
            font=(FONT, 9),
            bordercolor="#2a2f3a",
        )
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, font=(FONT, 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", SELECT_FG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 7))
        style.map("TNotebook.Tab", background=[("selected", FIELD)], foreground=[("selected", FG)])

    def _init_vars(self):
        self.title_var = tk.StringVar(value=self.config["window"].get("title_keyword", ""))
        self.window_info_var = tk.StringVar(value="未锁定")
        self.run_state_var = tk.StringVar(value="未启动")
        self.capture_var = tk.StringVar(value="-")
        self.hp_var = tk.StringVar(value="-")
        self.mp_var = tk.StringVar(value="-")
        self.enemies_var = tk.StringVar(value="0")
        self.skills_var = tk.StringVar(value="未校准")
        self.scene_var = tk.StringVar(value="未配置")
        self.action_var = tk.StringVar(value="-")
        self.dry_var = tk.BooleanVar(value=bool(self.config["safety"].get("dry_run", False)))
        self.task_title_var = tk.StringVar(value="")
        self.task_desc_var = tk.StringVar(value="")
        self.task_steps_var = tk.StringVar(value="")
        self.task_status_var = tk.StringVar(value="请选择任务")
        self.task_hint_var = tk.StringVar(value="")
        self.task_goal_var = tk.StringVar(value="")
        self.record_status_var = tk.StringVar(value="未录制")
        self._record_event_signature = ()
        self.replay_status_var = tk.StringVar(value="未回放")
        self.replay_current_var = tk.StringVar(value="当前事件: -")
        self.session_info_var = tk.StringVar(value="未选择会话")
        self.replay_speed_var = tk.StringVar(value=str(self.config["replay"].get("speed", 1.0)))
        self.replay_loops_var = tk.StringVar(value=str(self.config["replay"].get("loops", 1)))
        gap_cfg = self.config["replay"].get("loop_gap_s", [0.5, 2.0])
        if isinstance(gap_cfg, (int, float)):
            gap_default = float(gap_cfg)
        else:
            try:
                gap_default = (float(gap_cfg[0]) + float(gap_cfg[1])) / 2.0
            except (TypeError, ValueError, IndexError):
                gap_default = 1.0
        self.replay_loop_gap_var = tk.StringVar(value=f"{gap_default:g}")
        self.replay_checkpoint_immediate_var = tk.BooleanVar(
            value=bool(self.config["replay"].get("checkpoint_immediate", True))
        )

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(14, 10, 14, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        title_row = ttk.Frame(header)
        title_row.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))
        ttk.Label(title_row, text=f"自动化控制台 v{self.app_version}", style="Title.TLabel").pack(side="left")
        ttk.Label(title_row, textvariable=self.window_info_var, foreground=WARN).pack(side="right")

        ttk.Label(header, text="目标窗口标题").grid(row=1, column=0, sticky="w")
        ttk.Entry(header, textvariable=self.title_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(header, text="枚举窗口", command=self._pick_window).grid(row=1, column=2, padx=(0, 6))
        ttk.Button(header, text="锁定窗口", command=self._lock_window).grid(row=1, column=3)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.task_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.replay_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=12)
        self.monitor_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.task_tab, text="任务中心")
        self.notebook.add(self.replay_tab, text="录制回放")
        self.notebook.add(self.monitor_tab, text="实时监控")
        self.monitor_tab.columnconfigure(0, weight=1)
        self.monitor_tab.rowconfigure(0, weight=1)

        left = ttk.Frame(self.monitor_tab)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 8), pady=4)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.preview_canvas = tk.Canvas(left, bg=CANVAS_BG, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            left,
            text="提示：运行时请保持目标窗口前台；F8 开始/继续，F9 暂停，F1 开关叠加层。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        right = ttk.Frame(self.monitor_tab, style="Panel.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=4)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(5, weight=1)

        ttk.Label(right, text="运行状态", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        status = ttk.Frame(right, style="Panel.TFrame")
        status.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        status.columnconfigure(1, weight=1)
        rows = [
            ("运行状态", self.run_state_var),
            ("截屏", self.capture_var),
        ("状态1", self.hp_var),
        ("状态2", self.mp_var),
            ("目标数量", self.enemies_var),
            ("快捷项就绪", self.skills_var),
            ("当前场景", self.scene_var),
            ("当前动作", self.action_var),
        ]
        for index, (label, variable) in enumerate(rows):
            ttk.Label(status, text=label, style="Panel.TLabel").grid(row=index, column=0, sticky="w", pady=1)
            ttk.Label(status, textvariable=variable, style="Value.TLabel").grid(
                row=index, column=1, sticky="w", padx=(10, 0)
            )

        controls = ttk.Frame(right, style="Panel.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(controls, text="开始 / 继续  F8", style="Accent.TButton", command=self._toggle_run)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.pause_button = ttk.Button(controls, text="暂停  F9", command=self._pause)
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        dark_checkbutton(
            controls,
            "仅观察（不发送按键）",
            self.dry_var,
            self._toggle_dry,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(controls, text="校准 / 抓取模板", command=self._open_calibration).grid(
            row=2, column=0, sticky="ew", padx=(0, 4), pady=(6, 0)
        )
        ttk.Button(controls, text="保存当前截图", command=self._save_snapshot).grid(
            row=2, column=1, sticky="ew", padx=(4, 0), pady=(6, 0)
        )
        ttk.Button(controls, text="场景 / 流程管理", command=self._open_scene_manager).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        ttk.Label(right, text="运行日志", style="Section.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 4))
        log_frame = ttk.Frame(right, style="Panel.TFrame")
        log_frame.grid(row=5, column=0, sticky="nsew")
        right.rowconfigure(5, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=14,
            bg=LIST_BG,
            fg=LIST_FG,
            insertbackground=FG,
            relief="flat",
            wrap="word",
            font=(FONT, 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self._build_task_tab()
        self._build_replay_tab()
        self._refresh_sessions()
        self._on_task_select()

    def _build_task_tab(self):
        tab = self.task_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)

        head = ttk.Frame(tab, style="Panel.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="任务中心：选择目标，按检查项准备后运行", style="TaskTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(head, text="录制回放页", command=self._show_replay_tab).grid(row=0, column=1, sticky="e", padx=(0, 6))
        ttk.Button(head, text="查看实时监控 / 日志", command=self._show_monitor_tab).grid(row=0, column=2, sticky="e")

        left = ttk.Frame(tab, style="Panel.TFrame")
        left.grid(row=1, column=0, sticky="ns", padx=(0, 14))
        ttk.Label(left, text="自动化目标", style="Section.TLabel").pack(anchor="w")
        self.task_list = tk.Listbox(
            left,
            width=27,
            height=10,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=(FONT, 10),
        )
        self.task_list.pack(fill="y", expand=True, pady=(6, 6))
        self.task_list.bind("<<ListboxSelect>>", self._on_task_select)
        for task in TASK_DEFS:
            suffix = "" if task["implemented"] else "  [开发中]"
            self.task_list.insert("end", task["title"] + suffix)
        self.task_list.selection_set(0)
        ttk.Label(left, text="带“开发中”的任务暂不可运行", style="Panel.TLabel", wraplength=210).pack(anchor="w")

        detail = ttk.Frame(tab, style="Panel.TFrame")
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(8, weight=1)

        ttk.Label(detail, textvariable=self.task_title_var, style="TaskTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            detail,
            textvariable=self.task_desc_var,
            style="Panel.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 4))
        ttk.Label(
            detail,
            textvariable=self.task_steps_var,
            style="Panel.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 8))

        prereq = ttk.Frame(detail, style="Panel.TFrame")
        prereq.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        prereq.columnconfigure(1, weight=1)
        ttk.Label(prereq, text="运行前检查", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        specs = [
            ("window", "目标窗口"),
            ("hp", "状态条1"),
            ("player", "主体位置"),
            ("enemies", "目标模板"),
            ("skills", "快捷栏（建议）"),
            ("scenes", "场景模板"),
            ("flow", "跳转流程"),
        ]
        self.prereq_labels = {}
        for index, (key, label) in enumerate(specs, start=1):
            tk.Label(prereq, text=label, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w").grid(
                row=index, column=0, sticky="w", pady=1
            )
            status = tk.Label(prereq, text="待检查", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"), anchor="w")
            status.grid(row=index, column=1, sticky="w", padx=(14, 0))
            self.prereq_labels[key] = status

        self.goal_frame = ttk.Frame(detail, style="Panel.TFrame")
        self.goal_frame.grid(row=4, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(self.goal_frame, text="流程导航目标场景", style="Panel.TLabel").pack(side="left")
        self.task_goal_combo = ttk.Combobox(
            self.goal_frame,
            textvariable=self.task_goal_var,
            state="readonly",
            width=26,
        )
        self.task_goal_combo.pack(side="left", padx=8)
        self.task_goal_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_task_status(force=True))

        buttons = ttk.Frame(detail, style="Panel.TFrame")
        buttons.grid(row=5, column=0, sticky="ew", pady=(4, 6))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.task_start_button = ttk.Button(
            buttons,
            text="开始任务",
            style="Accent.TButton",
            command=self._start_selected_task,
        )
        self.task_start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.task_pause_button = ttk.Button(
            buttons,
            text="停止 / 暂停  F9",
            command=self._stop_task,
        )
        self.task_pause_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(buttons, text="校准 HUD", command=self._open_calibration).grid(
            row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0)
        )
        ttk.Button(buttons, text="场景 / 流程管理", command=self._open_scene_manager).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0)
        )
        dark_checkbutton(
            buttons,
            "仅观察（自动操作有效，流程任务需关闭）",
            self.dry_var,
            self._toggle_dry,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.task_hint_label = tk.Label(
            detail,
            textvariable=self.task_hint_var,
            bg=PANEL,
            fg=WARN,
            justify="left",
            anchor="w",
            wraplength=720,
            font=(FONT, 9),
        )
        self.task_hint_label.grid(row=6, column=0, sticky="w", pady=(4, 0))

        tk.Label(
            detail,
            textvariable=self.task_status_var,
            bg=PANEL,
            fg=ACCENT,
            justify="left",
            anchor="w",
            wraplength=720,
            font=(FONT, 9),
        ).grid(row=7, column=0, sticky="w", pady=(6, 0))

    def _build_replay_tab(self):
        tab = self.replay_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)

        head = ttk.Frame(tab, style="Panel.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="录制回放：手动打一遍，自动重复执行", style="TaskTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(head, text="打开 sessions 目录", command=self._open_sessions_dir).grid(row=0, column=1, sticky="e")

        left = ttk.Frame(tab, style="Panel.TFrame")
        left.grid(row=1, column=0, sticky="ns", padx=(0, 14))
        ttk.Label(left, text="录制会话", style="Section.TLabel").pack(anchor="w")
        self.session_list = tk.Listbox(
            left,
            width=33,
            height=20,
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
        self.session_list.pack(fill="y", expand=True, pady=(6, 6))
        self.session_list.bind("<<ListboxSelect>>", self._on_session_select)
        self.session_list.bind("<Double-Button-1>", self._rename_selected_session)
        tk.Label(
            left,
            text="双击会话可重命名",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        session_buttons = ttk.Frame(left, style="Panel.TFrame")
        session_buttons.pack(fill="x")
        ttk.Button(session_buttons, text="刷新", command=self._refresh_sessions).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(session_buttons, text="删除", command=self._delete_selected_session).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(session_buttons, text="编辑", command=self._open_checkpoint_editor).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        ttk.Button(session_buttons, text="自动加断点", command=self._open_auto_analyzer).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        right = ttk.Frame(tab, style="Panel.TFrame")
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)

        record = ttk.Frame(right, style="Panel.TFrame")
        record.grid(row=0, column=0, sticky="ew")
        record.columnconfigure(0, weight=1)
        record.columnconfigure(1, weight=1)
        ttk.Label(record, text="录制", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.record_start_button = ttk.Button(
            record, text="开始录制  F6", style="Accent.TButton", command=self._start_recording
        )
        self.record_start_button.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        self.record_stop_button = ttk.Button(record, text="停止并保存  F7", command=self._stop_recording)
        self.record_stop_button.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        tk.Label(
            record,
            textvariable=self.record_status_var,
            bg=PANEL,
            fg=ACCENT,
            anchor="w",
            justify="left",
            font=(FONT, 9),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(
            record,
            text="录制只采集目标窗口前台键鼠；停止后可在“编辑”里裁剪/加断点。F6/F7 开始/停止，触发切换后按 F5 插入同步断点。",
            style="Panel.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.record_events_label = ttk.Label(record, text="录制事件（最近 4 条）", style="Panel.TLabel")
        self.record_events_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.record_events_frame = ttk.Frame(record, style="Panel.TFrame")
        self.record_events_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.record_events_frame.columnconfigure(0, weight=1)
        self.record_event_list = tk.Listbox(
            self.record_events_frame,
            height=4,
            bg=FIELD,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Consolas", 9),
        )
        self.record_event_list.grid(row=0, column=0, sticky="ew")
        record_event_scroll = ttk.Scrollbar(
            self.record_events_frame, orient="vertical", command=self.record_event_list.yview
        )
        record_event_scroll.grid(row=0, column=1, sticky="ns")
        self.record_event_list.configure(yscrollcommand=record_event_scroll.set)
        self.record_events_label.grid_remove()
        self.record_events_frame.grid_remove()

        replay = ttk.Frame(right, style="Panel.TFrame")
        replay.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        replay.columnconfigure(4, weight=1)
        ttk.Label(replay, text="回放", style="Section.TLabel").grid(row=0, column=0, columnspan=5, sticky="w")

        options = ttk.Frame(replay, style="Panel.TFrame")
        options.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(options, text="速度", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(
            options,
            from_=0.25,
            to=4.0,
            increment=0.25,
            width=6,
            textvariable=self.replay_speed_var,
        ).pack(side="left", padx=(4, 14))
        ttk.Label(options, text="循环", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(
            options,
            from_=0,
            to=999,
            increment=1,
            width=6,
            textvariable=self.replay_loops_var,
        ).pack(side="left", padx=(4, 14))
        ttk.Label(options, text="间隔(s)", style="Panel.TLabel").pack(side="left")
        ttk.Spinbox(
            options,
            from_=0.0,
            to=600.0,
            increment=0.5,
            width=7,
            textvariable=self.replay_loop_gap_var,
        ).pack(side="left", padx=(4, 14))
        dark_checkbutton(options, "断点后立即继续", self.replay_checkpoint_immediate_var).pack(
            side="left"
        )

        controls = ttk.Frame(replay, style="Panel.TFrame")
        controls.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        self.replay_start_button = ttk.Button(
            controls, text="开始回放", style="Accent.TButton", command=self._start_replay_from_ui
        )
        self.replay_start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.replay_stop_button = ttk.Button(controls, text="停止回放  F9", command=self._stop_replay)
        self.replay_stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        tk.Label(
            replay,
            textvariable=self.replay_status_var,
            bg=PANEL,
            fg=ACCENT,
            anchor="w",
            justify="left",
            wraplength=760,
            font=(FONT, 9),
        ).grid(row=3, column=0, columnspan=5, sticky="w", pady=(4, 0))

        ttk.Label(right, text="会话详情", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 2))
        ttk.Label(
            right,
            textvariable=self.session_info_var,
            style="Panel.TLabel",
            wraplength=780,
            justify="left",
        ).grid(row=3, column=0, sticky="w")

        preview_frame = ttk.Frame(right, style="Panel.TFrame")
        preview_frame.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(2, weight=1)
        ttk.Label(
            preview_frame,
            text="事件时间线（回放时自动高亮，切到实时监控可看屏幕操作）",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            preview_frame,
            textvariable=self.replay_current_var,
            bg=PANEL,
            fg=WARN,
            anchor="w",
            justify="left",
            font=(FONT, 9, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(2, 4))
        self.event_list = tk.Listbox(
            preview_frame,
            height=10,
            bg=LIST_BG,
            exportselection=False,
            fg=LIST_FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            font=("Consolas", 9),
        )
        self.event_list.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=self.event_list.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.event_list.configure(yscrollcommand=scrollbar.set)

    def _show_replay_tab(self):
        self.notebook.select(self.replay_tab)

    def _open_sessions_dir(self):
        folder = sessions_dir(self.root)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _refresh_sessions(self, select_path=None):
        current = select_path or self.selected_session
        self._session_infos = list_sessions(self.root)
        self.session_list.delete(0, "end")
        for info in self._session_infos:
            minutes = int(info.duration // 60)
            seconds = int(info.duration % 60)
            self.session_list.insert("end", f"{info.name}  [{minutes:02d}:{seconds:02d}]  {info.event_count} 事件")
        if not self._session_infos:
            self.selected_session = None
            self.session_info_var.set("还没有录制会话")
            self._set_event_preview([])
            self._refresh_task_status(force=True)
            return
        index = 0
        if current:
            for position, info in enumerate(self._session_infos):
                if str(info.path) == str(current):
                    index = position
                    break
        self.session_list.selection_set(index)
        self.session_list.see(index)
        self._select_session(self._session_infos[index])

    def _on_session_select(self, _event=None):
        if self._replay_running:
            return
        selection = self.session_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._session_infos):
            self._select_session(self._session_infos[index])

    def _rename_selected_session(self, _event=None):
        if self._task_running or self._replay_running or self.recorder is not None or self._record_pending:
            messagebox.showwarning("占用中", "请先停止录制、回放或流程任务，再重命名会话。", parent=self)
            return
        selection = self.session_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个会话", parent=self)
            return
        index = int(selection[0])
        if not (0 <= index < len(self._session_infos)):
            return
        info = self._session_infos[index]
        name = simpledialog.askstring(
            "重命名会话",
            "输入新的会话名称（分支目标会同步更新）：",
            initialvalue=info.name,
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name or name == info.name:
            return
        try:
            new_path, updated = rename_session(info.path, name)
        except Exception as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self)
            return
        self._log(f"会话已重命名: {info.name} → {new_path.name}")
        if updated:
            self._log(f"已同步更新 {updated} 处分支目标会话引用")
        self._refresh_sessions(new_path)

    def _select_session(self, info):
        self.selected_session = info.path
        minutes = int(info.duration // 60)
        seconds = int(info.duration % 60)
        detail = (
            f"{info.name} | {minutes:02d}:{seconds:02d} | {info.event_count} 事件"
            f" | {info.window_title or '未知窗口'}"
        )
        if len(info.client_size) >= 2:
            detail += f" | 客户区 {info.client_size[0]}x{info.client_size[1]}"
        self.session_info_var.set(detail)
        events = sorted(load_events(info.path), key=lambda event: float(event.get("t", 0.0)))
        self._set_event_preview(events)
        self._refresh_task_status(force=True)
        self._update_replay_ui()

    def _render_event_list(self, events):
        self.event_list.delete(0, "end")
        lines = [
            f"{float(event.get('t', 0.0)):7.3f}  {format_replay_event(event)}"
            for event in events
        ]
        for start in range(0, len(lines), 500):
            self.event_list.insert("end", *lines[start : start + 500])
        self._replay_highlight_index = None

    def _set_event_preview(self, events):
        self._session_events = list(events)
        self._render_event_list(self._session_events)
        self.replay_current_var.set("当前事件: -")

    def _clear_replay_highlight(self):
        if self._replay_highlight_index is not None and self._replay_highlight_index < self.event_list.size():
            try:
                self.event_list.itemconfig(self._replay_highlight_index, background=LIST_BG, foreground=LIST_FG)
            except tk.TclError:
                pass
        self._replay_highlight_index = None

    def _on_replay_event(self, loop_index, loops_total, event_index, event_total, event):
        self._replay_current = {
            "loop": loop_index,
            "loops": loops_total,
            "index": event_index,
            "total": event_total,
            "event": dict(event),
        }

    def _sync_replay_highlight(self):
        current = self._replay_current
        if not current:
            return
        if not self.selected_session or self._replay_session_path is None:
            return
        if str(self.selected_session) != str(self._replay_session_path):
            return
        index = int(current.get("index", -1))
        display_events = (
            self._replay_display_events
            if self._replay_running
            else self._session_events
        )
        expected = (
            display_events[index]
            if 0 <= index < len(display_events)
            else None
        )
        highlight = (
            expected == current.get("event")
            and 0 <= index < self.event_list.size()
        )
        if highlight:
            if index != self._replay_highlight_index:
                if self._replay_highlight_index is not None and self._replay_highlight_index < self.event_list.size():
                    try:
                        self.event_list.itemconfig(
                            self._replay_highlight_index,
                            background=LIST_BG,
                            foreground=LIST_FG,
                        )
                    except tk.TclError:
                        pass
                try:
                    self.event_list.itemconfig(index, background=ACCENT, foreground=SELECT_FG)
                    self.event_list.see(max(0, index - 2))
                except tk.TclError:
                    pass
                self._replay_highlight_index = index
        elif self._replay_highlight_index is not None:
            if self._replay_highlight_index < self.event_list.size():
                try:
                    self.event_list.itemconfig(
                        self._replay_highlight_index,
                        background=LIST_BG,
                        foreground=LIST_FG,
                    )
                except tk.TclError:
                    pass
            self._replay_highlight_index = None
        event = current.get("event") or {}
        total_loops = current.get("loops", 1)
        loops_text = "∞" if total_loops == 0 else str(total_loops)
        prefix = ""
        if self._replay_running and self._replay_display_name:
            prefix = f"会话 {self._replay_display_name} · "
        self.replay_current_var.set(
            f"{prefix}第 {current.get('loop', 1)}/{loops_text} 轮 · "
            f"事件 {index + 1}/{current.get('total', self.event_list.size())}: "
            f"{format_replay_event(event)}"
        )

    def _open_checkpoint_editor(self):
        if self._task_running or self._replay_running or self.recorder is not None or self._record_pending:
            messagebox.showwarning("占用中", "请先停止录制、回放或流程任务，再编辑会话。", parent=self)
            return
        if not self.selected_session:
            messagebox.showinfo("提示", "请先选择一个录制会话", parent=self)
            return
        try:
            if self._checkpoint_editor is not None and self._checkpoint_editor.winfo_exists():
                self._checkpoint_editor.lift()
                return
        except tk.TclError:
            self._checkpoint_editor = None
        self._checkpoint_editor = CheckpointEditorWindow(
            self,
            self.root,
            self.selected_session,
            on_saved=self._on_checkpoints_saved,
        )

    def _on_checkpoints_saved(self):
        self._log("断点已写入会话")
        if self.selected_session:
            self._refresh_sessions(self.selected_session)
        self._refresh_task_status(force=True)

    def _open_auto_analyzer(self):
        if self._task_running or self._replay_running or self.recorder is not None or self._record_pending:
            messagebox.showwarning("占用中", "请先停止录制、回放或流程任务，再分析会话。", parent=self)
            return
        if not self.selected_session:
            messagebox.showinfo("提示", "请先选择一个录制会话", parent=self)
            return
        try:
            if self._analyzer_window is not None and self._analyzer_window.winfo_exists():
                self._analyzer_window.lift()
                return
        except tk.TclError:
            self._analyzer_window = None
        from src.analyze_ui import AnalyzerWindow

        self._analyzer_window = AnalyzerWindow(
            self,
            self.config,
            self.selected_session,
            on_saved=self._on_checkpoints_saved,
        )

    def _delete_selected_session(self):
        selection = self.session_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个会话", parent=self)
            return
        index = int(selection[0])
        if not (0 <= index < len(self._session_infos)):
            return
        info = self._session_infos[index]
        if self._replay_running and self.selected_session and str(self.selected_session) == str(info.path):
            messagebox.showwarning("正在回放", "不能删除正在回放的会话", parent=self)
            return
        if self.recorder is not None and self.recorder.folder is not None and info.path == self.recorder.folder:
            messagebox.showwarning("正在录制", "不能删除正在录制的会话", parent=self)
            return
        if not messagebox.askyesno("删除会话", f"确定删除 {info.name}？", parent=self):
            return
        delete_session(info.path)
        if self.selected_session and str(self.selected_session) == str(info.path):
            self.selected_session = None
        self._log(f"删除录制会话: {info.name}")
        self._refresh_sessions()

    def _start_recording(self):
        if self._replay_running or self._task_running:
            messagebox.showwarning("占用中", "请先停止当前回放或流程任务，再开始录制。", parent=self)
            return
        if self._record_pending or self.recorder is not None:
            return
        if not self.engine.active:
            self._rebuild_engine()
            if not self.engine.active:
                messagebox.showerror("无法录制", self.engine.last_error or "未找到目标窗口", parent=self)
                return
        countdown = max(0, int(self.config["safety"].get("countdown_s", 3)))
        self.engine.request_pause("准备录制，自动操作暂停")
        self.engine.set_external_lock(True, "录制中，自动操作暂停")
        self.engine.window.activate()
        self._record_pending = True
        self._record_countdown_end = time.perf_counter() + countdown
        self._record_message = ""
        self.record_status_var.set(f"{countdown} 秒后开始录制，请切到目标窗口")
        self._update_replay_ui()
        self._record_after_id = self.after(max(1, countdown) * 1000, self._begin_recording)
        self._log("录制: 倒计时开始")

    def _begin_recording(self):
        self._record_after_id = None
        if not self._record_pending:
            return
        self._record_pending = False
        if not self.engine.active:
            self.engine.set_external_lock(False)
            self._record_message = "录制启动失败：窗口未锁定"
            self.record_status_var.set(self._record_message)
            return
        self.recorder = InputRecorder(
            self.config,
            self.engine.window,
            self.root,
            log=self._log_from_thread,
            frame_provider=self.engine.current_frame,
        )
        try:
            folder = self.recorder.start()
        except Exception as exc:
            self.recorder = None
            self.engine.set_external_lock(False)
            self._record_message = f"录制启动失败: {exc}"
            self.record_status_var.set(self._record_message)
            messagebox.showerror("录制失败", str(exc), parent=self)
            return
        self._record_message = ""
        self.record_status_var.set("录制中...")
        self._update_replay_ui()
        self._log(f"录制中: {folder.name if folder else '-'}，按 F7 停止")

    def _stop_recording(self):
        if self._record_pending:
            self._record_pending = False
            if self._record_after_id is not None:
                self.after_cancel(self._record_after_id)
                self._record_after_id = None
            self.engine.set_external_lock(False)
            self._record_message = "已取消录制"
            self.record_status_var.set(self._record_message)
            self._update_replay_ui()
            self._log("录制已取消")
            return
        if self.recorder is None:
            return
        folder = self.recorder.stop()
        self.recorder = None
        self.engine.set_external_lock(False)
        name = folder.name if folder is not None else "-"
        self._record_message = f"录制已保存: {name}"
        self.record_status_var.set(self._record_message)
        self._log(f"录制已保存: {name}")
        self._refresh_sessions(folder)
        self._refresh_task_status(force=True)
        self._update_replay_ui()

    def _parse_replay_options(self):
        try:
            speed = float(self.replay_speed_var.get())
            loops = int(self.replay_loops_var.get())
            loop_gap = float(self.replay_loop_gap_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "速度、循环次数和间隔必须是数字", parent=self)
            return None
        if not (0.25 <= speed <= 4.0):
            messagebox.showerror("参数错误", "速度范围 0.25 ~ 4.0", parent=self)
            return None
        if not (0 <= loops <= 999):
            messagebox.showerror("参数错误", "循环次数范围 0 ~ 999（0 表示无限）", parent=self)
            return None
        if not (0.0 <= loop_gap <= 600.0):
            messagebox.showerror("参数错误", "回放间隔范围 0 ~ 600 秒", parent=self)
            return None
        return speed, loops, loop_gap

    def _start_replay_from_ui(self):
        if not self.selected_session:
            messagebox.showwarning("没有会话", "请先录制或选择一个会话。", parent=self)
            return
        self._start_replay(self.selected_session)

    def _start_replay(self, session):
        if self._replay_running:
            return
        if self.recorder is not None or self._record_pending:
            messagebox.showwarning("正在录制", "请先停止录制再回放。", parent=self)
            return
        if self._task_running:
            messagebox.showwarning("占用中", "请先停止流程任务再回放。", parent=self)
            return
        if self.dry_var.get():
            messagebox.showwarning("仅观察模式", "回放需要真实按键，请先关闭“仅观察”。", parent=self)
            return
        session_path = Path(session)
        if not session_path.exists():
            messagebox.showerror("会话不存在", f"找不到会话:\n{session_path}", parent=self)
            return
        options = self._parse_replay_options()
        if options is None:
            return
        speed, loops, loop_gap = options
        if not self.engine.active:
            self._rebuild_engine()
            if not self.engine.active:
                messagebox.showerror("无法回放", self.engine.last_error or "未找到目标窗口", parent=self)
                return
        countdown = max(0, int(self.config["safety"].get("countdown_s", 3)))
        self.engine.request_pause("回放任务，自动操作暂停")
        self.engine.set_external_lock(True, "回放任务占用输入")
        self._replay_abort.clear()
        self._replay_running = True
        self._replay_progress = "准备回放..."
        self._replay_message = ""
        self._replay_session_path = session_path
        self._replay_current = None
        self._replay_display_events = list(self._session_events)
        self._replay_display_name = session_path.name
        self._replay_branch_note = ""
        self._pending_replay_session = None
        self._replay_restore_pending = False
        self._clear_replay_highlight()
        self.replay_current_var.set("等待回放...")
        self._update_replay_ui()
        loops_label = "无限" if loops == 0 else f"{loops} 轮"
        self._log(f"任务中心: 启动回放 {session_path.name}，{loops_label}，{speed:.2f}x，间隔 {loop_gap:g}s")
        engine = self.engine
        config = self.config
        root = self.root
        checkpoint_immediate = bool(self.replay_checkpoint_immediate_var.get())
        abort = self._replay_abort

        def worker():
            try:
                engine.window.activate()
                for remaining in range(countdown, 0, -1):
                    if abort.is_set():
                        self._replay_message = "回放已取消"
                        self._log_from_thread("回放已取消")
                        return
                    self._replay_progress = f"倒计时 {remaining} 秒，请保持目标窗口前台"
                    self._log_from_thread(f"回放 {remaining} 秒后开始，请保持目标窗口前台")
                    time.sleep(1.0)
                engine.window.activate()

                def on_progress(loop_index, loops_total, event_index, event_total):
                    total = "∞" if loops_total == 0 else str(loops_total)
                    self._replay_progress = (
                        f"回放中 第 {loop_index}/{total} 轮 · 事件 {event_index}/{event_total}"
                    )

                def on_session_change(folder, label):
                    try:
                        events = load_events(folder)
                    except Exception:
                        events = []
                    self._pending_replay_session = (str(folder), events, str(label or ""))

                scene_recognizer = SceneRecognizer(config, root)

                def scene_checker():
                    frame = engine.current_frame()
                    if frame is None:
                        return None
                    return scene_recognizer.recognize(frame).scene_id

                player = ReplayPlayer(
                    config,
                    engine.window,
                    engine.inputs,
                    session_path,
                    speed=speed,
                    loops=loops,
                    loop_gap_s=loop_gap,
                    abort_event=abort,
                    log=self._log_from_thread,
                    progress=on_progress,
                    event_callback=self._on_replay_event,
                    scene_checker=scene_checker,
                    checkpoint_immediate=checkpoint_immediate,
                    frame_getter=engine.current_frame,
                    session_callback=on_session_change,
                )
                result = player.run()
                prefix = "回放完成: " if result.success else "回放结束: "
                loops_text = f"{result.loops} 轮" if loops else "无限"
                self._replay_message = (
                    f"{prefix}{result.message}（{loops_text}，{result.events} 事件，{result.elapsed:.1f}s）"
                )
                self._replay_progress = ""
                self._log_from_thread(self._replay_message)
            except Exception as exc:
                self._replay_message = f"回放异常: {exc}"
                self._log_from_thread("回放异常: " + traceback.format_exc(limit=2))
            finally:
                engine.inputs.release_all()
                engine.set_external_lock(False)
                self._replay_running = False
                self._replay_progress = ""
                self._replay_restore_pending = True

        self._replay_thread = threading.Thread(target=worker, daemon=True, name="replay-task")
        self._replay_thread.start()

    def _stop_replay(self):
        if not self._replay_running:
            return
        self._replay_abort.set()
        self._replay_message = "正在停止回放..."
        self._replay_progress = "正在停止..."
        if self.engine is not None:
            self.engine.inputs.release_all()
        self._log("任务中心: 停止回放")

    def _refresh_record_events(self, events):
        recent = list(events or [])[-4:]
        signature = tuple(repr(event) for event in recent)
        if signature == self._record_event_signature:
            return
        self._record_event_signature = signature
        self.record_event_list.delete(0, "end")
        for event in recent:
            t = float(event.get("t", 0.0) or 0.0)
            self.record_event_list.insert("end", f"{t:8.3f}  {format_replay_event(event)}")
        if recent:
            self.record_event_list.see("end")

    def _update_replay_ui(self):
        if self.recorder is not None and not self.recorder.recording:
            self._stop_recording()
            return
        recent_events = []
        if self._record_pending:
            remaining = max(0, int(math.ceil(self._record_countdown_end - time.perf_counter())))
            self.record_status_var.set(f"{remaining} 秒后开始录制，请切到目标窗口")
        elif self.recorder is not None:
            status = self.recorder.status()
            recent_events = status.get("recent_events") or []
            focus = "目标窗口前台" if status["focused"] else "目标窗口不在前台，录制暂停"
            self.record_status_var.set(
                f"录制中 · {status['elapsed']:.1f}s · {status['event_count']} 事件 · "
                f"{status.get('frame_count', 0)} 帧 · {focus}"
            )
        else:
            self.record_status_var.set(self._record_message or "未录制")
        show_record_events = self.recorder is not None and self.recorder.recording
        for widget in (self.record_events_label, self.record_events_frame):
            if show_record_events:
                widget.grid()
            else:
                widget.grid_remove()
        if self.recorder is not None and show_record_events:
            self._refresh_record_events(recent_events)

        pending = self._pending_replay_session
        if pending is not None:
            self._pending_replay_session = None
            folder, events, label = pending
            self._replay_display_events = list(events)
            self._replay_display_name = Path(folder).name
            self._render_event_list(self._replay_display_events)
            if label:
                self._replay_branch_note = f"分支 → {Path(folder).name}（{label}）"
                self._log(f"回放分支跳转: {label} → {Path(folder).name}")
            else:
                self._replay_branch_note = ""
        if not self._replay_running and self._replay_restore_pending:
            self._replay_restore_pending = False
            if self._replay_branch_note:
                self._replay_message = f"{self._replay_message}；{self._replay_branch_note}"
            self._replay_branch_note = ""
            self._replay_display_events = list(self._session_events)
            self._replay_display_name = ""
            self._render_event_list(self._session_events)
            self._replay_current = None
            self.replay_current_var.set("当前事件: -")

        if self._replay_running:
            status_text = self._replay_progress or "回放中..."
            if self._replay_branch_note:
                status_text = f"{status_text} · {self._replay_branch_note}"
            self.replay_status_var.set(status_text)
        else:
            self.replay_status_var.set(self._replay_message or "未回放")

        aux_busy = (
            self._record_pending
            or self.recorder is not None
            or self._replay_running
            or self._task_running
        )
        window_ok = self.engine is not None and self.engine.active
        self.record_start_button.configure(
            state="normal" if window_ok and not aux_busy else "disabled"
        )
        self.record_stop_button.configure(
            state="normal" if (self._record_pending or self.recorder is not None) else "disabled"
        )
        self.replay_start_button.configure(
            state="normal" if window_ok and self.selected_session and not aux_busy else "disabled"
        )
        self.replay_stop_button.configure(state="normal" if self._replay_running else "disabled")
        try:
            self.session_list.configure(state="disabled" if self._replay_running else "normal")
        except tk.TclError:
            pass

    def _show_monitor_tab(self):
        self.notebook.select(self.monitor_tab)

    def _create_engine(self):
        self.engine = AutomationEngine(self.config, self.root, log=self._log_from_thread)
        if not self.engine.start():
            self._log("初始化: " + (self.engine.last_error or "未连接窗口"))
        self._last_frame_ref = None
        self._force_preview = True

    def _rebuild_engine(self):
        if self.engine is not None:
            self.engine.stop()
        self._create_engine()

    def _selected_task_index(self):
        selection = self.task_list.curselection()
        if selection:
            index = int(selection[0])
            if 0 <= index < len(TASK_DEFS):
                return index
        for index, task in enumerate(TASK_DEFS):
            if task["id"] == self.current_task:
                return index
        return 0

    def _on_task_select(self, _event=None):
        if self._task_refreshing:
            return
        task = TASK_DEFS[self._selected_task_index()]
        self.current_task = task["id"]
        self.task_title_var.set(task["title"])
        self.task_desc_var.set(task["desc"])
        self.task_steps_var.set(task["steps"])
        if task["id"] == "route":
            self.goal_frame.grid()
        else:
            self.goal_frame.grid_remove()
        self._refresh_task_status(force=True)

    def _scene_ids_with_markers(self):
        scene_ids = []
        for scene_id in list_scene_ids(self.root):
            if list_markers(self.root, scene_id):
                scene_ids.append(scene_id)
        return scene_ids

    def _set_prereq(self, key, ok, text, optional=False):
        label = self.prereq_labels.get(key)
        if label is None:
            return
        if ok:
            color = ACCENT
        elif optional:
            color = WARN
        else:
            color = DANGER
        label.configure(text=text, fg=color)

    def _refresh_task_status(self, snapshot=None, force=False):
        if snapshot is None and self.engine is not None:
            snapshot = self.engine.snapshot()
        window_ok = bool(snapshot and snapshot.get("active"))
        hp_ok = bool((self.config.get("bars") or {}).get("hp", {}).get("roi"))
        skills_ok = bool(
            (self.config.get("skills") or {}).get("bar_roi")
            or (self.config.get("skills") or {}).get("slot_rois")
        )
        anchor = (self.config.get("player") or {}).get("anchor") or [0.5, 0.6]
        player_template = self.root / "templates" / "player" / "player.png"
        player_ok = list(anchor) != [0.5, 0.6] or player_template.is_file()
        enemy_dir = self.root / "templates" / "enemy"
        enemy_count = len(list(enemy_dir.glob("*.png"))) + len(list(enemy_dir.glob("*.jpg"))) if enemy_dir.is_dir() else 0
        markers = self._scene_ids_with_markers()
        transitions = (self.config.get("map_graph") or {}).get("transitions") or []
        flow_scenes = set()
        for transition in transitions:
            if isinstance(transition, dict):
                if transition.get("from"):
                    flow_scenes.add(str(transition["from"]))
                if transition.get("to"):
                    flow_scenes.add(str(transition["to"]))
        unmarked_scenes = sorted(flow_scenes - set(markers))
        session_ok = bool(self.selected_session and Path(self.selected_session).exists())
        aux_busy = self._record_pending or self.recorder is not None or self._replay_running

        self._set_prereq("window", window_ok, "已锁定" if window_ok else "未锁定")
        self._set_prereq("hp", hp_ok, "已校准" if hp_ok else "未校准", optional=True)
        self._set_prereq(
            "player",
            player_ok,
            "已设置" if player_ok else "使用默认位置",
            optional=True,
        )
        self._set_prereq("enemies", enemy_count > 0, f"{enemy_count} 个" if enemy_count else "0 个", optional=True)
        self._set_prereq("skills", skills_ok, "已校准" if skills_ok else "未校准", optional=True)
        self._set_prereq("scenes", bool(markers), f"{len(markers)} 个场景" if markers else "0 个", optional=True)
        self._set_prereq("flow", bool(transitions), f"{len(transitions)} 条跳转" if transitions else "0 条", optional=True)

        values = markers
        signature = tuple(values)
        if signature != getattr(self, "_goal_values_signature", None):
            self._goal_values_signature = signature
            self.task_goal_combo.configure(values=list(values))
            if self.task_goal_var.get() not in values:
                self.task_goal_var.set(values[0] if values else "")

        ready_combat = window_ok and enemy_count > 0
        ready_route = window_ok and bool(markers) and bool(transitions)
        ready_replay = window_ok and session_ok
        ready_map = {
            "combat": ready_combat,
            "route": ready_route,
            "dungeon_loop": False,
            "replay": ready_replay,
        }

        selected_index = self._selected_task_index()
        task_signature = tuple((task["id"], ready_map.get(task["id"], False)) for task in TASK_DEFS)
        if task_signature != self._task_list_signature:
            self._task_list_signature = task_signature
            self._task_refreshing = True
            self.task_list.delete(0, "end")
            for task in TASK_DEFS:
                if not task["implemented"]:
                    suffix = "  [开发中]"
                else:
                    suffix = "  [就绪]" if ready_map.get(task["id"]) else "  [缺配置]"
                self.task_list.insert("end", task["title"] + suffix)
            self.task_list.selection_set(selected_index)
            self._task_refreshing = False

        task = TASK_DEFS[selected_index]
        goal = self.task_goal_var.get().strip()
        missing = []
        if not task["implemented"]:
            missing = []
        elif task["id"] == "combat":
            if not window_ok:
                missing.append("锁定目标窗口")
            if enemy_count == 0:
                missing.append("抓取目标模板")
        elif task["id"] == "route":
            if not window_ok:
                missing.append("锁定目标窗口")
            if not markers:
                missing.append("添加场景模板")
            if not transitions:
                missing.append("配置跳转流程")
            if unmarked_scenes:
                missing.append("为 " + "、".join(unmarked_scenes[:3]) + " 添加场景标记")
            if not goal:
                missing.append("选择目标场景")
            if self.dry_var.get():
                missing.append("关闭仅观察")
        elif task["id"] == "replay":
            if not window_ok:
                missing.append("锁定目标窗口")
            if not session_ok:
                missing.append("在录制回放页录制或选择会话")
            if self.dry_var.get():
                missing.append("关闭仅观察")

        busy_combat = bool(snapshot and (snapshot.get("running") or snapshot.get("pending")))
        can_start = (
            task["implemented"]
            and not missing
            and not self._task_running
            and not aux_busy
            and not (task["id"] == "combat" and busy_combat)
        )
        self.task_start_button.configure(state="normal" if can_start else "disabled")
        pause_active = (
            self._task_running
            or busy_combat
            or self._replay_running
            or self.recorder is not None
            or self._record_pending
        )
        self.task_pause_button.configure(state="normal" if pause_active else "disabled")

        if not task["implemented"]:
            self.task_hint_var.set("该功能尚未实现，当前版本不提供。")
            self.task_hint_label.configure(fg=WARN)
        elif missing:
            self.task_hint_var.set("还需要: " + "、".join(missing) + "。可使用下方按钮去校准或配置。")
            self.task_hint_label.configure(fg=WARN)
        else:
            hint = "检查通过，可以开始。"
            if task["id"] == "combat" and not hp_ok:
                hint += " 提示：状态条1未校准，自动恢复和受击后撤不会生效。"
            if task["id"] == "combat" and not skills_ok:
                hint += " 快捷栏未校准，将仅按内置冷却执行快捷操作。"
            if task["id"] == "replay":
                hint += " 可在“录制回放页”调整速度、循环次数和间隔。"
            if aux_busy:
                hint += " 当前有录制/回放/流程任务在运行，停止后才能开始。"
            self.task_hint_var.set(hint)
            self.task_hint_label.configure(fg=WARN if aux_busy or not hp_ok else ACCENT)

        if self._record_pending:
            remaining = max(0, int(math.ceil(self._record_countdown_end - time.perf_counter())))
            self.task_status_var.set(f"录制倒计时 {remaining} 秒，请切到目标窗口")
        elif self.recorder is not None:
            recorder_status = self.recorder.status()
            self.task_status_var.set(
                f"录制中 · {recorder_status['elapsed']:.1f}s · {recorder_status['event_count']} 事件"
            )
        elif self._replay_running:
            self.task_status_var.set(self._replay_progress or "回放中...")
        elif self.current_task == "replay":
            if self._replay_message:
                self.task_status_var.set(self._replay_message)
            elif session_ok:
                self.task_status_var.set(f"已选择会话: {Path(self.selected_session).name}")
            else:
                self.task_status_var.set("请在“录制回放页”录制或选择会话")
        elif self._task_running:
            self.task_status_var.set(f"流程任务: {self._task_phase or '执行中'}")
        elif self.current_task == "route" and self._task_message:
            self.task_status_var.set(self._task_message)
        elif self.current_task == "combat" and snapshot:
            state_data = snapshot.get("state") or {}
            hp = state_data.get("hp")
            enemy_count = len(state_data.get("enemies") or [])
            hp_text = "-" if hp is None else f"{hp:.2f}"
            if snapshot.get("pending"):
                self.task_status_var.set(f"自动操作倒计时 {math.ceil(snapshot.get('remaining', 0))} 秒")
            elif snapshot.get("running"):
                self.task_status_var.set(f"自动操作运行中 · HP {hp_text} · 目标 {enemy_count}")
            elif snapshot.get("active"):
                self.task_status_var.set(f"自动操作已暂停 · HP {hp_text} · 目标 {enemy_count}")
            else:
                self.task_status_var.set("未连接目标窗口")
        elif self._task_message and self._task_message != self._shown_task_message:
            self.task_status_var.set(self._task_message)
            self._shown_task_message = self._task_message
        elif not task["implemented"]:
            self.task_status_var.set("请选择已实现的任务")
        else:
            self.task_status_var.set("等待开始")

    def _start_selected_task(self):
        task = TASK_DEFS[self._selected_task_index()]
        if not task["implemented"]:
            messagebox.showinfo("开发中", "该任务尚未实现。", parent=self)
            return
        if self._task_running or self._record_pending or self.recorder is not None or self._replay_running:
            messagebox.showwarning("占用中", "当前有录制、回放或流程任务在运行，请先停止。", parent=self)
            return
        if task["id"] == "combat":
            if not self.engine.active:
                self._rebuild_engine()
                if not self.engine.active:
                    messagebox.showerror("无法启动", self.engine.last_error or "未找到目标窗口", parent=self)
                    return
            self._task_message = ""
            self._shown_task_message = ""
            self.engine.request_start()
            self.task_status_var.set("自动操作已启动，请切回目标窗口")
            self._log("任务中心: 启动自动操作")
            return
        if task["id"] == "route":
            goal = self.task_goal_var.get().strip()
            if not goal:
                messagebox.showwarning("缺少目标", "请先选择目标场景。", parent=self)
                return
            if self.dry_var.get():
                messagebox.showwarning("仅观察模式", "流程任务需要真实按键，请先关闭“仅观察”。", parent=self)
                return
            self._start_route_task(goal)
            return
        if task["id"] == "replay":
            if not self.selected_session:
                messagebox.showwarning("没有会话", "请先在“录制回放页”录制或选择一个会话。", parent=self)
                self._show_replay_tab()
                return
            if self.dry_var.get():
                messagebox.showwarning("仅观察模式", "回放需要真实按键，请先关闭“仅观察”。", parent=self)
                return
            self._start_replay(self.selected_session)

    def _start_route_task(self, goal):
        if self._task_running:
            return
        if self._record_pending or self.recorder is not None or self._replay_running:
            messagebox.showwarning("占用中", "请先停止录制或回放，再启动流程任务。", parent=self)
            return
        if not self.engine.active:
            self._rebuild_engine()
            if not self.engine.active:
                messagebox.showerror("无法启动", self.engine.last_error or "未找到目标窗口", parent=self)
                return
        recognizer = SceneRecognizer(self.config, self.root)
        graph = MapGraph(self.config)
        if not recognizer.enabled:
            messagebox.showwarning("缺少场景模板", "请先在“场景 / 流程管理”中添加场景标记。", parent=self)
            return
        if not graph.transitions:
            messagebox.showwarning("缺少流程", "请先在“场景 / 流程管理”中配置跳转流程。", parent=self)
            return
        flow_scenes = set()
        for transition in graph.transitions:
            flow_scenes.add(transition.from_scene)
            flow_scenes.add(transition.to_scene)
        unmarked = sorted(flow_scenes - set(recognizer.scene_ids))
        if unmarked:
            messagebox.showwarning(
                "缺少场景标记",
                "以下场景没有标记图片，跳转无法确认：\n" + "、".join(unmarked),
                parent=self,
            )
            return
        if goal not in recognizer.scene_ids:
            messagebox.showwarning("目标无效", f"目标场景 {goal} 没有可用模板。", parent=self)
            return
        self.engine.request_pause("流程任务，自动操作暂停")
        self._task_abort.clear()
        self._task_running = True
        self._task_phase = "准备"
        self._task_message = ""
        self._shown_task_message = ""
        self.task_status_var.set("流程任务准备中...")
        self._log(f"任务中心: 启动流程导航 -> {goal}")
        engine = self.engine
        config = self.config
        root = self.root
        abort = self._task_abort
        engine.set_external_lock(True, "流程任务占用输入，自动操作暂停")

        def worker():
            try:
                engine.window.activate()
                for remaining in range(3, 0, -1):
                    if abort.is_set():
                        self._task_message = "流程任务已停止"
                        self._log_from_thread("流程任务已停止")
                        return
                    self._task_phase = f"倒计时 {remaining} 秒"
                    self._log_from_thread(f"流程任务 {remaining} 秒后开始，请保持目标窗口在前台")
                    time.sleep(1.0)
                engine.window.activate()
                recognizer_local = SceneRecognizer(config, root)
                graph_local = MapGraph(config)
                executor = TransitionExecutor(
                    config,
                    recognizer_local,
                    engine.inputs,
                    engine.current_frame,
                    engine.window.client_rect,
                    log=self._log_from_thread,
                    abort_event=abort,
                )
                router = DungeonRouter(
                    graph_local,
                    recognizer_local,
                    executor,
                    engine.current_frame,
                    log=self._log_from_thread,
                    abort_event=abort,
                )
                self._task_phase = "执行中"
                result = router.run_to(goal)
                prefix = "流程任务成功: " if result.success else "流程任务失败: "
                self._task_message = prefix + result.message
                self._log_from_thread(self._task_message)
            except Exception as exc:
                self._task_message = f"流程任务异常: {exc}"
                self._log_from_thread("流程任务异常: " + traceback.format_exc(limit=2))
            finally:
                engine.inputs.release_all()
                engine.set_external_lock(False)
                self._task_running = False
                self._task_phase = "结束"

        self._task_thread = threading.Thread(target=worker, daemon=True, name="route-task")
        self._task_thread.start()

    def _stop_task(self):
        if self._record_pending or self.recorder is not None:
            self._stop_recording()
        if self._replay_running:
            self._stop_replay()
        if self._task_running:
            self._task_abort.set()
            self._task_message = "正在停止流程任务..."
            self._shown_task_message = ""
            self._log("任务中心: 停止流程任务")
        if self.engine is not None:
            self.engine.request_pause("任务中心暂停")
        if not self._task_running and not self._replay_running:
            self.task_status_var.set("已暂停")

    def _open_scene_manager(self):
        if self._task_running or self._record_pending or self.recorder is not None or self._replay_running:
            messagebox.showwarning("任务运行中", "请先停止录制、回放或流程任务，再修改场景与流程。", parent=self)
            return
        try:
            if self._scene_manager is not None and self._scene_manager.winfo_exists():
                self._scene_manager.lift()
                return
        except tk.TclError:
            self._scene_manager = None
        if self.engine is not None and (self.engine.running or self.engine.pending_start):
            self.engine.request_pause("打开场景与流程管理，已暂停")
        self._scene_manager = SceneManagerWindow(
            self,
            self.root,
            self.config,
            self.config_path,
            get_frame=self._capture_scene_frame,
            apply_config=self._apply_scene_config,
            log=self._log,
        )

    def _capture_scene_frame(self):
        if self.engine is None or not self.engine.active:
            return None
        self.engine.request_pause("场景截图，已暂停")
        if self.config["window"].get("auto_activate", True) and self.engine.window.hwnd:
            self.engine.window.activate()
            time.sleep(0.35)
        frame = self.engine.current_frame()
        return None if frame is None else frame.copy()

    def _apply_scene_config(self, config):
        if self._task_running or self._record_pending or self.recorder is not None or self._replay_running:
            return False
        self.config = config
        save_config(self.config_path, self.config)
        self.title_var.set(self.config["window"].get("title_keyword", ""))
        self.dry_var.set(bool(self.config["safety"].get("dry_run", False)))
        self._log("场景/流程配置已保存，正在重新加载引擎")
        self._rebuild_engine()
        self._refresh_task_status(force=True)
        return True

    def _log_from_thread(self, message):
        self.log_queue.put(str(message))

    def _append_log(self, message):
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            self._log_handle.write(line)
            self._log_handle.flush()
        except Exception:
            pass

    def _log(self, message):
        self._append_log(message)

    def _drain_logs(self):
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                return
            self._append_log(message)

    def _tick(self):
        self._drain_logs()
        now = time.perf_counter()
        if self.engine is not None:
            snapshot = self.engine.snapshot()
            self._update_status(snapshot)
            if now - self._last_preview_at >= 0.09:
                self._render_preview(snapshot)
                self._last_preview_at = now
            if now - self._last_task_refresh >= 0.8:
                self._refresh_task_status(snapshot)
                self._last_task_refresh = now
        if now - self._last_replay_ui >= 0.25:
            self._update_replay_ui()
            self._last_replay_ui = now
        self._sync_replay_highlight()
        self.after(90, self._tick)

    def _update_status(self, snapshot):
        if not snapshot["active"]:
            state = "未连接窗口"
        elif snapshot["pending"]:
            state = f"倒计时 {math.ceil(snapshot['remaining'])} 秒"
        elif snapshot["running"]:
            state = "运行中"
        else:
            state = "已暂停"
        self.run_state_var.set(state)
        window_text = snapshot.get("window") or snapshot.get("error") or "未锁定"
        self.window_info_var.set(window_text)
        if snapshot["active"]:
            self.capture_var.set(f"{snapshot['backend']} · {snapshot['fps']:.0f} fps")
        else:
            self.capture_var.set("-")
        state_data = snapshot.get("state") or {}
        hp = state_data.get("hp")
        mp = state_data.get("mp")
        self.hp_var.set("-" if hp is None else f"{hp:.2f}")
        self.mp_var.set("-" if mp is None else f"{mp:.2f}")
        self.enemies_var.set(str(len(state_data.get("enemies") or [])))
        skills = state_data.get("skills") or []
        if skills:
            ready = sum(1 for slot in skills if slot.get("ready"))
            self.skills_var.set(f"{ready}/{len(skills)}")
        else:
            self.skills_var.set("未校准")
        scene_state = snapshot.get("scene")
        if not snapshot.get("scene_enabled"):
            self.scene_var.set("未配置")
        elif scene_state is None:
            self.scene_var.set("未知")
        elif scene_state.loading:
            self.scene_var.set("加载中")
        elif scene_state.scene_id:
            self.scene_var.set(f"{scene_state.scene_id}  {scene_state.score:.2f}")
        else:
            self.scene_var.set("未知")
        self.action_var.set(snapshot.get("action", "-"))
        busy = snapshot["running"] or snapshot["pending"]
        self.start_button.configure(state="disabled" if busy else "normal")
        self.pause_button.configure(state="normal" if busy else "disabled")

    def _render_preview(self, snapshot):
        frame = snapshot.get("frame")
        canvas = self.preview_canvas
        canvas_w = max(1, canvas.winfo_width())
        canvas_h = max(1, canvas.winfo_height())
        if frame is None:
            if self._last_frame_ref is not None or self._force_preview:
                canvas.delete("all")
                canvas.create_text(
                    canvas_w // 2,
                    canvas_h // 2,
                    text="未获取到目标画面\n请锁定窗口，并确认目标窗口使用无边框窗口模式",
                    fill=MUTED,
                    font=(FONT, 11),
                    justify="center",
                )
                self._last_frame_ref = None
                self._force_preview = False
            return
        current = self._replay_current if self._replay_running else None
        replay_key = (current.get("loop"), current.get("index")) if current else None
        size = (canvas_w, canvas_h)
        if (
            frame is self._last_frame_ref
            and size == self._last_canvas_size
            and not self._force_preview
            and replay_key == self._last_replay_event_key
        ):
            return
        self._last_frame_ref = frame
        self._last_canvas_size = size
        self._last_replay_event_key = replay_key
        self._force_preview = False
        height, width = frame.shape[:2]
        scale = min(canvas_w / width, canvas_h / height, 1.0)
        display_w = max(1, int(width * scale))
        display_h = max(1, int(height * scale))
        if scale < 1.0:
            small = cv2.resize(frame, (display_w, display_h), interpolation=cv2.INTER_AREA)
        else:
            small = frame.copy()
        if self.overlay_enabled:
            draw_overlay(small, snapshot.get("state"), None, scale)
        if current:
            self._draw_replay_overlay(small, current.get("event") or {})
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        canvas.delete("all")
        canvas.create_image(canvas_w // 2, canvas_h // 2, image=photo, anchor="center")
        self._preview_photo = photo

    def _draw_replay_overlay(self, canvas, event):
        kind = str(event.get("type") or "")
        key = str(event.get("key") or "")
        button = str(event.get("button") or "")
        text = f"REPLAY {kind}"
        if key:
            text += f"  {key.upper()}"
        if button:
            text += f"  {button.upper()}"
        pos = event.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            text += f"  ({float(pos[0]) * 100:.0f}%, {float(pos[1]) * 100:.0f}%)"
        height, width = canvas.shape[:2]
        cv2.rectangle(canvas, (0, max(0, height - 36)), (min(width, 520), height), (0, 0, 0), -1)
        cv2.putText(
            canvas,
            text,
            (10, max(16, height - 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            px = int(float(pos[0]) * width)
            py = int(float(pos[1]) * height)
            cv2.circle(canvas, (px, py), 16, (0, 255, 255), 2)
            cv2.drawMarker(canvas, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 28, 2)

    def _pick_window(self):
        windows = [info for info in list_windows() if info["title"]]
        if not windows:
            messagebox.showinfo("枚举窗口", "没有找到可见窗口", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("选择目标窗口")
        dialog.configure(bg=BG)
        dialog.geometry("680x460")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="双击选择目标窗口", style="Muted.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        listbox = tk.Listbox(
            dialog,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground=SELECT_FG,
            relief="flat",
            highlightthickness=0,
            font=(FONT, 9),
        )
        listbox.pack(fill="both", expand=True, padx=12, pady=4)
        for info in windows:
            listbox.insert("end", f"{info['title']}    [{info['class']}]")

        def choose(_event=None):
            selection = listbox.curselection()
            if not selection:
                return
            self.title_var.set(windows[int(selection[0])]["title"])
            dialog.destroy()

        listbox.bind("<Double-Button-1>", choose)
        ttk.Button(dialog, text="使用选中窗口", command=choose).pack(pady=10)

    def _lock_window(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("提示", "请填写目标窗口标题关键字，或点击“枚举窗口”选择。", parent=self)
            return
        if self._task_running or self._record_pending or self.recorder is not None or self._replay_running:
            messagebox.showwarning("占用中", "请先停止录制、回放或任务，再重新锁定窗口。", parent=self)
            return
        self.config["window"]["title_keyword"] = title
        save_config(self.config_path, self.config)
        self._log(f"锁定窗口关键字: {title}")
        self._rebuild_engine()

    def _toggle_run(self):
        if self.engine is None:
            return
        if self._task_running or self._record_pending or self.recorder is not None or self._replay_running:
            messagebox.showwarning("占用中", "请先停止录制、回放或流程任务。", parent=self)
            return
        if not self.engine.active:
            self._rebuild_engine()
            if not self.engine.active:
                messagebox.showerror("无法启动", self.engine.last_error or "未找到目标窗口", parent=self)
                return
        if self.engine.running or self.engine.pending_start:
            self.engine.request_pause("已暂停")
        else:
            self.engine.request_start()

    def _pause(self):
        if self.engine is not None:
            self.engine.request_pause("已暂停")

    def _toggle_dry(self):
        if self._task_running or self._replay_running or self.recorder is not None or self._record_pending:
            self.dry_var.set(not bool(self.dry_var.get()))
            messagebox.showwarning("任务运行中", "请先停止录制、回放或流程任务，再切换“仅观察”模式。", parent=self)
            return
        value = bool(self.dry_var.get())
        if self.engine is not None:
            self.engine.set_dry_run(value)
        self.config["safety"]["dry_run"] = value
        save_config(self.config_path, self.config)
        self._log("仅观察模式: " + ("开启" if value else "关闭"))
        self._refresh_task_status(force=True)

    def _save_snapshot(self):
        if self.engine is None:
            return
        frame = self.engine.snapshot().get("frame")
        if frame is None:
            messagebox.showwarning("没有画面", "当前没有可保存的目标画面。", parent=self)
            return
        folder = self.root / "captures"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"gui_snapshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        imwrite(path, frame)
        self._log(f"截图已保存: {path}")

    def _open_calibration(self):
        if self.engine is None:
            return
        snapshot = self.engine.snapshot()
        frame = snapshot.get("frame")
        if frame is None:
            messagebox.showwarning("无法校准", "当前没有目标画面，请先锁定窗口并确认预览正常。", parent=self)
            return
        if self.engine.running or self.engine.pending_start:
            self.engine.request_pause("打开校准工具，已暂停")
        CalibrationWindow(
            self,
            self.config,
            self.root,
            frame.copy(),
            self.config_path,
            on_saved=self._on_calibration_saved,
        )

    def _on_calibration_saved(self):
        self.config = load_config(self.config_path)
        self.dry_var.set(bool(self.config["safety"].get("dry_run", False)))
        self.title_var.set(self.config["window"].get("title_keyword", ""))
        self._log("校准已保存，正在重新加载配置与模板")
        self._rebuild_engine()

    def _poll_hotkeys(self):
        if not self.engine.active:
            toggle_key = self.config["hotkeys"].get("toggle", "f8")
            if self.hotkeys.pressed(toggle_key):
                self._toggle_run()
        any_running = (
            self._task_running
            or self._replay_running
            or self.recorder is not None
            or self._record_pending
        )
        pause_key = self.config["hotkeys"].get("quit", "f9")
        if any_running and self.hotkeys.pressed(pause_key):
            self._stop_task()

        record_toggle = self.config["hotkeys"].get("record_toggle", "f6")
        record_stop = self.config["hotkeys"].get("record_stop", "f7")
        if self.hotkeys.pressed(record_toggle):
            if self._record_pending or self.recorder is not None:
                self._stop_recording()
            else:
                self._start_recording()
        if self.hotkeys.pressed(record_stop):
            if self._record_pending or self.recorder is not None:
                self._stop_recording()

        checkpoint_key = self.config["hotkeys"].get("record_checkpoint", "f5")
        if self.hotkeys.pressed(checkpoint_key):
            if (
                self.recorder is not None
                and self.engine is not None
                and self.engine.window.is_foreground()
                and self.recorder.insert_checkpoint("sync")
            ):
                self._record_message = "已插入同步断点"
                self._log("录制: 插入同步断点")

        debug_key = self.config["hotkeys"].get("debug", "f1")
        if self.hotkeys.pressed(debug_key):
            self.overlay_enabled = not self.overlay_enabled
            self._force_preview = True
            self._log("叠加层: " + ("开启" if self.overlay_enabled else "关闭"))
        self.after(100, self._poll_hotkeys)

    def report_callback_exception(self, exc, value, tb):
        text = "".join(traceback.format_exception(exc, value, tb))
        try:
            crash = self.root / "logs" / "gui_crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            with open(crash, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except Exception:
            pass
        self._append_log("界面异常: " + text.strip().splitlines()[-1])

    def _on_close(self):
        self._task_abort.set()
        self._replay_abort.set()
        if self._record_after_id is not None:
            try:
                self.after_cancel(self._record_after_id)
            except Exception:
                pass
            self._record_after_id = None
        if self.recorder is not None:
            try:
                self.recorder.stop()
            except Exception:
                pass
            self.recorder = None
        try:
            if self.engine is not None:
                self.engine.stop()
        finally:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self.destroy()


def main():
    set_dpi_awareness()
    app = AutomationApp()
    app.mainloop()


if __name__ == "__main__":
    sys.exit(main())
