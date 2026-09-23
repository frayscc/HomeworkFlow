from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main() -> None:
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as directory:
        environment = os.environ.copy()
        environment.update({
            "HOMEWORKFLOW_DATA_DIR": directory,
            "HOMEWORKFLOW_NO_BROWSER": "1",
            "HOMEWORKFLOW_PORT": "18765",
        })
        process = subprocess.Popen([str(executable)], env=environment)
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError(f"packaged application exited with code {process.returncode}")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18765/api/health", timeout=1) as response:
                        payload = json.load(response)
                    if payload.get("status") == "ok":
                        print(json.dumps(payload))
                        return
                except Exception:
                    time.sleep(0.5)
            raise RuntimeError("packaged application did not become ready")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
