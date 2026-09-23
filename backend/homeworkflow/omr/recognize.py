from __future__ import annotations

from typing import Any

import cv2
import numpy as np


CLASSES = ("blank", "slash_forward", "slash_back", "x", "review")


def align_with_markers(image: np.ndarray, manifest: dict[str, Any]) -> np.ndarray:
    """Align one already-cropped A5 form using its four solid corner markers."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    binary = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)[1]
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if not (0.00007 * width * height <= area <= 0.001 * width * height):
            continue
        if not 0.75 <= w / max(h, 1) <= 1.33:
            continue
        if float(binary[y:y + h, x:x + w].mean()) / 255 < 0.72:
            continue
        candidates.append(np.array([x + w / 2, y + h / 2], dtype=np.float32))
    if len(candidates) < 4:
        raise ValueError("定位标记不足，A5 表可能被裁切或遮挡")
    observed_corners = [np.array([0, 0]), np.array([width, 0]),
                        np.array([0, height]), np.array([width, height])]
    observed = np.array([min(candidates, key=lambda point: np.linalg.norm(point - corner))
                         for corner in observed_corners], dtype=np.float32)
    page = manifest["page"]
    markers = manifest["markers"]
    expected = []
    for marker in (markers[2], markers[3], markers[0], markers[1]):
        expected.append([
            (marker["x_mm"] + marker["width_mm"] / 2) / page["width_mm"] * width,
            (page["height_mm"] - marker["y_mm"] - marker["height_mm"] / 2) / page["height_mm"] * height,
        ])
    transform = cv2.getPerspectiveTransform(observed, np.array(expected, dtype=np.float32))
    return cv2.warpPerspective(image, transform, (width, height), borderValue=(255, 255, 255))


def roi_pixels(roi: dict[str, float], page: dict[str, float], width: int, height: int) -> tuple[int, int, int, int]:
    x0 = round(roi["x_mm"] / page["width_mm"] * width)
    x1 = round((roi["x_mm"] + roi["width_mm"]) / page["width_mm"] * width)
    y0 = round((page["height_mm"] - roi["y_mm"] - roi["height_mm"]) / page["height_mm"] * height)
    y1 = round((page["height_mm"] - roi["y_mm"]) / page["height_mm"] * height)
    return x0, y0, x1, y1


def classify_slot(gray: np.ndarray) -> tuple[str, float, dict[str, float]]:
    h, w = gray.shape
    # Ignore the printed checkbox border plus a small alignment tolerance.  A
    # wider inset prevents sub-pixel perspective jitter from looking like ink.
    inset = max(2, round(min(h, w) * 0.28))
    inner = gray[inset:h - inset, inset:w - inset]
    ink = inner < 175
    density = int(ink.sum()) / max(1, inner.size)
    yy, xx = np.indices(inner.shape)
    tolerance = max(1.5, inner.shape[0] * 0.12)
    forward_band = np.abs((inner.shape[0] - 1 - yy) - xx) <= tolerance
    back_band = np.abs(yy - xx) <= tolerance
    forward = float(ink[forward_band].mean())
    back = float(ink[back_band].mean())
    features = {"ink_density": round(density, 4), "forward": round(forward, 4), "back": round(back, 4)}
    if density < 0.018:
        return "blank", min(0.99, 0.82 + (0.018 - density) * 8), features
    # Dense fill is a deliberate request for human review and must be tested
    # before X, otherwise a black box would look strong on both diagonals.
    if density >= 0.55:
        return "review", 0.0, features
    if forward >= 0.42 and back >= 0.42 and density >= 0.16:
        return "x", min(0.99, 0.60 + min(forward, back)), features
    if forward >= 0.16 and forward >= back * 1.45:
        return "slash_forward", min(0.98, 0.55 + forward), features
    if back >= 0.16 and back >= forward * 1.45:
        return "slash_back", min(0.98, 0.55 + back), features
    return "review", 0.0, features


def recognize_slots(image: np.ndarray, manifest: dict[str, Any]) -> dict[str, Any]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    observations = []
    for slot in manifest["slots"]:
        x0, y0, x1, y1 = roi_pixels(slot["roi"], manifest["page"], gray.shape[1], gray.shape[0])
        classification, confidence, features = classify_slot(gray[y0:y1, x0:x1])
        observations.append({"slot_id": slot["slot_id"], "classification": classification,
                             "confidence": round(confidence, 4), "features": features})
    counts = {name: sum(item["classification"] == name for item in observations) for name in CLASSES}
    return {"algorithm_version": "homework-prototype-1", "counts": counts, "observations": observations}
