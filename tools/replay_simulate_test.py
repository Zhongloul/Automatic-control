import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyzer import analyze_session, apply_suggestions
from src.config import create_default_config
from src.imageio import imwrite
from src.recorder import InputRecorder
from src.replay import ReplayPlayer
from src.session_store import (
    append_event,
    create_session,
    delete_session,
    list_sessions,
    load_events,
    read_meta,
    rename_session,
    save_events,
    write_meta,
)
from src.session_trim import trim_session

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


class FakeWindow:
    def __init__(self, foreground=True):
        self.foreground = foreground
        self.title = "Fake Game"
        self.window_class = "FakeClass"

    def is_foreground(self):
        return self.foreground

    def client_rect(self):
        return (100, 50, 1380, 770)


class FakeInput:
    def __init__(self, available=True):
        self.available_flag = available
        self.calls = []
        self.timeline = []
        self.release_count = 0

    def available(self):
        return self.available_flag

    def key_down(self, key):
        self.calls.append(("key_down", key))
        self.timeline.append((time.perf_counter(), "key_down", key))

    def key_up(self, key):
        self.calls.append(("key_up", key))
        self.timeline.append((time.perf_counter(), "key_up", key))

    def mouse_down_screen(self, x, y, button="left"):
        self.calls.append(("mouse_down", x, y, button))
        self.timeline.append((time.perf_counter(), "mouse_down", button))

    def mouse_up_screen(self, x=None, y=None, button="left"):
        self.calls.append(("mouse_up", x, y, button))
        self.timeline.append((time.perf_counter(), "mouse_up", button))

    def click_screen(self, x, y, button="left"):
        self.calls.append(("click", x, y, button))
        self.timeline.append((time.perf_counter(), "click", button))

    def move_screen(self, x, y):
        self.calls.append(("move", x, y))
        self.timeline.append((time.perf_counter(), "move", ""))

    def release_all(self):
        self.release_count += 1


def build_session(root):
    folder = create_session(
        root,
        {
            "window_title": "Fake Game",
            "client_size": [1280, 720],
            "keys": ["d", "x"],
        },
    )
    events = [
        {"t": 0.0, "type": "key_down", "key": "d"},
        {"t": 0.2, "type": "key_up", "key": "d"},
        {"t": 0.3, "type": "mouse_down", "button": "left", "pos": [0.5, 0.5]},
        {"t": 0.35, "type": "mouse_up", "button": "left", "pos": [0.5, 0.5]},
        {"t": 0.4, "type": "key_down", "key": "x"},
        {"t": 0.45, "type": "key_up", "key": "x"},
    ]
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    write_meta(
        folder,
        {
            "name": folder.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": 0.45,
            "event_count": len(events),
            "window_title": "Fake Game",
            "client_size": [1280, 720],
            "keys": ["d", "x"],
        },
    )
    return folder


def test_session_store(root):
    folder = build_session(root)
    infos = list_sessions(root)
    check("session listed", len(infos) == 1 and infos[0].name == folder.name, f"count={len(infos)}")
    events = load_events(folder)
    check("session events loaded", len(events) == 6, f"events={len(events)}")
    check("session preview limit", len(load_events(folder, limit=3)) == 3)
    events.append({"t": 0.5, "type": "checkpoint", "name": "wait_seconds", "seconds": 1.0})
    save_events(folder, events)
    reloaded = load_events(folder)
    check("session save events", len(reloaded) == 7 and reloaded[-1].get("name") == "wait_seconds")
    check("session backup created", (folder / "events.jsonl.bak").is_file())
    delete_session(folder)
    check("session deleted", not folder.exists())


