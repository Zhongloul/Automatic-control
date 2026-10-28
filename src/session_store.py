import json
import os
import shutil
import stat
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


def _clear_readonly(path):
    path = Path(path)
    if not path.exists():
        return
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def _replace_with_retry(source, target, attempts=5):
    target = Path(target)
    for attempt in range(attempts):
        try:
            _clear_readonly(target)
            os.replace(source, target)
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(0.3)


def prepare_session(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    _clear_readonly(folder)
    for name in ("events.jsonl", "events.jsonl.bak", "meta.json"):
        _clear_readonly(folder / name)
    probe = folder / ".write_test"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        raise PermissionError(f"会话目录不可写: {folder} ({exc})") from exc


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _clear_readonly(path.parent)
    temp = path.with_name(path.name + ".tmp")
    _clear_readonly(temp)
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    _replace_with_retry(temp, path)


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
    prepare_session(folder)
    path = folder / "events.jsonl"
    if path.is_file():
        try:
            backup = folder / "events.jsonl.bak"
            _clear_readonly(backup)
            shutil.copy2(path, backup)
            _clear_readonly(backup)
        except OSError:
            pass
    temp = folder / "events.jsonl.tmp"
    _clear_readonly(temp)
    with open(temp, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    _replace_with_retry(temp, path)


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
    for folder in base.iterdir():
        if not folder.is_dir() or not (folder / "events.jsonl").is_file():
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
    infos.sort(key=lambda info: (info.created_at, info.name), reverse=True)
    return infos


def validate_session_name(name):
    name = str(name or "").strip()
    if not name:
        return "名称不能为空"
    if name in (".", ".."):
        return "名称无效"
    if any(character in name for character in '<>:"/\\|?*'):
        return '名称不能包含 < > : " / \\ | ? *'
    if name.endswith((" ", ".")):
        return "名称不能以空格或点结尾"
    if len(name) > 80:
        return "名称不能超过 80 个字符"
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{index}" for index in range(1, 10))
    reserved.update(f"LPT{index}" for index in range(1, 10))
    if name.split(".")[0].upper() in reserved:
        return "名称不能使用 Windows 保留字"
    return ""


def _update_branch_references(base, old_name, new_name):
    changed_files = 0
    changed_refs = 0
    for folder in Path(base).iterdir():
        if not folder.is_dir() or not (folder / "events.jsonl").is_file():
            continue
        try:
            events = load_events(folder)
        except OSError:
            continue
        changed = 0
        for event in events:
            if str(event.get("name") or "").lower() != "branch":
                continue
            cases = event.get("cases")
            if isinstance(cases, list):
                for case in cases:
                    if not isinstance(case, dict):
                        continue
                    goto = case.get("goto")
                    if isinstance(goto, dict) and str(goto.get("session") or "") == old_name:
                        goto["session"] = new_name
                        changed += 1
            default = event.get("default")
            if isinstance(default, dict):
                goto = default.get("goto") if isinstance(default.get("goto"), dict) else default
                if str(goto.get("session") or "") == old_name:
                    goto["session"] = new_name
                    changed += 1
        if changed:
            try:
                save_events(folder, events)
            except OSError:
                continue
            changed_files += 1
            changed_refs += changed
    return changed_refs


def rename_session(folder, new_name):
    folder = Path(folder)
    new_name = str(new_name or "").strip()
    error = validate_session_name(new_name)
    if error:
        raise ValueError(error)
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    if folder.name == new_name:
        return folder, 0
    target = folder.parent / new_name
    if target.exists():
        raise FileExistsError(f"会话已存在: {new_name}")
    old_name = folder.name
    folder.rename(target)
    meta = read_meta(target)
    meta["name"] = new_name
    try:
        write_meta(target, meta)
    except OSError:
        pass
    updated = _update_branch_references(target.parent, old_name, new_name)
    return target, updated


def delete_session(folder):
    folder = Path(folder)
    if folder.is_dir():
        shutil.rmtree(folder)
