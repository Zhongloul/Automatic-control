import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import create_default_config
from src.imageio import imread, imwrite
from src.map_graph import MapGraph, TransitionExecutor
from src.scene import (
    SceneRecognizer,
    delete_marker,
    delete_scene,
    list_markers,
    save_marker,
    validate_scene_id,
)
from src.scene_ui import validate_actions

PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[PASS] {name} {detail}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} {detail}")


def build_root():
    root = ROOT / "captures" / "scene_sim_root"
    shutil.rmtree(root, ignore_errors=True)
    for scene_id, seed in (("town", 11), ("dungeon", 22)):
        folder = root / "templates" / "scenes" / scene_id
        folder.mkdir(parents=True)
        rng = np.random.RandomState(seed)
        marker = rng.randint(1, 255, (36, 110, 3), dtype=np.uint8)
        imwrite(folder / "marker.png", marker)
    return root


def build_frame(root, scene_id):
    rng = np.random.RandomState(2026)
    frame = rng.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    marker = imread(root / "templates" / "scenes" / scene_id / "marker.png")
    frame[70:106, 80:190] = marker
    return frame


def build_config():
    config = create_default_config()
    config["scenes"]["confirm_frames"] = 1
    config["scenes"]["poll_interval_s"] = 0.02
    config["scenes"]["loading"]["confirm_frames"] = 1
    config["scenes"]["loading"]["max_mean_brightness"] = 5.0
    config["scenes"]["loading"]["max_std"] = 5.0
    config["scenes"]["profiles"] = {
        "town": {"threshold": 0.95},
        "dungeon": {"threshold": 0.95},
    }
    return config


def test_recognition(root):
    config = build_config()
    recognizer = SceneRecognizer(config, root)
    check("scene profiles loaded", set(recognizer.scene_ids) == {"town", "dungeon"}, f"ids={recognizer.scene_ids}")

    town = build_frame(root, "town")
    dungeon = build_frame(root, "dungeon")
    loading = np.zeros((720, 1280, 3), dtype=np.uint8)

    state = recognizer.recognize(town)
    check("recognize town", state.scene_id == "town", f"state={state.scene_id} score={state.score:.3f}")

    state = recognizer.recognize(loading)
    check("recognize loading", state.loading is True, f"loading={state.loading}")

    state = recognizer.recognize(dungeon)
    check("recognize dungeon", state.scene_id == "dungeon", f"state={state.scene_id} score={state.score:.3f}")

    state = recognizer.recognize(dungeon)
    check("scene stable no change", state.changed is False, f"changed={state.changed}")


def test_marker_store(root):
    image = np.zeros((24, 36, 3), dtype=np.uint8)
    image[:, :] = (30, 160, 220)
    path = save_marker(root, "extra_scene", image)
    check("marker saved", path.is_file(), f"path={path.name}")
    check("marker listed", path in list_markers(root, "extra_scene"))
    check("invalid scene id rejected", not validate_scene_id("bad/name"))
    delete_marker(path)
    check("marker deleted", not path.exists())
    delete_scene(root, "extra_scene")
    check("scene deleted", not (root / "templates" / "scenes" / "extra_scene").exists())

    ok, _message = validate_actions([{"type": "key_tap", "key": "f"}])
    check("action validation ok", ok)
    bad_ok, _message = validate_actions([{"type": "click", "pos": [1.5, 0.5]}])
    check("action validation rejects bad pos", not bad_ok)


def test_map_graph():
    config = build_config()
    config["map_graph"] = {
        "enabled": True,
        "start_scene": "town",
        "transitions": [
            {"name": "town_to_dungeon", "from": "town", "to": "dungeon", "actions": []},
            {"name": "dungeon_to_boss", "from": "dungeon", "to": "boss", "actions": []},
        ],
    }
    graph = MapGraph(config)
    route = graph.path("town", "boss")
    check("map graph route", route is not None and [item.name for item in route] == ["town_to_dungeon", "dungeon_to_boss"], f"route={route and [item.name for item in route]}")
    check("map graph missing route", graph.path("boss", "town") is None, "boss->town")


class FakeInput:
    def __init__(self, available=True):
        self.available_flag = available
        self.calls = []

    def available(self):
        return self.available_flag

    def tap(self, key, duration=None):
        self.calls.append(("tap", key))

    def hold(self, key, seconds):
        self.calls.append(("hold", key, seconds))

    def click_screen(self, x, y, button="left"):
        self.calls.append(("click", x, y, button))

    def move_screen(self, x, y):
        self.calls.append(("move", x, y))


def test_transition(root):
    config = build_config()
    config["map_graph"] = {
        "enabled": True,
        "transitions": [
            {
                "name": "town_to_dungeon",
                "from": "town",
                "to": "dungeon",
                "actions": [
                    {"type": "key_tap", "key": "f"},
                    {"type": "wait", "seconds": 0.02},
                ],
                "retries": 0,
                "require_from_scene": True,
                "from_timeout_s": 2.0,
                "settle_s": 0.02,
                "require_loading": True,
                "loading_timeout_s": 1.0,
                "scene_timeout_s": 3.0,
            }
        ],
    }
    town = build_frame(root, "town")
    dungeon = build_frame(root, "dungeon")
    loading = np.zeros((720, 1280, 3), dtype=np.uint8)
    start = time.perf_counter()

    def provider():
        elapsed = time.perf_counter() - start
        if elapsed < 0.15:
            return town
        if elapsed < 0.45:
            return loading
        return dungeon

    recognizer = SceneRecognizer(config, root)
    fake_input = FakeInput(True)
    executor = TransitionExecutor(
        config,
        recognizer,
        fake_input,
        provider,
        lambda: (0, 0, 1280, 720),
        log=lambda message: None,
    )
    transition = MapGraph(config).get("town_to_dungeon")
    result = executor.execute(transition)
    check("transition success", result.success, f"message={result.message} elapsed={result.elapsed:.2f}")
    check("transition sent key", ("tap", "f") in fake_input.calls, f"calls={fake_input.calls}")

    blocked_input = FakeInput(False)
    executor2 = TransitionExecutor(
        config,
        SceneRecognizer(config, root),
        blocked_input,
        lambda: town,
        lambda: (0, 0, 1280, 720),
        log=lambda message: None,
    )
    result2 = executor2.execute(transition)
    check("transition blocked input", not result2.success, f"message={result2.message}")


def main():
    print("=== 场景与跨图逻辑仿真测试 ===")
    root = build_root()
    try:
        test_recognition(root)
        test_marker_store(root)
        test_map_graph()
        test_transition(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
