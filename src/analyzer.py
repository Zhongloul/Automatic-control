from pathlib import Path

from .keyframes import (
    estimate_threshold,
    find_frame,
    load_frame,
    load_frame_index,
    make_signature,
    save_reference,
    signature_diff,
)
from .session_store import load_events, read_meta, save_events, write_meta

ANALYZER_DEFAULTS = {
    "min_gap_s": 1.5,
    "change_threshold": 0.05,
    "baseline_offset_s": 0.35,
    "target_lead_s": 0.25,
    "target_window_s": 1.0,
    "threshold_floor": 0.05,
    "threshold_cap": 0.22,
    "checkpoint_offset_s": 0.05,
}


def _settings(config):
    merged = dict(ANALYZER_DEFAULTS)
    if isinstance(config, dict):
        user = config.get("analyzer") or {}
        if isinstance(user, dict):
            for key, value in user.items():
                if key in merged:
                    try:
                        merged[key] = float(value)
                    except (TypeError, ValueError):
                        continue
    for key in ("min_gap_s", "change_threshold"):
        if merged[key] < 0:
            merged[key] = ANALYZER_DEFAULTS[key]
    return merged


def _event_time(event):
    try:
        return float(event.get("t", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _is_checkpoint(event):
    return str(event.get("type") or "").lower() == "checkpoint"


def _held_at(events, index):
    keys = set()
    buttons = set()
    for event in events[: index + 1]:
        kind = str(event.get("type") or "").lower()
        if kind == "key_down":
            keys.add(str(event.get("key") or ""))
        elif kind == "key_up":
            keys.discard(str(event.get("key") or ""))
        elif kind == "mouse_down":
            buttons.add(str(event.get("button") or "left"))
        elif kind == "mouse_up":
            buttons.discard(str(event.get("button") or "left"))
    return bool(keys or buttons)


def analyze_session(folder, config=None):
    folder = Path(folder)
    settings = _settings(config)
    events = load_events(folder)
    events.sort(key=_event_time)
    frames = load_frame_index(folder)
    result = {
        "suggestions": [],
        "frame_count": len(frames),
        "reason": "",
    }
    if not events:
        result["reason"] = "会话没有事件"
        return result
    if not frames:
        result["reason"] = "该会话没有录制画面（旧版本录制或录制时未锁定窗口），请重新录制后再分析"
        return result

    actions = [event for event in events if not _is_checkpoint(event)]
    checkpoints = [event for event in events if _is_checkpoint(event)]
    min_gap = settings["min_gap_s"]
    action_positions = {id(event): index for index, event in enumerate(events)}

    for first, second in zip(actions, actions[1:]):
        first_t = _event_time(first)
        second_t = _event_time(second)
        gap = second_t - first_t
        if gap < min_gap:
            continue
        if any(first_t <= _event_time(event) < second_t for event in checkpoints):
            continue
        position = action_positions.get(id(first))
        if position is None or _held_at(events, position):
            continue

        baseline_entry = find_frame(
            frames,
            first_t + settings["baseline_offset_s"],
            latest=second_t - 0.05,
            earliest=first_t,
        )
        target_entry = find_frame(
            frames,
            second_t - settings["target_lead_s"],
            latest=second_t,
            earliest=second_t - settings["target_window_s"],
        )
        if baseline_entry is None or target_entry is None:
            continue
        if abs(baseline_entry["t"] - (first_t + settings["baseline_offset_s"])) > 0.8:
            continue
        if abs(target_entry["t"] - (second_t - settings["target_lead_s"])) > 0.6:
            continue

        baseline_image = load_frame(folder, baseline_entry)
        target_image = load_frame(folder, target_entry)
        baseline_sig = make_signature(baseline_image)
        target_sig = make_signature(target_image)
        if baseline_sig is None or target_sig is None:
            continue
        change = signature_diff(baseline_sig, target_sig)
        if change < settings["change_threshold"]:
            continue

        threshold = estimate_threshold(
            frames,
            folder,
            target_sig,
            second_t,
            floor=settings["threshold_floor"],
            cap=settings["threshold_cap"],
        )
        reference_name = f"state_{len(result['suggestions']) + 1:02d}_{second_t:.2f}.jpg"
        reference = save_reference(folder, target_image, reference_name)
        if not reference:
            continue
        suggestions = result["suggestions"]
        suggestions.append(
            {
                "t": round(first_t + settings["checkpoint_offset_s"], 3),
                "type": "checkpoint",
                "name": "wait_visual",
                "source": "image",
                "ref": reference,
                "threshold": round(threshold, 4),
                "roi": [0.0, 0.0, 1.0, 1.0],
                "note": (
                    f"{first_t:.2f}s 后空闲 {gap:.1f}s，画面变化 {change:.2f}，"
                    f"下一动作 {second_t:.2f}s {str(second.get('type') or '')}"
                ),
                "gap_s": round(gap, 3),
                "score": round(change, 4),
                "next_t": round(second_t, 3),
            }
        )
    if not result["suggestions"]:
        result["reason"] = "没有发现需要同步的长空闲段"
    return result


def apply_suggestions(folder, suggestions):
    folder = Path(folder)
    events = load_events(folder)
    existing = {
        (
            round(_event_time(event), 3),
            str(event.get("name") or ""),
            str(event.get("ref") or ""),
        )
        for event in events
        if _is_checkpoint(event)
    }
    added = 0
    for suggestion in suggestions:
        key = (
            round(float(suggestion.get("t", 0.0)), 3),
            str(suggestion.get("name") or ""),
            str(suggestion.get("ref") or ""),
        )
        if key in existing:
            continue
        event = {
            "t": round(float(suggestion.get("t", 0.0)), 3),
            "type": "checkpoint",
            "name": "wait_visual",
            "source": "image",
            "ref": str(suggestion.get("ref") or ""),
            "threshold": round(float(suggestion.get("threshold", 0.1)), 4),
            "roi": list(suggestion.get("roi") or [0.0, 0.0, 1.0, 1.0]),
        }
        events.append(event)
        existing.add(key)
        added += 1
    if not added:
        return 0
    events.sort(key=_event_time)
    save_events(folder, events)
    meta = read_meta(folder)
    meta["event_count"] = len(events)
    meta["duration"] = round(max((_event_time(event) for event in events), default=0.0), 3)
    write_meta(folder, meta)
    return added
