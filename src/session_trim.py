import json
from pathlib import Path

from .keyframes import frames_dir, load_frame_index
from .session_store import load_events, read_meta, save_events, write_meta

BOUNDARY_TYPES = {"key_down", "key_up", "mouse_down", "mouse_up", "click"}


def trim_events(events, trim_head=True, trim_tail=True, total_elapsed=None):
    items = [dict(event) for event in events]
    items.sort(key=lambda event: float(event.get("t", 0.0) or 0.0))
    if not items or (not trim_head and not trim_tail):
        duration = float(total_elapsed or 0.0)
        return {
            "events": items,
            "start": 0.0,
            "end": duration,
            "duration": duration,
            "trimmed": False,
            "dropped": 0,
        }
    indexes = [
        index
        for index, event in enumerate(items)
        if str(event.get("type") or "").lower() in BOUNDARY_TYPES
    ]
    if not indexes:
        duration = float(total_elapsed or 0.0)
        return {
            "events": items,
            "start": 0.0,
            "end": duration,
            "duration": duration,
            "trimmed": False,
            "dropped": 0,
        }
    first_t = float(items[indexes[0]].get("t", 0.0) or 0.0)
    last_t = float(items[indexes[-1]].get("t", 0.0) or 0.0)
    if last_t < first_t:
        first_t, last_t = last_t, first_t
    original_end = float(total_elapsed or 0.0)
    if original_end < last_t:
        original_end = last_t
    start = first_t if trim_head else 0.0
    end = last_t if trim_tail else original_end
    if end < start:
        end = start
    kept = []
    for event in items:
        event_t = float(event.get("t", 0.0) or 0.0)
        if start <= event_t <= end:
            item = dict(event)
            item["t"] = round(event_t - start, 4)
            kept.append(item)
    return {
        "events": kept,
        "start": start,
        "end": end,
        "duration": max(0.0, end - start),
        "trimmed": start > 0 or end < original_end or len(kept) != len(items),
        "dropped": len(items) - len(kept),
    }


def trim_frames(folder, start, end):
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


def trim_session(folder, enabled=True, trim_head=True, trim_tail=True, total_elapsed=None):
    folder = Path(folder)
    events = load_events(folder)
    result = trim_events(
        events,
        trim_head=trim_head if enabled else False,
        trim_tail=trim_tail if enabled else False,
        total_elapsed=total_elapsed,
    )
    if not result["trimmed"]:
        return {
            "trimmed": False,
            "offset": 0.0,
            "tail": 0.0,
            "duration": result["duration"],
            "events": len(result["events"]),
            "frames": _current_frame_count(folder),
        }
    start = result["start"]
    end = result["end"]
    original_end = float(total_elapsed or 0.0)
    if original_end < end:
        original_end = end
    tail = max(0.0, original_end - end)
    try:
        save_events(folder, result["events"])
    except OSError:
        pass
    frame_count = trim_frames(folder, start, end)
    meta = read_meta(folder)
    meta["duration"] = round(result["duration"], 3)
    meta["event_count"] = len(result["events"])
    meta["frame_count"] = frame_count
    if start > 0:
        meta["trimmed_lead_s"] = round(start, 3)
    if tail > 0:
        meta["trimmed_tail_s"] = round(tail, 3)
    write_meta(folder, meta)
    return {
        "trimmed": True,
        "offset": start,
        "tail": tail,
        "duration": result["duration"],
        "events": len(result["events"]),
        "frames": frame_count,
        "dropped": result["dropped"],
    }
