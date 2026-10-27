import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.capture import FrameSource
from src.config import load_config
from src.imageio import imwrite
from src.window import GameWindow, list_windows, set_dpi_awareness


def main():
    parser = argparse.ArgumentParser(description="抓取一帧游戏画面并保存，便于离线调试")
    parser.add_argument("--config", default=None)
    parser.add_argument("--title", default=None, help="临时指定窗口标题关键字")
    args = parser.parse_args()

    set_dpi_awareness()
    config_path = Path(args.config) if args.config else ROOT / "config.json"
    config = load_config(config_path)
    if args.title:
        config["window"]["title_keyword"] = args.title

    window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
    if not window.find():
        print("未找到游戏窗口，请在 config.json 中填写 window.title_keyword，或用 --title 指定。")
        for info in list_windows()[:30]:
            print(f"  {info['title']} | class={info['class']}")
        return 1

    source = FrameSource(config["capture"].get("backend", "auto"))
    print(f"窗口: {window.describe()}")
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
        print("截屏失败，请将游戏改为无边框窗口模式后重试。")
        return 1

    captures_dir = ROOT / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    path = captures_dir / f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    imwrite(path, frame)
    height, width = frame.shape[:2]
    print(f"已保存: {path} ({width}x{height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
