from pathlib import Path

from homeworkflow.config import _default_data_dir


def test_frozen_data_is_stored_beside_the_program():
    windows_exe = Path("C:/Tools/HomeworkFlow/HomeworkFlow.exe")
    assert _default_data_dir(frozen=True, platform="win32", executable=windows_exe) == (
        windows_exe.resolve().parent / "HomeworkFlow-data"
    )
    mac_exe = Path("/Applications/HomeworkFlow.app/Contents/MacOS/HomeworkFlow")
    assert _default_data_dir(frozen=True, platform="darwin", executable=mac_exe) == (
        Path("/Applications/HomeworkFlow-data")
    )
