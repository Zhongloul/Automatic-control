import argparse
import ctypes
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.capture import FrameSource
from src.config import load_config, save_config
from src.imageio import imwrite
from src.window import GameWindow, list_windows, set_dpi_awareness

WINDOW_NAME = "calibrate"
MAX_WIDTH = 1600
MAX_HEIGHT = 900

RECT_STEPS = [
    {
        "path": ("bars", "hp", "roi"),
        "title": "STEP 1/4  HP BAR",
        "help": "Drag a box around the HP bar.  Enter=confirm   S=skip   Esc=quit",
    },
    {
        "path": ("bars", "mp", "roi"),
        "title": "STEP 2/4  MP BAR",
        "help": "Drag a box around the MP bar.  Enter=confirm   S=skip (no MP bar)   Esc=quit",
    },
    {
        "path": ("skills", "bar_roi"),
        "title": "STEP 3/4  SKILL BAR",
        "help": "Drag a box around the whole skill bar.  Enter=confirm   S=skip   Esc=quit",
    },
]

PLAYER_STEP = {
    "title": "STEP 4/4  PLAYER ANCHOR",
    "help": "Click the center of your character.  Enter=confirm   S=skip   Esc=quit",
}


class Selector:
    def __init__(self, mode="rect"):
        self.mode = mode
        self.start = None
        self.end = None
        self.point = None
        self.dragging = False

    def reset(self):
        self.start = None
        self.end = None
        self.point = None
        self.dragging = False

    def on_mouse(self, event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.start = (x, y)
            self.end = (x, y)
            self.dragging = True
            if self.mode == "point":
                self.point = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.dragging:
            self.end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            self.end = (x, y)
            self.dragging = False
            if self.mode == "point":
                self.point = (x, y)

    def rect(self):
        if self.start is None or self.end is None:
            return None
        x1, x2 = sorted((self.start[0], self.end[0]))
        y1, y2 = sorted((self.start[1], self.end[1]))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        return x1, y1, x2, y2

    def draw(self, canvas):
        if self.mode == "rect":
            rect = self.rect()
            if rect:
                cv2.rectangle(canvas, (rect[0], rect[1]), (rect[2], rect[3]), (0, 255, 0), 1)
                cv2.putText(
                    canvas,
                    f"{rect[2] - rect[0]}x{rect[3] - rect[1]}",
                    (rect[0] + 3, max(14, rect[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )
        elif self.point:
            cv2.drawMarker(canvas, self.point, (0, 0, 255), cv2.MARKER_CROSS, 26, 2)


def draw_banner(canvas, title, help_text):
    height, width = canvas.shape[:2]
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (width, 62), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, canvas, 0.4, 0, canvas)
    cv2.putText(canvas, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, help_text, (12, 49), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (210, 210, 210), 1, cv2.LINE_AA)


def saved_rect(config, path, width, height):
    node = config
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    roi = node
    if not roi or len(roi) != 4:
        return None
    x, y, w, h = (float(value) for value in roi)
    return int(x * width), int(y * height), int((x + w) * width), int((y + h) * height)


def draw_saved(canvas, config):
    specs = [
        (("bars", "hp", "roi"), (60, 220, 255), "HP"),
        (("bars", "mp", "roi"), (255, 200, 80), "MP"),
        (("skills", "bar_roi"), (80, 255, 80), "SKILLS"),
    ]
    height, width = canvas.shape[:2]
    for path, color, label in specs:
        rect = saved_rect(config, path, width, height)
        if not rect:
            continue
        cv2.rectangle(canvas, (rect[0], rect[1]), (rect[2], rect[3]), color, 1)
        cv2.putText(canvas, label, (rect[0] + 2, max(12, rect[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    anchor = config["player"].get("anchor")
    if anchor and len(anchor) == 2:
        point = (int(anchor[0] * width), int(anchor[1] * height))
        cv2.drawMarker(canvas, point, (255, 120, 120), cv2.MARKER_TILTED_CROSS, 16, 1)


def ask_selection(base_canvas, config, title, help_text, mode="rect", allow_player=False):
    selector = Selector(mode)
    selector.reset()
    cv2.setMouseCallback(WINDOW_NAME, selector.on_mouse)
    while True:
        canvas = base_canvas.copy()
        draw_saved(canvas, config)
        selector.draw(canvas)
        draw_banner(canvas, title, help_text)
        cv2.imshow(WINDOW_NAME, canvas)
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 32):
            if mode == "point":
                if selector.point:
                    return "confirm", selector.point
            else:
                rect = selector.rect()
                if rect:
                    return "confirm", rect
        elif key in (ord("s"), ord("S")):
            return "skip", None
        elif key == 27:
            return "abort", None
        elif allow_player and key in (ord("p"), ord("P")):
            rect = selector.rect()
            if rect:
                return "player", rect


def dict_set(config, path, value):
    node = config
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def capture_frame(config):
    window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
    if not window.find():
        print("未找到游戏窗口，无法校准。请先填写 config.json 的 window.title_keyword，或用 --title 指定。")
        print("当前可见窗口:")
        for info in list_windows()[:30]:
            print(f"  {info['title']} | class={info['class']}")
        return None
    print(f"窗口: {window.describe()}")
    source = FrameSource(config["capture"].get("backend", "auto"))
    print(f"截屏后端: {source.backend}")
    region = window.client_rect()
    frame = None
    for _ in range(30):
        frame = source.grab(region)
        if frame is not None:
            break
        time.sleep(0.1)
    source.close()
    if frame is None:
        print("截屏失败。请将游戏改为无边框窗口模式后重试。")
        return None
    return frame


def capture_screen():
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    source = FrameSource("mss")
    frame = None
    for _ in range(10):
        frame = source.grab((0, 0, width, height))
        if frame is not None:
            break
        time.sleep(0.1)
    source.close()
    if frame is None:
        print("主屏幕截屏失败。")
        return None
    print(f"已捕获主屏幕: {width}x{height}")
    return frame


def next_template_index(folder):
    index = 1
    while (folder / f"enemy_{index}.png").exists():
        index += 1
    return index


def main():
    parser = argparse.ArgumentParser(description="自动化脚本 UI 校准工具")
    parser.add_argument("--config", default=None)
    parser.add_argument("--title", default=None, help="临时指定窗口标题关键字")
    parser.add_argument("--screen", action="store_true", help="直接截取主屏幕（窗口检测失败时使用）")
    args = parser.parse_args()

    set_dpi_awareness()
    config_path = Path(args.config) if args.config else ROOT / "config.json"
    config = load_config(config_path)
    if args.title:
        config["window"]["title_keyword"] = args.title

    frame = capture_screen() if args.screen else capture_frame(config)
    if frame is None:
        return 1

    original_height, original_width = frame.shape[:2]
    scale = min(1.0, MAX_WIDTH / original_width, MAX_HEIGHT / original_height)
    if scale < 1.0:
        display = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        display = frame.copy()
    display_height, display_width = display.shape[:2]

    print("校准窗口已打开。按 Enter/空格确认选择，S 跳过，Esc 退出。")
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)

    for step in RECT_STEPS:
        result, value = ask_selection(display, config, step["title"], step["help"])
        if result == "abort":
            print("已取消校准，配置未保存。")
            cv2.destroyAllWindows()
            return 1
        if result == "skip":
            print(f"跳过: {step['title']}")
            continue
        x1, y1, x2, y2 = value
        roi = [
            round(x1 / display_width, 5),
            round(y1 / display_height, 5),
            round((x2 - x1) / display_width, 5),
            round((y2 - y1) / display_height, 5),
        ]
        dict_set(config, step["path"], roi)
        print(f"已记录 {step['path']}: {roi}")

    result, value = ask_selection(
        display,
        config,
        PLAYER_STEP["title"],
        PLAYER_STEP["help"],
        mode="point",
    )
    if result == "abort":
        print("已取消校准，配置未保存。")
        cv2.destroyAllWindows()
        return 1
    if result == "confirm":
        px, py = value
        anchor = [round(px / display_width, 5), round(py / display_height, 5)]
        config["player"]["anchor"] = anchor
        print(f"已记录角色锚点: {anchor}")

    enemy_dir = ROOT / "templates" / "enemy"
    player_dir = ROOT / "templates" / "player"
    enemy_dir.mkdir(parents=True, exist_ok=True)
    player_dir.mkdir(parents=True, exist_ok=True)
    saved_count = 0
    while True:
        title = f"ENEMY TEMPLATE #{next_template_index(enemy_dir)}  (optional but recommended)"
        help_text = (
            "Drag a box around one enemy sprite.  Enter=save   P=save as player   "
            "S/Esc=finish"
        )
        result, value = ask_selection(display, config, title, help_text, allow_player=True)
        if result in ("skip", "abort"):
            break
        x1, y1, x2, y2 = value
        ox1, oy1 = int(x1 / scale), int(y1 / scale)
        ox2, oy2 = int(x2 / scale), int(y2 / scale)
        crop = frame[oy1:oy2, ox1:ox2]
        if crop.size == 0:
            continue
        if result == "player":
            path = player_dir / "player.png"
            imwrite(path, crop)
            print(f"已保存角色模板: {path}")
            continue
        index = next_template_index(enemy_dir)
        path = enemy_dir / f"enemy_{index}.png"
        imwrite(path, crop)
        saved_count += 1
        print(f"已保存敌人模板: {path}")

    cv2.destroyAllWindows()
    save_config(config_path, config)
    print(f"配置已保存: {config_path}")
    print(f"本次保存敌人模板 {saved_count} 个。运行 main.py 前建议先执行 tools/self_test.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
