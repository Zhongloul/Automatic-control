import copy
import json
import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2
from PIL import Image, ImageTk

from .imageio import imread
from .map_graph import MapGraph
from .scene import (
    SceneRecognizer,
    delete_marker,
    delete_scene,
    list_markers,
    list_scene_ids,
    rename_scene,
    save_marker,
    scenes_dir,
    validate_scene_id,
)
from .theme import ACCENT, BG, CANVAS_BG, DANGER, FG, FIELD, MUTED, PANEL, SELECT_FG, WARN
from .ui_common import hide_window_if_testing

FONT = "Microsoft YaHei UI"

ACTION_TYPES = ("key_tap", "key", "key_hold", "wait", "click", "move", "run_session")


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


def normalize_image(image):
    if image is None:
        return None
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def validate_actions(actions):
    if not isinstance(actions, list):
        return False, "动作必须是 JSON 数组"
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            return False, f"第 {index} 个动作不是对象"
        kind = str(action.get("type") or "").lower()
        if kind not in ACTION_TYPES:
            return False, f"第 {index} 个动作类型无效: {kind}"
        if kind in ("key_tap", "key", "key_hold") and not action.get("key"):
            return False, f"第 {index} 个动作缺少 key"
        if kind == "key_hold" and not isinstance(action.get("seconds"), (int, float)):
            return False, f"第 {index} 个 key_hold 缺少 seconds"
        if kind == "wait" and not isinstance(action.get("seconds"), (int, float)):
            return False, f"第 {index} 个 wait 缺少 seconds"
        if kind == "run_session" and not str(action.get("session") or "").strip():
            return False, f"第 {index} 个 run_session 缺少 session"
        if kind in ("click", "move"):
            pos = action.get("pos")
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                return False, f"第 {index} 个动作 pos 必须是 [x, y]"
            try:
                x, y = float(pos[0]), float(pos[1])
            except (TypeError, ValueError):
                return False, f"第 {index} 个动作 pos 无效"
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                return False, f"第 {index} 个动作 pos 超出 0~1"
    return True, ""


