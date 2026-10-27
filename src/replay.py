import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .keyframes import load_reference, make_signature, signature_diff
from .session_store import load_events


@dataclass
class ReplayResult:
    success: bool
    message: str
    loops: int = 0
    events: int = 0
    elapsed: float = 0.0


MOUSE_BUTTONS = ("left", "right", "middle")


class ReplayPlayer:
    def __init__(
        self,
        config,
        window,
        input_controller,
        session_folder,
        speed=1.0,
        loops=1,
        loop_gap_s=None,
        abort_event=None,
        log=None,
        progress=None,
        event_callback=None,
        scene_checker=None,
        checkpoint_immediate=None,
        frame_getter=None,
        session_callback=None,
    ):
        self.config = config
        self.window = window
        self.inputs = input_controller
        self.session_folder = session_folder
        self.events = load_events(session_folder)
        self.events.sort(key=lambda event: float(event.get("t", 0.0)))
        self.speed = max(0.25, min(4.0, float(speed)))
        self.loops = max(0, min(999, int(loops)))
        self.abort_event = abort_event or threading.Event()
        self.log = log or (lambda message: None)
        self.progress = progress or (lambda *args: None)
        self.event_callback = event_callback or (lambda *args: None)
        self.scene_checker = scene_checker
        self.frame_getter = frame_getter
        self.session_callback = session_callback
        self._last_branch_label = ""
        replay_cfg = config.get("replay") or {}
        configured_immediate = bool(replay_cfg.get("checkpoint_immediate", True))
        self.checkpoint_immediate = (
            configured_immediate if checkpoint_immediate is None else bool(checkpoint_immediate)
        )
        self.state_poll_interval_s = max(0.02, float(replay_cfg.get("state_poll_interval_s", 0.1)))
        self.state_confirm_frames = max(1, int(replay_cfg.get("state_confirm_frames", 2)))
        self.checkpoint_settle_s = max(0.0, float(replay_cfg.get("checkpoint_settle_s", 0.4)))
        self.checkpoint_timeout_s = max(1.0, float(replay_cfg.get("checkpoint_timeout_s", 30.0)))
        self.loop_gap_s = replay_cfg.get("loop_gap_s", [0.5, 2.0])
        self.loop_gap_override = None if loop_gap_s is None else max(0.0, float(loop_gap_s))
        self.foreground_timeout_s = max(1.0, float(replay_cfg.get("foreground_timeout_s", 15.0)))
        self.max_duration_s = max(10.0, float(replay_cfg.get("max_duration_s", 3600.0)))
        self._held_keys = set()
        self._held_buttons = set()
        self._checkpoint_error = ""

    def _aborted(self):
        return self.abort_event.is_set()

    def _sleep(self, seconds):
        end = time.perf_counter() + max(0.0, float(seconds))
        while True:
            if self._aborted():
                return False
            remaining = end - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(0.02, remaining))

    def _wait_foreground(self, timeout=None):
        timeout = self.foreground_timeout_s if timeout is None else timeout
        deadline = time.perf_counter() + timeout
        while not self.window.is_foreground():
            if self._aborted():
                return False
            if time.perf_counter() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def _release_all(self):
        try:
            self.inputs.release_all()
        except Exception:
            pass
        self._held_keys.clear()
        self._held_buttons.clear()

    def _restore_held_keys(self):
        for key in list(self._held_keys):
            try:
                self.inputs.key_down(key)
            except Exception:
                self._held_keys.discard(key)

    def _wait_scene_checkpoint(self, event):
        scene_id = str(event.get("scene") or "").strip()
        if not scene_id:
            self._checkpoint_error = "wait_scene 断点缺少 scene 字段"
            return -1.0
        if self.scene_checker is None:
            self._checkpoint_error = "没有场景识别器，无法等待场景"
            return -1.0
        try:
            self.inputs.release_all()
        except Exception:
            pass
        self.log(f"断点等待场景: {scene_id}")
        start = time.perf_counter()
        deadline = start + self.checkpoint_timeout_s
        while True:
            if self._aborted():
                return -1.0
            try:
                current = self.scene_checker()
            except Exception:
                current = None
            if current == scene_id:
                break
            if time.perf_counter() >= deadline:
                self._checkpoint_error = f"等待场景 {scene_id} 超时"
                return -1.0
            time.sleep(min(self.state_poll_interval_s, 0.2))
        if not self._sleep(self.checkpoint_settle_s):
            return -1.0
        self._restore_held_keys()
        self.log(f"已进入场景 {scene_id}，继续回放")
        return time.perf_counter() - start

    def _wait_image_checkpoint(self, event):
        ref = str(event.get("ref") or "").strip()
        if not ref:
            self._checkpoint_error = "等待画面断点缺少参考图"
            return -1.0
        if self.frame_getter is None:
            self._checkpoint_error = "没有画面来源，无法等待画面状态"
            return -1.0
        reference = load_reference(self.session_folder, ref)
        if reference is None:
            self._checkpoint_error = f"参考图不存在: {ref}"
            return -1.0
        roi = event.get("roi") or [0.0, 0.0, 1.0, 1.0]
        threshold = max(0.0001, min(1.0, float(event.get("threshold", 0.1) or 0.1)))
        reference_sig = make_signature(reference, roi)
        if reference_sig is None:
            self._checkpoint_error = f"参考图无法解析: {ref}"
            return -1.0
        try:
            self.inputs.release_all()
        except Exception:
            pass
        self.log(f"断点等待画面状态: {ref}（阈值 {threshold:.2f}）")
        start = time.perf_counter()
        deadline = start + self.checkpoint_timeout_s
        hits = 0
        last_diff = 1.0
        while True:
            if self._aborted():
                return -1.0
            try:
                frame = self.frame_getter()
            except Exception:
                frame = None
            if frame is not None:
                signature = make_signature(frame, roi)
                if signature is not None:
                    last_diff = signature_diff(signature, reference_sig)
                    if last_diff <= threshold:
                        hits += 1
                        if hits >= self.state_confirm_frames:
                            break
                    else:
                        hits = 0
            if time.perf_counter() >= deadline:
                self._checkpoint_error = (
                    f"等待画面状态超时（当前差异 {last_diff:.3f}，阈值 {threshold:.2f}）"
                )
                return -1.0
            time.sleep(min(self.state_poll_interval_s, 0.2))
        if not self._sleep(self.checkpoint_settle_s):
            return -1.0
        self._restore_held_keys()
        self.log(f"画面状态已匹配（差异 {last_diff:.3f}），继续回放")
        return time.perf_counter() - start

    def _handle_checkpoint(self, event):
        name = str(event.get("name") or "sync").lower()
        start = time.perf_counter()
        if name == "wait_seconds":
            seconds = max(0.0, float(event.get("seconds", 0.0) or 0.0))
            try:
                self.inputs.release_all()
            except Exception:
                pass
            if not self._sleep(seconds):
                return -1.0
            self._restore_held_keys()
            return time.perf_counter() - start
        if name == "wait_visual":
            source = str(event.get("source") or "").lower()
            if source not in ("scene", "image"):
                source = "scene" if event.get("scene") else "image"
            if source == "scene":
                return self._wait_scene_checkpoint(event)
            return self._wait_image_checkpoint(event)
        if name == "wait_scene":
            return self._wait_scene_checkpoint(event)
        if name == "wait_state":
            return self._wait_image_checkpoint(event)
        if name == "sync":
            if event.get("ref"):
                return self._wait_image_checkpoint(event)
            if not self._sleep(self.checkpoint_settle_s):
                return -1.0
            return time.perf_counter() - start
        self.log(f"未知断点类型，已跳过: {name}")
        return 0.0

    def _to_screen(self, pos):
        rect = self.window.client_rect()
        if not rect:
            return None
        try:
            rx, ry = float(pos[0]), float(pos[1])
        except (TypeError, ValueError, IndexError):
            return None
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None
        if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
            return None
        return int(left + rx * width), int(top + ry * height)

    def _execute(self, event):
        kind = str(event.get("type") or "").lower()
        if kind == "key_down":
            key = event.get("key")
            if key:
                self.inputs.key_down(str(key).lower())
                self._held_keys.add(str(key).lower())
        elif kind == "key_up":
            key = event.get("key")
            if key:
                self.inputs.key_up(str(key).lower())
                self._held_keys.discard(str(key).lower())
        elif kind == "mouse_move":
            point = self._to_screen(event.get("pos") or [])
            if point:
                self.inputs.move_screen(point[0], point[1])
        elif kind in ("mouse_down", "mouse_up", "click"):
            button = str(event.get("button") or "left").lower()
            if button not in MOUSE_BUTTONS:
                return
            pos = event.get("pos") or [0.5, 0.5]
            point = self._to_screen(pos)
            if point is None:
                return
            x, y = point
            if kind == "mouse_down":
                self.inputs.mouse_down_screen(x, y, button)
                self._held_buttons.add(button)
            elif kind == "mouse_up":
                self.inputs.mouse_up_screen(x, y, button)
                self._held_buttons.discard(button)
            else:
                self.inputs.click_screen(x, y, button)

    def _schedule(self):
        schedule = []
        previous = -1.0
        for event in self.events:
            base = float(event.get("t", 0.0)) / self.speed
            target = max(previous, base)
            schedule.append((target, event))
            previous = target
        return schedule

    def _loop_gap(self):
        if self.loop_gap_override is not None:
            return self.loop_gap_override
        value = self.loop_gap_s
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError, IndexError):
            return 0.0
        return random.uniform(max(0.0, min(low, high)), max(0.0, max(low, high)))

    def _load_session_events(self, folder):
        self.session_folder = Path(folder)
        events = load_events(self.session_folder)
        events.sort(key=lambda event: float(event.get("t", 0.0)))
        self.events = events
        return bool(events)

    def _resolve_branch_target(self, goto):
        if not isinstance(goto, dict):
            return None
        name = str(goto.get("session") or "").strip()
        if not name:
            return None
        path = Path(name)
        if not path.is_absolute():
            path = Path(self.session_folder).parent / name
        if not path.is_dir() or not (path / "events.jsonl").is_file():
            return None
        return path

    def _wait_branch_checkpoint(self, event):
        self._last_branch_label = ""
        cases = event.get("cases")
        if not isinstance(cases, list) or not cases:
            return None, "分支断点缺少 cases"
        timeout = max(0.1, float(event.get("timeout_s", self.checkpoint_timeout_s)))
        prepared = []
        for case in cases:
            if not isinstance(case, dict):
                continue
            source = str(case.get("source") or "").lower()
            goto = case.get("goto")
            if not isinstance(goto, dict):
                goto = {"session": case.get("session")}
            if source == "scene":
                scene_id = str(case.get("scene") or "").strip()
                if not scene_id:
                    return None, "分支条件缺少场景 ID"
                prepared.append({"kind": "scene", "scene": scene_id, "goto": goto})
            else:
                ref = str(case.get("ref") or "").strip()
                if not ref:
                    return None, "分支条件缺少参考图"
                reference = load_reference(self.session_folder, ref)
                signature = make_signature(reference, case.get("roi"))
                if signature is None:
                    return None, f"分支参考图无法解析: {ref}"
                threshold = max(0.0001, min(1.0, float(case.get("threshold", 0.1) or 0.1)))
                confirm = max(1, int(case.get("confirm_frames", self.state_confirm_frames)))
                prepared.append(
                    {
                        "kind": "image",
                        "ref": ref,
                        "signature": signature,
                        "roi": case.get("roi"),
                        "threshold": threshold,
                        "confirm": confirm,
                        "hits": 0,
                        "goto": goto,
                    }
                )
        if not prepared:
            return None, "分支断点没有有效条件"
        needs_scene = any(case["kind"] == "scene" for case in prepared)
        needs_image = any(case["kind"] == "image" for case in prepared)
        if needs_scene and self.scene_checker is None and not needs_image:
            return None, "没有场景识别器，无法判断分支"
        if needs_image and self.frame_getter is None and not needs_scene:
            return None, "没有画面来源，无法判断分支"
        try:
            self.inputs.release_all()
        except Exception:
            pass
        self.log(f"断点等待分支条件（{len(prepared)} 个）")
        start = time.perf_counter()
        deadline = start + timeout
        while True:
            if self._aborted():
                return None, ""
            scene_now = None
            if needs_scene:
                try:
                    scene_now = self.scene_checker()
                except Exception:
                    scene_now = None
            frame = None
            for index, case in enumerate(prepared):
                matched = False
                label = ""
                if case["kind"] == "scene":
                    matched = scene_now == case["scene"]
                    label = f"scene={case['scene']}"
                else:
                    if frame is None and self.frame_getter is not None:
                        try:
                            frame = self.frame_getter()
                        except Exception:
                            frame = None
                    if frame is not None:
                        signature = make_signature(frame, case["roi"])
                        diff = signature_diff(signature, case["signature"])
                        if diff <= case["threshold"]:
                            case["hits"] += 1
                            if case["hits"] >= case["confirm"]:
                                matched = True
                        else:
                            case["hits"] = 0
                        label = f"ref={case['ref']}（差异 {diff:.3f}）"
                if matched:
                    folder = self._resolve_branch_target(case["goto"])
                    if folder is None:
                        return None, f"分支目标会话无效: {case['goto']}"
                    self.log(f"分支命中 {label} → {folder.name}")
                    self._last_branch_label = label
                    return folder, ""
            if time.perf_counter() >= deadline:
                default = event.get("default")
                if isinstance(default, dict):
                    goto = default.get("goto") if isinstance(default.get("goto"), dict) else default
                    folder = self._resolve_branch_target(goto)
                    if folder is not None:
                        self.log(f"分支超时，走默认路径 → {folder.name}")
                        self._last_branch_label = "超时默认路径"
                        return folder, ""
                return None, f"分支条件等待超时（{timeout:.1f}s）"
            time.sleep(min(self.state_poll_interval_s, 0.2))

    def run(self):
        start = time.perf_counter()
        if not self.events:
            return ReplayResult(False, "会话没有可回放事件", 0, 0, 0.0)
        if getattr(self.inputs, "dry_run", False):
            return ReplayResult(False, "仅观察模式下不能回放", 0, 0, 0.0)
        if not self._wait_foreground():
            return ReplayResult(False, "等待目标窗口前台超时", 0, 0, 0.0)
        if not self.inputs.available():
            return ReplayResult(False, "输入不可用：请关闭仅观察并保持目标窗口在前台", 0, 0, 0.0)
        loops_label = "无限" if self.loops == 0 else f"{self.loops} 轮"
        self.log(
            f"开始回放 {self.session_folder} | {len(self.events)} 个事件 | {loops_label} | {self.speed:.2f}x"
        )

        root_folder = Path(self.session_folder)
        total_executed = 0
        completed_loops = 0
        loop_index = 0
        try:
            while True:
                if self.loops and loop_index >= self.loops:
                    break
                if self._aborted():
                    break
                if loop_index > 0:
                    gap = self._loop_gap()
                    if gap > 0 and not self._sleep(gap):
                        break
                if not self._wait_foreground():
                    return ReplayResult(False, "回放中等待目标窗口前台超时", completed_loops, total_executed, time.perf_counter() - start)
                if not self._load_session_events(root_folder):
                    return ReplayResult(False, "会话没有可回放事件", completed_loops, total_executed, time.perf_counter() - start)
                if self.session_callback is not None:
                    try:
                        self.session_callback(str(root_folder), "")
                    except Exception:
                        pass
                branch_depth = 0
                while True:
                    schedule = self._schedule()
                    loop_start = time.perf_counter()
                    time_shift = 0.0
                    paused = False
                    switched = False
                    for event_index, (target, event) in enumerate(schedule):
                        if self._aborted():
                            break
                        while True:
                            if self._aborted():
                                break
                            if not self.window.is_foreground():
                                if not paused:
                                    paused = True
                                    self.inputs.release_all()
                                    self.log("回放暂停：目标窗口不在前台")
                                pause_start = time.perf_counter()
                                if not self._wait_foreground():
                                    return ReplayResult(False, "回放中等待目标窗口前台超时", completed_loops, total_executed, time.perf_counter() - start)
                                time_shift += time.perf_counter() - pause_start + 0.15
                                self._restore_held_keys()
                                paused = False
                                self.log("回放继续")
                            target_time = loop_start + target + time_shift
                            remaining = target_time - time.perf_counter()
                            if remaining <= 0:
                                break
                            time.sleep(min(0.02, remaining))
                        if self._aborted():
                            break
                        self.event_callback(
                            loop_index + 1,
                            self.loops,
                            event_index,
                            len(schedule),
                            event,
                        )
                        if str(event.get("type") or "").lower() == "checkpoint":
                            if str(event.get("name") or "").lower() == "branch":
                                target_folder, branch_error = self._wait_branch_checkpoint(event)
                                if self._aborted():
                                    break
                                if branch_error:
                                    return ReplayResult(
                                        False,
                                        branch_error,
                                        completed_loops,
                                        total_executed,
                                        time.perf_counter() - start,
                                    )
                                branch_depth += 1
                                if branch_depth > 20:
                                    return ReplayResult(
                                        False,
                                        "分支跳转次数过多",
                                        completed_loops,
                                        total_executed,
                                        time.perf_counter() - start,
                                    )
                                if not self._load_session_events(target_folder):
                                    return ReplayResult(
                                        False,
                                        f"分支目标会话没有事件: {target_folder}",
                                        completed_loops,
                                        total_executed,
                                        time.perf_counter() - start,
                                    )
                                if self.session_callback is not None:
                                    try:
                                        self.session_callback(
                                            str(target_folder), self._last_branch_label
                                        )
                                    except Exception:
                                        pass
                                self.log(f"分支切换会话: {target_folder.name}")
                                switched = True
                                break
                            delay = self._handle_checkpoint(event)
                            if delay < 0:
                                if self._aborted():
                                    return ReplayResult(False, "回放已停止", completed_loops, total_executed, time.perf_counter() - start)
                                return ReplayResult(
                                    False,
                                    self._checkpoint_error or "断点等待失败",
                                    completed_loops,
                                    total_executed,
                                    time.perf_counter() - start,
                                )
                            if self.checkpoint_immediate and event_index + 1 < len(schedule):
                                next_target = schedule[event_index + 1][0]
                                time_shift = (time.perf_counter() - loop_start) - next_target
                                self.log("断点完成，下一事件立即执行")
                            else:
                                time_shift += delay
                            total_executed += 1
                            self.progress(loop_index + 1, self.loops, event_index + 1, len(schedule))
                            continue
                        try:
                            self._execute(event)
                        except Exception as exc:
                            self.log(f"回放事件失败，已跳过: {exc}")
                        total_executed += 1
                        self.progress(loop_index + 1, self.loops, event_index + 1, len(schedule))
                        if (
                            self.loops
                            and self.max_duration_s > 0
                            and time.perf_counter() - start > self.max_duration_s
                        ):
                            return ReplayResult(False, "超过最大回放时长", completed_loops, total_executed, time.perf_counter() - start)
                    if switched:
                        continue
                    if self._aborted():
                        break
                    completed_loops += 1
                    self.inputs.release_all()
                    self._held_keys.clear()
                    self._held_buttons.clear()
                    loop_index += 1
                    break
                if self._aborted():
                    break
            if self._aborted():
                return ReplayResult(False, "回放已停止", completed_loops, total_executed, time.perf_counter() - start)
            return ReplayResult(True, "回放完成", completed_loops, total_executed, time.perf_counter() - start)
        finally:
            self._release_all()