def test_session_rename(root):
    target = build_session(root)
    source = create_session(
        root,
        {"window_title": "Fake Game", "client_size": [1280, 720], "keys": []},
    )
    branch_event = {
        "t": 0.0,
        "type": "checkpoint",
        "name": "branch",
        "cases": [{"source": "scene", "scene": "ok", "goto": {"session": target.name}}],
        "default": {"goto": {"session": target.name}},
    }
    with open(source / "events.jsonl", "a", encoding="utf-8") as handle:
        append_event(handle, branch_event)
    new_path, updated = rename_session(target, "成功路径")
    check("session rename path", new_path.is_dir() and not target.exists(), f"new={new_path}")
    check("session rename references", updated == 2, f"updated={updated}")
    renamed_events = load_events(source)
    goto_values = []
    if renamed_events:
        event = renamed_events[0]
        goto_values = [
            case.get("goto", {}).get("session") for case in event.get("cases") or []
        ]
        goto_values.append(event.get("default", {}).get("goto", {}).get("session"))
    check("session rename branch refs", goto_values == ["成功路径", "成功路径"], f"goto={goto_values}")
    infos = list_sessions(root)
    check(
        "session rename listed",
        any(info.name == "成功路径" for info in infos) and not any(info.name == target.name for info in infos),
        f"names={[info.name for info in infos]}",
    )
    try:
        rename_session(new_path, "bad/name")
        rejected = False
    except ValueError:
        rejected = True
    check("session rename invalid rejected", rejected)
    try:
        rename_session(new_path, source.name)
        collision = False
    except FileExistsError:
        collision = True
    check("session rename collision rejected", collision)
    delete_session(new_path)
    delete_session(source)


def test_session_trim(root):
    folder = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": []})
    events = [
        {"t": 0.5, "type": "checkpoint", "name": "sync"},
        {"t": 2.0, "type": "key_down", "key": "d"},
        {"t": 2.2, "type": "key_up", "key": "d"},
        {"t": 2.5, "type": "checkpoint", "name": "wait_seconds", "seconds": 0.1},
        {"t": 3.0, "type": "mouse_down", "button": "left", "pos": [0.5, 0.5]},
        {"t": 5.0, "type": "mouse_up", "button": "left", "pos": [0.5, 0.5]},
        {"t": 6.0, "type": "checkpoint", "name": "sync"},
    ]
    save_events(folder, events)
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    index_lines = []
    for second in range(1, 7):
        name = f"{second}.jpg"
        imwrite(frames_dir / name, np.zeros((180, 320, 3), dtype=np.uint8))
        index_lines.append(json.dumps({"t": float(second), "file": name}))
    (frames_dir / "index.jsonl").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    stats = trim_session(folder, total_elapsed=7.0)
    trimmed = load_events(folder)
    times = [round(float(event.get("t", 0)), 3) for event in trimmed]
    check("trim session times", times == [0.0, 0.2, 0.5, 1.0, 3.0], f"times={times}")
    check("trim session stats", stats["duration"] == 3.0 and stats["offset"] == 2.0 and stats["tail"] == 2.0, f"stats={stats}")
    meta = read_meta(folder)
    check(
        "trim session meta",
        meta.get("duration") == 3.0
        and meta.get("event_count") == 5
        and meta.get("frame_count") == 4
        and meta.get("trimmed_lead_s") == 2.0
        and meta.get("trimmed_tail_s") == 2.0,
        f"meta={meta}",
    )
    remaining = sorted(path.name for path in frames_dir.glob("*.jpg"))
    check("trim session frames", remaining == ["2.jpg", "3.jpg", "4.jpg", "5.jpg"], f"frames={remaining}")
    delete_session(folder)

    empty = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": []})
    save_events(empty, [{"t": 1.0, "type": "checkpoint", "name": "sync"}])
    no_action = trim_session(empty, total_elapsed=2.0)
    check(
        "trim no action unchanged",
        not no_action["trimmed"] and load_events(empty)[0]["t"] == 1.0,
        f"stats={no_action}",
    )
    delete_session(empty)


