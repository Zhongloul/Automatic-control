import argparse
import ctypes
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Report:
    def __init__(self):
        self.failures = 0
        self.warnings = 0

    def ok(self, message):
        print(f"[OK]   {message}")

    def warn(self, message):
        print(f"[WARN] {message}")
        self.warnings += 1

    def fail(self, message):
        print(f"[FAIL] {message}")
        self.failures += 1


def check_packages(report):
    try:
        import numpy
        import cv2
        import mss

        report.ok(f"numpy {numpy.__version__} / opencv {cv2.__version__} / mss {mss.__version__}")
    except Exception as exc:
        report.fail(f"依赖缺失: {exc}")
        return False
    try:
        import dxcam

        report.ok("dxcam 可用（将优先使用高速截屏）")
    except Exception:
        report.warn("dxcam 未安装，自动回退 GDI 截屏（不使用 CAPTUREBLT，不会闪烁光标）")
    return True


def validate_roi(name, roi, report):
    if roi is None:
        report.warn(f"{name} 未校准")
        return
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        report.fail(f"{name} 格式错误，应为 [x, y, w, h]")
        return
    x, y, width, height = (float(value) for value in roi)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.001 or y + height > 1.001:
        report.fail(f"{name} 超出画面范围: {roi}")
    else:
        report.ok(f"{name} = {roi}")


def check_config(config, report):
    print("\n-- 配置检查 --")
    window_cfg = config.get("window") or {}
    if not window_cfg.get("title_keyword"):
        report.warn("window.title_keyword 为空，请先填写游戏窗口标题关键字")
    else:
        report.ok(f"窗口标题关键字: {window_cfg['title_keyword']}")

    bars = config.get("bars") or {}
    for name in ("hp", "mp"):
        validate_roi(f"bars.{name}.roi", (bars.get(name) or {}).get("roi"), report)
    validate_roi("skills.bar_roi", (config.get("skills") or {}).get("bar_roi"), report)
    slot_rois = (config.get("skills") or {}).get("slot_rois")
    if slot_rois:
        for index, roi in enumerate(slot_rois):
            validate_roi(f"skills.slot_rois[{index}]", roi, report)

    anchor = (config.get("player") or {}).get("anchor")
    if not anchor or len(anchor) != 2:
        report.fail("player.anchor 格式错误，应为 [x, y] 相对坐标")
    else:
        x, y = (float(value) for value in anchor)
        if 0 <= x <= 1 and 0 <= y <= 1:
            report.ok(f"player.anchor = {anchor}")
        else:
            report.fail(f"player.anchor 超出画面范围: {anchor}")

    combat_cfg = config.get("combat") or {}
    if not combat_cfg.get("skills"):
        report.warn("combat.skills 为空，只会使用普通攻击")
    if not (combat_cfg.get("basic_attack") or {}).get("key"):
        report.warn("combat.basic_attack.key 为空，不会进行普通攻击")

    skill_slots = int((config.get("skills") or {}).get("slot_count", 0))
    for skill in combat_cfg.get("skills") or []:
        slot = skill.get("slot")
        if slot is not None and skill_slots and (slot < 0 or slot >= skill_slots):
            report.fail(f"技能 {skill.get('key')} 的 slot={slot} 超出 slot_count={skill_slots}")

    hotkeys = config.get("hotkeys") or {}
    allowed = {f"f{index}" for index in range(1, 13)}
    for name, value in hotkeys.items():
        if str(value).lower() not in allowed:
            report.fail(f"热键 {name}={value} 不受支持，仅支持 f1~f12")


def check_keys(config, report):
    print("\n-- 按键映射检查 --")
    from src.input import resolve_key

    combat_cfg = config.get("combat") or {}
    keys = set()
    basic = (combat_cfg.get("basic_attack") or {}).get("key")
    potion = (combat_cfg.get("potion") or {}).get("key")
    for value in (basic, potion, combat_cfg.get("navigate_jump_key")):
        if value:
            keys.add(value)
    retreat = (combat_cfg.get("danger") or {}).get("retreat_key")
    if retreat:
        keys.add(retreat)
    for skill in combat_cfg.get("skills") or []:
        if skill.get("key"):
            keys.add(skill["key"])
    for key in sorted(keys):
        try:
            _vk, scan, extended = resolve_key(key)
            report.ok(f"按键 {key!r} -> scan=0x{scan:02X} extended={extended}")
        except Exception as exc:
            report.fail(f"按键 {key!r} 无法映射: {exc}")


def check_templates(report):
    print("\n-- 模板检查 --")
    enemy_dir = ROOT / "templates" / "enemy"
    player_dir = ROOT / "templates" / "player"
    enemies = list(enemy_dir.glob("*.png")) + list(enemy_dir.glob("*.jpg"))
    players = list(player_dir.glob("*.png")) + list(player_dir.glob("*.jpg"))
    if enemies:
        report.ok(f"敌人模板 {len(enemies)} 个: {', '.join(path.name for path in enemies[:8])}")
    else:
        report.warn("没有敌人模板，将无法识别敌人；请用 tools/calibrate.py 抓取")
    if players:
        report.ok(f"角色模板 {len(players)} 个")
    else:
        report.warn("没有角色模板，将使用 player.anchor 作为角色位置")


