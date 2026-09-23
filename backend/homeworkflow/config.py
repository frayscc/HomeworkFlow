from __future__ import annotations

import os
import sys
from pathlib import Path


FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS")) if FROZEN else Path(__file__).resolve().parents[2]
FONT_PATH = ROOT / "assets" / "fonts" / "NotoSansSC-Regular.ttf"
FRONTEND_PATH = ROOT / "frontend" / "index.html"


def _default_data_dir() -> Path:
    if not FROZEN:
        return ROOT / "var"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HomeworkFlow"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HomeworkFlow"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "HomeworkFlow"


def data_dir() -> Path:
    path = Path(os.environ.get("HOMEWORKFLOW_DATA_DIR", _default_data_dir())).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
