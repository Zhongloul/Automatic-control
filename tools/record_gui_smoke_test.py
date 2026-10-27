import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui
from src.session_store import delete_session
from src.window import set_dpi_awareness


def main():
    set_dpi_awareness()
    app = gui.AutomationApp()
    errors = []
    state = {"session": None}

    def report(exc, value, tb):
        traceback.print_exception(exc, value, tb)
        errors.append(str(value))

    app.report_callback_exception = report
    app.config["window"]["title_keyword"] = app.title()
    app.config["safety"]["countdown_s"] = 1
    app._rebuild_engine()
    if not app.engine.active:
        print("ENGINE_NOT_ACTIVE")
        app._on_close()
        return 1

    def start():
        app._start_recording()

    def stop():
        folder = app.recorder.folder if app.recorder is not None else None
        app._stop_recording()
        state["session"] = folder or app.selected_session

    def finish():
        session = state["session"]
        if session is not None:
            delete_session(session)
            app.selected_session = None
            app._refresh_sessions()
        app.after(200, app._on_close)

    app.after(500, start)
    app.after(3200, stop)
    app.after(3800, finish)
    app.mainloop()
    if errors:
        print(f"RECORD_GUI_ERRORS={len(errors)}")
        return 1
    if state["session"] is None:
        print("NO_SESSION")
        return 1
    print("RECORD_GUI_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