def check_scenes(config, report):
    print("\n-- 场景与跨图配置检查 --")
    from src.map_graph import MapGraph
    from src.scene import SceneRecognizer

    recognizer = SceneRecognizer(config, ROOT)
    if recognizer.profiles:
        report.ok(f"场景模板 {len(recognizer.profiles)} 个: {', '.join(recognizer.scene_ids)}")
    else:
        report.warn("未配置场景模板（templates/scenes/<场景名>/*.png），跨图识别暂不可用")
    for warning in recognizer.warnings:
        report.warn(warning)

    graph = MapGraph(config)
    if graph.transitions:
        report.ok(f"地图跳转 {len(graph.transitions)} 条")
        for transition in graph.transitions:
            if recognizer.profiles:
                if transition.from_scene not in recognizer.profiles:
                    report.warn(f"跳转 {transition.name} 的起始场景 {transition.from_scene} 没有模板")
                if transition.to_scene not in recognizer.profiles:
                    report.warn(f"跳转 {transition.name} 的目标场景 {transition.to_scene} 没有模板")
    else:
        report.warn("未配置 map_graph.transitions，自动跳图暂不可用")
    for warning in graph.warnings:
        report.warn(warning)


def check_window_and_capture(config, report):
    print("\n-- 窗口与截屏检查 --")
    from src.capture import FrameSource
    from src.window import GameWindow, list_windows, set_dpi_awareness

    set_dpi_awareness()
    window = GameWindow(config["window"].get("title_keyword"), config["window"].get("class_keyword"))
    frame = None
    if window.find():
        report.ok(f"已找到窗口: {window.describe()}")
        region = window.client_rect()
    else:
        report.warn("未找到配置的游戏窗口，改为截取主屏幕验证截屏功能")
        user32 = ctypes.windll.user32
        region = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        print("  当前可见窗口（供参考）:")
        for info in list_windows()[:20]:
            print(f"    {info['title']} | class={info['class']}")

    try:
        source = FrameSource(config["capture"].get("backend", "auto"))
        report.ok(f"截屏后端: {source.backend}")
        for _ in range(30):
            frame = source.grab(region)
            if frame is not None:
                break
            time.sleep(0.1)
        source.close()
    except Exception as exc:
        report.fail(f"截屏初始化失败: {exc}")
        return None

    if frame is None:
        report.fail("截屏失败：请使用无边框窗口模式，确认窗口未被最小化且未被其他窗口完全遮挡")
        return None

    height, width = frame.shape[:2]
    report.ok(f"画面尺寸: {width}x{height}")
    if float(frame.mean()) < 1.0:
        report.warn("画面接近全黑，可能是独占全屏导致，请改为无边框窗口模式")
    return frame


def check_perception(config, frame, report):
    print("\n-- 感知模块检查 --")
    from src.perception import Perception

    try:
        perception = Perception(config, ROOT)
        state = perception.perceive(frame)
        hp = state.get("hp")
        mp = state.get("mp")
        enemies = state.get("enemies") or []
        skills = state.get("skills") or []
        ready = sum(1 for slot in skills if slot.get("ready"))
        report.ok(
            "感知输出: hp=%s mp=%s enemies=%d skills=%d ready=%d"
            % ("-" if hp is None else f"{hp:.2f}", "-" if mp is None else f"{mp:.2f}", len(enemies), len(skills), ready)
        )
        if hp is not None and hp <= 0.0:
            report.warn("HP 读数为 0，血条颜色范围可能需要调整")
    except Exception:
        report.fail("感知模块异常:\n" + traceback.format_exc(limit=2))


def main():
    parser = argparse.ArgumentParser(description="自动化脚本环境自检")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    print("=== 自动化脚本环境自检 ===")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    print(f"项目目录: {ROOT}")

    report = Report()
    if not check_packages(report):
        return 1

    from src.config import load_config

    config_path = Path(args.config) if args.config else ROOT / "config.json"
    config = load_config(config_path)
    print(f"配置文件: {config_path}")

    check_config(config, report)
    check_keys(config, report)
    check_templates(report)
    check_scenes(config, report)
    frame = check_window_and_capture(config, report)
    if frame is not None:
        check_perception(config, frame, report)

    print("\n=== 自检结果 ===")
    print(f"失败项: {report.failures}  警告项: {report.warnings}")
    if report.failures:
        print("请先修复失败项后再运行 main.py")
        return 1
    print("基础环境正常。下一步:")
    print("  1. 未校准的话运行 tools/calibrate.py")
    print("  2. 运行 tools/snapshot.py 检查截图是否正确")
    print("  3. 用 python main.py --dry-run 观察调试视图，确认识别正常")
    print("  4. 按 F8 正式运行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
