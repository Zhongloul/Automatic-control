import json
from pathlib import Path

import cv2
import numpy as np

from .imageio import imread, imwrite

SIGNATURE_SIZE = (96, 54)


def frames_dir(folder):
    return Path(folder) / "frames"


def refs_dir(folder):
    return Path(folder) / "state_refs"


def _index_path(folder):
    return frames_dir(folder) / "index.jsonl"


def valid_roi(roi):
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


def crop_roi(frame, roi=None):
    if frame is None or frame.size == 0:
        return None
    box = valid_roi(roi)
    if box is None:
        return frame
    height, width = frame.shape[:2]
    x, y, roi_w, roi_h = box
    x1 = min(width - 1, max(0, int(round(x * width))))
    y1 = min(height - 1, max(0, int(round(y * height))))
    x2 = min(width, max(x1 + 1, int(round((x + roi_w) * width))))
    y2 = min(height, max(y1 + 1, int(round((y + roi_h) * height))))
    return frame[y1:y2, x1:x2]


def make_signature(frame, roi=None):
    image = crop_roi(frame, roi)
    if image is None:
        return None
    try:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
    except cv2.error:
        return None
    return gray.astype(np.float32) / 255.0


def signature_diff(first, second):
    if first is None or second is None:
        return 1.0
    if first.shape != second.shape:
        return 1.0
    return float(np.mean(np.abs(first - second)))


def estimate_threshold(frames, folder, reference_sig, target_t, floor=0.05, cap=0.22, window_s=0.6):
    samples = []
    for entry in frames:
        if entry["t"] > target_t:
            break
        if entry["t"] < target_t - window_s:
            continue
        signature = make_signature(load_frame(folder, entry))
        if signature is not None:
            samples.append(signature_diff(signature, reference_sig))
    if not samples:
        return floor
    samples.sort()
    median = samples[len(samples) // 2]
    return min(cap, max(floor, median * 1.8 + 0.02))


class FrameStore:
    def __init__(self, folder, max_width=480, quality=85):
        self.folder = Path(folder)
        self.max_width = max(64, int(max_width))
        self.quality = max(30, min(95, int(quality)))
        self._handle = None
        self.count = 0
        path = frames_dir(self.folder)
        path.mkdir(parents=True, exist_ok=True)
        self._handle = open(_index_path(self.folder), "a", encoding="utf-8")

    def save(self, t, frame):
        if self._handle is None or frame is None or frame.size == 0:
            return False
        try:
            height, width = frame.shape[:2]
            if width <= 0 or height <= 0:
                return False
            scale = min(1.0, self.max_width / float(width))
            if scale < 1.0:
                frame = cv2.resize(
                    frame,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            name = f"{self.count:05d}.jpg"
            path = frames_dir(self.folder) / name
            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
            )
            if not ok:
                return False
            buffer.tofile(str(path))
            entry = {"t": round(float(t), 3), "file": name}
            self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._handle.flush()
            self.count += 1
            return True
        except (OSError, cv2.error):
            return False

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None


def load_frame_index(folder):
    folder = Path(folder)
    path = _index_path(folder)
    entries = []
    if not path.is_file():
        return entries
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                t = float(entry.get("t"))
                name = str(entry.get("file") or "").strip()
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if not name or not (frames_dir(folder) / name).is_file():
                continue
            entries.append({"t": t, "file": name})
    entries.sort(key=lambda item: item["t"])
    return entries


def load_frame(folder, entry):
    if not entry:
        return None
    return imread(frames_dir(folder) / entry["file"])


def save_reference(folder, frame, name):
    if frame is None or not name:
        return ""
    folder = Path(folder)
    relative = Path("state_refs") / name
    if not imwrite(folder / relative, frame):
        return ""
    return relative.as_posix()


def resolve_reference(folder, ref):
    path = Path(str(ref))
    if not path.is_absolute():
        path = Path(folder) / path
    return path


def load_reference(folder, ref):
    path = resolve_reference(folder, ref)
    if not path.is_file():
        return None
    return imread(path)


def find_frame(frames, t, latest=None, earliest=None):
    candidates = frames
    if latest is not None:
        candidates = [entry for entry in candidates if entry["t"] <= latest]
    if earliest is not None:
        candidates = [entry for entry in candidates if entry["t"] >= earliest]
    if not candidates:
        return None
    return min(candidates, key=lambda entry: abs(entry["t"] - t))
