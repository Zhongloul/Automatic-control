import cv2

from .overlay import draw_overlay


class DebugView:
    def __init__(self, config):
        self.config = config
        self.enabled = bool(config["debug"].get("enabled", True))
        self.scale = float(config["capture"].get("display_scale", 1.0)) or 1.0
        self.window_name = "auto_play_debug"
        self.window_created = False

    def toggle(self):
        self.enabled = not self.enabled
        if not self.enabled:
            self.destroy()
        return self.enabled

    def prepare(self):
        if not self.enabled:
            return
        self._ensure_window()

    def _ensure_window(self):
        if self.window_created:
            return True
        try:
            cv2.namedWindow(self.window_name, cv2.WINDOW_AUTOSIZE)
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_TOPMOST, 1)
            self.window_created = True
            return True
        except cv2.error:
            self.window_created = False
            return False

    def render(self, frame, state, status_lines):
        if not self.enabled:
            return
        if frame is None:
            if self.window_created:
                cv2.waitKey(1)
            return
        if not self._ensure_window():
            return
        canvas = draw_overlay(frame.copy(), state, status_lines)
        if self.scale != 1.0:
            canvas = cv2.resize(canvas, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        try:
            cv2.imshow(self.window_name, canvas)
            cv2.waitKey(1)
            if cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1:
                self.enabled = False
                self.window_created = False
        except cv2.error:
            self.window_created = False

    def destroy(self):
        if self.window_created:
            try:
                cv2.destroyWindow(self.window_name)
            except cv2.error:
                pass
            self.window_created = False
