import time
from pathlib import Path

import cv2
import numpy as np

from .imageio import imread


def rel_roi_to_px(roi, width, height):
    if not roi or len(roi) != 4:
        return None
    x, y, w, h = (float(value) for value in roi)
    left = int(round(x * width))
    top = int(round(y * height))
    box_w = int(round(w * width))
    box_h = int(round(h * height))
    left = max(0, min(left, width - 2))
    top = max(0, min(top, height - 2))
    box_w = max(1, min(box_w, width - left))
    box_h = max(1, min(box_h, height - top))
    return left, top, box_w, box_h


def _iou(first, second):
    ax2 = first["x"] + first["w"]
    ay2 = first["y"] + first["h"]
    bx2 = second["x"] + second["w"]
    by2 = second["y"] + second["h"]
    inter_w = max(0, min(ax2, bx2) - max(first["x"], second["x"]))
    inter_h = max(0, min(ay2, by2) - max(first["y"], second["y"]))
    inter = inter_w * inter_h
    union = first["w"] * first["h"] + second["w"] * second["h"] - inter
    return inter / union if union > 0 else 0.0


def non_max_suppression(detections, iou_threshold=0.35):
    ordered = sorted(detections, key=lambda item: item["score"], reverse=True)
    kept = []
    for detection in ordered:
        if all(_iou(detection, other) < iou_threshold for other in kept):
            kept.append(detection)
    return kept


class TemplateBank:
    def __init__(self, folder, threshold=0.8, iou_threshold=0.35):
        self.threshold = float(threshold)
        self.iou_threshold = float(iou_threshold)
        self.templates = []
        folder = Path(folder)
        if folder.is_dir():
            paths = sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg"))
            for path in paths:
                image = imread(path, cv2.IMREAD_COLOR)
                if image is None:
                    continue
                self.templates.append((path.name, cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)))

    def detect(self, gray_frame, roi=None):
        detections = []
        if not self.templates:
            return detections
        if roi:
            x, y, w, h = roi
            search = gray_frame[y : y + h, x : x + w]
        else:
            x = y = 0
            search = gray_frame
        if search.size == 0:
            return detections
        for name, template in self.templates:
            if template.shape[0] > search.shape[0] or template.shape[1] > search.shape[1]:
                continue
            result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= self.threshold)
            for ty, tx in zip(ys, xs):
                score = float(result[ty, tx])
                detections.append(
                    {
                        "x": int(x + tx),
                        "y": int(y + ty),
                        "w": int(template.shape[1]),
                        "h": int(template.shape[0]),
                        "score": score,
                        "name": name,
                    }
                )
        return non_max_suppression(detections, self.iou_threshold)


