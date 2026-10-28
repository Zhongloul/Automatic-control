import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("data", _INPUTUNION),
    ]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

MOUSE_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

VK_MAP = {}
for _letter in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[_letter] = ord(_letter.upper())
for _digit in "0123456789":
    VK_MAP[_digit] = ord(_digit)
VK_MAP.update(
    {
        "space": 0x20,
        "enter": 0x0D,
        "return": 0x0D,
        "esc": 0x1B,
        "escape": 0x1B,
        "tab": 0x09,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "insert": 0x2D,
        "delete": 0x2E,
        "del": 0x2E,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "numlock": 0x90,
        "capslock": 0x14,
        "backspace": 0x08,
        "`": 0xC0,
        "-": 0xBD,
        "=": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        "\\": 0xDC,
        ";": 0xBA,
        "'": 0xDE,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
        "printscreen": 0x2C,
        "pause": 0x13,
        "shift_l": 0xA0,
        "shift_r": 0xA1,
        "ctrl_l": 0xA2,
        "ctrl_r": 0xA3,
        "alt_l": 0xA4,
        "alt_r": 0xA5,
        "apps": 0x5D,
        "menu": 0x5D,
        "win": 0x5B,
        "lwin": 0x5B,
        "rwin": 0x5C,
        "numpad0": 0x60,
        "numpad1": 0x61,
        "numpad2": 0x62,
        "numpad3": 0x63,
        "numpad4": 0x64,
        "numpad5": 0x65,
        "numpad6": 0x66,
        "numpad7": 0x67,
        "numpad8": 0x68,
        "numpad9": 0x69,
        "numpad_multiply": 0x6A,
        "numpad_add": 0x6B,
        "numpad_separator": 0x6C,
        "numpad_subtract": 0x6D,
        "numpad_decimal": 0x6E,
        "numpad_divide": 0x6F,
        "browser_back": 0xA6,
        "browser_forward": 0xA7,
        "browser_refresh": 0xA8,
        "browser_stop": 0xA9,
        "browser_search": 0xAA,
        "browser_favorites": 0xAB,
        "browser_home": 0xAC,
        "volume_mute": 0xAD,
        "volume_down": 0xAE,
        "volume_up": 0xAF,
        "media_next": 0xB0,
        "media_prev": 0xB1,
        "media_stop": 0xB2,
        "media_play": 0xB3,
        "launch_mail": 0xB4,
        "launch_media": 0xB5,
        "launch_app1": 0xB6,
        "launch_app2": 0xB7,
    }
)
for _index in range(1, 25):
    VK_MAP[f"f{_index}"] = 0x6F + _index

EXTENDED_KEYS = {
    "up",
    "down",
    "left",
    "right",
    "insert",
    "delete",
    "del",
    "home",
    "end",
    "pageup",
    "pagedown",
    "numlock",
    "printscreen",
    "ctrl_r",
    "alt_r",
    "apps",
    "menu",
    "win",
    "lwin",
    "rwin",
    "numpad_divide",
}

VK_ONLY_KEYS = {
    "pause",
    "numpad_separator",
    "browser_back",
    "browser_forward",
    "browser_refresh",
    "browser_stop",
    "browser_search",
    "browser_favorites",
    "browser_home",
    "volume_mute",
    "volume_down",
    "volume_up",
    "media_next",
    "media_prev",
    "media_stop",
    "media_play",
    "launch_mail",
    "launch_media",
    "launch_app1",
    "launch_app2",
}


def resolve_key(name):
    if isinstance(name, int):
        vk = name
        extended = False
        key = ""
    else:
        key = str(name).strip().lower()
        if key not in VK_MAP:
            raise ValueError(f"不支持的按键名: {name}")
        vk = VK_MAP[key]
        extended = key in EXTENDED_KEYS
    scan = 0 if key in VK_ONLY_KEYS else user32.MapVirtualKeyW(vk, 0)
    return vk, scan, extended


