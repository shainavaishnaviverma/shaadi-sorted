"""Single entry point for LLM calls (Portkey, gpt-6-luna) plus the token meter."""
import json
import os
import time
from datetime import datetime

from . import config
from .config import DATA_DIR

LOG_FILE = DATA_DIR / "usage_log.jsonl"


class LLMUnavailable(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def total_tokens(state: dict) -> int:
    u = state["usage"]
    return u["input"] + u["cached"] + u["output"]


def estimate_cost(usage: dict) -> float:
    p = config.PRICE_PER_M
    return (usage["input"] * p["input"] + usage["cached"] * p["cached"] + usage["output"] * p["output"]) / 1_000_000


def record(state: dict, agent: str, usage: dict, ms: int) -> None:
    u = state["usage"]
    for k in ("input", "cached", "output"):
        u[k] += usage[k]
    u["calls"] += 1
    a = u["by_agent"].setdefault(agent, {"input": 0, "cached": 0, "output": 0, "calls": 0})
    for k in ("input", "cached", "output"):
        a[k] += usage[k]
    a["calls"] += 1
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "agent": agent, **usage, "ms": ms}) + "\n")
    except OSError:
        pass


def _client():
    from openai import OpenAI

    key = os.environ.get("PORTKEY_API_KEY")
    if not key:
        raise LLMUnavailable("PORTKEY_API_KEY is not set. Copy .env.example to .env and add your key.")
    return OpenAI(api_key=key, base_url=config.BASE_URL, timeout=90, max_retries=1)


def chat(state: dict, agent: str, messages: list, tools: list = None, json_mode: bool = False) -> dict:
    """Returns {"content": str, "tool_calls": [{"id","name","arguments"}]}. Updates state['usage']."""
    if total_tokens(state) >= config.TOKEN_CAP:
        raise BudgetExceeded(f"Token cap of {config.TOKEN_CAP:,} reached. Reset the meter to continue.")
    kwargs = {"model": config.MODEL, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    start = time.time()
    try:
        resp = _client().chat.completions.create(**kwargs)
    except LLMUnavailable:
        raise
    except Exception as exc:  # network, auth, model errors
        raise LLMUnavailable(f"The AI service call failed: {type(exc).__name__}: {str(exc)[:160]}")
    u = resp.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    prompt = getattr(u, "prompt_tokens", 0) or 0
    record(state, agent, {"input": max(prompt - cached, 0), "cached": cached, "output": getattr(u, "completion_tokens", 0) or 0}, int((time.time() - start) * 1000))
    msg = resp.choices[0].message
    return {
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"}
            for tc in (msg.tool_calls or [])
        ],
    }


def parse_json(text: str):
    """Tolerant JSON parse (handles ```json fences)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])
