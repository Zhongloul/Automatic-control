import json
from pathlib import Path

from .keyframes import frames_dir, load_frame_index
from .session_store import load_events, read_meta, save_events, write_meta

BOUNDARY_TYPES = {"key_down", "key_up", "mouse_down", "mouse_up", "click"}


def _trim_frames(folder, start, end):
    entries = load_frame_index(folder)
    if not entries:
        return 0
    kept = []
    keep_names = set()
    for entry in entries:
        t = float(entry["t"])
        if start <= t <= end:
            kept.append({"t": round(t - start, 3), "file": entry["file"]})
            keep_names.add(entry["file"])
    index_path = frames_dir(folder) / "index.jsonl"
    with open(index_path, "w", encoding="utf-8") as handle:
        for entry in kept:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    for path in frames_dir(folder).glob("*.jpg"):
        if path.name not in keep_names:
            try:
                path.unlink()
            except OSError:
                pass
    return len(kept)


def _current_frame_count(folder):
    try:
        return len(load_frame_index(folder))
    except OSError:
        return 0


def trim_session(folder, enabled=True, total_elapsed=None):
    folder = Path(folder)
    events = load_events(folder)
    events.sort(key=lambda event: float(event.get("t", 0.0) or 0.0))
    if not events:
        return {
            "trimmed": False,
            "offset": 0.0,
            "tail": 0.0,
            "duration": 0.0,
            "events": 0,
            "frames": _current_frame_count(folder),
        }
    if not enabled:
        return {
            "trimmed": False,
            "offset": 0.0,
            "tail": 0.0,
            "duration": float(total_elapsed or 0.0),
            "events": len(events),
            "frames": _current_frame_count(folder),
        }
    indexes = [
        index
        for index, event in enumerate(events)
        if str(event.get("type") or "").lower() in BOUNDARY_TYPES
    ]
    if not indexes:
        return {
            "trimmed": False,
            "offset": 0.0,
            "tail": 0.0,
            "duration": float(total_elapsed or 0.0),
            "events": len(events),
            "frames": _current_frame_count(folder),
        }
    first_index = indexes[0]
    last_index = indexes[-1]
    first_t = float(events[first_index].get("t", 0.0) or 0.0)
    last_t = float(events[last_index].get("t", 0.0) or 0.0)
    if last_t < first_t:
        first_t, last_t = last_t, first_t
    duration = max(0.0, last_t - first_t)
    kept_events = []
    for event in events[first_index : last_index + 1]:
        item = dict(event)
        item["t"] = round(float(item.get("t", 0.0) or 0.0) - first_t, 4)
        kept_events.append(item)
    dropped = len(events) - len(kept_events)
    if dropped or first_t > 0:
        save_events(folder, kept_events)
    frame_count = _trim_frames(folder, first_t, last_t)
    original_end = float(total_elapsed or 0.0)
    if original_end < last_t:
        original_end = last_t
    tail = max(0.0, original_end - last_t)
    meta = read_meta(folder)
    meta["duration"] = round(duration, 3)
    meta["event_count"] = len(kept_events)
    meta["frame_count"] = frame_count
    if first_t > 0:
        meta["trimmed_lead_s"] = round(first_t, 3)
    if tail > 0:
        meta["trimmed_tail_s"] = round(tail, 3)
    write_meta(folder, meta)
    return {
        "trimmed": first_t > 0 or tail > 0 or dropped > 0,
        "offset": first_t,
        "tail": tail,
        "duration": duration,
        "events": len(kept_events),
        "frames": frame_count,
        "dropped": dropped,
    }
