import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
GA_ROOT = 2
SW_RESTORE = 9


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def window_class(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def list_windows():
    result = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        if not title:
            return True
        result.append({"hwnd": hwnd, "title": title, "class": window_class(hwnd)})
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


def client_rect(hwnd):
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 4 or height < 4:
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    left = origin.x
    top = origin.y
    return (left, top, left + width, top + height)


def is_foreground(hwnd):
    if not hwnd:
        return False
    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return True
    root = user32.GetAncestor(foreground, GA_ROOT)
    return root == hwnd


def activate(hwnd):
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    return is_foreground(hwnd)


class GameWindow:
    def __init__(self, title_keyword="", class_keyword=""):
        self.title_keyword = (title_keyword or "").strip()
        self.class_keyword = (class_keyword or "").strip().lower()
        self.hwnd = None
        self.title = ""
        self.window_class = ""

    def find(self):
        if not self.title_keyword and not self.class_keyword:
            self.hwnd = None
            return False
        keyword = self.title_keyword.lower()
        candidates = []
        for info in list_windows():
            title = info["title"]
            cls = info["class"]
            if keyword and keyword not in title.lower():
                continue
            if self.class_keyword and self.class_keyword not in cls.lower():
                continue
            rect = client_rect(info["hwnd"])
            if rect is None:
                continue
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            if width < 200 or height < 120:
                continue
            candidates.append((width * height, info, rect))
        if not candidates:
            self.hwnd = None
            return False
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, info, _ = candidates[0]
        self.hwnd = info["hwnd"]
        self.title = info["title"]
        self.window_class = info["class"]
        return True

    def refresh(self):
        if self.hwnd and user32.IsWindow(self.hwnd):
            return True
        return self.find()

    def client_rect(self):
        if not self.hwnd:
            return None
        return client_rect(self.hwnd)

    def is_foreground(self):
        return is_foreground(self.hwnd)

    def activate(self):
        return activate(self.hwnd)

    def describe(self):
        if not self.hwnd:
            return "未找到窗口"
        rect = client_rect(self.hwnd)
        size = f"{rect[2] - rect[0]}x{rect[3] - rect[1]}" if rect else "unknown"
        return f"{self.title} | class={self.window_class} | {size}"
