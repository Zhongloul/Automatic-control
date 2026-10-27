import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .imageio import imread, imwrite


@dataclass
class SceneState:
    scene_id: Optional[str] = None
    score: float = 0.0
    candidate: Optional[str] = None
    candidate_score: float = 0.0
    loading: bool = False
    changed: bool = False
    timestamp: float = 0.0


@dataclass
class SceneTemplate:
    name: str
    image: np.ndarray
    roi: Optional[tuple] = None


@dataclass
class SceneProfile:
    scene_id: str
    templates: list = field(default_factory=list)
    threshold: float = 0.82
    mode: str = "any"
    hash_bits: Optional[np.ndarray] = None
    hash_roi: tuple = (0.0, 0.0, 1.0, 1.0)
    hash_max_distance: int = 10


def _valid_roi(roi):
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        return None
    try:
        x, y, width, height = (float(value) for value in roi)
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    if x + width > 1.001 or y + height > 1.001:
        return None
    return (x, y, width, height)


class SceneRecognizer:
    def __init__(self, config, root):
        self.config = config
        self.root = Path(root)
        self.warnings = []
        scenes_cfg = config.get("scenes") or {}
        self.default_threshold = float(scenes_cfg.get("default_threshold", 0.82))
        self.confirm_frames = max(1, int(scenes_cfg.get("confirm_frames", 2)))
        self.poll_interval = max(0.02, float(scenes_cfg.get("poll_interval_s", 0.2)))
        loading_cfg = scenes_cfg.get("loading") or {}
        self.loading_enabled = bool(loading_cfg.get("enabled", True))
        self.loading_max_mean = float(loading_cfg.get("max_mean_brightness", 18.0))
        self.loading_max_std = float(loading_cfg.get("max_std", 12.0))
        self.loading_confirm = max(1, int(loading_cfg.get("confirm_frames", 3)))
        self.profiles = self._load_profiles(scenes_cfg.get("profiles") or {})
        self.enabled = bool(self.profiles)
        self._candidate = None
        self._candidate_count = 0
        self._scene_id = None
        self._scene_score = 0.0
        self._loading_count = 0

    @property
    def scene_ids(self):
        return list(self.profiles.keys())

    def _load_profiles(self, overrides):
        base_dir = self.root / "templates" / "scenes"
        folder_ids = []
        if base_dir.is_dir():
            folder_ids = [path.name for path in base_dir.iterdir() if path.is_dir()]
        scene_ids = sorted(set(folder_ids) | set(overrides.keys()))
        profiles = {}
        for scene_id in scene_ids:
            folder = base_dir / scene_id
            override = overrides.get(scene_id) or {}
            templates = self._load_templates(scene_id, folder, override.get("templates"))
            hash_bits, hash_roi, hash_max_distance = self._load_hash(scene_id, folder, override)
            if not templates and hash_bits is None:
                self.warnings.append(f"场景 {scene_id} 没有可用模板，已跳过")
                continue
            threshold = float(override.get("threshold", self.default_threshold))
            mode = str(override.get("match_mode", "any")).lower()
            if mode not in ("any", "all"):
                mode = "any"
            profiles[scene_id] = SceneProfile(
                scene_id=scene_id,
                templates=templates,
                threshold=threshold,
                mode=mode,
                hash_bits=hash_bits,
                hash_roi=hash_roi,
                hash_max_distance=hash_max_distance,
            )
        return profiles

    def _resolve_path(self, value, folder):
        path = Path(value)
        if path.is_absolute():
            return path
        candidate = self.root / path
        if candidate.exists():
            return candidate
        return folder / path

    def _load_templates(self, scene_id, folder, explicit):
        items = explicit
        if items is None:
            items = []
            if folder.is_dir():
                items = sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg"))
        templates = []
        for item in items:
            if isinstance(item, (str, Path)):
                image_path = self._resolve_path(item, folder)
                roi = None
            elif isinstance(item, dict):
                image_value = item.get("image") or item.get("path")
                if not image_value:
                    self.warnings.append(f"场景 {scene_id} 的模板缺少 image 字段")
                    continue
                image_path = self._resolve_path(image_value, folder)
                roi = _valid_roi(item.get("roi")) if item.get("roi") is not None else None
                if item.get("roi") is not None and roi is None:
                    self.warnings.append(f"场景 {scene_id} 的模板 ROI 无效: {item.get('roi')}")
                    continue
            else:
                self.warnings.append(f"场景 {scene_id} 的模板格式无效: {item}")
                continue
            image = imread(image_path, cv2.IMREAD_GRAYSCALE)
            if image is None:
                self.warnings.append(f"无法读取场景模板: {image_path}")
                continue
            templates.append(SceneTemplate(image_path.name, image, roi))
        return templates

    def _load_hash(self, scene_id, folder, override):
        hash_image = override.get("hash_image")
        if not hash_image:
            return None, (0.0, 0.0, 1.0, 1.0), 10
        roi = _valid_roi(override.get("hash_roi", [0.0, 0.0, 1.0, 1.0]))
        if roi is None:
            self.warnings.append(f"场景 {scene_id} 的 hash_roi 无效，已忽略哈希")
            return None, (0.0, 0.0, 1.0, 1.0), 10
        image = imread(self._resolve_path(hash_image, folder), cv2.IMREAD_GRAYSCALE)
        if image is None:
            self.warnings.append(f"无法读取场景哈希参考图: {hash_image}")
            return None, roi, 10
        reference = self._crop_roi(image, roi)
        if reference is None:
            self.warnings.append(f"场景 {scene_id} 的哈希参考图裁剪失败")
            return None, roi, 10
        max_distance = max(0, int(override.get("hash_max_distance", 10)))
        return self._phash(reference), roi, max_distance

    @staticmethod
    def _phash(gray):
        small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        coefficients = cv2.dct(np.float32(small))
        block = coefficients[:8, :8].flatten()
        median = float(np.median(block[1:]))
        return (block > median).astype(np.uint8)

    @staticmethod
    def _crop_roi(image, roi):
        height, width = image.shape[:2]
        x, y, w, h = roi
        left = max(0, min(int(round(x * width)), width - 1))
        top = max(0, min(int(round(y * height)), height - 1))
        box_w = max(1, min(int(round(w * width)), width - left))
        box_h = max(1, min(int(round(h * height)), height - top))
        crop = image[top : top + box_h, left : left + box_w]
        return crop if crop.size else None

    def _template_score(self, profile, gray):
        best = -1.0
        for template in profile.templates:
            search = gray
            if template.roi:
                search = self._crop_roi(gray, template.roi)
                if search is None:
                    continue
            if template.image.shape[0] > search.shape[0] or template.image.shape[1] > search.shape[1]:
                continue
            result = cv2.matchTemplate(search, template.image, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, _max_loc = cv2.minMaxLoc(result)
            best = max(best, float(max_val))
        return None if best < 0 else best

    def _hash_distance(self, profile, gray):
        if profile.hash_bits is None:
            return None
        crop = self._crop_roi(gray, profile.hash_roi)
        if crop is None:
            return None
        bits = self._phash(crop)
        return int(np.count_nonzero(bits != profile.hash_bits))

    def _score_profile(self, profile, gray):
        template_score = self._template_score(profile, gray)
        distance = self._hash_distance(profile, gray)
        hash_score = None if distance is None else max(0.0, 1.0 - distance / 64.0)
        if template_score is None and hash_score is None:
            return None
        if profile.mode == "all":
            if template_score is None or hash_score is None:
                return None
            if distance is not None and distance > profile.hash_max_distance:
                return None
            score = min(template_score, hash_score)
        else:
            score = max(value for value in (template_score, hash_score) if value is not None)
            if template_score is None and distance is not None and distance > profile.hash_max_distance:
                return None
        if score < profile.threshold:
            return None
        return score

    def _analyze_loading(self, gray):
        if not self.loading_enabled:
            self._loading_count = 0
            return False
        mean = float(gray.mean())
        std = float(gray.std())
        is_dark = mean <= self.loading_max_mean and std <= self.loading_max_std
        self._loading_count = self._loading_count + 1 if is_dark else 0
        return self._loading_count >= self.loading_confirm

    def detect_loading(self, frame):
        if frame is None or not self.loading_enabled:
            return False
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._analyze_loading(gray)

    def recognize(self, frame):
        now = time.perf_counter()
        if frame is None:
            return SceneState(timestamp=now)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._analyze_loading(gray):
            self._candidate = None
            self._candidate_count = 0
            self._scene_id = None
            self._scene_score = 0.0
            return SceneState(loading=True, timestamp=now)

        if not self.enabled:
            return SceneState(timestamp=now)

        best_id = None
        best_score = 0.0
        for scene_id, profile in self.profiles.items():
            score = self._score_profile(profile, gray)
            if score is not None and score > best_score:
                best_id = scene_id
                best_score = score

        if best_id is None:
            self._candidate = None
            self._candidate_count = 0
            self._scene_id = None
            self._scene_score = 0.0
            return SceneState(timestamp=now)

        if best_id == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = best_id
            self._candidate_count = 1

        if self._candidate_count >= self.confirm_frames:
            changed = self._scene_id is not None and self._scene_id != best_id
            self._scene_id = best_id
            self._scene_score = best_score
            return SceneState(
                scene_id=best_id,
                score=best_score,
                candidate=best_id,
                candidate_score=best_score,
                changed=changed,
                timestamp=now,
            )

        return SceneState(
            scene_id=self._scene_id,
            score=self._scene_score,
            candidate=best_id,
            candidate_score=best_score,
            timestamp=now,
        )

    def reset(self):
        self._candidate = None
        self._candidate_count = 0
        self._scene_id = None
        self._scene_score = 0.0
        self._loading_count = 0


def scenes_dir(root):
    return Path(root) / "templates" / "scenes"


def validate_scene_id(scene_id):
    text = str(scene_id or "").strip()
    if not text:
        return False
    return not any(character in text for character in '\\/:*?"<>|')


def list_scene_ids(root):
    folder = scenes_dir(root)
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if path.is_dir())


def list_markers(root, scene_id):
    folder = scenes_dir(root) / str(scene_id)
    if not folder.is_dir():
        return []
    paths = sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.jpeg"))
    return [path for path in paths if path.is_file()]


def save_marker(root, scene_id, image):
    scene_id = str(scene_id).strip()
    if not validate_scene_id(scene_id):
        raise ValueError(f"场景名不合法: {scene_id}")
    if image is None or getattr(image, "size", 0) == 0:
        raise ValueError("标记图片为空")
    folder = scenes_dir(root) / scene_id
    folder.mkdir(parents=True, exist_ok=True)
    index = 1
    while (folder / f"marker_{index}.png").exists():
        index += 1
    path = folder / f"marker_{index}.png"
    if not imwrite(path, image):
        raise RuntimeError(f"保存标记失败: {path}")
    return path


def delete_marker(path):
    path = Path(path)
    if path.is_file():
        path.unlink()


def delete_scene(root, scene_id):
    folder = scenes_dir(root) / str(scene_id)
    if folder.is_dir():
        shutil.rmtree(folder)
