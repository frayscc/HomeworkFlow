from datetime import date

from pypdf import PdfReader

from homeworkflow.forms import DEFAULT_SUBJECTS, generate_weekly_packet, selected_dates
from homeworkflow.roster import Roster, Student


def demo_roster() -> Roster:
    return Roster("演示班", tuple(Student(str(index), f"演示学生{index:02d}") for index in range(1, 50)))


def test_weekdays_and_page_pairing(tmp_path):
    assert [item.isoformat() for item in selected_dates(date(2026, 9, 28), date(2026, 10, 4))] == [
        "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"
    ]
    pdf = tmp_path / "weekly.pdf"
    manifest = tmp_path / "weekly.manifest.json"
    result = generate_weekly_packet(pdf, manifest, roster=demo_roster(),
                                    start=date(2026, 9, 28), end=date(2026, 10, 4))
    assert len(PdfReader(str(pdf)).pages) == 4
    assert len(result["forms"]) == 8
    assert sum(not form["is_spare"] for form in result["forms"]) == len(DEFAULT_SUBJECTS)
    assert sum(form["is_spare"] for form in result["forms"]) == 1
    assert all(len(form["slots"]) == 49 * 5 for form in result["forms"])


def test_more_than_five_dates_requires_split():
    try:
        selected_dates(date(2026, 9, 28), date(2026, 10, 5))
    except ValueError as exc:
        assert "最多容纳 5 个日期" in str(exc)
    else:
        raise AssertionError("six weekdays were accepted")

