import ctypes
import threading
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path

from .input import resolve_key
from .keyframes import (
    FrameStore,
    estimate_threshold,
    find_frame,
    load_frame,
    load_frame_index,
    make_signature,
    save_reference,
)
from .session_store import append_event, create_session, load_events, save_events, write_meta

MOUSE_VKS = {"left": 0x01, "right": 0x02, "middle": 0x04}
ACTION_TYPES = {"key_down", "key_up", "mouse_down", "mouse_up"}

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL


class InputRecorder:
    def __init__(self, config, window, root, log=None, frame_provider=None):
        self.config = config
        self.window = window
        self.root = Path(root)
        self.log = log or (lambda message: None)
        self.frame_provider = frame_provider
        recorder_cfg = config.get("recorder") or {}
        self.poll_hz = max(10, min(250, int(recorder_cfg.get("poll_hz", 125))))
        self.poll_interval = 1.0 / self.poll_hz
        self.record_mouse_move = bool(recorder_cfg.get("record_mouse_move", False))
        try:
            sample_hz = float(recorder_cfg.get("frame_sample_hz", 4.0) or 0.0)
        except (TypeError, ValueError):
            sample_hz = 0.0
        self.frame_sample_interval = 1.0 / sample_hz if sample_hz > 0 else 0.0
        self.frame_max_width = max(64, int(recorder_cfg.get("frame_max_width", 480) or 480))
        self.keys = []
        self.key_vks = {}
        seen = set()
        for name in recorder_cfg.get("keys") or []:
            key = str(name).strip().lower()
            if not key or key in seen:
                continue
            try:
                vk, _scan, _extended = resolve_key(key)
            except ValueError:
                continue
            seen.add(key)
            self.keys.append(key)
            self.key_vks[key] = vk

        self.folder = None
        self._handle = None
        self._thread = None
        self._stop = threading.Event()
        self.recording = False
        self.elapsed = 0.0
        self.event_count = 0
        self._recent_events = deque(maxlen=200)
        self.focused = False
        self._key_state = {}
        self._mouse_state = {}
        self._mouse_recorded = {}
        self._last_mouse = None
        self._last_move_at = 0.0
        self._lost_logged = False
        self._meta = {}
        self._checkpoint_lock = threading.Lock()
        self._pending_checkpoints = []
        self._frame_store = None
        self._frame_count = 0
        self._next_frame_at = 0.0
        self._sync_learn_pending = []
        self._sync_ref_patches = []
        self._suppress_sync_learning = False

    def start(self):
        if self.recording:
            return None
        rect = self.window.client_rect() or (0, 0, 0, 0)
        width = max(0, rect[2] - rect[0])
        height = max(0, rect[3] - rect[1])
        self._meta = {
            "name": "",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": 0.0,
            "event_count": 0,
            "window_title": getattr(self.window, "title", ""),
            "window_class": getattr(self.window, "window_class", ""),
            "client_size": [width, height],
            "keys": list(self.keys),
            "poll_hz": self.poll_hz,
            "frame_sample_hz": round(1.0 / self.frame_sample_interval, 2)
            if self.frame_sample_interval > 0
            else 0.0,
        }
        self.folder = create_session(self.root, self._meta)
        self._handle = open(self.folder / "events.jsonl", "a", encoding="utf-8")
        self._frame_store = None
        self._frame_count = 0
        self._next_frame_at = 0.0
        self._sync_learn_pending = []
        self._sync_ref_patches = []
        self._suppress_sync_learning = False
        if self.frame_provider is not None and self.frame_sample_interval > 0:
            try:
                self._frame_store = FrameStore(self.folder, max_width=self.frame_max_width)
            except OSError as exc:
                self.log(f"录制画面采样初始化失败: {exc}")
        self._key_state = {key: False for key in self.keys}
        self._mouse_state = {button: False for button in MOUSE_VKS}
        self._mouse_recorded = {button: False for button in MOUSE_VKS}
        self.elapsed = 0.0
        self.event_count = 0
        self._recent_events.clear()
        self.focused = False
        self._last_mouse = None
        self._last_move_at = 0.0
        self._lost_logged = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="input-recorder")
        self.recording = True
        self._thread.start()
        self.log(f"开始录制输入: {self.folder.name}")
        return self.folder

    def stop(self):
        if not self.recording:
            return self.folder
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        return self.folder

    def insert_checkpoint(self, name="sync", scene="", seconds=0.0):
        if not self.recording:
            return False
        checkpoint = {"name": name}
        if scene:
            checkpoint["scene"] = scene
        if seconds:
            checkpoint["seconds"] = float(seconds)
        with self._checkpoint_lock:
            self._pending_checkpoints.append(checkpoint)
        return True

    def _drain_checkpoints(self):
        with self._checkpoint_lock:
            pending = list(self._pending_checkpoints)
            self._pending_checkpoints.clear()
        for checkpoint in pending:
            event = self._emit("checkpoint", **checkpoint)
            if event is not None and str(checkpoint.get("name") or "").lower() == "sync":
                self._sync_learn_pending.append(float(event["t"]))

    def status(self):
        return {
            "recording": self.recording,
            "elapsed": round(self.elapsed, 2),
            "event_count": self.event_count,
            "frame_count": self._frame_count,
            "focused": self.focused,
            "folder": self.folder.name if self.folder is not None else "",
            "recent_events": list(self._recent_events)[-12:],
        }

    def _sample_frame(self):
        if self._frame_store is None or self.frame_provider is None:
            return
        if self.elapsed < self._next_frame_at:
            return
        self._next_frame_at = self.elapsed + self.frame_sample_interval
        try:
            frame = self.frame_provider()
        except Exception:
            frame = None
        if self._frame_store.save(self.elapsed, frame):
            self._frame_count += 1

    def _loop(self):
        last = time.perf_counter()
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                delta = now - last
                last = now
                focused = self.window.is_foreground()
                self.focused = focused
                if focused:
                    self.elapsed += delta
                    self._lost_logged = False
                    self._drain_checkpoints()
                    self._sample()
                    self._sample_frame()
                else:
                    if self._has_down():
                        self._release_all()
                    if not self._lost_logged:
                        self._lost_logged = True
                        self.log("录制暂停：目标窗口不在前台")
                remaining = self.poll_interval - (time.perf_counter() - now)
                time.sleep(max(0.001, remaining))
        except Exception as exc:
            self.log(f"录制异常: {exc}")
        finally:
            self._release_all()
            self._finish()

    def _finish(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None
        self.recording = False
        if self._frame_store is not None:
            self._frame_store.close()
            self._frame_store = None
        if self.folder is not None:
            self._apply_sync_refs()
            meta = dict(self._meta)
            meta.update(
                {
                    "name": self.folder.name,
                    "duration": round(self.elapsed, 3),
                    "event_count": self.event_count,
                    "frame_count": self._frame_count,
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            try:
                write_meta(self.folder, meta)
            except Exception as exc:
                self.log(f"写录制元数据失败: {exc}")
            self.log(f"录制结束: {self.folder.name} | {self.elapsed:.1f}s | {self.event_count} 个事件")

    def _emit(self, kind, **fields):
        if self._handle is None:
            return None
        event = {"t": round(self.elapsed, 4), "type": kind}
        event.update(fields)
        append_event(self._handle, event)
        self.event_count += 1
        self._recent_events.append(dict(event))
        if (
            not self._suppress_sync_learning
            and kind in ACTION_TYPES
            and self._sync_learn_pending
        ):
            self._learn_sync_refs(event["t"])
        return event

    def _learn_sync_refs(self, action_t):
        pending = self._sync_learn_pending
        self._sync_learn_pending = []
        if not pending or self.folder is None:
            return
        frames = load_frame_index(self.folder)
        if not frames:
            self.log("同步断点未学习参考帧：没有录制画面")
            return
        for checkpoint_t in pending:
            entry = find_frame(
                frames,
                action_t - 0.25,
                latest=action_t,
                earliest=checkpoint_t,
            )
            if entry is None:
                entry = find_frame(frames, action_t - 0.05, latest=action_t, earliest=checkpoint_t)
            if entry is None:
                self.log(f"同步断点未找到参考画面: t={checkpoint_t:.2f}s")
                continue
            image = load_frame(self.folder, entry)
            signature = make_signature(image)
            if image is None or signature is None:
                continue
            reference = save_reference(self.folder, image, f"sync_{checkpoint_t:.2f}.jpg")
            if not reference:
                self.log(f"同步断点参考图保存失败: t={checkpoint_t:.2f}s")
                continue
            threshold = estimate_threshold(frames, self.folder, signature, action_t)
            self._sync_ref_patches.append(
                {
                    "t": checkpoint_t,
                    "ref": reference,
                    "threshold": round(threshold, 4),
                    "roi": [0.0, 0.0, 1.0, 1.0],
                }
            )
            self.log(
                f"同步断点已学习参考帧: t={checkpoint_t:.2f}s | 阈值 {threshold:.2f}"
            )

    def _apply_sync_refs(self):
        if not self._sync_ref_patches:
            return
        try:
            events = load_events(self.folder)
            patched = 0
            for patch in self._sync_ref_patches:
                for event in events:
                    if (
                        str(event.get("type") or "").lower() == "checkpoint"
                        and str(event.get("name") or "").lower() == "sync"
                        and abs(float(event.get("t", 0.0)) - patch["t"]) < 1e-6
                    ):
                        event["ref"] = patch["ref"]
                        event["threshold"] = patch["threshold"]
                        event["roi"] = list(patch["roi"])
                        patched += 1
                        break
            if patched:
                save_events(self.folder, events)
                self.log(f"同步断点参考帧已写入会话: {patched} 个")
        except Exception as exc:
            self.log(f"写入同步参考帧失败: {exc}")

    def _mouse_ratio(self):
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None, False
        rect = self.window.client_rect()
        if not rect:
            return None, False
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None, False
        inside = left <= point.x < right and top <= point.y < bottom
        x = min(max((point.x - left) / width, 0.0), 1.0)
        y = min(max((point.y - top) / height, 0.0), 1.0)
        return [round(x, 4), round(y, 4)], inside

    def _sample(self):
        for key in self.keys:
            down = bool(user32.GetAsyncKeyState(self.key_vks[key]) & 0x8000)
            if down != self._key_state.get(key, False):
                self._key_state[key] = down
                self._emit("key_down" if down else "key_up", key=key)

        ratio, inside = self._mouse_ratio()
        if ratio is not None and self.record_mouse_move and inside:
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            if self._last_mouse is not None:
                dx = point.x - self._last_mouse[0]
                dy = point.y - self._last_mouse[1]
                if dx * dx + dy * dy >= 9 and self.elapsed - self._last_move_at >= 0.03:
                    self._emit("mouse_move", pos=ratio)
                    self._last_move_at = self.elapsed
            self._last_mouse = (point.x, point.y)

        for button, vk in MOUSE_VKS.items():
            down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            previous = self._mouse_state.get(button, False)
            if down == previous:
                continue
            self._mouse_state[button] = down
            if down:
                if ratio is not None and inside:
                    self._mouse_recorded[button] = True
                    self._emit("mouse_down", button=button, pos=ratio)
            elif self._mouse_recorded.get(button):
                self._mouse_recorded[button] = False
                if ratio is not None:
                    self._emit("mouse_up", button=button, pos=ratio)

    def _has_down(self):
        return any(self._key_state.values()) or any(self._mouse_state.values())

    def _release_all(self):
        previous = self._suppress_sync_learning
        self._suppress_sync_learning = True
        try:
            for key, down in list(self._key_state.items()):
                if down:
                    self._emit("key_up", key=key)
                    self._key_state[key] = False
            for button, down in list(self._mouse_state.items()):
                if down and self._mouse_recorded.get(button):
                    self._emit("mouse_up", button=button, pos=self._mouse_ratio()[0] or [0.5, 0.5])
                self._mouse_state[button] = False
                self._mouse_recorded[button] = False
        finally:
            self._suppress_sync_learning = previous
