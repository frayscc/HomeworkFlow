from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FONT_PATH = ROOT / "assets" / "fonts" / "NotoSansSC-Regular.ttf"
FRONTEND_PATH = ROOT / "frontend" / "index.html"


def data_dir() -> Path:
    path = Path(os.environ.get("HOMEWORKFLOW_DATA_DIR", ROOT / "var")).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path