def test_replay(root):
    folder = build_session(root)
    config = create_default_config()
    config["replay"]["loop_gap_s"] = [0.01, 0.01]
    fake_input = FakeInput(True)
    fake_window = FakeWindow(True)
    progress = []

    player = ReplayPlayer(
        config,
        fake_window,
        fake_input,
        folder,
        speed=4.0,
        loops=2,
        progress=lambda *args: progress.append(args),
    )
    result = player.run()
    check("replay success", result.success, f"message={result.message}")
    check("replay loops", result.loops == 2, f"loops={result.loops}")
    check("replay event count", result.events == 12, f"events={result.events}")
    keys = [call for call in fake_input.calls if call[0] in ("key_down", "key_up")]
    check(
        "replay key order",
        keys == [
            ("key_down", "d"),
            ("key_up", "d"),
            ("key_down", "x"),
            ("key_up", "x"),
            ("key_down", "d"),
            ("key_up", "d"),
            ("key_down", "x"),
            ("key_up", "x"),
        ],
        f"keys={keys}",
    )
    clicks = [call for call in fake_input.calls if call[0] in ("mouse_down", "mouse_up")]
    check("replay mouse clicks", len(clicks) == 4, f"clicks={clicks}")
    if clicks:
        check("replay click position", clicks[0][1] == 740 and clicks[0][2] == 410, f"click={clicks[0]}")
    check("replay progress", progress and progress[-1][0] == 2 and progress[-1][1] == 2, f"last={progress[-1] if progress else None}")

    abort_player = ReplayPlayer(
        config,
        fake_window,
        FakeInput(True),
        folder,
        speed=4.0,
        loops=1,
        abort_event=__import__("threading").Event(),
    )
    abort_player.abort_event.set()
    abort_result = abort_player.run()
    check("replay abort", not abort_result.success, f"message={abort_result.message}")

    unavailable = ReplayPlayer(
        config,
        fake_window,
        FakeInput(False),
        folder,
        speed=4.0,
        loops=1,
    )
    unavailable_result = unavailable.run()
    check("replay blocked input", not unavailable_result.success, f"message={unavailable_result.message}")
    delete_session(folder)


def test_checkpoint_wait_scene(root):
    folder = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": ["d", "x"]})
    events = [
        {"t": 0.0, "type": "key_down", "key": "d"},
        {"t": 0.05, "type": "key_up", "key": "d"},
        {"t": 0.1, "type": "checkpoint", "name": "wait_visual", "source": "scene", "scene": "town"},
        {"t": 0.15, "type": "key_down", "key": "x"},
        {"t": 0.2, "type": "key_up", "key": "x"},
    ]
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    write_meta(
        folder,
        {
            "name": folder.name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": 0.2,
            "event_count": len(events),
            "window_title": "Fake Game",
            "client_size": [1280, 720],
            "keys": ["d", "x"],
        },
    )
    state = {"start": None, "ready_at": None}

    def scene_checker():
        if state["start"] is None:
            state["start"] = time.perf_counter()
        if time.perf_counter() - state["start"] < 0.3:
            return None
        if state["ready_at"] is None:
            state["ready_at"] = time.perf_counter()
        return "town"

    config = create_default_config()
    config["replay"]["checkpoint_settle_s"] = 0.05
    fake_input = FakeInput(True)
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        fake_input,
        folder,
        speed=1.0,
        loops=1,
        scene_checker=scene_checker,
    )
    result = player.run()
    x_calls = [item for item in fake_input.timeline if item[1] == "key_down" and item[2] == "x"]
    check("checkpoint wait_visual scene success", result.success, f"message={result.message}")
    check(
        "checkpoint delayed until scene",
        bool(x_calls) and state["ready_at"] is not None and x_calls[0][0] >= state["ready_at"],
        f"x={x_calls[0][0] if x_calls else None} ready={state['ready_at']}",
    )
    delete_session(folder)


def test_checkpoint_wait_visual_image(root):
    folder = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": ["x"]})
    reference = np.zeros((180, 320, 3), dtype=np.uint8)
    reference[:, :] = (60, 40, 20)
    reference[40:140, 80:240] = (30, 220, 30)
    imwrite(folder / "state_refs" / "ref.jpg", reference)
    events = [
        {"t": 0.0, "type": "checkpoint", "name": "wait_visual", "source": "image", "ref": "state_refs/ref.jpg", "threshold": 0.08},
        {"t": 5.0, "type": "key_down", "key": "x"},
        {"t": 5.1, "type": "key_up", "key": "x"},
    ]
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    config = create_default_config()
    config["replay"]["checkpoint_settle_s"] = 0.05
    config["replay"]["state_poll_interval_s"] = 0.02
    config["replay"]["state_confirm_frames"] = 2
    config["replay"]["checkpoint_timeout_s"] = 2.0
    state = {"start": None, "ready_at": None}

    def frame_getter():
        if state["start"] is None:
            state["start"] = time.perf_counter()
        if time.perf_counter() - state["start"] < 0.3:
            return np.zeros((720, 1280, 3), dtype=np.uint8)
        if state["ready_at"] is None:
            state["ready_at"] = time.perf_counter()
        return reference

    fake_input = FakeInput(True)
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        fake_input,
        folder,
        speed=1.0,
        loops=1,
        frame_getter=frame_getter,
    )
    started = time.perf_counter()
    result = player.run()
    elapsed = time.perf_counter() - started
    x_calls = [item for item in fake_input.timeline if item[1] == "key_down" and item[2] == "x"]
    check("checkpoint wait_visual image success", result.success, f"message={result.message}")
    check(
        "checkpoint wait_visual image matched",
        bool(x_calls) and state["ready_at"] is not None and x_calls[0][0] >= state["ready_at"],
        f"x={x_calls[0][0] if x_calls else None} ready={state['ready_at']}",
    )
    check("checkpoint wait_visual compressed timeline", elapsed < 2.5, f"elapsed={elapsed:.2f}")
    delete_session(folder)


