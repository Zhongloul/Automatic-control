from pathlib import Path

import cv2
import numpy as np


def imread(path, flags=cv2.IMREAD_COLOR):
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, image):
    path = Path(path)
    suffix = path.suffix or ".png"
    success, buffer = cv2.imencode(suffix, image)
    if not success:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        buffer.tofile(str(path))
        return True
    except OSError:
        return False
