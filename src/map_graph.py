import time
from collections import deque
from dataclasses import dataclass, field

from .scene import SceneRecognizer


@dataclass
class Transition:
    name: str
    from_scene: str
    to_scene: str
    actions: list = field(default_factory=list)
    retries: int = 1
    require_from_scene: bool = True
    from_timeout_s: float = 6.0
    settle_s: float = 0.4
    loading_timeout_s: float = 8.0
    scene_timeout_s: float = 25.0
    require_loading: bool = False


@dataclass
class TransitionResult:
    success: bool
    message: str
    elapsed: float = 0.0
    attempts: int = 0


@dataclass
class RouteResult:
    success: bool
    message: str
    route: list = field(default_factory=list)
    elapsed: float = 0.0


class MapGraph:
    def __init__(self, config):
        cfg = config.get("map_graph") or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.start_scene = str(cfg.get("start_scene") or "")
        self.transitions = []
        self.warnings = []
        names = set()
        for item in cfg.get("transitions") or []:
            if not isinstance(item, dict):
                self.warnings.append(f"跳转配置格式无效: {item}")
                continue
            from_scene = str(item.get("from") or "").strip()
            to_scene = str(item.get("to") or "").strip()
            if not from_scene or not to_scene:
                self.warnings.append("跳转配置缺少 from/to")
                continue
            name = str(item.get("name") or f"{from_scene}->{to_scene}")
            if name in names:
                self.warnings.append(f"跳转名称重复，已跳过: {name}")
                continue
            actions = item.get("actions") or []
            if not isinstance(actions, list):
                actions = [actions]
            names.add(name)
            self.transitions.append(
                Transition(
                    name=name,
                    from_scene=from_scene,
                    to_scene=to_scene,
                    actions=actions,
                    retries=max(0, int(item.get("retries", 1))),
                    require_from_scene=bool(item.get("require_from_scene", True)),
                    from_timeout_s=float(item.get("from_timeout_s", 6.0)),
                    settle_s=float(item.get("settle_s", 0.4)),
                    loading_timeout_s=float(item.get("loading_timeout_s", 8.0)),
                    scene_timeout_s=float(item.get("scene_timeout_s", 25.0)),
                    require_loading=bool(item.get("require_loading", False)),
                )
            )

    def get(self, name):
        for transition in self.transitions:
            if transition.name == name:
                return transition
        return None

    def edges_from(self, scene_id):
        return [transition for transition in self.transitions if transition.from_scene == scene_id]

    def path(self, start_scene, goal_scene):
        if start_scene == goal_scene:
            return []
        queue = deque([start_scene])
        previous = {start_scene: None}
        used_transition = {}
        while queue:
            current = queue.popleft()
            for transition in self.edges_from(current):
                next_scene = transition.to_scene
                if next_scene in previous:
                    continue
                previous[next_scene] = current
                used_transition[next_scene] = transition
                if next_scene == goal_scene:
                    queue.clear()
                    break
                queue.append(next_scene)
        if goal_scene not in previous:
            return None
        route = []
        node = goal_scene
        while previous[node] is not None:
            route.append(used_transition[node])
            node = previous[node]
        return list(reversed(route))