def test_auto_checkpoint_suggestions(root):
    folder = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": []})
    events = [
        {"t": 0.0, "type": "mouse_down", "button": "left", "pos": [0.5, 0.5]},
        {"t": 0.1, "type": "mouse_up", "button": "left", "pos": [0.5, 0.5]},
        {"t": 11.0, "type": "mouse_down", "button": "left", "pos": [0.3, 0.6]},
        {"t": 11.1, "type": "mouse_up", "button": "left", "pos": [0.3, 0.6]},
    ]
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    index_lines = []
    for index in range(22):
        t = index * 0.5
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        if t < 1.0:
            image[:, :] = (60, 40, 20)
        elif t < 10.0:
            image[:, :] = (15, 15, 15)
        else:
            image[:, :] = (60, 40, 20)
            image[40:140, 80:240] = (30, 220, 30)
        name = f"{index:05d}.jpg"
        imwrite(frames_dir / name, image)
        index_lines.append(json.dumps({"t": round(t, 3), "file": name}))
    (frames_dir / "index.jsonl").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    result = analyze_session(folder, create_default_config())
    suggestions = result.get("suggestions") or []
    check("analyzer suggested checkpoint", len(suggestions) == 1, f"suggestions={len(suggestions)}")
    if suggestions:
        suggestion = suggestions[0]
        check(
            "analyzer created reference",
            suggestion.get("name") == "wait_visual"
            and suggestion.get("source") == "image"
            and (folder / suggestion.get("ref", "")).is_file(),
            f"ref={suggestion.get('ref')}",
        )
        added = apply_suggestions(folder, suggestions)
        check("analyzer applied checkpoint", added == 1, f"added={added}")
    delete_session(folder)


def test_sync_reference_learning(root):
    folder = create_session(root, {"window_title": "Fake Game", "client_size": [1280, 720], "keys": []})
    events = [
        {"t": 0.5, "type": "checkpoint", "name": "sync"},
        {"t": 11.0, "type": "mouse_down", "button": "left", "pos": [0.3, 0.6]},
    ]
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    frames_dir = folder / "frames"
    frames_dir.mkdir()
    index_lines = []
    for index in range(23):
        t = index * 0.5
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        image[:, :] = (15, 15, 15) if t < 10.0 else (60, 40, 20)
        if t >= 10.0:
            image[40:140, 80:240] = (30, 220, 30)
        name = f"{index:05d}.jpg"
        imwrite(frames_dir / name, image)
        index_lines.append(json.dumps({"t": round(t, 3), "file": name}))
    (frames_dir / "index.jsonl").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    recorder = InputRecorder(create_default_config(), FakeWindow(True), root, log=lambda message: None)
    recorder.folder = folder
    recorder._sync_learn_pending = [0.5]
    recorder._learn_sync_refs(11.0)
    recorder._apply_sync_refs()
    saved = load_events(folder)
    checkpoint = next(
        (event for event in saved if event.get("type") == "checkpoint" and event.get("name") == "sync"),
        None,
    )
    check(
        "sync learned reference",
        checkpoint is not None
        and bool(checkpoint.get("ref"))
        and (folder / str(checkpoint.get("ref"))).is_file(),
        f"checkpoint={checkpoint}",
    )
    if checkpoint:
        check(
            "sync reference threshold",
            0.0 < float(checkpoint.get("threshold", 0)) <= 0.3,
            f"threshold={checkpoint.get('threshold')}",
        )
    delete_session(folder)


