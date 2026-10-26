#!/usr/bin/env python3
"""One command to run Shaadi Sorted:  python run.py

Creates a local .venv, installs requirements, then starts the server on http://localhost:8000
"""
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".installed"


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Shaadi Sorted needs Python 3.10 or newer.")
    if not PY.exists():
        print("Creating virtual environment (.venv) ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    req = ROOT / "requirements.txt"
    if not STAMP.exists() or STAMP.stat().st_mtime < req.stat().st_mtime:
        print("Installing dependencies (about a minute the first time) ...")
        subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "-r", str(req)])
        STAMP.write_text("ok")
    port = os.environ.get("PORT", "8000")
    url = f"http://localhost:{port}"
    print(f"\nShaadi Sorted is starting at {url}  (Ctrl+C to stop)\n")
    if os.environ.get("NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    os.chdir(ROOT)
    cmd = [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port]
    if os.name == "nt":
        sys.exit(subprocess.call(cmd))
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    main()
