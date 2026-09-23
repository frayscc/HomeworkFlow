from __future__ import annotations

import json
import os
import socket
import threading
import uuid
import webbrowser
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .config import FRONTEND_PATH, data_dir
from .database import Database
from .forms import DEFAULT_SUBJECTS, generate_weekly_packet
from .roster import Roster, parse_roster, parse_text_roster
from .reporting import export_week_xlsx
from .scanning import ManifestRepository, scan_document


app = FastAPI(title="HomeworkFlow local API", version=__version__)


def database() -> Database:
    return Database(data_dir() / "homeworkflow.sqlite3")


class ReviewDecision(BaseModel):
    final_status: str


async def roster_from_input(roster_text: str, class_name: str,
                            roster_file: Optional[UploadFile]) -> Roster:
    if roster_text.strip():
        return parse_text_roster(roster_text, class_name.strip() or "未命名班级")
    if roster_file is not None:
        roster = parse_roster(await roster_file.read(), roster_file.filename or "roster.xlsx")
        return roster.with_class_name(class_name) if class_name.strip() else roster
    raise ValueError("请粘贴学生名单，每行格式为“学号 姓名”")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_PATH, media_type="text/html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/config")
def config() -> dict[str, object]:
    return {"subjects": list(DEFAULT_SUBJECTS), "max_dates": 5, "max_students": 49}


@app.post("/api/roster/preview")
async def roster_preview(roster_text: str = Form(""), class_name: str = Form(""),
                         roster_file: Optional[UploadFile] = File(None)) -> dict[str, object]:
    try:
        roster = await roster_from_input(roster_text, class_name, roster_file)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"class_name": roster.class_name, "student_count": len(roster.students),
            "students": [{"number": item.number, "name": item.name} for item in roster.students]}


@app.post("/api/forms/generate")
async def generate_forms(
    roster_text: str = Form(""),
    roster_file: Optional[UploadFile] = File(None),
    class_name: str = Form(""),
    start_date: date = Form(...),
    end_date: date = Form(...),
    subjects: str = Form(...),
    weekdays_only: bool = Form(True),
    copies: int = Form(1),
) -> FileResponse:
    try:
        roster = await roster_from_input(roster_text, class_name, roster_file)
        subject_list = json.loads(subjects)
        if not isinstance(subject_list, list) or not all(isinstance(item, str) for item in subject_list):
            raise ValueError("学科参数格式无效")
        export_dir = data_dir() / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        safe_class = "".join(char for char in roster.class_name if char not in '<>:"/\\|?*')[:40]
        batch_id = str(uuid.uuid4())
        stem = f"{safe_class}_{start_date.isoformat()}_{end_date.isoformat()}_{batch_id[:8]}_作业周表"
        output_pdf = export_dir / f"{stem}.pdf"
        output_manifest = export_dir / f"{stem}.manifest.json"
        bundle = generate_weekly_packet(output_pdf, output_manifest, roster=roster, start=start_date, end=end_date,
                                        subjects=subject_list, weekdays_only=weekdays_only, copies=copies,
                                        batch_id=batch_id)
        database().register_bundle(bundle, output_pdf, output_manifest)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = f"{roster.class_name}_{start_date.isoformat()}_作业周表.pdf"
    response = FileResponse(output_pdf, media_type="application/pdf", filename=filename)
    response.headers["X-HomeworkFlow-Manifest"] = quote(output_manifest.name)
    return response


@app.get("/api/manifests/{filename}")
def download_manifest(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith(".manifest.json"):
        raise HTTPException(status_code=404, detail="manifest 不存在")
    path = data_dir() / "exports" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="manifest 不存在")
    return FileResponse(path, media_type="application/json", filename=filename)


@app.post("/api/scans/analyze")
async def analyze_scan(scan_file: UploadFile = File(...)) -> dict[str, object]:
    try:
        content = await scan_file.read()
        import_id = str(uuid.uuid4())
        source_name = Path(scan_file.filename or "scan.jpg").name
        import_dir = data_dir() / "imports" / import_id
        import_dir.mkdir(parents=True, exist_ok=True)
        source_path = import_dir / source_name
        source_path.write_bytes(content)
        export_dir = data_dir() / "exports"
        repository = ManifestRepository((export_dir,))
        forms = scan_document(content, source_name, repository, import_dir / "aligned")
        db = database()
        for manifest_name in {item["manifest_name"] for item in forms if "manifest_name" in item}:
            manifest_path = export_dir / manifest_name
            bundle = json.loads(manifest_path.read_text(encoding="utf-8"))
            pdf_path = export_dir / f"{manifest_name.removesuffix('.manifest.json')}.pdf"
            db.register_bundle(bundle, pdf_path, manifest_path)
        db.record_import(import_id, source_name, source_path, forms)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    successful = sum("error" not in item for item in forms)
    return {"import_id": import_id, "source_name": scan_file.filename, "form_count": len(forms),
            "successful_count": successful, "forms": forms}


@app.get("/api/weeks")
def list_weeks() -> dict[str, object]:
    return {"weeks": database().list_weeks()}


@app.get("/api/weeks/{batch_id}/reviews")
def pending_reviews(batch_id: str) -> dict[str, object]:
    return {"reviews": database().pending_reviews(batch_id)}


@app.post("/api/observations/{observation_id}/review")
def review_observation(observation_id: int, decision: ReviewDecision) -> dict[str, object]:
    try:
        return {"observation": database().review(observation_id, decision.final_status)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/observations/{observation_id}/image")
def observation_image(observation_id: int) -> FileResponse:
    try:
        return FileResponse(database().observation_image(observation_id), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/weeks/{batch_id}/confirm")
def confirm_week(batch_id: str) -> dict[str, object]:
    try:
        return {"week": database().confirm_week(batch_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/weeks/{batch_id}/export")
async def export_week(batch_id: str, template_file: Optional[UploadFile] = File(None)) -> FileResponse:
    try:
        db = database()
        report = db.report_data(batch_id)
        template_content = await template_file.read() if template_file is not None else None
        safe_class = "".join(char for char in report["week"]["class_name"] if char not in '<>:"/\\|?*')[:40]
        filename = f"{safe_class}_{report['week']['start_date']}_周汇总_{batch_id[:8]}.xlsx"
        path = data_dir() / "exports" / filename
        export_week_xlsx(path, report, template_content)
        db.add_export(batch_id, path, "xlsx")
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            filename=filename)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run() -> None:
    port = int(os.environ["HOMEWORKFLOW_PORT"]) if os.environ.get("HOMEWORKFLOW_PORT") else _free_port()
    if os.environ.get("HOMEWORKFLOW_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    run()
