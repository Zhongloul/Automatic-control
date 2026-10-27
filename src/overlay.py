import cv2

BAR_STYLES = {
    "hp": ("HP", (60, 220, 255)),
    "mp": ("MP", (255, 200, 80)),
}


def draw_overlay(canvas, state=None, status_lines=None, scale=1.0):
    state = state or {}
    _draw_rois(canvas, state, scale)
    _draw_enemies(canvas, state, scale)
    _draw_player(canvas, state, scale)
    if status_lines:
        _draw_status(canvas, status_lines)
    return canvas


def _px(value, scale):
    return int(round(float(value) * scale))


def _draw_rois(canvas, state, scale):
    rects = state.get("rects") or {}
    for name, (label, color) in BAR_STYLES.items():
        rect = rects.get(name)
        if not rect:
            continue
        x, y, width, height = rect
        x1, y1 = _px(x, scale), _px(y, scale)
        x2, y2 = _px(x + width, scale), _px(y + height, scale)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1)
        value = state.get(name)
        text = f"{label} {'-' if value is None else f'{value:.2f}'}"
        cv2.putText(canvas, text, (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    slots = state.get("skills") or []
    for index, rect in enumerate(rects.get("skills") or []):
        x, y, width, height = rect
        ready = slots[index]["ready"] if index < len(slots) else False
        color = (80, 255, 80) if ready else (120, 120, 120)
        x1, y1 = _px(x, scale), _px(y, scale)
        x2, y2 = _px(x + width, scale), _px(y + height, scale)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1)
        cv2.putText(canvas, str(index + 1), (x1 + 2, y1 + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

    gameplay = rects.get("gameplay")
    if gameplay:
        x, y, width, height = gameplay
        cv2.rectangle(
            canvas,
            (_px(x, scale), _px(y, scale)),
            (_px(x + width, scale), _px(y + height, scale)),
            (90, 90, 90),
            1,
        )


def _draw_enemies(canvas, state, scale):
    for enemy in state.get("enemies") or []:
        x, y = _px(enemy["x"], scale), _px(enemy["y"], scale)
        x2, y2 = _px(enemy["x"] + enemy["w"], scale), _px(enemy["y"] + enemy["h"], scale)
        cv2.rectangle(canvas, (x, y), (x2, y2), (0, 255, 255), 1)
        cv2.putText(
            canvas,
            f"{enemy['score']:.2f}",
            (x, max(12, y - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )


def _draw_player(canvas, state, scale):
    player = state.get("player")
    if not player:
        return
    center = (_px(player[0], scale), _px(player[1], scale))
    cv2.drawMarker(canvas, center, (0, 0, 255), cv2.MARKER_CROSS, max(10, _px(18, scale)), 1)


def _draw_status(canvas, status_lines):
    line_height = 19
    box_height = 8 + line_height * len(status_lines)
    width = min(canvas.shape[1] - 2, 560)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (width, box_height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0, canvas)
    for index, line in enumerate(status_lines):
        cv2.putText(
            canvas,
            str(line),
            (8, 17 + index * line_height),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