def _send_key(vk, scan, extended, keyup):
    flags = 0
    if scan:
        flags |= KEYEVENTF_SCANCODE
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    event = INPUT(
        type=INPUT_KEYBOARD,
        data=_INPUTUNION(
            ki=KEYBDINPUT(
                wVk=0 if scan else int(vk),
                wScan=int(scan),
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def _send_mouse(flags, dx=0, dy=0, data=0):
    event = INPUT(
        type=INPUT_MOUSE,
        data=_INPUTUNION(
            mi=MOUSEINPUT(
                dx=int(dx),
                dy=int(dy),
                mouseData=int(data),
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


class InputController:
    def __init__(self, gate=None, hold_provider=None, dry_run=False):
        self.gate = gate
        self.hold_provider = hold_provider
        self.dry_run = dry_run
        self.pressed = set()
        self.mouse_buttons = set()

    def _allowed(self):
        if self.dry_run:
            return False
        if self.gate is not None and not self.gate():
            return False
        return True

    def available(self):
        return self._allowed()

    def key_down(self, name):
        key = str(name).strip().lower()
        if key in self.pressed:
            return True
        if not self._allowed():
            return False
        vk, scan, extended = resolve_key(key)
        _send_key(vk, scan, extended, False)
        self.pressed.add(key)
        return True

    def key_up(self, name):
        key = str(name).strip().lower()
        self.pressed.discard(key)
        vk, scan, extended = resolve_key(key)
        _send_key(vk, scan, extended, True)

    def hold(self, name, duration=None):
        if duration is None:
            duration = self.hold_provider() if self.hold_provider else 0.05
        duration = max(0.005, float(duration))
        if not self.key_down(name):
            return
        try:
            time.sleep(duration)
        finally:
            self.key_up(name)

    def tap(self, name, duration=None):
        self.hold(name, duration)

    def wait(self, duration):
        time.sleep(max(0.0, float(duration or 0.0)))

    def move_screen(self, x, y):
        if not self._allowed():
            return False
        user32.SetCursorPos(int(x), int(y))
        return True

    def click_screen(self, x, y, button="left", hold=0.03):
        flags = MOUSE_BUTTONS.get(str(button).lower())
        if flags is None:
            raise ValueError(f"不支持的鼠标按键: {button}")
        if not self._allowed():
            return False
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.01)
        name = str(button).lower()
        _send_mouse(flags[0])
        self.mouse_buttons.add(name)
        try:
            time.sleep(max(0.005, float(hold)))
        finally:
            _send_mouse(flags[1])
            self.mouse_buttons.discard(name)
        return True

    def mouse_down_screen(self, x, y, button="left"):
        name = str(button).lower()
        flags = MOUSE_BUTTONS.get(name)
        if flags is None:
            raise ValueError(f"不支持的鼠标按键: {button}")
        if not self._allowed():
            return False
        user32.SetCursorPos(int(x), int(y))
        _send_mouse(flags[0])
        self.mouse_buttons.add(name)
        return True

    def mouse_up_screen(self, x=None, y=None, button="left"):
        name = str(button).lower()
        flags = MOUSE_BUTTONS.get(name)
        if flags is None:
            raise ValueError(f"不支持的鼠标按键: {button}")
        if x is not None and y is not None:
            user32.SetCursorPos(int(x), int(y))
        _send_mouse(flags[1])
        self.mouse_buttons.discard(name)
        return True

    def execute(self, action):
        if action is None:
            return
        if action.kind == "tap":
            self.tap(action.key, action.duration)
        elif action.kind == "hold":
            self.hold(action.key, action.duration)
        elif action.kind == "wait":
            self.wait(action.duration)

    def release_all(self):
        for key in list(self.pressed):
            try:
                self.key_up(key)
            except Exception:
                self.pressed.discard(key)
        for button in list(self.mouse_buttons):
            flags = MOUSE_BUTTONS.get(button)
            if flags:
                _send_mouse(flags[1])
            self.mouse_buttons.discard(button)