def test_replay_loop_options(root):
    folder = build_session(root)
    config = create_default_config()
    config["replay"]["loop_gap_s"] = [0.5, 2.0]
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        FakeInput(True),
        folder,
        speed=4.0,
        loops=2,
        loop_gap_s=0.15,
    )
    check("replay loop gap override", abs(player._loop_gap() - 0.15) < 1e-6, f"gap={player._loop_gap()}")

    config["replay"]["loop_gap_s"] = 0.2
    numeric_player = ReplayPlayer(
        config,
        FakeWindow(True),
        FakeInput(True),
        folder,
        speed=4.0,
        loops=1,
    )
    check("replay loop gap numeric config", abs(numeric_player._loop_gap() - 0.2) < 1e-6)

    abort = __import__("threading").Event()
    progress = []

    def on_event(loop_index, loops_total, event_index, event_total, event):
        if loop_index == 3 and event_index == 0:
            abort.set()

    infinite_player = ReplayPlayer(
        config,
        FakeWindow(True),
        FakeInput(True),
        folder,
        speed=4.0,
        loops=0,
        loop_gap_s=0,
        abort_event=abort,
        event_callback=on_event,
        progress=lambda *args: progress.append(args),
    )
    result = infinite_player.run()
    check("infinite replay stops by abort", not result.success, f"message={result.message}")
    check("infinite replay ran loops", result.loops == 2, f"loops={result.loops}")
    check(
        "infinite replay progress total zero",
        bool(progress) and progress[-1][1] == 0,
        f"last={progress[-1] if progress else None}",
    )
    delete_session(folder)


def _write_test_session(root, events):
    folder = create_session(
        root,
        {"window_title": "Fake Game", "client_size": [1280, 720], "keys": ["d", "x", "a", "b", "c"]},
    )
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        for event in events:
            append_event(handle, event)
    return folder


