from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .config import FONT_PATH
from .roster import Roster, Student


FONT_NAME = "NotoSansSC"
A5_WIDTH_MM = 210.0
A5_HEIGHT_MM = 148.5
DEFAULT_SUBJECTS = ("语文", "数学", "英语", "物理", "道法", "政治", "历史")
SUBJECT_CODES = {"语文": "CHI", "数学": "MAT", "英语": "ENG", "物理": "PHY",
                 "道法": "MOR", "政治": "POL", "历史": "HIS"}
WEEKDAY_LABELS = "一二三四五六日"


def _register_font() -> None:
    try:
        pdfmetrics.getFont(FONT_NAME)
    except KeyError:
        pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))


def selected_dates(start: date, end: date, weekdays_only: bool = True) -> list[date]:
    if end < start:
        raise ValueError("结束日期不能早于开始日期")
    if (end - start).days > 31:
        raise ValueError("单次日期范围不能超过 32 天")
    result = []
    current = start
    while current <= end:
        if not weekdays_only or current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    if not result:
        raise ValueError("所选范围内没有需要打印的日期")
    if len(result) > 5:
        raise ValueError("当前 A5 周表最多容纳 5 个日期，请缩短范围或拆成两个批次")
    return result


def _rect(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x_mm": round(x, 4), "y_mm": round(y, 4),
            "width_mm": round(width, 4), "height_mm": round(height, 4)}


def _qr(payload: str) -> ImageReader:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=5, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    stream = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(stream, format="PNG")
    stream.seek(0)
    return ImageReader(stream)


def _compact_uuid(value: str) -> str:
    return base64.urlsafe_b64encode(uuid.UUID(value).bytes).decode("ascii").rstrip("=")


def _form_manifest(*, batch_id: str, form_id: str, roster: Roster, subject: str,
                   dates: list[date], copy_index: int, is_spare: bool) -> dict[str, Any]:
    marker_positions = ((5.5, 5.5), (201.0, 5.5), (5.5, 139.5), (201.0, 139.5))
    markers = [_rect(x, y, 3.5, 3.5) for x, y in marker_positions]
    students = list(roster.students)
    groups = [students[:13], students[13:25], students[25:37], students[37:49]]
    block_xs = (10.0, 58.0, 106.0, 154.0)
    block_width, number_width, name_width = 45.5, 7.5, 15.5
    header_height, row_height, table_top = 8.0, 5.1, 109.5
    mark_area = block_width - number_width - name_width
    date_width = mark_area / len(dates)
    roi_size = min(3.8, date_width - 0.45, row_height - 1.0)
    slots = []
    row_index = 0
    for block_x, group in zip(block_xs, groups):
        for local_row, student in enumerate(group):
            row_bottom = table_top - header_height - (local_row + 1) * row_height
            for date_index, item_date in enumerate(dates):
                cell_left = block_x + number_width + name_width + date_index * date_width
                roi_x = cell_left + (date_width - roi_size) / 2
                roi_y = row_bottom + (row_height - roi_size) / 2
                slots.append({
                    "slot_id": f"r{row_index:02d}-d{date_index}",
                    "student_number": student.number,
                    "student_name": student.name,
                    "row_index": row_index,
                    "date_index": date_index,
                    "date": item_date.isoformat(),
                    "roi": _rect(roi_x, roi_y, roi_size, roi_size),
                })
            row_index += 1
    geometry_basis = {"page": [A5_WIDTH_MM, A5_HEIGHT_MM], "markers": markers,
                      "dates": len(dates), "student_count": len(students),
                      "blocks": [len(group) for group in groups], "slots": [slot["roi"] for slot in slots]}
    layout_hash = hashlib.sha256(json.dumps(geometry_basis, sort_keys=True).encode()).hexdigest()
    subject_code = SUBJECT_CODES.get(subject) or hashlib.sha256(subject.encode()).hexdigest()[:3].upper()
    qr_payload = f"HF1|{_compact_uuid(batch_id)}|{_compact_uuid(form_id)}|{subject_code}|{layout_hash[:10]}"
    return {
        "format_version": 1,
        "template_id": "a5-weekly-49-v1",
        "batch_id": batch_id,
        "form_id": form_id,
        "class_name": roster.class_name,
        "subject": subject,
        "subject_code": subject_code,
        "copy_index": copy_index,
        "is_spare": is_spare,
        "dates": [item.isoformat() for item in dates],
        "students": [asdict(student) for student in students],
        "page": {"width_mm": A5_WIDTH_MM, "height_mm": A5_HEIGHT_MM},
        "markers": markers,
        "slots": slots,
        "layout_hash": layout_hash,
        "qr_payload": qr_payload,
    }


