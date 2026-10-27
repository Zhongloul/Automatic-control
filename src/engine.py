import random
import threading
import time
import traceback
from pathlib import Path

from .capture import CaptureThread, FrameSource
from .decision import CombatBrain
from .hotkeys import HotkeyWatcher
from .humanize import Humanizer
from .input import InputController
from .perception import Perception
from .scene import SceneRecognizer
from .window import GameWindow


class AutomationEngine:
    def __init__(self, config, root, log=None):
        self.config = config
        self.root = Path(root)
        self.log = log or (lambda message: None)
        self.window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
        rng = random.Random()
        self.humanizer = Humanizer(config["humanize"], rng)
        self.perception = Perception(config, self.root)
        self.scene_recognizer = SceneRecognizer(config, self.root)
        self.brain = CombatBrain(config, self.humanizer, rng)
        self.inputs = InputController(
            gate=self._foreground_gate if config["safety"].get("require_foreground", True) else None,
            hold_provider=self.humanizer.hold_duration,
            dry_run=bool(config["safety"].get("dry_run", False)),
        )
        self.source = None
        self.capture = None
        self.active = False
        self.running = False
        self.last_action = "-"
        self.last_error = ""
        self._pending_start = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._state = None
        self._preview_frame = None
        self._scene_state = None
        self._next_scene_at = 0.0
        self._scene_error_logged = False
        self._external_lock = False
        self._watcher = HotkeyWatcher()
        for warning in self.scene_recognizer.warnings:
            self.log(f"场景配置: {warning}")

    def _foreground_gate(self):
        return self.window.is_foreground()

    def start(self):
        if self.active:
            return True
        self.last_error = ""
        if not self.window.find():
            self.last_error = "未找到目标窗口，请检查窗口标题关键字"
            self.log(self.last_error)
            return False
        try:
            self.source = FrameSource(self.config["capture"].get("backend", "auto"))
        except Exception as exc:
            self.last_error = f"截屏初始化失败: {exc}"
            self.log(self.last_error)
            return False
        try:
            self.capture = CaptureThread(
                self.source,
                self.window.client_rect,
                self.config["capture"].get("fps", 45),
            )
            self.capture.start()
            deadline = time.perf_counter() + 3.0
            while self.capture.latest() is None and time.perf_counter() < deadline:
                time.sleep(0.05)
            if self.capture.latest() is None:
                raise RuntimeError("未捕获到画面，请将目标窗口改为无边框窗口模式")
        except Exception as exc:
            self.last_error = str(exc)
            self.log(self.last_error)
            if self.capture is not None:
                self.capture.stop()
                self.capture = None
            if self.source is not None:
                self.source.close()
                self.source = None
            return False

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="automation", daemon=True)
        self._thread.start()
        self.active = True
        self.log(f"已锁定窗口: {self.window.describe()} | 截屏后端: {self.source.backend}")
        return True

    def stop(self):
        self._pending_start = 0.0
        self.running = False
        try:
            self.inputs.release_all()
        except Exception:
            pass
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        if self.source is not None:
            self.source.close()
            self.source = None
        self.active = False

    def set_external_lock(self, locked, reason=""):
        self._external_lock = bool(locked)
        if locked:
            self.request_pause(reason or "外部任务占用输入")

    def request_start(self):
        if self._external_lock:
            self.log("当前有流程任务占用输入，无法启动自动操作")
            return False
        if not self.active:
            if not self.start():
                return False
        countdown = max(0.0, float(self.config["safety"].get("countdown_s", 3)))
        self._pending_start = time.perf_counter() + countdown
        self.log(f"倒计时 {int(countdown)} 秒后开始，请切回目标窗口")
        return True

    def request_pause(self, reason=""):
        was_active = self.running or self._pending_start > 0
        self._pending_start = 0.0
        self.running = False
        try:
            self.inputs.release_all()
        except Exception:
            pass
        if was_active and reason:
            self.log(reason)

    def toggle(self):
        if self.running or self._pending_start > 0:
            self.request_pause("已暂停")
        else:
            self.request_start()

    def set_dry_run(self, value):
        self.inputs.dry_run = bool(value)

    def current_frame(self):
        return self.capture.latest() if self.capture is not None else None

    @property
    def pending_start(self):
        return self._pending_start > 0

    def countdown_remaining(self):
        if not self._pending_start:
            return 0.0
        return max(0.0, self._pending_start - time.perf_counter())

    def snapshot(self):
        with self._lock:
            frame = self._preview_frame
            state = self._state
        return {
            "frame": frame,
            "state": state,
            "action": self.last_action,
            "running": self.running,
            "pending": self._pending_start > 0,
            "remaining": self.countdown_remaining(),
            "fps": self.capture.fps_actual if self.capture else 0.0,
            "backend": self.source.backend if self.source else "-",
            "active": self.active,
            "error": self.last_error,
            "window": self.window.describe() if self.window.hwnd else "",
            "dry_run": self.inputs.dry_run,
            "scene": self._scene_state,
            "scene_enabled": self.scene_recognizer.enabled,
        }

    def _loop(self):
        toggle_key = self.config["hotkeys"].get("toggle", "f8")
        pause_key = self.config["hotkeys"].get("quit", "f9")
        last_preview = 0.0
        next_decision = 0.0
        foreground_warned = False
        lost_warned = False

        while not self._stop.is_set():
            now = time.perf_counter()
            if self._watcher.pressed(toggle_key):
                self.toggle()
            if self._watcher.pressed(pause_key):
                self.request_pause("热键暂停")

            if self._pending_start and now >= self._pending_start:
                if self._external_lock:
                    self._pending_start = 0.0
                    self.running = False
                    self.inputs.release_all()
                    self.log("外部任务占用输入，已取消自动操作启动")
                else:
                    self.window.activate()
                    self.brain.reset(now)
                    self._pending_start = 0.0
                    self.running = True
                    self.log("开始运行")

            if not self.window.refresh():
                if self.running:
                    self.running = False
                    self.inputs.release_all()
                if not lost_warned:
                    lost_warned = True
                    self.log("目标窗口丢失，等待重新出现")
                time.sleep(0.3)
                continue
            lost_warned = False

            frame = self.capture.latest() if self.capture else None
            if frame is None:
                time.sleep(0.01)
                continue

            if (
                self.running
                and self.config["safety"].get("require_foreground", True)
                and not self.window.is_foreground()
            ):
                self.running = False
                self.inputs.release_all()
                if not foreground_warned:
                    foreground_warned = True
                    self.log("目标窗口不在前台，已自动暂停")
            elif self.window.is_foreground():
                foreground_warned = False

            if now >= next_decision:
                try:
                    state = self.perception.perceive(frame)
                    with self._lock:
                        self._state = state
                    if self.running:
                        action = self.brain.update(state, now)
                        self.last_action = action.describe()
                        self.inputs.execute(action)
                except Exception:
                    self.running = False
                    self.inputs.release_all()
                    summary = traceback.format_exc(limit=2).strip().splitlines()[-1]
                    self.log(f"感知/决策异常，已暂停: {summary}")
                next_decision = now + self.humanizer.decision_interval()

            if self.scene_recognizer.enabled and now >= self._next_scene_at:
                try:
                    scene_state = self.scene_recognizer.recognize(frame)
                    self._scene_state = scene_state
                    self._scene_error_logged = False
                    if scene_state.changed:
                        self.log(f"场景切换: {scene_state.scene_id} ({scene_state.score:.2f})")
                except Exception:
                    if not self._scene_error_logged:
                        self._scene_error_logged = True
                        summary = traceback.format_exc(limit=2).strip().splitlines()[-1]
                        self.log(f"场景识别异常: {summary}")
                self._next_scene_at = now + self.scene_recognizer.poll_interval

            if now - last_preview >= 0.066:
                with self._lock:
                    self._preview_frame = frame
                last_preview = now

            time.sleep(0.004)
