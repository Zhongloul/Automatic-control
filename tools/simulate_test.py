import random
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import create_default_config
from src.decision import CombatBrain
from src.humanize import Humanizer
from src.imageio import imwrite
from src.perception import Perception, TemplateBank

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


def build_config():
    config = create_default_config()
    config["bars"]["hp"]["roi"] = [0.05, 0.05, 0.2, 0.03]
    config["bars"]["mp"]["roi"] = [0.05, 0.09, 0.2, 0.03]
    config["skills"]["bar_roi"] = [0.4, 0.9, 0.3, 0.08]
    config["skills"]["slot_gap_ratio"] = 0.0
    config["skills"]["slot_count"] = 6
    config["player"]["anchor"] = [0.5, 0.6]
    config["humanize"]["hesitate_prob"] = 0.0
    config["humanize"]["aim_jitter_ratio"] = 0.0
    config["humanize"]["hold_ms"] = [40, 40]
    config["humanize"]["decision_ms"] = [100, 100]
    return config


def draw_scene(config):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (30, 30, 30)

    hp_x, hp_y, hp_w, hp_h = 64, 36, 256, 21
    frame[hp_y : hp_y + hp_h, hp_x : hp_x + int(hp_w * 0.85)] = (0, 0, 220)
    mp_x, mp_y, mp_w, mp_h = 64, 64, 256, 21
    frame[mp_y : mp_y + mp_h, mp_x : mp_x + int(mp_w * 0.60)] = (220, 0, 0)

    bar_x, bar_y, bar_w, bar_h = 512, 648, 384, 57
    slot_w = round(bar_w / 6)
    colors = [(0, 180, 0), (80, 80, 80)] * 3
    for index, color in enumerate(colors):
        x = bar_x + index * slot_w
        frame[bar_y : bar_y + bar_h, x : x + slot_w] = color
    return frame


def test_perception():
    config = build_config()
    frame = draw_scene(config)
    perception = Perception(config, ROOT)
    state = perception.perceive(frame)

    check("HP ratio", state["hp"] is not None and 0.82 <= state["hp"] <= 0.88, f"hp={state['hp']}")
    check("MP ratio", state["mp"] is not None and 0.57 <= state["mp"] <= 0.63, f"mp={state['mp']}")

    skills = state["skills"]
    check("skill slot count", len(skills) == 6, f"count={len(skills)}")
    expected = [True, False, True, False, True, False]
    actual = [slot["ready"] for slot in skills]
    check("skill ready pattern", actual == expected, f"actual={actual}")

    check("player anchor", state["player"] == (640.0, 432.0), f"player={state['player']}")
    check("no enemy without templates", state["enemies"] == [], f"enemies={len(state['enemies'])}")


def test_template_matching():
    temp_dir = ROOT / "captures" / "sim_templates"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)

    rng = np.random.RandomState(42)
    sprite = rng.randint(1, 255, size=(40, 24, 3), dtype=np.uint8)
    saved = imwrite(temp_dir / "enemy_test.png", sprite)
    check("template file write", saved, str(temp_dir))

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (20, 20, 20)
    frame[300:340, 700:724] = sprite
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    bank = TemplateBank(temp_dir, threshold=0.9, iou_threshold=0.3)
    detections = bank.detect(gray)
    check("template detection count", len(detections) == 1, f"count={len(detections)}")
    if detections:
        detection = detections[0]
        check(
            "template detection position",
            abs(detection["x"] - 700) <= 2 and abs(detection["y"] - 300) <= 2,
            f"x={detection['x']} y={detection['y']}",
        )

    shutil.rmtree(temp_dir, ignore_errors=True)


def make_state(enemy_dx=250, enemy_dy=80):
    return {
        "size": (1280, 720),
        "hp": 1.0,
        "mp": 1.0,
        "skills": [],
        "player": (640.0, 432.0),
        "enemies": [{"cx": 640.0 + enemy_dx, "cy": 432.0 + enemy_dy, "w": 24, "h": 40, "score": 0.95}],
    }


def test_decision_sequence():
    config = build_config()
    humanizer = Humanizer(config["humanize"], random.Random(1))
    brain = CombatBrain(config, humanizer, random.Random(2))
    state = make_state()
    now = 100.0
    actions = []
    keys = []
    for _ in range(40):
        action = brain.update(state, now)
        actions.append(action)
        if action.key:
            keys.append(action.key)
        if action.key == "down":
            state["enemies"][0]["cy"] -= 30
        elif action.key == "up":
            state["enemies"][0]["cy"] += 30
        elif action.key == "right":
            state["enemies"][0]["cx"] -= 40
        elif action.key == "left":
            state["enemies"][0]["cx"] += 40
        now += 0.1

    check("align down first", keys and keys[0] == "down", f"actions={[a.describe() for a in actions[:5]]}")
    check("approach right", "right" in keys, f"keys={keys[:12]}")
    attack_keys = {"x", "q", "w", "e", "r"}
    check("attack executed", any(key in attack_keys for key in keys), f"keys={keys}")


def test_potion_and_danger():
    config = build_config()
    humanizer = Humanizer(config["humanize"], random.Random(3))
    brain = CombatBrain(config, humanizer, random.Random(4))
    state = make_state()
    state["hp"] = 0.2
    now = 200.0
    action = brain.update(state, now)
    check("potion at low hp", action.key == "1", f"action={action.describe()}")

    brain = CombatBrain(config, humanizer, random.Random(5))
    now = 300.0
    state = make_state()
    state["hp"] = 1.0
    brain.update(state, now)
    state["hp"] = 0.75
    action = brain.update(state, now + 0.5)
    check("retreat on hp drop", action.key == "c", f"action={action.describe()}")


def test_navigation():
    config = build_config()
    humanizer = Humanizer(config["humanize"], random.Random(6))
    brain = CombatBrain(config, humanizer, random.Random(7))
    state = make_state()
    state["enemies"] = []
    actions = [brain.update(state, 400.0 + index * 0.1) for index in range(10)]
    keys = {action.key for action in actions if action.key}
    check("navigation moves", bool(keys & {"left", "right", "c"}), f"keys={sorted(key for key in keys if key)}")


def main():
    print("=== 离线仿真测试 ===")
    test_perception()
    test_template_matching()
    test_decision_sequence()
    test_potion_and_danger()
    test_navigation()
    print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
