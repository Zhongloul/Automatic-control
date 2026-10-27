import argparse
import logging
import random
import sys
import time

from src.capture import CaptureThread, FrameSource
from src.config import load_config, project_root
from src.debug_view import DebugView
from src.decision import CombatBrain
from src.hotkeys import HotkeyWatcher
from src.humanize import Humanizer
from src.input import InputController
from src.perception import Perception
from src.window import GameWindow, list_windows, set_dpi_awareness


def setup_logging(root):
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("auto_play")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)
    file_handler = logging.FileHandler(
        logs_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    return logger


def parse_args():
    parser = argparse.ArgumentParser(description="2D 横版动作游戏黑盒自动刷图脚本")
    parser.add_argument("--config", default=None, help="配置文件路径，默认使用项目根目录 config.json")
    parser.add_argument("--title", default=None, help="临时覆盖窗口标题关键字")
    parser.add_argument("--class", dest="window_class", default=None, help="临时覆盖窗口类名关键字")
    parser.add_argument("--dry-run", action="store_true", help="只观察不发送按键")
    parser.add_argument("--no-debug", action="store_true", help="关闭调试视图")
    parser.add_argument("--duration", type=float, default=0.0, help="运行指定秒数后自动退出，0 为不限制")
    parser.add_argument("--start", action="store_true", help="启动后自动进入运行状态")
    return parser.parse_args()


def countdown(logger, seconds):
    seconds = int(max(0, seconds))
    for remaining in range(seconds, 0, -1):
        logger.info("%d 秒后开始，请切换到游戏窗口...", remaining)
        time.sleep(1.0)


def run(args):
    root = project_root()
    config_path = args.config or (root / "config.json")
    config = load_config(config_path)
    if args.title is not None:
        config["window"]["title_keyword"] = args.title
    if args.window_class is not None:
        config["window"]["class_keyword"] = args.window_class
    if args.no_debug:
        config["debug"]["enabled"] = False
    if args.dry_run:
        config["safety"]["dry_run"] = True

    logger = setup_logging(root)
    set_dpi_awareness()

    window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
    if not window.find():
        logger.error("未找到游戏窗口。请在 config.json 的 window.title_keyword 中填写窗口标题关键字。")
        logger.info("当前可见窗口（供参考）:")
        for info in list_windows()[:30]:
            logger.info("  %s | class=%s", info["title"], info["class"])
        return 1
    logger.info("已锁定窗口: %s", window.describe())

    source = FrameSource(config["capture"].get("backend", "auto"))
    logger.info("截屏后端: %s", source.backend)
    capture = CaptureThread(source, window.client_rect, config["capture"].get("fps", 45))
    capture.start()

    deadline = time.perf_counter() + 3.0
    while capture.latest() is None and time.perf_counter() < deadline:
        time.sleep(0.05)
    frame = capture.latest()
    if frame is None:
        logger.error("未捕获到游戏画面。请把游戏改成无边框窗口模式，并确认窗口未被最小化。")
        capture.stop()
        source.close()
        return 1
    logger.info("画面尺寸: %dx%d", frame.shape[1], frame.shape[0])

    rng = random.Random()
    perception = Perception(config, root)
    humanizer = Humanizer(config["humanize"], rng)
    brain = CombatBrain(config, humanizer, rng)
    inputs = InputController(
        gate=window.is_foreground if config["safety"].get("require_foreground", True) else None,
        hold_provider=humanizer.hold_duration,
        dry_run=bool(config["safety"].get("dry_run", False)),
    )
    debug = DebugView(config)
    watcher = HotkeyWatcher()

    toggle_key = config["hotkeys"].get("toggle", "f8")
    quit_key = config["hotkeys"].get("quit", "f9")
    debug_key = config["hotkeys"].get("debug", "f1")

    if config["window"].get("auto_activate", True):
        window.activate()
    if debug.enabled:
        debug.prepare()
        time.sleep(0.15)
        if config["window"].get("auto_activate", True):
            window.activate()

    running = False
    if args.start or config["safety"].get("start_running"):
        countdown(logger, config["safety"].get("countdown_s", 3))
        brain.reset(time.perf_counter())
        running = True

    logger.info("热键: %s 开始/暂停 | %s 退出 | %s 调试视图", toggle_key.upper(), quit_key.upper(), debug_key.upper())
    logger.info("当前状态: %s", "运行中" if running else "已暂停")

    state = None
    last_action = "-"
    next_decision = 0.0
    started_at = time.perf_counter()
    black_warned_at = 0.0
    foreground_warned = False

    try:
        while True:
            now = time.perf_counter()
            if args.duration and now - started_at >= args.duration:
                logger.info("达到运行时长限制，自动退出")
                break
            if watcher.pressed(quit_key):
                logger.info("收到退出热键")
                break
            if watcher.pressed(toggle_key):
                if running:
                    running = False
                    inputs.release_all()
                    logger.info("已暂停（按 %s 继续）", toggle_key.upper())
                else:
                    if config["window"].get("auto_activate", True):
                        window.activate()
                    countdown(logger, config["safety"].get("countdown_s", 3))
                    brain.reset(time.perf_counter())
                    running = True
                    logger.info("开始运行")
                continue
            if watcher.pressed(debug_key):
                enabled = debug.toggle()
                logger.info("调试视图: %s", "开启" if enabled else "关闭")
                if enabled and config["window"].get("auto_activate", True):
                    window.activate()
                continue

            if not window.refresh():
                if running:
                    running = False
                    inputs.release_all()
                logger.warning("游戏窗口丢失，等待重新出现...")
                debug.render(None, None, ["WINDOW LOST"])
                time.sleep(0.4)
                continue

            frame = capture.latest()
            if frame is None:
                time.sleep(0.01)
                continue

            if frame.mean() < 1.0 and now - black_warned_at > 5.0:
                black_warned_at = now
                logger.warning("画面接近全黑：独占全屏可能导致截屏失败，请改为无边框窗口模式")

            if running and config["safety"].get("require_foreground", True) and not window.is_foreground():
                running = False
                inputs.release_all()
                if not foreground_warned:
                    foreground_warned = True
                    logger.warning("游戏不在前台，已自动暂停；切回游戏后按 %s 继续", toggle_key.upper())
            elif window.is_foreground():
                foreground_warned = False

            if now >= next_decision:
                try:
                    state = perception.perceive(frame)
                    if running:
                        action = brain.update(state, now)
                        last_action = action.describe()
                        inputs.execute(action)
                except Exception:
                    logger.exception("感知/决策异常，已自动暂停")
                    running = False
                    inputs.release_all()
                next_decision = now + humanizer.decision_interval()

            if debug.enabled:
                hp = state.get("hp") if state else None
                enemies = state.get("enemies") if state else None
                skills = state.get("skills") if state else None
                ready_count = sum(1 for slot in skills or [] if slot.get("ready")) if skills else 0
                status = [
                    f"{'RUNNING' if running else 'PAUSED'}   {toggle_key.upper()}=start/pause  {quit_key.upper()}=quit",
                    f"capture={source.backend} {capture.fps_actual:.0f}fps age={capture.age() * 1000:.0f}ms",
                    f"dry_run={inputs.dry_run}  action={last_action}",
                    f"hp={'-' if hp is None else f'{hp:.2f}'}  enemies={0 if enemies is None else len(enemies)}  skills_ready={ready_count}",
                ]
                debug.render(frame, state, status)

            time.sleep(0.004)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，退出")
    finally:
        inputs.release_all()
        capture.stop()
        source.close()
        debug.destroy()
    return 0


def main():
    args = parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