def _draw_marker(canvas: Canvas, x: float, y: float) -> None:
    canvas.setFillColorRGB(0, 0, 0)
    canvas.rect(x * mm, y * mm, 3.5 * mm, 3.5 * mm, fill=1, stroke=0)


def _draw_block(canvas: Canvas, base_y: float, x: float, students: list[Student], dates: list[date],
                slots_by_key: dict[tuple[str, str], dict[str, Any]]) -> None:
    width, number_width, name_width = 45.5, 7.5, 15.5
    header_height, row_height, top = 8.0, 5.1, 109.5
    bottom = top - header_height - len(students) * row_height
    mark_area, date_width = width - number_width - name_width, (width - number_width - name_width) / len(dates)
    y = lambda local: base_y + local

    canvas.setStrokeColorRGB(0.1, 0.1, 0.1)
    canvas.setLineWidth(0.65)
    canvas.rect(x * mm, y(bottom) * mm, width * mm, (top - bottom) * mm, fill=0, stroke=1)
    canvas.setLineWidth(0.3)
    for edge in (x + number_width, x + number_width + name_width):
        canvas.line(edge * mm, y(bottom) * mm, edge * mm, y(top) * mm)
    for index in range(1, len(dates)):
        edge = x + number_width + name_width + index * date_width
        canvas.line(edge * mm, y(bottom) * mm, edge * mm, y(top) * mm)
    canvas.line(x * mm, y(top - header_height) * mm, (x + width) * mm, y(top - header_height) * mm)

    canvas.setFont(FONT_NAME, 5.8)
    canvas.drawCentredString((x + number_width / 2) * mm, y(top - 5.1) * mm, "学号")
    canvas.drawCentredString((x + number_width + name_width / 2) * mm, y(top - 5.1) * mm, "姓名")
    for index, item_date in enumerate(dates):
        center = x + number_width + name_width + (index + 0.5) * date_width
        canvas.setFont(FONT_NAME, 5.0)
        canvas.drawCentredString(center * mm, y(top - 3.3) * mm, WEEKDAY_LABELS[item_date.weekday()])
        canvas.setFont(FONT_NAME, 3.9)
        canvas.drawCentredString(center * mm, y(top - 6.6) * mm, f"{item_date.month}/{item_date.day}")

    for row, student in enumerate(students):
        row_top = top - header_height - row * row_height
        row_bottom = row_top - row_height
        if row:
            canvas.line(x * mm, y(row_top) * mm, (x + width) * mm, y(row_top) * mm)
        canvas.setFont(FONT_NAME, 6.2)
        canvas.drawCentredString((x + number_width / 2) * mm, y(row_bottom + 1.35) * mm, student.number)
        canvas.setFont(FONT_NAME, 5.8)
        canvas.drawCentredString((x + number_width + name_width / 2) * mm,
                                 y(row_bottom + 1.35) * mm, student.name)
        for item_date in dates:
            roi = slots_by_key[(student.number, item_date.isoformat())]["roi"]
            canvas.setStrokeColorRGB(0.45, 0.45, 0.45)
            canvas.rect(roi["x_mm"] * mm, y(roi["y_mm"]) * mm,
                        roi["width_mm"] * mm, roi["height_mm"] * mm, fill=0, stroke=1)
            canvas.setStrokeColorRGB(0.1, 0.1, 0.1)


