import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.capture import FrameSource
from src.config import load_config
from src.imageio import imread
from src.input import InputController
from src.map_graph import DungeonRouter, MapGraph, TransitionExecutor
from src.scene import SceneRecognizer
from src.window import GameWindow, set_dpi_awareness


def describe_state(state):
    if state is None:
        return "无结果"
    if state.loading:
        return "加载中"
    if state.scene_id:
        return f"场景={state.scene_id} 分数={state.score:.3f}"
    if state.candidate:
        return f"待确认={state.candidate} 分数={state.candidate_score:.3f}"
    return "未知场景"


class LiveCapture:
    def __init__(self, config):
        self.window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
        if not self.window.find():
            raise RuntimeError("未找到游戏窗口，请先在 config.json 或 GUI 中锁定窗口")
        self.source = FrameSource(config["capture"].get("backend", "auto"))
        print(f"窗口: {self.window.describe()} | 截屏: {self.source.backend}")

    def grab(self):
        return self.source.grab(self.window.client_rect())

    def client_rect(self):
        return self.window.client_rect()

    def close(self):
        self.source.close()


def print_list(config):
    recognizer = SceneRecognizer(config, ROOT)
    print("场景模板:")
    if recognizer.scene_ids:
        for scene_id in recognizer.scene_ids:
            profile = recognizer.profiles[scene_id]
            print(
                f"  - {scene_id}: 模板 {len(profile.templates)} 个"
                f" | 阈值 {profile.threshold:.2f} | 模式 {profile.mode}"
                f" | 哈希 {'有' if profile.hash_bits is not None else '无'}"
            )
    else:
        print("  (无，请在 templates/scenes/<场景名>/ 放 PNG 截图)")
    for warning in recognizer.warnings:
        print(f"  [警告] {warning}")

    graph = MapGraph(config)
    print("地图跳转:")
    if graph.transitions:
        for transition in graph.transitions:
            print(
                f"  - {transition.name}: {transition.from_scene} -> {transition.to_scene}"
                f" | 动作 {len(transition.actions)} 个 | 重试 {transition.retries}"
            )
    else:
        print("  (无，请在 config.json 的 map_graph.transitions 中配置)")
    for warning in graph.warnings:
        print(f"  [警告] {warning}")


def run_image(config, image_path):
    recognizer = SceneRecognizer(config, ROOT)
    frame = imread(image_path)
    if frame is None:
        print(f"无法读取图片: {image_path}")
        return 1
    state = None
    for _ in range(max(1, recognizer.confirm_frames)):
        state = recognizer.recognize(frame)
    print(f"图片: {image_path}")
    print(f"结果: {describe_state(state)}")
    return 0 if state.scene_id or state.loading else 2


def run_wait(config, target, timeout):
    recognizer = SceneRecognizer(config, ROOT)
    if not recognizer.enabled:
        print("没有场景模板，无法等待目标场景。")
        return 1
    live = LiveCapture(config)
    deadline = time.perf_counter() + max(1.0, float(timeout))
    state = None
    last_text = None
    try:
        while time.perf_counter() < deadline:
            frame = live.grab()
            if frame is not None:
                state = recognizer.recognize(frame)
                text = describe_state(state)
                if text != last_text:
                    print(text)
                    last_text = text
                if state.scene_id == target:
                    print(f"已到达目标场景: {target}")
                    return 0
            time.sleep(recognizer.poll_interval)
    finally:
        live.close()
    print(f"等待场景 {target} 超时")
    return 1


def build_transition_tools(config, live, execute, abort_event=None):
    recognizer = SceneRecognizer(config, ROOT)
    Graph = MapGraph(config)
    input_controller = InputController(
        gate=live.window.is_foreground,
        hold_provider=None,
        dry_run=not execute,
    )
    executor = TransitionExecutor(
        config,
        recognizer,
        input_controller,
        live.grab,
        live.client_rect,
        log=print,
        abort_event=abort_event,
    )
    return recognizer, Graph, input_controller, executor


def run_transition(config, name, execute, countdown):
    graph = MapGraph(config)
    transition = graph.get(name)
    if transition is None:
        print(f"没有名为 {name} 的跳转配置。现有跳转:")
        for item in graph.transitions:
            print(f"  - {item.name}")
        return 1
    print("跳转配置:")
    print(json.dumps(
        {
            "name": transition.name,
            "from": transition.from_scene,
            "to": transition.to_scene,
            "actions": transition.actions,
        },
        ensure_ascii=False,
        indent=2,
    ))
    live = LiveCapture(config)
    input_controller = None
    try:
        _recognizer, _graph, input_controller, executor = build_transition_tools(config, live, execute)
        if execute:
            for remaining in range(max(0, int(countdown)), 0, -1):
                print(f"{remaining} 秒后执行，请切到游戏窗口...")
                time.sleep(1.0)
        result = executor.execute(transition, dry_run=not execute)
        print(f"结果: success={result.success} message={result.message} time={result.elapsed:.2f}s")
        return 0 if result.success else 1
    finally:
        input_controller.release_all()
        live.close()


def run_route(config, goal, execute, countdown):
    graph = MapGraph(config)
    live = LiveCapture(config)
    input_controller = None
    try:
        _recognizer, _graph, input_controller, executor = build_transition_tools(config, live, execute)
        router = DungeonRouter(graph, _recognizer, executor, live.grab, log=print)
        if not execute:
            current = router.current_scene(timeout=3.0)
            if current is None:
                print("无法识别当前场景，无法规划路线")
                return 1
            route = graph.path(current, goal)
            if route is None:
                print(f"没有从 {current} 到 {goal} 的路线")
                return 1
            print(f"路线: {current} -> " + " -> ".join(item.to_scene for item in route))
            for item in route:
                print(f"  - {item.name}: {item.from_scene} -> {item.to_scene}")
            return 0
        for remaining in range(max(0, int(countdown)), 0, -1):
            print(f"{remaining} 秒后执行，请切到游戏窗口...")
            time.sleep(1.0)
        result = router.run_to(goal)
        print(f"结果: success={result.success} message={result.message} time={result.elapsed:.2f}s")
        return 0 if result.success else 1
    finally:
        if input_controller is not None:
            input_controller.release_all()
        live.close()


def main():
    parser = argparse.ArgumentParser(description="场景识别与跨图跳转测试工具")
    parser.add_argument("--config", default=None)
    parser.add_argument("--list", action="store_true", help="列出场景模板和跳转配置")
    parser.add_argument("--image", default=None, help="识别单张截图")
    parser.add_argument("--wait", default=None, help="等待指定场景出现")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--transition", default=None, help="执行指定跳转")
    parser.add_argument("--route", default=None, help="规划/执行到目标场景的路线")
    parser.add_argument("--execute", action="store_true", help="真正发送按键，默认只演练")
    parser.add_argument("--countdown", type=int, default=3)
    args = parser.parse_args()

    set_dpi_awareness()
    config_path = Path(args.config) if args.config else ROOT / "config.json"
    config = load_config(config_path)

    if args.list:
        print_list(config)
        return 0
    if args.image:
        return run_image(config, args.image)
    if args.wait:
        return run_wait(config, args.wait, args.timeout)
    if args.transition:
        return run_transition(config, args.transition, args.execute, args.countdown)
    if args.route:
        return run_route(config, args.route, args.execute, args.countdown)

    print_list(config)
    print("\n用法示例:")
    print("  python tools/scene_test.py --image captures/test.png")
    print("  python tools/scene_test.py --wait dungeon_1 --timeout 30")
    print("  python tools/scene_test.py --transition town_to_dungeon")
    print("  python tools/scene_test.py --transition town_to_dungeon --execute")
    print("  python tools/scene_test.py --route dungeon_1 --execute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
