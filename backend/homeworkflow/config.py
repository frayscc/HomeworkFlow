from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS")) if FROZEN else Path(__file__).resolve().parents[2]
FONT_PATH = ROOT / "assets" / "fonts" / "NotoSansSC-Regular.ttf"
FRONTEND_PATH = ROOT / "frontend" / "index.html"


def _default_data_dir(*, frozen: bool = FROZEN, platform: str = sys.platform,
                      executable: Path = Path(sys.executable)) -> Path:
    if not frozen:
        return ROOT / "var"
    executable = executable.resolve()
    if platform == "win32":
        return executable.parent / "HomeworkFlow-data"
    if platform == "darwin":
        return executable.parents[3] / "HomeworkFlow-data"
    return executable.parent / "HomeworkFlow-data"


def _legacy_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HomeworkFlow"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "HomeworkFlow"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "HomeworkFlow"


def data_dir() -> Path:
    override = os.environ.get("HOMEWORKFLOW_DATA_DIR")
    path = Path(override or _default_data_dir()).expanduser().resolve()
    if FROZEN and not override and not path.exists():
        legacy = _legacy_data_dir().expanduser().resolve()
        if legacy.is_dir() and legacy != path:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy, path, dirs_exist_ok=True)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        if not FROZEN or override:
            raise
        path = _legacy_data_dir().expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
    return path