class Perception:
    def __init__(self, config, root):
        self.config = config
        self.root = Path(root)
        perception_cfg = config["perception"]
        self.enemy_bank = TemplateBank(
            self.root / "templates" / "enemy",
            perception_cfg.get("enemy_threshold", 0.8),
            perception_cfg.get("nms_iou", 0.35),
        )
        self.player_template = self._load_player_template(config["player"].get("template"))
        self.player_threshold = float(perception_cfg.get("player_threshold", 0.72))
        self.gameplay_roi = perception_cfg.get("gameplay_roi", [0.0, 0.0, 1.0, 1.0])

    def _load_player_template(self, relative_path):
        if not relative_path:
            return None
        path = self.root / relative_path
        if not path.is_file():
            return None
        image = imread(path, cv2.IMREAD_COLOR)
        if image is None:
            return None
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def perceive(self, frame):
        height, width = frame.shape[:2]
        state = {
            "size": (width, height),
            "time": time.perf_counter(),
            "enemies": [],
            "player": None,
            "skills": [],
            "rects": {},
        }

        bar_rects = {}
        for name in ("hp", "mp"):
            bar_cfg = self.config["bars"].get(name) or {}
            roi = bar_cfg.get("roi")
            bar_rects[name] = rel_roi_to_px(roi, width, height) if roi else None
        skill_rects = self._skill_rects(width, height)

        state["rects"] = {
            "hp": bar_rects["hp"],
            "mp": bar_rects["mp"],
            "skills": skill_rects,
            "gameplay": rel_roi_to_px(self.gameplay_roi, width, height),
        }
        state["hp"] = self._bar_ratio(frame, "hp", bar_rects["hp"])
        state["mp"] = self._bar_ratio(frame, "mp", bar_rects["mp"])
        state["skills"] = self._skills(frame, skill_rects)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        state["player"] = self._player_position(gray, width, height)
        state["enemies"] = self._enemies(gray, width, height)
        return state

    def _bar_ratio(self, frame, name, rect):
        if rect is None:
            return None
        bar_cfg = self.config["bars"][name]
        x, y, width, height = rect
        crop = frame[y : y + height, x : x + width]
        if crop.size == 0:
            return 0.0
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for entry in bar_cfg.get("hsv_ranges", []):
            low = np.array(entry[0], dtype=np.uint8)
            high = np.array(entry[1], dtype=np.uint8)
            mask = cv2.bitwise_or(mask, cv2.inRange(hsv, low, high))
        column_counts = (mask > 0).sum(axis=0)
        min_ratio = float(bar_cfg.get("min_column_ratio", 0.25))
        required = max(1, int(height * min_ratio))
        valid_columns = np.where(column_counts >= required)[0]
        if valid_columns.size == 0:
            return 0.0
        return float(valid_columns[-1] + 1) / float(width)

    def _skill_rects(self, width, height):
        skills_cfg = self.config["skills"]
        explicit = skills_cfg.get("slot_rois")
        if explicit:
            rects = []
            for roi in explicit:
                rect = rel_roi_to_px(roi, width, height)
                if rect:
                    rects.append(rect)
            return rects
        bar_roi = skills_cfg.get("bar_roi")
        bar = rel_roi_to_px(bar_roi, width, height) if bar_roi else None
        if bar is None:
            return []
        left, top, bar_w, bar_h = bar
        count = max(1, int(skills_cfg.get("slot_count", 6)))
        gap_ratio = max(0.0, float(skills_cfg.get("slot_gap_ratio", 0.15)))
        unit = bar_w / (count + gap_ratio * (count - 1))
        slot_w = max(1, int(round(unit)))
        gap = max(0, int(round(unit * gap_ratio)))
        rects = []
        x = left
        for _ in range(count):
            remaining = left + bar_w - x
            if remaining <= 0:
                break
            rects.append((x, top, min(slot_w, remaining), bar_h))
            x += slot_w + gap
        return rects

    def _skills(self, frame, rects):
        if not rects:
            return []
        skills_cfg = self.config["skills"]
        saturation_min = float(skills_cfg.get("gray_saturation_max", 55))
        value_min = float(skills_cfg.get("value_ready_min", 200))
        center_ratio = float(skills_cfg.get("center_ratio", 0.6))
        results = []
        for x, y, width, height in rects:
            inset_x = int(width * (1.0 - center_ratio) / 2.0)
            inset_y = int(height * (1.0 - center_ratio) / 2.0)
            crop = frame[y + inset_y : y + height - inset_y, x + inset_x : x + width - inset_x]
            if crop.size == 0:
                results.append({"ready": False, "saturation": 0.0, "value": 0.0})
                continue
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            saturation = float(hsv[:, :, 1].mean())
            value = float(hsv[:, :, 2].mean())
            ready = saturation >= saturation_min or value >= value_min
            results.append({"ready": bool(ready), "saturation": saturation, "value": value})
        return results

    def _player_position(self, gray, width, height):
        template = self.player_template
        if template is not None and template.shape[0] <= height and template.shape[1] <= width:
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
            if max_val >= self.player_threshold:
                return (
                    float(max_loc[0]) + template.shape[1] / 2.0,
                    float(max_loc[1]) + template.shape[0] / 2.0,
                )
        anchor = self.config["player"].get("anchor", [0.5, 0.6])
        return (float(anchor[0]) * width, float(anchor[1]) * height)

    def _enemies(self, gray, width, height):
        roi = rel_roi_to_px(self.gameplay_roi, width, height)
        detections = self.enemy_bank.detect(gray, roi)
        for detection in detections:
            detection["cx"] = detection["x"] + detection["w"] / 2.0
            detection["cy"] = detection["y"] + detection["h"] / 2.0
        return detections
