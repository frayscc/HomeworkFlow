from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pypdfium2 as pdfium

from .omr.recognize import align_with_markers, recognize_slots, roi_pixels


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class ManifestMatch:
    bundle_path: Path
    bundle: dict[str, Any]
    form: dict[str, Any]


class ManifestRepository:
    """Read issued manifests from disk and resolve the identity carried by a QR code."""

    def __init__(self, roots: Iterable[Path]):
        self._by_qr: dict[str, ManifestMatch] = {}
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.manifest.json"):
                try:
                    bundle = json.loads(path.read_text(encoding="utf-8"))
                    for form in bundle.get("forms", []):
                        payload = form.get("qr_payload")
                        if payload:
                            self._by_qr[payload] = ManifestMatch(path, bundle, form)
                except (OSError, ValueError, TypeError):
                    continue

    def resolve(self, payload: str) -> ManifestMatch:
        try:
            return self._by_qr[payload]
        except KeyError as exc:
            raise ValueError("二维码对应的 manifest 不在本机，请先导入或重新生成该批次") from exc


def _render_pdf(content: bytes, scale: float = 3.0) -> list[np.ndarray]:
    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        raise ValueError("无法读取 PDF 文件") from exc
    images = []
    try:
        for page in document:
            rgb = page.render(scale=scale).to_numpy()
            if rgb.ndim == 3 and rgb.shape[2] == 4:
                rgb = cv2.cvtColor(rgb, cv2.COLOR_RGBA2RGB)
            images.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    finally:
        document.close()
    if not images:
        raise ValueError("PDF 中没有页面")
    return images


def decode_document(content: bytes, filename: str) -> list[np.ndarray]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _render_pdf(content)
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("扫描件仅支持 PDF、PNG、JPG、WEBP、BMP 或 TIFF")
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取扫描图片")
    return [image]


def _qr_count(image: np.ndarray) -> int:
    detector = cv2.QRCodeDetector()
    try:
        ok, decoded, _, _ = detector.detectAndDecodeMulti(image)
        return sum(bool(value) for value in decoded) if ok else 0
    except cv2.error:
        return 0


def split_a4_pages(pages: Iterable[np.ndarray]) -> list[np.ndarray]:
    """Split portrait A4 scans into two landscape A5 forms; pass A5 scans through."""
    forms = []
    for image in pages:
        height, width = image.shape[:2]
        is_portrait_a4 = height > width * 1.15
        if not is_portrait_a4 and _qr_count(image) >= 2:
            image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
            height, width = image.shape[:2]
            is_portrait_a4 = height > width
        if is_portrait_a4:
            midpoint = height // 2
            forms.extend((image[:midpoint].copy(), image[midpoint:].copy()))
        else:
            forms.append(image.copy())
    return forms


def orient_by_qr(image: np.ndarray) -> tuple[np.ndarray, str]:
    """Rotate an A5 form until its QR is decoded in the expected top-right area."""
    decoded_candidates: list[tuple[np.ndarray, str]] = []
    current = image
    for _ in range(4):
        payload, relative_center = _decode_qr(current)
        if payload:
            decoded_candidates.append((current, payload))
            height, width = current.shape[:2]
            if width > height and relative_center is not None and relative_center[0] > 0.55 and relative_center[1] < 0.48:
                return current, payload
        current = cv2.rotate(current, cv2.ROTATE_90_CLOCKWISE)
    if decoded_candidates:
        return decoded_candidates[0]
    raise ValueError("未识别到 HomeworkFlow 二维码；请确认图像清晰且二维码完整")


def _decode_qr(image: np.ndarray) -> tuple[str, tuple[float, float] | None]:
    """Decode with a few lossless preprocessing variants to reduce OpenCV QR flakiness."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    for base in (image, gray, threshold):
        for scale in (1.0, 1.5, 2.0):
            candidate = base if scale == 1.0 else cv2.resize(
                base, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
            )
            payload, points, _ = cv2.QRCodeDetector().detectAndDecode(candidate)
            if payload:
                if points is None:
                    return payload, None
                center = np.asarray(points).reshape(-1, 2).mean(axis=0)
                height, width = candidate.shape[:2]
                return payload, (float(center[0] / width), float(center[1] / height))
    return "", None


def scan_document(content: bytes, filename: str, repository: ManifestRepository,
                  artifact_dir: Path | None = None) -> list[dict[str, Any]]:
    results = []
    for index, raw_form in enumerate(split_a4_pages(decode_document(content, filename)), start=1):
        try:
            oriented, payload = orient_by_qr(raw_form)
            match = repository.resolve(payload)
            aligned = align_with_markers(oriented, match.form)
            recognition = recognize_slots(aligned, match.form)
            slot_details = {item["slot_id"]: item for item in match.form["slots"]}
            for observation in recognition["observations"]:
                slot = slot_details[observation["slot_id"]]
                observation.update({key: slot[key] for key in ("student_number", "student_name", "date", "roi")})
            aligned_name = None
            if artifact_dir is not None:
                artifact_dir.mkdir(parents=True, exist_ok=True)
                aligned_name = f"{index:03d}-{match.form['form_id']}.jpg"
                cv2.imwrite(str(artifact_dir / aligned_name), aligned, [cv2.IMWRITE_JPEG_QUALITY, 92])
                crop_dir = artifact_dir / "crops"
                crop_dir.mkdir(exist_ok=True)
                height, width = aligned.shape[:2]
                for observation in recognition["observations"]:
                    if observation["classification"] != "review" and observation["confidence"] >= 0.70:
                        observation["crop_path"] = None
                        continue
                    x0, y0, x1, y1 = roi_pixels(observation["roi"], match.form["page"], width, height)
                    pad = max(8, round(min(x1 - x0, y1 - y0) * 0.7))
                    crop = aligned[max(0, y0 - pad):min(height, y1 + pad),
                                   max(0, x0 - pad):min(width, x1 + pad)]
                    crop_name = f"{match.form['form_id']}-{observation['slot_id']}.png"
                    crop_path = crop_dir / crop_name
                    cv2.imwrite(str(crop_path), crop)
                    observation["crop_path"] = str(crop_path)
            results.append({
                "source_part": index,
                "batch_id": match.bundle["batch_id"],
                "form_id": match.form["form_id"],
                "class_name": match.form["class_name"],
                "subject": match.form["subject"],
                "copy_index": match.form["copy_index"],
                "is_spare": match.form["is_spare"],
                "manifest_name": match.bundle_path.name,
                "aligned_image": aligned_name,
                **recognition,
            })
        except ValueError as exc:
            results.append({"source_part": index, "error": str(exc)})
    return results
