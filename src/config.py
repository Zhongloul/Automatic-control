import copy
import json
import sys
from pathlib import Path

DEFAULT_RECORD_KEYS = (
    [chr(code) for code in range(ord("a"), ord("z") + 1)]
    + [str(digit) for digit in range(10)]
    + ["space", "shift", "ctrl", "alt", "enter", "tab", "up", "down", "left", "right"]
)

DEFAULT_CONFIG = {
    "window": {
        "title_keyword": "",
        "class_keyword": "",
        "auto_activate": True,
    },
    "capture": {
        "backend": "auto",
        "fps": 45,
        "display_scale": 1.0,
    },
    "perception": {
        "gameplay_roi": [0.0, 0.0, 1.0, 1.0],
        "enemy_threshold": 0.8,
        "player_threshold": 0.72,
        "nms_iou": 0.35,
    },
    "player": {
        "anchor": [0.5, 0.6],
        "template": "templates/player/player.png",
    },
    "bars": {
        "hp": {
            "roi": None,
            "hsv_ranges": [
                [[0, 120, 60], [10, 255, 255]],
                [[170, 120, 60], [180, 255, 255]],
            ],
            "min_column_ratio": 0.25,
        },
        "mp": {
            "roi": None,
            "hsv_ranges": [
                [[95, 100, 60], [135, 255, 255]],
            ],
            "min_column_ratio": 0.25,
        },
    },
    "skills": {
        "bar_roi": None,
        "slot_rois": None,
        "slot_count": 6,
        "slot_gap_ratio": 0.15,
        "gray_saturation_max": 55,
        "value_ready_min": 200,
        "center_ratio": 0.6,
    },
    "scenes": {
        "default_threshold": 0.82,
        "confirm_frames": 2,
        "poll_interval_s": 0.2,
        "loading": {
            "enabled": True,
            "max_mean_brightness": 18.0,
            "max_std": 12.0,
            "confirm_frames": 3,
        },
        "profiles": {},
    },
    "map_graph": {
        "enabled": False,
        "start_scene": "",
        "click_margin_ratio": 0.0,
        "transitions": [],
    },
    "combat": {
        "attack_range_ratio": 0.095,
        "y_tolerance_ratio": 0.022,
        "x_move_gain": 0.45,
        "y_move_gain": 0.35,
        "min_move_s": 0.03,
        "max_move_s": 0.22,
        "attack_gap_s": [0.12, 0.25],
        "search_direction": "right",
        "search_step_s": [0.08, 0.16],
        "search_segment_s": [2.0, 4.0],
        "navigate_jump_key": "c",
        "navigate_jump_prob": 0.08,
        "basic_attack": {
            "key": "x",
            "range_ratio": 0.06,
            "y_tolerance_ratio": 0.03,
        },
        "potion": {
            "key": "1",
            "hp_threshold": 0.35,
            "cooldown_s": 8.0,
        },
        "skills": [
            {"key": "q", "slot": 0, "range_ratio": 0.10, "y_tolerance_ratio": 0.035, "cooldown_s": 4.0, "weight": 8},
            {"key": "w", "slot": 1, "range_ratio": 0.09, "y_tolerance_ratio": 0.030, "cooldown_s": 6.0, "weight": 10},
            {"key": "e", "slot": 2, "range_ratio": 0.12, "y_tolerance_ratio": 0.040, "cooldown_s": 8.0, "weight": 12},
            {"key": "r", "slot": 3, "range_ratio": 0.14, "y_tolerance_ratio": 0.050, "cooldown_s": 15.0, "weight": 15},
        ],
        "danger": {
            "hp_drop_window_s": 1.2,
            "hp_drop_threshold": 0.12,
            "retreat_s": 0.5,
            "retreat_key": "c",
        },
    },
    "humanize": {
        "hold_ms": [35, 85],
        "decision_ms": [70, 130],
        "hesitate_prob": 0.02,
        "hesitate_ms": [100, 260],
        "cooldown_jitter_s": [0.0, 0.35],
        "aim_jitter_ratio": 0.002,
    },
    "recorder": {
        "sessions_dir": "sessions",
        "poll_hz": 125,
        "record_mouse_move": False,
        "frame_sample_hz": 4.0,
        "frame_max_width": 480,
        "keys": DEFAULT_RECORD_KEYS,
    },
    "replay": {
        "speed": 1.0,
        "loops": 1,
        "loop_gap_s": [0.5, 2.0],
        "foreground_timeout_s": 15.0,
        "checkpoint_settle_s": 0.4,
        "checkpoint_immediate": True,
        "state_poll_interval_s": 0.1,
        "state_confirm_frames": 2,
        "checkpoint_timeout_s": 30.0,
        "max_duration_s": 3600.0,
    },
    "analyzer": {
        "min_gap_s": 1.5,
        "change_threshold": 0.05,
        "baseline_offset_s": 0.35,
        "target_lead_s": 0.25,
        "target_window_s": 1.0,
        "threshold_floor": 0.05,
        "threshold_cap": 0.22,
        "checkpoint_offset_s": 0.05,
    },
    "dungeon_loop": {
        "loops": 0,
        "clear_hold_s": 2.0,
        "combat_timeout_s": 120.0,
        "enemy_seen_timeout_s": 15.0,
        "interval_s": 2.0,
        "nav_retries": 1,
    },
    "hotkeys": {
        "toggle": "f8",
        "quit": "f9",
        "debug": "f1",
        "record_toggle": "f6",
        "record_stop": "f7",
        "record_checkpoint": "f5",
    },
    "debug": {
        "enabled": True,
        "show_fps": True,
    },
    "safety": {
        "dry_run": False,
        "require_foreground": True,
        "start_running": False,
        "countdown_s": 3,
    },
}


def create_default_config():
    return copy.deepcopy(DEFAULT_CONFIG)


def _deep_merge(base, override):
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path):
    path = Path(path)
    if not path.exists():
        save_config(path, DEFAULT_CONFIG)
        return create_default_config()
    with open(path, "r", encoding="utf-8") as handle:
        user_config = json.load(handle)
    return _deep_merge(DEFAULT_CONFIG, user_config)


def save_config(path, config):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def project_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