class MarkerCaptureWindow(tk.Toplevel):
    def __init__(self, master, image, title="框选场景标记", instruction="拖拽框住一个稳定出现的场景标记"):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(True, True)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = max(1000, int(screen_w * 0.92))
        height = max(650, int(screen_h * 0.90))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(1000, 650)
        self.image = normalize_image(image)
        self.image_height, self.image_width = self.image.shape[:2]
        self.result = None
        self.photo = None
        self.scale = 1.0
        self.fit_scale = 1.0
        self.max_scale = 2.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.display_width = 1
        self.display_height = 1
        self.start = None
        self.end = None
        self.dragging = False
        self.panning = False
        self.pan_start = None
        self.pan_origin = (0.0, 0.0)
        self._resize_after_id = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        canvas_frame = tk.Frame(self, bg=CANVAS_BG)
        canvas_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.canvas = tk.Canvas(
            canvas_frame,
            bg=CANVAS_BG,
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_drag)
        self.canvas.bind("<ButtonRelease-3>", self._on_pan_end)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        panel = ttk.Frame(self, style="Panel.TFrame", padding=14)
        panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="场景标记", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            panel,
            text=instruction,
            style="Panel.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 10))
        self.size_var = tk.StringVar(value="尚未选择区域")
        ttk.Label(panel, textvariable=self.size_var, style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 8))

        zoom_frame = ttk.Frame(panel, style="Panel.TFrame")
        zoom_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        zoom_frame.columnconfigure(0, weight=1)
        zoom_frame.columnconfigure(1, weight=1)
        ttk.Button(zoom_frame, text="放大 +", command=self._zoom_in).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(zoom_frame, text="缩小 -", command=self._zoom_out).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(zoom_frame, text="适应窗口", command=self._fit_image).grid(
            row=1, column=0, sticky="ew", padx=(0, 3), pady=(4, 0)
        )
        ttk.Button(zoom_frame, text="1:1", command=self._zoom_100).grid(
            row=1, column=1, sticky="ew", padx=(3, 0), pady=(4, 0)
        )
        self.zoom_var = tk.StringVar(value="缩放 100%")
        ttk.Label(zoom_frame, textvariable=self.zoom_var, style="Panel.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        self.confirm_button = ttk.Button(panel, text="保存标记", style="Accent.TButton", command=self._confirm)
        self.confirm_button.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(panel, text="整张图片作为标记", command=self._use_full).grid(row=5, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(panel, text="重新选择", command=self._clear).grid(row=6, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(panel, text="取消", command=self._cancel).grid(row=7, column=0, sticky="ew")
        ttk.Label(
            panel,
            text="操作：鼠标滚轮缩放，右键或中键拖动平移，左键框选标记。",
            style="Panel.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=8, column=0, sticky="w", pady=(14, 0))
        ttk.Label(
            panel,
            text="提示：标记要选场景名、场景 UI 等固定元素，不要选会消失的怪物或门。",
            style="Panel.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=9, column=0, sticky="w", pady=(8, 0))

        self.update()
        self._fit_image()
        self.transient(master)
        self.grab_set()
        self._update_confirm()

    def _compute_fit_scale(self):
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        return min(canvas_w / max(1, self.image_width), canvas_h / max(1, self.image_height), 1.0)

    def _fit_image(self):
        self.fit_scale = self._compute_fit_scale()
        max_pixels_scale = math.sqrt(12_000_000 / max(1, self.image_width * self.image_height))
        self.max_scale = max(self.fit_scale, min(2.0, max_pixels_scale))
        self.scale = self.fit_scale
        self._center_image()
        self._render_image()

    def _zoom_100(self):
        target = min(1.0, self.max_scale)
        self._set_scale(max(self.fit_scale, target))

    def _zoom_in(self):
        self._set_scale(self.scale * 1.25)

    def _zoom_out(self):
        self._set_scale(self.scale / 1.25)

    def _center_image(self):
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        self.display_width = max(1, int(round(self.image_width * self.scale)))
        self.display_height = max(1, int(round(self.image_height * self.scale)))
        self.offset_x = (canvas_w - self.display_width) / 2.0
        self.offset_y = (canvas_h - self.display_height) / 2.0
        self._clamp_offset()

    def _clamp_offset(self):
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        display_w = max(1, int(round(self.image_width * self.scale)))
        display_h = max(1, int(round(self.image_height * self.scale)))
        self.display_width = display_w
        self.display_height = display_h
        if display_w <= canvas_w:
            self.offset_x = (canvas_w - display_w) / 2.0
        else:
            self.offset_x = min(0.0, max(canvas_w - display_w, self.offset_x))
        if display_h <= canvas_h:
            self.offset_y = (canvas_h - display_h) / 2.0
        else:
            self.offset_y = min(0.0, max(canvas_h - display_h, self.offset_y))

    def _set_scale(self, scale, anchor=None):
        target = max(self.fit_scale, min(self.max_scale, float(scale)))
        if abs(target - self.scale) < 1e-6:
            return
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        if anchor is None:
            anchor = (canvas_w / 2.0, canvas_h / 2.0)
        image_x = (anchor[0] - self.offset_x) / self.scale
        image_y = (anchor[1] - self.offset_y) / self.scale
        self.scale = target
        self.offset_x = anchor[0] - image_x * self.scale
        self.offset_y = anchor[1] - image_y * self.scale
        self._clamp_offset()
        self._render_image()

    def _render_image(self):
        self.display_width = max(1, int(round(self.image_width * self.scale)))
        self.display_height = max(1, int(round(self.image_height * self.scale)))
        self._clamp_offset()
        if self.display_width != self.image_width or self.display_height != self.image_height:
            interpolation = cv2.INTER_AREA if self.scale < 1.0 else cv2.INTER_LINEAR
            resized = cv2.resize(
                self.image,
                (self.display_width, self.display_height),
                interpolation=interpolation,
            )
        else:
            resized = self.image
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("img")
        self.canvas.create_image(self.offset_x, self.offset_y, image=photo, anchor="nw", tags="img")
        self.canvas.tag_lower("img")
        self.photo = photo
        if hasattr(self, "zoom_var"):
            self.zoom_var.set(f"缩放 {self.scale * 100:.0f}%  ·  {self.image_width}x{self.image_height}")
        self._draw()

    def _on_mousewheel(self, event):
        if event.delta == 0:
            return "break"
        factor = 1.18 if event.delta > 0 else 1.0 / 1.18
        self._set_scale(self.scale * factor, (event.x, event.y))
        return "break"

    def _on_canvas_resize(self, _event=None):
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(120, self._apply_resize_fit)

    def _apply_resize_fit(self):
        self._resize_after_id = None
        self._fit_image()

    def _on_pan_start(self, event):
        if (
            self.display_width <= self.canvas.winfo_width()
            and self.display_height <= self.canvas.winfo_height()
        ):
            return
        self.panning = True
        self.pan_start = (event.x, event.y)
        self.pan_origin = (self.offset_x, self.offset_y)
        self.canvas.configure(cursor="fleur")

    def _on_pan_drag(self, event):
        if not self.panning or self.pan_start is None:
            return
        old_x, old_y = self.offset_x, self.offset_y
        self.offset_x = self.pan_origin[0] + (event.x - self.pan_start[0])
        self.offset_y = self.pan_origin[1] + (event.y - self.pan_start[1])
        self._clamp_offset()
        dx = self.offset_x - old_x
        dy = self.offset_y - old_y
        if dx or dy:
            self.canvas.move("img", dx, dy)
            self.canvas.move("sel", dx, dy)

    def _on_pan_end(self, _event=None):
        self.panning = False
        self.pan_start = None
        self.canvas.configure(cursor="")

    def _inside(self, x, y):
        return (
            self.offset_x <= x <= self.offset_x + self.display_width
            and self.offset_y <= y <= self.offset_y + self.display_height
        )

    def _to_image(self, x, y):
        ix = (x - self.offset_x) / self.scale
        iy = (y - self.offset_y) / self.scale
        ix = min(max(ix, 0.0), self.image_width - 1.0)
        iy = min(max(iy, 0.0), self.image_height - 1.0)
        return ix, iy

    def _to_canvas(self, x, y):
        return self.offset_x + x * self.scale, self.offset_y + y * self.scale

    def _rect(self):
        if self.start is None or self.end is None:
            return None
        x1, x2 = sorted((self.start[0], self.end[0]))
        y1, y2 = sorted((self.start[1], self.end[1]))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _on_press(self, event):
        if not self._inside(event.x, event.y):
            return
        self.start = self._to_image(event.x, event.y)
        self.end = self.start
        self.dragging = True
        self._draw()

    def _on_drag(self, event):
        if not self.dragging:
            return
        self.end = self._to_image(event.x, event.y)
        self._draw()

    def _on_release(self, event):
        if not self.dragging:
            return
        self.end = self._to_image(event.x, event.y)
        self.dragging = False
        self._draw()

    def _draw(self):
        self.canvas.delete("sel")
        rect = self._rect()
        if rect:
            x1, y1, x2, y2 = rect
            c1 = self._to_canvas(x1, y1)
            c2 = self._to_canvas(x2, y2)
            self.canvas.create_rectangle(c1[0], c1[1], c2[0], c2[1], outline=ACCENT, width=2, tags="sel")
            self.size_var.set(f"标记尺寸: {x2 - x1} x {y2 - y1}")
        else:
            self.size_var.set("尚未选择区域")
        self._update_confirm()

    def _update_confirm(self):
        self.confirm_button.configure(state="normal" if self._rect() else "disabled")

    def _clear(self):
        self.start = None
        self.end = None
        self.dragging = False
        self._draw()

    def _confirm(self):
        rect = self._rect()
        if rect is None:
            return
        self.result = rect
        self.grab_release()
        self.destroy()

    def _use_full(self):
        self.result = (0, 0, self.image_width, self.image_height)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


class TransitionEditorWindow(tk.Toplevel):
    def __init__(self, master, transition, scene_ids, on_save):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title("编辑跳转")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("720x680")
        self.transition = copy.deepcopy(transition) if transition else {}
        self.on_save = on_save

        panel = ttk.Frame(self, style="Panel.TFrame", padding=14)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(9, weight=1)

        data = self.transition
        self.name_var = tk.StringVar(value=str(data.get("name") or ""))
        self.from_var = tk.StringVar(value=str(data.get("from") or ""))
        self.to_var = tk.StringVar(value=str(data.get("to") or ""))
        self.retries_var = tk.StringVar(value=str(data.get("retries", 1)))
        self.from_timeout_var = tk.StringVar(value=str(data.get("from_timeout_s", 6.0)))
        self.loading_timeout_var = tk.StringVar(value=str(data.get("loading_timeout_s", 8.0)))
        self.scene_timeout_var = tk.StringVar(value=str(data.get("scene_timeout_s", 25.0)))
        self.settle_var = tk.StringVar(value=str(data.get("settle_s", 0.4)))
        self.require_from_var = tk.BooleanVar(value=bool(data.get("require_from_scene", True)))
        self.require_loading_var = tk.BooleanVar(value=bool(data.get("require_loading", False)))

        def add_row(row, label, widget):
            ttk.Label(panel, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=3)
            widget.grid(row=row, column=1, sticky="ew", pady=3)

        add_row(0, "名称", ttk.Entry(panel, textvariable=self.name_var))
        add_row(1, "起始场景", ttk.Combobox(panel, textvariable=self.from_var, values=scene_ids, state="normal"))
        add_row(2, "目标场景", ttk.Combobox(panel, textvariable=self.to_var, values=scene_ids, state="normal"))
        add_row(3, "重试次数", ttk.Spinbox(panel, from_=0, to=20, textvariable=self.retries_var))

        options = ttk.Frame(panel, style="Panel.TFrame")
        dark_checkbutton(options, "必须先识别起始场景", self.require_from_var).pack(side="left", padx=(0, 12))
        dark_checkbutton(options, "等待加载画面", self.require_loading_var).pack(side="left")
        add_row(4, "选项", options)

        add_row(5, "起始等待(s)", ttk.Entry(panel, textvariable=self.from_timeout_var))
        add_row(6, "加载等待(s)", ttk.Entry(panel, textvariable=self.loading_timeout_var))
        add_row(7, "目标等待(s)", ttk.Entry(panel, textvariable=self.scene_timeout_var))
        add_row(8, "动作后停顿(s)", ttk.Entry(panel, textvariable=self.settle_var))

        actions_frame = ttk.Frame(panel, style="Panel.TFrame")
        actions_frame.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        actions_frame.columnconfigure(0, weight=1)
        actions_frame.rowconfigure(1, weight=1)
        ttk.Label(
            actions_frame,
            text='动作 JSON（支持 key_tap / key_hold / wait / click / move / run_session）',
            style="Panel.TLabel",
        ).grid(row=0, column=0, sticky="w")
        actions_text = json.dumps(data.get("actions") or [], ensure_ascii=False, indent=2)
        self.actions_text = tk.Text(
            actions_frame,
            height=10,
            bg=FIELD,
            exportselection=False,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            wrap="none",
            font=("Consolas", 9),
        )
        self.actions_text.grid(row=1, column=0, sticky="nsew")
        self.actions_text.insert("1.0", actions_text)

        self.status_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self.status_var, style="Panel.TLabel", foreground=WARN).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="校验动作", command=self._validate).pack(side="left")
        ttk.Button(buttons, text="取消", command=self._cancel).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="保存", style="Accent.TButton", command=self._save).pack(side="right")

        self.transient(master)
        self.grab_set()

    def _collect(self):
        name = self.name_var.get().strip()
        from_scene = self.from_var.get().strip()
        to_scene = self.to_var.get().strip()
        if not from_scene or not to_scene:
            return None, "起始场景和目标场景不能为空"
        try:
            actions = json.loads(self.actions_text.get("1.0", "end").strip() or "[]")
        except json.JSONDecodeError as exc:
            return None, f"动作 JSON 解析失败: {exc}"
        ok, message = validate_actions(actions)
        if not ok:
            return None, message
        try:
            retries = max(0, int(self.retries_var.get()))
            from_timeout = max(0.1, float(self.from_timeout_var.get()))
            loading_timeout = max(0.1, float(self.loading_timeout_var.get()))
            scene_timeout = max(0.1, float(self.scene_timeout_var.get()))
            settle = max(0.0, float(self.settle_var.get()))
        except ValueError as exc:
            return None, f"数值字段无效: {exc}"
        if not name:
            name = f"{from_scene}->{to_scene}"
        data = {
            "name": name,
            "from": from_scene,
            "to": to_scene,
            "actions": actions,
            "retries": retries,
            "require_from_scene": bool(self.require_from_var.get()),
            "require_loading": bool(self.require_loading_var.get()),
            "from_timeout_s": from_timeout,
            "loading_timeout_s": loading_timeout,
            "scene_timeout_s": scene_timeout,
            "settle_s": settle,
        }
        return data, ""

    def _validate(self):
        _data, message = self._collect()
        self.status_var.set(message if message else "动作格式正确")

    def _save(self):
        data, message = self._collect()
        if data is None:
            self.status_var.set(message)
            messagebox.showerror("配置错误", message, parent=self)
            return
        self.on_save(data)
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self.grab_release()
        self.destroy()


class SceneManagerWindow(tk.Toplevel):
    def __init__(self, master, root, config, config_path, get_frame, apply_config, log):
        super().__init__(master)
        hide_window_if_testing(self)
        self.title("场景与流程管理")
        self.configure(bg=BG)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1180, screen_w - 80)
        height = min(820, screen_h - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(1000, 680)
        self.root_dir = Path(root)
        self.config = copy.deepcopy(config)
        self.config_path = Path(config_path)
        self.get_frame = get_frame
        self.apply_config = apply_config
        self.log = log
        self.dirty = False
        self.marker_paths = []
        self.photo = None
        self._flow_iids = {}

        self._build_ui()
        self._refresh_scenes()
        self._refresh_flow()
        self.transient(master)
        self.lift()
        self.after(150, self._refresh_markers)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 0))
        self.scene_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.flow_tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(self.scene_tab, text="场景模板")
        self.notebook.add(self.flow_tab, text="跳转流程")
        self._build_scene_tab()
        self._build_flow_tab()

        bottom = ttk.Frame(self, style="Panel.TFrame", padding=(12, 8))
        bottom.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        bottom.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="添加标记或修改流程后，点击“保存并应用”生效。")
        ttk.Label(bottom, textvariable=self.status_var, style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="保存并应用", style="Accent.TButton", command=self._apply).grid(row=0, column=1)

    def _build_scene_tab(self):
        tab = self.scene_tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="步骤：新增场景 → 从目标窗口抓取或导入图片 → 框选稳定标记 → 保存并应用。子目录名就是场景 ID。",
            style="Panel.TLabel",
            wraplength=1000,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        left = ttk.Frame(tab, style="Panel.TFrame")
        left.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(left, text="场景列表", style="Section.TLabel").pack(anchor="w")
        self.scene_list = tk.Listbox(
            left,
            width=28,
            height=18,
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
        self.scene_list.pack(fill="y", expand=True, pady=(6, 6))
        self.scene_list.bind("<<ListboxSelect>>", self._on_scene_select)
        self.scene_list.bind("<Double-Button-1>", self._rename_selected_scene)
        tk.Label(
            left,
            text="双击场景可重命名",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        buttons = ttk.Frame(left, style="Panel.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="新增场景", command=self._add_scene).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(buttons, text="刷新", command=self._refresh_scenes).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Button(buttons, text="删除场景", command=self._delete_scene).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        right = ttk.Frame(tab, style="Panel.TFrame")
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        ttk.Label(right, text="标记图片", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.marker_list = tk.Listbox(
            right,
            height=5,
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
        self.marker_list.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        self.marker_list.bind("<<ListboxSelect>>", self._on_marker_select)

        marker_buttons = ttk.Frame(right, style="Panel.TFrame")
        marker_buttons.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(marker_buttons, text="从目标画面抓取", command=self._capture_marker).pack(side="left")
        ttk.Button(marker_buttons, text="导入图片", command=self._import_marker).pack(side="left", padx=6)
        ttk.Button(marker_buttons, text="删除标记", command=self._delete_marker).pack(side="left")

        self.preview_canvas = tk.Canvas(right, bg=CANVAS_BG, height=320, highlightthickness=0)
        self.preview_canvas.grid(row=3, column=0, sticky="nsew", pady=(0, 8))

        actions = ttk.Frame(right, style="Panel.TFrame")
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="测试当前画面", command=self._test_current_frame).pack(side="left")
        ttk.Button(actions, text="重新加载识别", command=self._reload_recognizer).pack(side="left", padx=6)

    def _build_flow_tab(self):
        tab = self.flow_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        top = ttk.Frame(tab, style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(2, weight=1)
        ttk.Label(top, text="跳转流程（列表顺序影响同一起点多条边的选择）", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.flow_enabled_var = tk.BooleanVar(value=bool(self.config["map_graph"].get("enabled", False)))
        dark_checkbutton(
            top,
            "启用流程跳转",
            self.flow_enabled_var,
            self._toggle_flow_enabled,
        ).grid(row=0, column=1, padx=(12, 0))
        ttk.Label(
            top,
            text="用上移/下移调整顺序",
            style="Panel.TLabel",
        ).grid(row=0, column=2, sticky="e")

        container = ttk.Frame(tab, style="Panel.TFrame")
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        columns = ("order", "name", "start", "end", "actions", "retries")
        self.flow_tree = ttk.Treeview(container, columns=columns, show="headings", selectmode="browse")
        headings = {
            "order": "顺序",
            "name": "名称",
            "start": "起点",
            "end": "终点",
            "actions": "动作数",
            "retries": "重试",
        }
        widths = {"order": 50, "name": 220, "start": 140, "end": 140, "actions": 70, "retries": 60}
        for column in columns:
            self.flow_tree.heading(column, text=headings[column])
            self.flow_tree.column(column, width=widths[column], anchor="center" if column != "name" else "w")
        self.flow_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.flow_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.flow_tree.configure(yscrollcommand=scrollbar.set)
        self.flow_tree.bind("<Double-Button-1>", lambda _event: self._edit_transition())

        buttons = ttk.Frame(tab, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="新增", command=self._add_transition).pack(side="left")
        ttk.Button(buttons, text="编辑", command=self._edit_transition).pack(side="left", padx=6)
        ttk.Button(buttons, text="删除", command=self._delete_transition).pack(side="left")
        ttk.Button(buttons, text="上移", command=lambda: self._move_transition(-1)).pack(side="left", padx=(18, 6))
        ttk.Button(buttons, text="下移", command=lambda: self._move_transition(1)).pack(side="left")
        ttk.Button(buttons, text="导入 JSON", command=self._import_flow).pack(side="right")
        ttk.Button(buttons, text="导出 JSON", command=self._export_flow).pack(side="right", padx=6)

    def _transitions(self):
        return self.config["map_graph"].setdefault("transitions", [])

    def _all_scene_ids(self):
        ids = set(list_scene_ids(self.root_dir))
        ids.update((self.config.get("scenes") or {}).get("profiles", {}).keys())
        for transition in self._transitions():
            if isinstance(transition, dict):
                for key in ("from", "to"):
                    value = transition.get(key)
                    if value:
                        ids.add(str(value))
        return sorted(ids)

    def _selected_scene(self):
        selection = self.scene_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        ids = getattr(self, "scene_ids", [])
        return ids[index] if 0 <= index < len(ids) else None

    def _refresh_scenes(self, select=None):
        current = select or self._selected_scene()
        ids = self._all_scene_ids()
        self.scene_ids = ids
        self.scene_list.delete(0, "end")
        for scene_id in ids:
            count = len(list_markers(self.root_dir, scene_id))
            self.scene_list.insert("end", f"{scene_id}    ({count} 张)")
        if ids:
            index = ids.index(current) if current in ids else 0
            self.scene_list.selection_set(index)
            self.scene_list.see(index)
        self._refresh_markers()

    def _on_scene_select(self, _event=None):
        self._refresh_markers()

    def _refresh_markers(self):
        scene_id = self._selected_scene()
        if scene_id is None and getattr(self, "scene_ids", None):
            self.scene_list.selection_clear(0, "end")
            self.scene_list.selection_set(0)
            self.scene_list.activate(0)
            scene_id = self.scene_ids[0]
        self.marker_list.delete(0, "end")
        self.marker_paths = list_markers(self.root_dir, scene_id) if scene_id else []
        for path in self.marker_paths:
            self.marker_list.insert("end", path.name)
        if self.marker_paths:
            self.marker_list.selection_set(0)
            self._show_marker(self.marker_paths[0])
        else:
            self._show_marker(None)

    def _on_marker_select(self, _event=None):
        selection = self.marker_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.marker_paths):
            self._show_marker(self.marker_paths[index])

    def _show_marker(self, path):
        canvas_w = max(1, self.preview_canvas.winfo_width())
        canvas_h = max(1, self.preview_canvas.winfo_height())
        self.preview_canvas.delete("all")
        if path is None:
            self.preview_canvas.create_text(
                canvas_w // 2,
                canvas_h // 2,
                text="暂无标记图片\n可“从目标画面抓取”或“导入图片”",
                fill=MUTED,
                font=(FONT, 10),
                justify="center",
            )
            self.photo = None
            return
        image = imread(path)
        if image is None:
            self.preview_canvas.create_text(canvas_w // 2, canvas_h // 2, text="图片读取失败", fill=DANGER, font=(FONT, 10))
            return
        image = normalize_image(image)
        height, width = image.shape[:2]
        scale = min(canvas_w / width, canvas_h / height, 1.0)
        display_w = max(1, int(width * scale))
        display_h = max(1, int(height * scale))
        if scale < 1.0:
            image = cv2.resize(image, (display_w, display_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.preview_canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.photo, anchor="center")

    def _add_scene(self):
        scene_id = simpledialog.askstring("新增场景", "输入场景 ID，例如 town / dungeon_1:", parent=self)
        if not scene_id:
            return
        scene_id = scene_id.strip()
        if not validate_scene_id(scene_id):
            messagebox.showerror("无效名称", "场景名不能为空，也不能包含 \\ / : * ? \" < > |", parent=self)
            return
        folder = scenes_dir(self.root_dir) / scene_id
        folder.mkdir(parents=True, exist_ok=True)
        self.log(f"新增场景: {scene_id}")
        self.status_var.set(f"已创建场景 {scene_id}，请添加至少一张标记图片")
        self._refresh_scenes(select=scene_id)

    def _rename_selected_scene(self, _event=None):
        old_id = self._selected_scene()
        if not old_id:
            messagebox.showinfo("提示", "请先选择一个场景", parent=self)
            return
        new_id = simpledialog.askstring(
            "重命名场景",
            "输入新的场景 ID（会同步更新流程和回放断点）：",
            initialvalue=old_id,
            parent=self,
        )
        if new_id is None:
            return
        new_id = new_id.strip()
        if not new_id or new_id == old_id:
            return
        if not validate_scene_id(new_id):
            messagebox.showerror("无效名称", "场景名不能为空，也不能包含 \\ / : * ? \" < > |", parent=self)
            return
        if (scenes_dir(self.root_dir) / new_id).exists():
            messagebox.showerror("重命名失败", f"场景已存在: {new_id}", parent=self)
            return
        try:
            updated = rename_scene(self.root_dir, old_id, new_id)
        except Exception as exc:
            messagebox.showerror("重命名失败", str(exc), parent=self)
            return
        profiles = self.config.setdefault("scenes", {}).setdefault("profiles", {})
        if old_id in profiles:
            profiles[new_id] = profiles.pop(old_id)
        map_cfg = self.config.setdefault("map_graph", {})
        if str(map_cfg.get("start_scene") or "") == old_id:
            map_cfg["start_scene"] = new_id
        flow_refs = 0
        for transition in map_cfg.setdefault("transitions", []):
            if not isinstance(transition, dict):
                continue
            for key in ("from", "to"):
                if str(transition.get(key) or "") == old_id:
                    transition[key] = new_id
                    flow_refs += 1
        self.dirty = True
        self.log(f"重命名场景: {old_id} -> {new_id}，回放引用 {updated} 处，流程引用 {flow_refs} 处")
        self.status_var.set(
            f"已重命名 {old_id} → {new_id}；更新回放引用 {updated} 处、流程引用 {flow_refs} 处，点击“保存并应用”写入配置"
        )
        self._refresh_scenes(select=new_id)
        self._refresh_flow()

    def _delete_scene(self):
        scene_id = self._selected_scene()
        if not scene_id:
            messagebox.showinfo("提示", "请先选择一个场景", parent=self)
            return
        if not messagebox.askyesno("删除场景", f"确定删除场景 {scene_id} 及其所有标记图片？", parent=self):
            return
        delete_scene(self.root_dir, scene_id)
        (self.config.get("scenes") or {}).get("profiles", {}).pop(scene_id, None)
        self.dirty = True
        self.log(f"删除场景: {scene_id}")
        self.status_var.set(f"已删除场景 {scene_id}，点击“保存并应用”生效")
        self._refresh_scenes()
        self._refresh_flow()

    def _acquire_frame(self):
        frame = self.get_frame()
        try:
            self.lift()
            self.focus_set()
        except Exception:
            pass
        return frame

    def _capture_marker(self):
        scene_id = self._selected_scene()
        if not scene_id:
            scene_id = simpledialog.askstring("场景 ID", "输入要添加标记的场景 ID:", parent=self)
        if not scene_id:
            return
        scene_id = scene_id.strip()
        if not validate_scene_id(scene_id):
            messagebox.showerror("无效名称", "场景名不能为空，也不能包含 \\ / : * ? \" < > |", parent=self)
            return
        self.status_var.set("正在抓取目标画面...")
        self.update_idletasks()
        frame = self._acquire_frame()
        if frame is None:
            self.status_var.set("抓取失败：请先锁定目标窗口并确认预览正常")
            messagebox.showwarning("没有画面", "当前没有目标画面，请先在主界面锁定窗口。", parent=self)
            return
        window = MarkerCaptureWindow(
            self,
            frame,
            title=f"为场景 {scene_id} 抓取标记",
            instruction="拖拽框住该场景稳定出现的元素（场景名/场景 UI），不要选怪物或门。",
        )
        self.wait_window(window)
        if window.result is None:
            return
        x1, y1, x2, y2 = window.result
        crop = frame[y1:y2, x1:x2]
        try:
            path = save_marker(self.root_dir, scene_id, crop)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.dirty = True
        self.log(f"保存场景标记: {path}")
        self.status_var.set(f"已保存 {path.name}，点击“保存并应用”后识别生效")
        self._refresh_scenes(select=scene_id)

    def _import_marker(self):
        scene_id = self._selected_scene()
        if not scene_id:
            scene_id = simpledialog.askstring("场景 ID", "输入要导入标记的场景 ID:", parent=self)
        if not scene_id:
            return
        scene_id = scene_id.strip()
        if not validate_scene_id(scene_id):
            messagebox.showerror("无效名称", "场景名不能为空，也不能包含 \\ / : * ? \" < > |", parent=self)
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="选择场景截图或标记图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        image = normalize_image(imread(path))
        if image is None:
            messagebox.showerror("读取失败", f"无法读取图片:\n{path}", parent=self)
            return
        window = MarkerCaptureWindow(
            self,
            image,
            title=f"从图片为场景 {scene_id} 选取标记",
            instruction="拖拽框住标记区域；如果整张图就是标记，可以拖满整张图。",
        )
        self.wait_window(window)
        if window.result is None:
            return
        x1, y1, x2, y2 = window.result
        crop = image[y1:y2, x1:x2]
        try:
            saved = save_marker(self.root_dir, scene_id, crop)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.dirty = True
        self.log(f"导入场景标记: {saved}")
        self.status_var.set(f"已导入 {saved.name}，点击“保存并应用”后识别生效")
        self._refresh_scenes(select=scene_id)

    def _delete_marker(self):
        selection = self.marker_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要删除的标记图片", parent=self)
            return
        index = int(selection[0])
        if not (0 <= index < len(self.marker_paths)):
            return
        path = self.marker_paths[index]
        if not messagebox.askyesno("删除标记", f"确定删除 {path.name}？", parent=self):
            return
        delete_marker(path)
        self.dirty = True
        self.log(f"删除场景标记: {path}")
        self.status_var.set(f"已删除 {path.name}")
        self._refresh_markers()

    def _test_current_frame(self):
        recognizer = SceneRecognizer(self.config, self.root_dir)
        if not recognizer.enabled:
            messagebox.showinfo("没有模板", "还没有场景标记，请先添加至少一张标记图片。", parent=self)
            return
        frame = self._acquire_frame()
        if frame is None:
            messagebox.showwarning("没有画面", "当前没有目标画面，请先在主界面锁定窗口。", parent=self)
            return
        state = None
        for _ in range(max(1, recognizer.confirm_frames)):
            state = recognizer.recognize(frame)
        if state.loading:
            text = "识别结果: 加载中"
        elif state.scene_id:
            text = f"识别结果: {state.scene_id}  分数 {state.score:.3f}"
        elif state.candidate:
            text = f"待确认: {state.candidate}  分数 {state.candidate_score:.3f}"
        else:
            text = "识别结果: 未知场景"
        self.status_var.set(text)
        self.log(text)

    def _reload_recognizer(self):
        self._apply()

    def _toggle_flow_enabled(self):
        self.config["map_graph"]["enabled"] = bool(self.flow_enabled_var.get())
        self.dirty = True

    def _refresh_flow(self):
        for iid in self.flow_tree.get_children():
            self.flow_tree.delete(iid)
        self._flow_iids = {}
        for index, transition in enumerate(self._transitions()):
            if not isinstance(transition, dict):
                continue
            iid = self.flow_tree.insert(
                "",
                "end",
                values=(
                    index + 1,
                    transition.get("name", ""),
                    transition.get("from", ""),
                    transition.get("to", ""),
                    len(transition.get("actions") or []),
                    transition.get("retries", 1),
                ),
            )
            self._flow_iids[iid] = index
        children = self.flow_tree.get_children()
        if children:
            self.flow_tree.selection_set(children[0])
        self.flow_enabled_var.set(bool(self.config["map_graph"].get("enabled", False)))

    def _selected_transition_index(self):
        selection = self.flow_tree.selection()
        if not selection:
            return None
        return self._flow_iids.get(selection[0])

    def _add_transition(self):
        TransitionEditorWindow(self, None, self._all_scene_ids(), self._on_transition_saved)

    def _on_transition_saved(self, transition):
        self._transitions().append(transition)
        self.dirty = True
        self._refresh_flow()
        self._refresh_scenes()
        self.status_var.set("已新增跳转，点击“保存并应用”生效")

    def _edit_transition(self):
        index = self._selected_transition_index()
        if index is None or not (0 <= index < len(self._transitions())):
            messagebox.showinfo("提示", "请先选择一个跳转", parent=self)
            return
        transition = self._transitions()[index]

        def save(data):
            self._transitions()[index] = data
            self.dirty = True
            self._refresh_flow()
            self.status_var.set("已修改跳转，点击“保存并应用”生效")

        TransitionEditorWindow(self, transition, self._all_scene_ids(), save)

    def _delete_transition(self):
        index = self._selected_transition_index()
        if index is None:
            return
        if not messagebox.askyesno("删除跳转", "确定删除选中的跳转？", parent=self):
            return
        self._transitions().pop(index)
        self.dirty = True
        self._refresh_flow()
        self.status_var.set("已删除跳转，点击“保存并应用”生效")

    def _move_transition(self, delta):
        index = self._selected_transition_index()
        if index is None:
            return
        transitions = self._transitions()
        new_index = index + delta
        if not (0 <= new_index < len(transitions)):
            return
        transitions[index], transitions[new_index] = transitions[new_index], transitions[index]
        self.dirty = True
        self._refresh_flow()
        children = self.flow_tree.get_children()
        if 0 <= new_index < len(children):
            self.flow_tree.selection_set(children[new_index])
            self.flow_tree.see(children[new_index])
        self.status_var.set("已调整顺序，点击“保存并应用”生效")

    def _import_flow(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="导入流程 JSON",
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        if isinstance(data, dict):
            raw_transitions = data.get("transitions")
            enabled = data.get("enabled")
            start_scene = data.get("start_scene")
        else:
            raw_transitions = data
            enabled = True
            start_scene = None
        if not isinstance(raw_transitions, list):
            messagebox.showerror("导入失败", "JSON 必须是跳转数组，或包含 transitions 数组", parent=self)
            return
        valid = []
        for item in raw_transitions:
            if not isinstance(item, dict) or not item.get("from") or not item.get("to"):
                continue
            item = copy.deepcopy(item)
            item.setdefault("name", f"{item['from']}->{item['to']}")
            item.setdefault("actions", [])
            if not isinstance(item.get("actions"), list):
                continue
            valid.append(item)
        if not valid:
            messagebox.showerror("导入失败", "没有找到有效的跳转配置", parent=self)
            return
        replace = messagebox.askyesnocancel(
            "导入流程",
            f"找到 {len(valid)} 条跳转。\n\n是 = 替换现有流程\n否 = 追加到现有流程\n取消 = 不导入",
            parent=self,
        )
        if replace is None:
            return
        if replace:
            self.config["map_graph"]["transitions"] = valid
        else:
            self._transitions().extend(valid)
        if enabled is not None:
            self.config["map_graph"]["enabled"] = bool(enabled)
        else:
            self.config["map_graph"]["enabled"] = True
        if start_scene:
            self.config["map_graph"]["start_scene"] = str(start_scene)
        self.dirty = True
        self._refresh_flow()
        self._refresh_scenes()
        self.status_var.set(f"已导入 {len(valid)} 条跳转，点击“保存并应用”生效")

    def _export_flow(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出流程 JSON",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        data = {
            "enabled": bool(self.config["map_graph"].get("enabled", False)),
            "start_scene": self.config["map_graph"].get("start_scene", ""),
            "transitions": self._transitions(),
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.status_var.set(f"已导出流程: {path}")

    def _validate_flow(self):
        errors = []
        for index, transition in enumerate(self._transitions(), start=1):
            if not isinstance(transition, dict):
                errors.append(f"第 {index} 条不是对象")
                continue
            if not transition.get("from") or not transition.get("to"):
                errors.append(f"第 {index} 条缺少 from/to")
            ok, message = validate_actions(transition.get("actions") or [])
            if not ok:
                errors.append(f"第 {index} 条: {message}")
        return errors

    def _apply(self):
        errors = self._validate_flow()
        if errors:
            messagebox.showerror("流程配置错误", "\n".join(errors[:10]), parent=self)
            return
        self.config["map_graph"]["transitions"] = self._transitions()
        applied = self.apply_config(self.config)
        if applied is False:
            self.status_var.set("保存被取消：请先在主界面停止正在运行的任务")
            return
        self.dirty = False
        self.status_var.set("配置已保存并重新加载识别")
        self.log("场景/流程配置已保存并应用")

    def _on_close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel(
                "未保存",
                "有未应用的修改。\n\n是 = 保存并关闭\n否 = 直接关闭\n取消 = 留下",
                parent=self,
            )
            if answer is None:
                return
            if answer:
                self._apply()
        self.grab_release()
        self.destroy()
