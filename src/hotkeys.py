import ctypes

user32 = ctypes.windll.user32

VK_MAP = {f"f{index}": 0x6F + index for index in range(1, 13)}


class HotkeyWatcher:
    def __init__(self):
        self.previous = {}

    def pressed(self, name):
        key = (name or "").strip().lower()
        vk = VK_MAP.get(key)
        if vk is None:
            return False
        is_down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        was_down = self.previous.get(key, False)
        self.previous[key] = is_down
        return is_down and not was_down
