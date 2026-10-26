"""Settings and .env loading.

.env lookup order (first non-empty value wins):
  1. the app's own folder      (<repo>/.env)
  2. the folder one level up   (<repo>/../.env)
"""
import os
from pathlib import Path

from dotenv import dotenv_values

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
ENV_PATHS = [APP_DIR / ".env", APP_DIR.parent / ".env"]

MODEL = "gpt-6-luna"  # course rule: the only model allowed on PORTKEY_API_KEY
BASE_URL = "https://api.portkey.ai/v1"

# Safety / cost limits
TOKEN_CAP = int(os.getenv("SHAADI_TOKEN_CAP", "400000"))  # total tokens before LLM calls are refused
MAX_TOOL_CALLS_PER_TURN = 5
MAX_LLM_ROUNDS_PER_TURN = 4
MAX_CHAT_CHARS = 600
HISTORY_TURNS = 8

# Cost estimate. These rates are ASSUMED placeholders (USD per 1M tokens); edit to match your Portkey pricing.
PRICE_PER_M = {"input": 1.00, "cached": 0.10, "output": 4.00}


def load_env() -> list:
    """Load KEY=VALUE pairs from the .env files. Empty values are ignored. Returns the files used."""
    used = []
    for path in ENV_PATHS:
        if not path.is_file():
            continue
        used.append(str(path))
        for key, value in dotenv_values(path).items():
            if value and not os.environ.get(key):
                os.environ[key] = value
    return used


ENV_FILES_LOADED = load_env()


def has_key() -> bool:
    return bool(os.environ.get("PORTKEY_API_KEY"))
