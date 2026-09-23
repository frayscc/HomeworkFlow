from datetime import date

import cv2

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