def test_branch_checkpoint(root):
    ok_folder = _write_test_session(
        root,
        [
            {"t": 0.0, "type": "key_down", "key": "a"},
            {"t": 0.05, "type": "key_up", "key": "a"},
        ],
    )
    fail_folder = _write_test_session(
        root,
        [
            {"t": 0.0, "type": "key_down", "key": "b"},
            {"t": 0.05, "type": "key_up", "key": "b"},
        ],
    )
    retry_folder = _write_test_session(
        root,
        [
            {"t": 0.0, "type": "key_down", "key": "c"},
            {"t": 0.05, "type": "key_up", "key": "c"},
        ],
    )
    reference = np.zeros((180, 320, 3), dtype=np.uint8)
    reference[:, :] = (60, 40, 20)
    reference[40:140, 80:240] = (30, 220, 30)
    config = create_default_config()
    config["replay"]["checkpoint_settle_s"] = 0.02
    config["replay"]["state_poll_interval_s"] = 0.02
    config["replay"]["checkpoint_timeout_s"] = 2.0

    root_folder = _write_test_session(
        root,
        [
            {
                "t": 0.0,
                "type": "checkpoint",
                "name": "branch",
                "timeout_s": 2.0,
                "cases": [
                    {"source": "scene", "scene": "ok", "goto": {"session": ok_folder.name}},
                    {
                        "source": "image",
                        "ref": "state_refs/fail.jpg",
                        "threshold": 0.2,
                        "goto": {"session": fail_folder.name},
                    },
                ],
                "default": {"goto": {"session": retry_folder.name}},
            },
            {"t": 0.1, "type": "key_down", "key": "x"},
            {"t": 0.15, "type": "key_up", "key": "x"},
        ],
    )
    imwrite(root_folder / "state_refs" / "fail.jpg", reference)
    state = {"start": None}

    def scene_checker():
        if state["start"] is None:
            state["start"] = time.perf_counter()
        return "ok" if time.perf_counter() - state["start"] >= 0.2 else None

    fake_input = FakeInput(True)
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        fake_input,
        root_folder,
        speed=1.0,
        loops=1,
        loop_gap_s=0,
        scene_checker=scene_checker,
    )
    result = player.run()
    keys = [call for call in fake_input.calls if call[0] in ("key_down", "key_up")]
    check("branch scene success", result.success, f"message={result.message}")
    check(
        "branch scene target executed only",
        [call[1] for call in keys] == ["a", "a"],
        f"keys={keys}",
    )

    image_root = _write_test_session(
        root,
        [
            {
                "t": 0.0,
                "type": "checkpoint",
                "name": "branch",
                "timeout_s": 2.0,
                "cases": [
                    {
                        "source": "image",
                        "ref": "state_refs/fail.jpg",
                        "threshold": 0.2,
                        "goto": {"session": fail_folder.name},
                    }
                ],
            },
            {"t": 0.1, "type": "key_down", "key": "x"},
            {"t": 0.15, "type": "key_up", "key": "x"},
        ],
    )
    imwrite(image_root / "state_refs" / "fail.jpg", reference)
    state = {"start": None}

    def frame_getter():
        if state["start"] is None:
            state["start"] = time.perf_counter()
        if time.perf_counter() - state["start"] < 0.2:
            return np.zeros((720, 1280, 3), dtype=np.uint8)
        return reference

    fake_input = FakeInput(True)
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        fake_input,
        image_root,
        speed=1.0,
        loops=1,
        loop_gap_s=0,
        frame_getter=frame_getter,
    )
    result = player.run()
    keys = [call for call in fake_input.calls if call[0] in ("key_down", "key_up")]
    check("branch image success", result.success, f"message={result.message}")
    check(
        "branch image target executed only",
        [call[1] for call in keys] == ["b", "b"],
        f"keys={keys}",
    )

    timeout_root = _write_test_session(
        root,
        [
            {
                "t": 0.0,
                "type": "checkpoint",
                "name": "branch",
                "timeout_s": 0.3,
                "cases": [
                    {
                        "source": "image",
                        "ref": "state_refs/fail.jpg",
                        "threshold": 0.01,
                        "goto": {"session": fail_folder.name},
                    }
                ],
                "default": {"goto": {"session": retry_folder.name}},
            },
            {"t": 0.1, "type": "key_down", "key": "x"},
            {"t": 0.15, "type": "key_up", "key": "x"},
        ],
    )
    imwrite(timeout_root / "state_refs" / "fail.jpg", reference)
    fake_input = FakeInput(True)
    player = ReplayPlayer(
        config,
        FakeWindow(True),
        fake_input,
        timeout_root,
        speed=1.0,
        loops=1,
        loop_gap_s=0,
        frame_getter=lambda: np.zeros((720, 1280, 3), dtype=np.uint8),
    )
    result = player.run()
    keys = [call for call in fake_input.calls if call[0] in ("key_down", "key_up")]
    check("branch timeout default", result.success, f"message={result.message}")
    check(
        "branch timeout default target",
        [call[1] for call in keys] == ["c", "c"],
        f"keys={keys}",
    )
    for folder in (ok_folder, fail_folder, retry_folder, root_folder, image_root, timeout_root):
        delete_session(folder)


def test_recorder_lifecycle(root):
    fake_window = FakeWindow(True)
    config = create_default_config()
    config["recorder"]["poll_hz"] = 100
    config["recorder"]["trim_idle"] = False
    recorder = InputRecorder(config, fake_window, root, log=lambda message: None)
    folder = recorder.start()
    time.sleep(0.1)
    inserted = recorder.insert_checkpoint("sync")
    time.sleep(0.15)
    result = recorder.stop()
    check("recorder created session", folder is not None and folder.is_dir(), f"folder={folder}")
    check("recorder stopped", not recorder.recording)
    check("recorder checkpoint inserted", inserted)
    infos = list_sessions(root)
    check("recorder meta listed", any(info.path == folder for info in infos))
    if folder is not None:
        events = load_events(folder)
        check("recorder checkpoint saved", any(event.get("type") == "checkpoint" for event in events), f"events={events[:3]}")
        meta = __import__("json").loads((folder / "meta.json").read_text(encoding="utf-8"))
        check("recorder duration", float(meta.get("duration", 0)) > 0.1, f"duration={meta.get('duration')}")
        delete_session(folder)


def main():
    print("=== 录制/回放逻辑仿真测试 ===")
    root = ROOT / "captures" / "replay_sim_root"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        test_session_store(root)
        test_session_rename(root)
        test_session_trim(root)
        test_replay(root)
        test_checkpoint_wait_scene(root)
        test_checkpoint_wait_visual_image(root)
        test_sync_reference_learning(root)
        test_auto_checkpoint_suggestions(root)
        test_replay_loop_options(root)
        test_branch_checkpoint(root)
        test_recorder_lifecycle(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
