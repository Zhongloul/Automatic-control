import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionInfo:
    name: str
    path: Path
    created_at: str = ""
    duration: float = 0.0
    event_count: int = 0
    window_title: str = ""
    client_size: list = field(default_factory=list)
    keys: list = field(default_factory=list)


def sessions_dir(root):
    return Path(root) / "sessions"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def create_session(root, meta):
    base = sessions_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("replay_%Y%m%d_%H%M%S")
    folder = base / stamp
    index = 1
    while folder.exists():
        folder = base / f"{stamp}_{index}"
        index += 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events.jsonl").touch()
    data = {
        "name": folder.name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": 0.0,
        "event_count": 0,
        **meta,
    }
    _write_json(folder / "meta.json", data)
    return folder


def write_meta(folder, meta):
    _write_json(Path(folder) / "meta.json", meta)


def read_meta(folder):
    path = Path(folder) / "meta.json"
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def append_event(handle, event):
    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    handle.flush()


def save_events(folder, events):
    folder = Path(folder)
    path = folder / "events.jsonl"
    if path.is_file():
        try:
            shutil.copy2(path, folder / "events.jsonl.bak")
        except OSError:
            pass
    temp = folder / "events.jsonl.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def load_events(folder, limit=None):
    path = Path(folder) / "events.jsonl"
    events = []
    if not path.is_file():
        return events
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
                if limit is not None and len(events) >= limit:
                    break
    return events


def list_sessions(root):
    base = sessions_dir(root)
    if not base.is_dir():
        return []
    infos = []
    for folder in sorted(base.glob("replay_*"), reverse=True):
        if not folder.is_dir():
            continue
        meta = read_meta(folder)
        event_count = int(meta.get("event_count", 0) or 0)
        if event_count == 0:
            events_path = folder / "events.jsonl"
            if events_path.is_file():
                with open(events_path, "r", encoding="utf-8") as handle:
                    event_count = sum(1 for line in handle if line.strip())
        infos.append(
            SessionInfo(
                name=folder.name,
                path=folder,
                created_at=str(meta.get("created_at", "")),
                duration=float(meta.get("duration", 0.0) or 0.0),
                event_count=event_count,
                window_title=str(meta.get("window_title", "")),
                client_size=list(meta.get("client_size") or []),
                keys=list(meta.get("keys") or []),
            )
        )
    return infos


def delete_session(folder):
    folder = Path(folder)
    if folder.is_dir():
        shutil.rmtree(folder)