def _draw_form(canvas: Canvas, base_y: float, manifest: dict[str, Any]) -> None:
    y = lambda local: base_y + local
    for marker in manifest["markers"]:
        _draw_marker(canvas, marker["x_mm"], y(marker["y_mm"]))
    canvas.drawImage(_qr(manifest["qr_payload"]), 184 * mm, y(119.2) * mm, 14.5 * mm, 14.5 * mm,
                     preserveAspectRatio=True, mask="auto")
    subject = manifest["subject"]
    dates = [date.fromisoformat(value) for value in manifest["dates"]]
    students = [Student(**item) for item in manifest["students"]]
    slots_by_key = {(slot["student_number"], slot["date"]): slot for slot in manifest["slots"]}

    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont(FONT_NAME, 12.4)
    spare = "（备用）" if manifest["is_spare"] else ""
    canvas.drawCentredString(105 * mm, y(134.0) * mm, f"{subject}作业周统计表{spare}")
    canvas.setFont(FONT_NAME, 7.2)
    canvas.drawString(11 * mm, y(124.5) * mm, f"班级：{manifest['class_name']}")
    canvas.drawString(70 * mm, y(124.5) * mm,
                      f"周期：{dates[0].year}年{dates[0].month}月{dates[0].day}日 - {dates[-1].month}月{dates[-1].day}日")
    canvas.setFont(FONT_NAME, 5.4)
    canvas.drawRightString(181.5 * mm, y(128.5) * mm, f"{subject} · 第{manifest['copy_index']}份")
    canvas.setFont(FONT_NAME, 6.1)
    canvas.drawString(11 * mm, y(116.2) * mm,
                      "填写：/ 或 \\ = 已交　空白 = 缺交　X = 缺交更正　涂黑/不清楚 = 人工复核")

    groups = [students[:13], students[13:25], students[25:37], students[37:49]]
    for x, group in zip((10.0, 58.0, 106.0, 154.0), groups):
        _draw_block(canvas, base_y, x, group, dates, slots_by_key)

    canvas.setLineWidth(0.55)
    canvas.rect(10 * mm, y(9.7) * mm, 190 * mm, 19.5 * mm, fill=0, stroke=1)
    canvas.setFont(FONT_NAME, 6.4)
    canvas.drawString(12 * mm, y(25.0) * mm, "学科代表备注（补交、请假及特殊情况；本区域默认不自动识别）")
    canvas.setStrokeColorRGB(0.75, 0.75, 0.75)
    canvas.setLineWidth(0.25)
    canvas.line(12 * mm, y(18.0) * mm, 198 * mm, y(18.0) * mm)
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFont(FONT_NAME, 5.1)
    canvas.drawCentredString(105 * mm, y(6.2) * mm,
                            f"按 100% / 实际大小打印 · 四角黑块须完整 · {manifest['layout_hash'][:12]}")


def generate_weekly_packet(output_pdf: Path, output_manifest: Path, *, roster: Roster,
                           start: date, end: date, subjects: Iterable[str] = DEFAULT_SUBJECTS,
                           weekdays_only: bool = True, copies: int = 1,
                           batch_id: str | None = None) -> dict[str, Any]:
    _register_font()
    dates = selected_dates(start, end, weekdays_only)
    subject_list = [item.strip() for item in subjects if item.strip()]
    if not subject_list:
        raise ValueError("至少选择一个学科")
    if not 1 <= copies <= 5:
        raise ValueError("打印份数必须在 1-5 之间")
    batch_id = batch_id or str(uuid.uuid4())
    forms = []
    for subject in subject_list:
        for copy_index in range(1, copies + 1):
            forms.append(_form_manifest(batch_id=batch_id, form_id=str(uuid.uuid4()), roster=roster,
                                        subject=subject, dates=dates, copy_index=copy_index, is_spare=False))
    if len(forms) % 2:
        last = forms[-1]
        forms.append(_form_manifest(batch_id=batch_id, form_id=str(uuid.uuid4()), roster=roster,
                                    subject=last["subject"], dates=dates,
                                    copy_index=int(last["copy_index"]) + 1, is_spare=True))

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output_pdf), pagesize=A4, pageCompression=1)
    canvas.setTitle(f"{roster.class_name}作业周统计表")
    canvas.setAuthor("HomeworkFlow")
    for index in range(0, len(forms), 2):
        _draw_form(canvas, A5_HEIGHT_MM, forms[index])
        _draw_form(canvas, 0, forms[index + 1])
        canvas.setStrokeColorRGB(0.4, 0.4, 0.4)
        canvas.setDash(3 * mm, 2 * mm)
        canvas.line(4.5 * mm, A5_HEIGHT_MM * mm, 205.5 * mm, A5_HEIGHT_MM * mm)
        canvas.setDash()
        canvas.setFillColorRGB(1, 1, 1)
        canvas.rect(88 * mm, (A5_HEIGHT_MM - 2.7) * mm, 34 * mm, 5.4 * mm, fill=1, stroke=0)
        canvas.setFillColorRGB(0.25, 0.25, 0.25)
        canvas.setFont(FONT_NAME, 6)
        canvas.drawCentredString(105 * mm, (A5_HEIGHT_MM - 1.7) * mm, "- 沿虚线裁切 -")
        canvas.showPage()
    canvas.save()

    bundle = {
        "format_version": 1,
        "batch_id": batch_id,
        "class_name": roster.class_name,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "selected_dates": [item.isoformat() for item in dates],
        "subjects": subject_list,
        "copies": copies,
        "forms": forms,
    }
    output_manifest.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle

