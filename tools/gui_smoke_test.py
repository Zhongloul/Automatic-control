import os
import sys
import time
import traceback
from pathlib import Path

os.environ["KILO_HIDDEN_UI"] = "1"

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui
from src.replay_ui import CheckpointEditorWindow
from src.scene import delete_scene, save_marker
from src.scene_ui import SceneManagerWindow, TransitionEditorWindow
from src.session_store import append_event, create_session, delete_session, write_meta
from src.ui_common import icon_path
from src.window import set_dpi_awareness


def build_frame():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (40, 40, 40)
    frame[20:42, 30:230] = (0, 0, 220)
    frame[56:74, 30:230] = (220, 0, 0)
    for index in range(6):
        color = (0, 180, 0) if index % 2 == 0 else (80, 80, 80)
        frame[650:686, 500 + index * 42 : 536 + index * 42] = color
    return frame


def build_state():
    return {
        "size": (1280, 720),
        "hp": 0.85,
        "mp": 0.62,
        "skills": [{"ready": True}, {"ready": False}, {"ready": True}, {"ready": False}, {"ready": True}, {"ready": False}],
        "enemies": [
            {"x": 800, "y": 300, "w": 30, "h": 50, "cx": 815.0, "cy": 325.0, "score": 0.92, "name": "enemy_1.png"},
        ],
        "player": (640.0, 400.0),
        "rects": {
            "hp": (30, 20, 200, 22),
            "mp": (30, 56, 200, 18),
            "skills": [(500 + index * 42, 650, 36, 36) for index in range(6)],
            "gameplay": (0, 0, 1280, 720),
        },
    }


def main():
    set_dpi_awareness()
    app = gui.AutomationApp()
    assert not app.winfo_viewable(), "GUI smoke test must run hidden"
    assert icon_path() is not None, "application icon missing"
    errors = []

    def report(exc, value, tb):
        traceback.print_exception(exc, value, tb)
        errors.append(str(value))

    app.report_callback_exception = report
    frame = build_frame()

    def inject():
        app.engine._preview_frame = frame
        app.engine._state = build_state()
        app._force_preview = True
        app._tick()
        dialog = gui.CalibrationWindow(app, app.config, app.root, frame, app.config_path)
        app.after(900, dialog.destroy)

    state = {"manager": None, "editor": None, "session": None}

    def create_smoke_session():
        folder = create_session(
            app.root,
            {"window_title": "smoke", "client_size": [1280, 720], "keys": ["d"]},
        )
        with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
            append_event(handle, {"t": 0.0, "type": "key_down", "key": "d"})
            append_event(handle, {"t": 0.1, "type": "key_up", "key": "d"})
            append_event(handle, {"t": 0.2, "type": "checkpoint", "name": "sync"})
        write_meta(
            folder,
            {
                "name": folder.name,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": 0.2,
                "event_count": 3,
                "window_title": "smoke",
                "client_size": [1280, 720],
                "keys": ["d"],
            },
        )
        state["session"] = folder
        app._refresh_sessions(folder)
        assert app.selected_session == folder

    def open_manager():
        save_marker(app.root, "smoke_scene", frame[20:50, 30:120])
        manager = SceneManagerWindow(
            app,
            app.root,
            app.config,
            app.config_path,
            get_frame=lambda: frame,
            apply_config=lambda config: True,
            log=app._log,
        )
        state["manager"] = manager
        assert "smoke_scene" in manager.scene_ids
        manager._refresh_scenes(select="smoke_scene")

        def check_manager():
            assert manager.marker_paths, "scene marker list is empty"
            assert manager.marker_list.size() > 0, "scene marker listbox is empty"
            open_editor()

        app.after(400, check_manager)

    def open_editor():
        editor = TransitionEditorWindow(
            app,
            {
                "name": "smoke",
                "from": "smoke_scene",
                "to": "smoke_scene",
                "actions": [{"type": "key_tap", "key": "f"}],
            },
            ["smoke_scene"],
            lambda data: None,
        )
        state["editor"] = editor
        app.after(400, editor._cancel)

    def finish():
        if state["manager"] is not None:
            state["manager"].destroy()
        delete_scene(app.root, "smoke_scene")
        if state["session"] is not None:
            delete_session(state["session"])
            app.selected_session = None
            app._refresh_sessions()
        app.after(200, app._on_close)

    def step_session():
        create_smoke_session()
        app.after(300, step_checkpoints)

    def step_checkpoints():
        editor = CheckpointEditorWindow(
            app,
            app.root,
            state["session"],
            on_saved=lambda: None,
        )
        assert len(editor._checkpoint_refs) == 1
        original_count = len(editor.events)
        editor._push_undo()
        editor.events.append({"t": 9.0, "type": "checkpoint", "name": "sync"})
        editor._refresh_event_list()
        editor._refresh_checkpoint_list()
        editor._undo()
        assert len(editor.events) == original_count
        editor._redo()
        assert len(editor.events) == original_count + 1
        editor._undo()
        assert len(editor.events) == original_count
        editor.event_list.selection_clear(0, "end")
        editor.event_list.selection_set(1)
        editor._on_event_select()
        editor.time_var.set("0.500")
        editor._auto_apply_time()
        assert any(
            event.get("type") == "key_down" and abs(float(event.get("t", 0)) - 0.5) < 1e-6
            for event in editor.events
        )
        editor._undo()
        assert any(
            event.get("type") == "key_down" and abs(float(event.get("t", 0))) < 1e-6
            for event in editor.events
        )
        before_create = len(editor.events)
        editor._on_add_button()
        assert len(editor.events) == before_create + 1
        editor._undo()
        assert len(editor.events) == before_create
        editor._form_mode = "checkpoint"
        editor._selected_event = None
        editor.event_list.selection_clear(0, "end")
        editor.checkpoint_list.selection_clear(0, "end")
        editor._update_field_state()
        editor.type_var.set("branch")
        editor._update_field_state()
        assert editor.type_label.winfo_manager() == "grid"
        assert editor.branch_label.winfo_manager() == "grid"
        assert editor.threshold_entry.winfo_manager() == ""
        editor._open_branch_editor()
        branch_window = editor._branch_window
        assert branch_window is not None and not branch_window.winfo_viewable()

        def close_branch():
            branch_window._cancel()
            editor._on_close()

        app.after(400, close_branch)
        app.after(700, step_manager)

    def step_manager():
        open_manager()
        app.after(1500, finish)

    def step_calibration():
        inject()
        app.after(1200, step_session)

    app.after(500, step_calibration)
    app.mainloop()
    if errors:
        print(f"GUI_SMOKE_ERRORS={len(errors)}")
        return 1
    print("GUI_SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
