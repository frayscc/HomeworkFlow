from datetime import date

import cv2
import numpy as np

from homeworkflow.forms import generate_weekly_packet
from homeworkflow.roster import Roster, Student
from homeworkflow.omr.recognize import roi_pixels
from homeworkflow.scanning import ManifestRepository, decode_document, scan_document, split_a4_pages


def small_roster() -> Roster:
    return Roster("扫描测试班", tuple(Student(str(index), f"学生{index:02d}") for index in range(1, 7)))


def test_generated_a4_can_be_split_identified_and_recognized(tmp_path):
    pdf = tmp_path / "weekly.pdf"
    manifest = tmp_path / "weekly.manifest.json"
    generate_weekly_packet(pdf, manifest, roster=small_roster(),
                           start=date(2026, 9, 28), end=date(2026, 10, 2),
                           subjects=("数学", "英语"))

    rendered = decode_document(pdf.read_bytes(), pdf.name)
    assert len(split_a4_pages(rendered)) == 2

    results = scan_document(pdf.read_bytes(), pdf.name, ManifestRepository((tmp_path,)))
    assert [item["subject"] for item in results] == ["数学", "英语"]
    assert all(item["counts"]["blank"] == 6 * 5 for item in results)
    assert all(item["counts"]["review"] == 0 for item in results)


def test_review_slot_produces_a_crop(tmp_path):
    pdf = tmp_path / "weekly.pdf"
    manifest = tmp_path / "weekly.manifest.json"
    bundle = generate_weekly_packet(pdf, manifest, roster=small_roster(),
                                    start=date(2026, 9, 28), end=date(2026, 9, 28),
                                    subjects=("数学", "英语"))
    image = split_a4_pages(decode_document(pdf.read_bytes(), pdf.name))[0]
    form = bundle["forms"][0]
    x0, y0, x1, y1 = roi_pixels(form["slots"][0]["roi"], form["page"], image.shape[1], image.shape[0])
    cv2.rectangle(image, (x0, y0), (x1, y1), (0, 0, 0), thickness=-1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok

    results = scan_document(encoded.tobytes(), "marked.png", ManifestRepository((tmp_path,)), tmp_path / "artifacts")
    observation = results[0]["observations"][0]
    assert observation["classification"] == "review"
    assert observation["crop_path"]
    assert (tmp_path / "artifacts" / "crops").is_dir()


def test_color_phone_photo_is_rectified_and_recognized(tmp_path):
    pdf = tmp_path / "weekly.pdf"
    manifest = tmp_path / "weekly.manifest.json"
    bundle = generate_weekly_packet(pdf, manifest, roster=small_roster(),
                                    start=date(2026, 9, 28), end=date(2026, 10, 2),
                                    subjects=("生物", "地理"))
    form_image = split_a4_pages(decode_document(pdf.read_bytes(), pdf.name))[0]
    form = bundle["forms"][0]
    x0, y0, x1, y1 = roi_pixels(form["slots"][0]["roi"], form["page"],
                                form_image.shape[1], form_image.shape[0])
    thickness = max(3, (x1 - x0) // 7)
    cv2.line(form_image, (x0 + 2, y1 - 2), (x1 - 2, y0 + 2), (20, 20, 20), thickness)

    canvas = np.zeros((1700, 2400, 3), dtype=np.uint8)
    canvas[:] = (95, 65, 45)
    source = np.array([[0, 0], [form_image.shape[1] - 1, 0],
                       [form_image.shape[1] - 1, form_image.shape[0] - 1],
                       [0, form_image.shape[0] - 1]], dtype=np.float32)
    destination = np.array([[230, 210], [2180, 100], [2250, 1510], [140, 1590]], dtype=np.float32)
    transform = cv2.getPerspectiveTransform(source, destination)
    photographed = cv2.warpPerspective(form_image, transform, (canvas.shape[1], canvas.shape[0]))
    mask = cv2.warpPerspective(np.full(form_image.shape[:2], 255, dtype=np.uint8),
                               transform, (canvas.shape[1], canvas.shape[0]))
    canvas[mask > 0] = photographed[mask > 0]
    gradient = np.linspace(0.68, 1.0, canvas.shape[1], dtype=np.float32)[None, :, None]
    canvas = np.clip(canvas.astype(np.float32) * gradient, 0, 255).astype(np.uint8)
    canvas = cv2.rotate(canvas, cv2.ROTATE_90_CLOCKWISE)
    ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 86])
    assert ok

    results = scan_document(encoded.tobytes(), "phone-photo.jpg", ManifestRepository((tmp_path,)))
    assert results[0]["subject"] == "生物"
    assert results[0]["counts"]["slash_forward"] == 1
    assert results[0]["counts"]["blank"] == 29