class TransitionExecutor:
    def __init__(
        self,
        config,
        recognizer: SceneRecognizer,
        input_controller,
        frame_provider,
        client_rect_provider,
        log=None,
        abort_event=None,
    ):
        self.config = config
        self.recognizer = recognizer
        self.input = input_controller
        self.frame_provider = frame_provider
        self.client_rect_provider = client_rect_provider
        self.log = log or (lambda message: None)
        self.abort_event = abort_event
        scenes_cfg = config.get("scenes") or {}
        graph_cfg = config.get("map_graph") or {}
        self.poll_interval = max(0.01, float(scenes_cfg.get("poll_interval_s", 0.2)))
        self.click_margin_ratio = max(0.0, float(graph_cfg.get("click_margin_ratio", 0.0)))

    def _aborted(self):
        return self.abort_event is not None and self.abort_event.is_set()

    def _sleep(self, seconds):
        end = time.perf_counter() + max(0.0, float(seconds))
        while True:
            if self._aborted():
                return False
            remaining = end - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(0.05, remaining))

    def _wait_for_scene(self, scene_id, timeout):
        deadline = time.perf_counter() + max(0.0, float(timeout))
        while time.perf_counter() < deadline:
            if self._aborted():
                return False
            frame = self.frame_provider()
            if frame is not None:
                state = self.recognizer.recognize(frame)
                if state.scene_id == scene_id:
                    return True
            time.sleep(self.poll_interval)
        return False

    def _wait_for_loading(self, timeout):
        deadline = time.perf_counter() + max(0.0, float(timeout))
        while time.perf_counter() < deadline:
            if self._aborted():
                return False
            frame = self.frame_provider()
            if frame is not None:
                state = self.recognizer.recognize(frame)
                if state.loading:
                    return True
            time.sleep(self.poll_interval)
        return False

    def _run_actions(self, actions):
        for action in actions:
            if self._aborted():
                return False, "已中止"
            if not self.input.available():
                return False, "输入被拦截：目标窗口不在前台"
            try:
                self._run_action(action)
            except Exception as exc:
                return False, str(exc)
        return True, ""

    def _run_action(self, action):
        if not isinstance(action, dict):
            raise ValueError(f"动作格式无效: {action}")
        kind = str(action.get("type") or "").lower()
        if kind in ("key", "key_tap"):
            key = action.get("key")
            if not key:
                raise ValueError("key_tap 缺少 key")
            self.input.tap(key, action.get("hold_s"))
            return
        if kind == "key_hold":
            key = action.get("key")
            if not key:
                raise ValueError("key_hold 缺少 key")
            self.input.hold(key, float(action.get("seconds", 0.2)))
            return
        if kind == "wait":
            if not self._sleep(float(action.get("seconds", 0.2))):
                raise RuntimeError("已中止")
            return
        if kind in ("click", "move"):
            pos = action.get("pos") or [0.5, 0.5]
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                raise ValueError(f"{kind} 的 pos 格式无效")
            rel_x = float(pos[0])
            rel_y = float(pos[1])
            margin = self.click_margin_ratio
            if not (margin <= rel_x <= 1.0 - margin and margin <= rel_y <= 1.0 - margin):
                raise ValueError(f"点击坐标超出安全范围: {pos}")
            rect = self.client_rect_provider()
            if not rect:
                raise RuntimeError("无法获取目标窗口客户区")
            left, top, right, bottom = rect
            x = int(left + rel_x * (right - left))
            y = int(top + rel_y * (bottom - top))
            if kind == "click":
                self.input.click_screen(x, y, action.get("button", "left"))
            else:
                self.input.move_screen(x, y)
            return
        raise ValueError(f"未知动作类型: {kind}")

    def execute(self, transition: Transition, dry_run=False):
        start = time.perf_counter()
        if dry_run:
            return TransitionResult(
                success=True,
                message=f"dry-run: {len(transition.actions)} 个动作",
                elapsed=time.perf_counter() - start,
                attempts=0,
            )
        attempts = max(1, transition.retries + 1)
        last_message = ""
        for attempt in range(1, attempts + 1):
            if self._aborted():
                return TransitionResult(False, "已中止", time.perf_counter() - start, attempt)
            if not self.input.available():
                return TransitionResult(
                    False,
                    "输入被拦截：目标窗口不在前台",
                    time.perf_counter() - start,
                    attempt,
                )
            if transition.require_from_scene:
                self.log(f"[{transition.name}] 等待起始场景 {transition.from_scene}")
                if not self._wait_for_scene(transition.from_scene, transition.from_timeout_s):
                    last_message = f"等待起始场景 {transition.from_scene} 超时"
                    self.log(f"[{transition.name}] {last_message}")
                    continue
            self.log(f"[{transition.name}] 执行 {len(transition.actions)} 个动作（第 {attempt} 次）")
            ok, message = self._run_actions(transition.actions)
            if not ok:
                last_message = message
                self.log(f"[{transition.name}] 动作失败: {message}")
                continue
            if transition.settle_s > 0 and not self._sleep(transition.settle_s):
                return TransitionResult(False, "已中止", time.perf_counter() - start, attempt)
            if transition.require_loading:
                self._wait_for_loading(min(transition.loading_timeout_s, 5.0))
            self.log(f"[{transition.name}] 等待目标场景 {transition.to_scene}")
            if self._wait_for_scene(transition.to_scene, transition.scene_timeout_s):
                elapsed = time.perf_counter() - start
                self.log(f"[{transition.name}] 跳转成功，用时 {elapsed:.2f}s")
                return TransitionResult(True, f"已到达 {transition.to_scene}", elapsed, attempt)
            last_message = f"等待目标场景 {transition.to_scene} 超时"
            self.log(f"[{transition.name}] {last_message}")
        return TransitionResult(False, last_message or "跳转失败", time.perf_counter() - start, attempts)


class DungeonRouter:
    def __init__(self, graph, recognizer, executor, frame_provider, log=None, abort_event=None):
        self.graph = graph
        self.recognizer = recognizer
        self.executor = executor
        self.frame_provider = frame_provider
        self.log = log or (lambda message: None)
        self.abort_event = abort_event
        self.poll_interval = recognizer.poll_interval

    def _aborted(self):
        return self.abort_event is not None and self.abort_event.is_set()

    def current_scene(self, timeout=3.0):
        deadline = time.perf_counter() + max(0.0, float(timeout))
        while time.perf_counter() < deadline:
            if self._aborted():
                return None
            frame = self.frame_provider()
            if frame is not None:
                state = self.recognizer.recognize(frame)
                if state.scene_id:
                    return state.scene_id
            time.sleep(self.poll_interval)
        return None

    def run_to(self, goal_scene):
        start = time.perf_counter()
        current = self.current_scene()
        if current is None:
            return RouteResult(False, "无法识别当前场景", [], time.perf_counter() - start)
        if current == goal_scene:
            return RouteResult(True, "已在目标场景", [], time.perf_counter() - start)
        route = self.graph.path(current, goal_scene)
        if route is None:
            return RouteResult(False, f"没有从 {current} 到 {goal_scene} 的路线", [], time.perf_counter() - start)
        names = []
        self.log(f"路线: {current} -> " + " -> ".join(transition.to_scene for transition in route))
        for transition in route:
            result = self.executor.execute(transition)
            if not result.success:
                return RouteResult(
                    False,
                    f"跳转 {transition.name} 失败: {result.message}",
                    names,
                    time.perf_counter() - start,
                )
            names.append(transition.name)
        return RouteResult(True, f"已到达 {goal_scene}", names, time.perf_counter() - start)
