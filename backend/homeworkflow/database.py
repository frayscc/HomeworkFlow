from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    number TEXT NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(class_id, number)
);
CREATE TABLE IF NOT EXISTS homework_weeks (
    batch_id TEXT PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    class_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'issued',
    pdf_path TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS homework_week_dates (
    batch_id TEXT NOT NULL REFERENCES homework_weeks(batch_id),
    date TEXT NOT NULL,
    PRIMARY KEY(batch_id, date)
);
CREATE TABLE IF NOT EXISTS homework_forms (
    form_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES homework_weeks(batch_id),
    subject TEXT NOT NULL,
    subject_code TEXT NOT NULL,
    copy_index INTEGER NOT NULL,
    is_spare INTEGER NOT NULL,
    qr_payload TEXT NOT NULL UNIQUE,
    layout_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework_imports (
    import_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework_observations (
    id INTEGER PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES homework_imports(import_id),
    batch_id TEXT NOT NULL REFERENCES homework_weeks(batch_id),
    form_id TEXT NOT NULL REFERENCES homework_forms(form_id),
    slot_id TEXT NOT NULL,
    student_number TEXT NOT NULL,
    student_name TEXT NOT NULL,
    date TEXT NOT NULL,
    raw_classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    features_json TEXT NOT NULL,
    final_status TEXT,
    review_required INTEGER NOT NULL,
    reviewed INTEGER NOT NULL,
    reviewed_at TEXT,
    crop_path TEXT,
    UNIQUE(import_id, form_id, slot_id)
);
CREATE TABLE IF NOT EXISTS homework_notes (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES homework_weeks(batch_id),
    form_id TEXT REFERENCES homework_forms(form_id),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS homework_exports (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES homework_weeks(batch_id),
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_batch ON homework_observations(batch_id);
CREATE INDEX IF NOT EXISTS idx_observations_review ON homework_observations(batch_id, review_required, reviewed);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(homework_observations)")}
            if "crop_path" not in columns:
                connection.execute("ALTER TABLE homework_observations ADD COLUMN crop_path TEXT")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def register_bundle(self, bundle: dict[str, Any], pdf_path: Path, manifest_path: Path) -> None:
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO classes(name, created_at) VALUES (?, ?)",
                               (bundle["class_name"], _now()))
            class_id = connection.execute("SELECT id FROM classes WHERE name = ?", (bundle["class_name"],)).fetchone()[0]
            students = bundle["forms"][0]["students"] if bundle["forms"] else []
            for student in students:
                connection.execute(
                    """INSERT INTO students(class_id, number, name) VALUES (?, ?, ?)
                       ON CONFLICT(class_id, number) DO UPDATE SET name=excluded.name, active=1""",
                    (class_id, student["number"], student["name"]),
                )
            connection.execute(
                """INSERT OR IGNORE INTO homework_weeks
                   (batch_id, class_id, class_name, start_date, end_date, status, pdf_path, manifest_path, created_at)
                   VALUES (?, ?, ?, ?, ?, 'issued', ?, ?, ?)""",
                (bundle["batch_id"], class_id, bundle["class_name"], bundle["start_date"], bundle["end_date"],
                 str(pdf_path), str(manifest_path), _now()),
            )
            connection.executemany("INSERT OR IGNORE INTO homework_week_dates(batch_id, date) VALUES (?, ?)",
                                   [(bundle["batch_id"], value) for value in bundle["selected_dates"]])
            for form in bundle["forms"]:
                connection.execute(
                    """INSERT OR IGNORE INTO homework_forms
                       (form_id, batch_id, subject, subject_code, copy_index, is_spare, qr_payload, layout_hash, manifest_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (form["form_id"], bundle["batch_id"], form["subject"], form["subject_code"],
                     form["copy_index"], int(form["is_spare"]), form["qr_payload"], form["layout_hash"],
                     json.dumps(form, ensure_ascii=False)),
                )

    def record_import(self, import_id: str, source_name: str, source_path: Path,
                      forms: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO homework_imports VALUES (?, ?, ?, ?)",
                               (import_id, source_name, str(source_path), _now()))
            for form in forms:
                if "error" in form:
                    continue
                for item in form["observations"]:
                    raw = item["classification"]
                    confidence = float(item["confidence"])
                    required = raw == "review" or confidence < 0.70
                    if raw in {"slash_forward", "slash_back"}:
                        final_status = "submitted"
                    elif raw in {"blank", "x"}:
                        final_status = "missing"
                    else:
                        final_status = None
                    connection.execute(
                        """INSERT INTO homework_observations
                           (import_id, batch_id, form_id, slot_id, student_number, student_name, date,
                            raw_classification, confidence, features_json, final_status, review_required, reviewed, crop_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (import_id, form["batch_id"], form["form_id"], item["slot_id"], item["student_number"],
                         item["student_name"], item["date"], raw, confidence,
                         json.dumps(item["features"], ensure_ascii=False), final_status,
                         int(required), int(not required), item.get("crop_path")),
                    )
            batch_ids = {item["batch_id"] for item in forms if "batch_id" in item}
            for batch_id in batch_ids:
                connection.execute("UPDATE homework_weeks SET status='scanned' WHERE batch_id=? AND status='issued'",
                                   (batch_id,))

    def list_weeks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT w.*,
                          (SELECT COUNT(*) FROM homework_forms f WHERE f.batch_id=w.batch_id) AS form_count,
                          (SELECT COUNT(DISTINCT o.import_id) FROM homework_observations o WHERE o.batch_id=w.batch_id) AS import_count,
                          (SELECT COUNT(*) FROM homework_observations o
                           WHERE o.batch_id=w.batch_id AND o.review_required=1 AND o.reviewed=0) AS pending_reviews
                   FROM homework_weeks w
                   ORDER BY w.created_at DESC"""
            ).fetchall()
            return [dict(item) for item in rows]

    def pending_reviews(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT o.*, f.subject, f.copy_index
                   FROM homework_observations o JOIN homework_forms f ON f.form_id=o.form_id
                   WHERE o.batch_id=? AND o.review_required=1 AND o.reviewed=0
                   ORDER BY f.subject, o.date, o.student_number""", (batch_id,)
            ).fetchall()
            return [dict(item) for item in rows]

    def review(self, observation_id: int, final_status: str) -> dict[str, Any]:
        if final_status not in {"submitted", "missing"}:
            raise ValueError("复核结论必须是 submitted 或 missing")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE homework_observations SET final_status=?, reviewed=1, reviewed_at=?
                   WHERE id=? AND review_required=1""", (final_status, _now(), observation_id)
            )
            if cursor.rowcount != 1:
                raise ValueError("待复核记录不存在或已经处理")
            return dict(connection.execute("SELECT * FROM homework_observations WHERE id=?", (observation_id,)).fetchone())

    def confirm_week(self, batch_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            week = connection.execute("SELECT * FROM homework_weeks WHERE batch_id=?", (batch_id,)).fetchone()
            if week is None:
                raise ValueError("周批次不存在")
            observation_count = connection.execute(
                "SELECT COUNT(*) FROM homework_observations WHERE batch_id=?", (batch_id,)).fetchone()[0]
            pending = connection.execute(
                """SELECT COUNT(*) FROM homework_observations
                   WHERE batch_id=? AND (final_status IS NULL OR (review_required=1 AND reviewed=0))""",
                (batch_id,),
            ).fetchone()[0]
            if observation_count == 0:
                raise ValueError("该批次还没有扫描识别结果")
            missing_subjects = [row[0] for row in connection.execute(
                """SELECT DISTINCT f.subject FROM homework_forms f
                   WHERE f.batch_id=? AND f.is_spare=0 AND NOT EXISTS (
                     SELECT 1 FROM homework_observations o JOIN homework_forms scanned ON scanned.form_id=o.form_id
                     WHERE o.batch_id=f.batch_id AND scanned.subject=f.subject
                   )""", (batch_id,)
            )]
            if missing_subjects:
                raise ValueError(f"以下学科还没有扫描结果：{'、'.join(missing_subjects)}")
            if pending:
                raise ValueError(f"还有 {pending} 项需要人工复核")
            connection.execute("UPDATE homework_weeks SET status='confirmed', confirmed_at=? WHERE batch_id=?",
                               (_now(), batch_id))
            return dict(connection.execute("SELECT * FROM homework_weeks WHERE batch_id=?", (batch_id,)).fetchone())

    def observation_image(self, observation_id: int) -> Path:
        with self.connect() as connection:
            row = connection.execute("SELECT crop_path FROM homework_observations WHERE id=?", (observation_id,)).fetchone()
        if row is None or not row[0]:
            raise ValueError("该记录没有复核裁图")
        path = Path(row[0])
        if not path.is_file():
            raise ValueError("复核裁图已不存在")
        return path

    def add_export(self, batch_id: str, path: Path, kind: str) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO homework_exports(batch_id, path, kind, created_at) VALUES (?, ?, ?, ?)",
                               (batch_id, str(path), kind, _now()))

    def report_data(self, batch_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            week = connection.execute("SELECT * FROM homework_weeks WHERE batch_id=?", (batch_id,)).fetchone()
            if week is None:
                raise ValueError("周批次不存在")
            if week["status"] != "confirmed":
                raise ValueError("周批次确认后才能导出")
            form_row = connection.execute(
                "SELECT manifest_json FROM homework_forms WHERE batch_id=? ORDER BY is_spare, subject LIMIT 1",
                (batch_id,),
            ).fetchone()
            students = json.loads(form_row[0])["students"] if form_row else []
            dates = [row[0] for row in connection.execute(
                "SELECT date FROM homework_week_dates WHERE batch_id=? ORDER BY date", (batch_id,)
            )]
            subjects = [row[0] for row in connection.execute(
                "SELECT DISTINCT subject FROM homework_forms WHERE batch_id=? AND is_spare=0 ORDER BY form_id",
                (batch_id,),
            )]
            rows = connection.execute(
                """SELECT o.*, f.subject, f.is_spare
                   FROM homework_observations o
                   JOIN homework_forms f ON f.form_id=o.form_id
                   WHERE o.batch_id=? AND o.id=(
                     SELECT MAX(newer.id) FROM homework_observations newer
                     WHERE newer.batch_id=o.batch_id AND newer.form_id=o.form_id AND newer.slot_id=o.slot_id
                   )""", (batch_id,)
            ).fetchall()
            notes = [dict(row) for row in connection.execute(
                "SELECT * FROM homework_notes WHERE batch_id=? ORDER BY created_at", (batch_id,)
            )]
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            grouped.setdefault((item["subject"], item["student_number"], item["date"]), []).append(item)
        results = []
        for (subject, number, item_date), items in grouped.items():
            final_status = "submitted" if any(item["final_status"] == "submitted" for item in items) else "missing"
            results.append({
                "subject": subject, "student_number": number, "student_name": items[0]["student_name"],
                "date": item_date, "final_status": final_status,
                "raw_classifications": ",".join(sorted({item["raw_classification"] for item in items})),
            })
        return {"week": dict(week), "students": students, "dates": dates, "subjects": subjects,
                "results": results, "notes": notes}
