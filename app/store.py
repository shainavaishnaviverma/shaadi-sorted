"""State persistence: one JSON file, atomic writes, one lock."""
import json
import os
import threading
from pathlib import Path

from .config import DATA_DIR

STATE_FILE = DATA_DIR / "state.json"
LOCK = threading.RLock()


def empty_usage() -> dict:
    return {"input": 0, "cached": 0, "output": 0, "calls": 0, "by_agent": {}}


def empty_state(usage: dict = None) -> dict:
    return {
        "status": "empty",
        "usage": usage or empty_usage(),
        "safety_log": [],
        "pending": None,
        "chat": [],
    }


def load() -> dict:
    with LOCK:
        if STATE_FILE.is_file():
            try:
                return json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                pass
        return empty_state()


def save(state: dict) -> None:
    with LOCK:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(STATE_FILE) + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        os.replace(tmp, STATE_FILE)
