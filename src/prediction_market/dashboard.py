from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app_path = Path(__file__).resolve().parents[2] / "apps" / "monitor_dashboard.py"
    command = [sys.executable, "-m", "streamlit", "run", str(app_path), *sys.argv[1:]]
    try:
        raise SystemExit(subprocess.call(command))
    except KeyboardInterrupt:
        raise SystemExit(130)
