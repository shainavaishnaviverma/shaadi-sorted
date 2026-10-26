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
    if tools:
        return _chat_responses(state, agent, messages, tools)  # function tools need the Responses API on this gateway
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


def _to_responses_input(messages: list) -> list:
    """Convert chat-style messages (with tool_calls / tool results) to Responses API input items."""
    items = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
        elif role == "assistant" and m.get("_raw"):
            items.extend(m["_raw"])  # exact items the model produced (keeps reasoning items paired with calls)
        elif role == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for c in m["tool_calls"]:
                items.append({"type": "function_call", "call_id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]})
        else:
            items.append({"role": role, "content": m["content"] or ""})
    return items


def _chat_responses(state: dict, agent: str, messages: list, tools: list) -> dict:
    flat_tools = [{"type": "function", "name": t["function"]["name"], "description": t["function"]["description"],
                   "parameters": t["function"]["parameters"], "strict": False} for t in tools]
    kwargs = {"model": config.MODEL, "input": _to_responses_input(messages), "tools": flat_tools, "reasoning": {"effort": "low"}}
    start = time.time()
    client = _client()
    try:
        try:
            resp = client.responses.create(**kwargs)
        except Exception as exc:
            if "reasoning" in str(exc).lower() and "reasoning" in kwargs:  # gateway rejected the effort setting: retry without it
                kwargs.pop("reasoning")
                resp = client.responses.create(**kwargs)
            else:
                raise
    except Exception as exc:
        raise LLMUnavailable(f"The AI service call failed: {type(exc).__name__}: {str(exc)[:200]}")
    u = resp.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    prompt = getattr(u, "input_tokens", 0) or 0
    record(state, agent, {"input": max(prompt - cached, 0), "cached": cached, "output": getattr(u, "output_tokens", 0) or 0},
           int((time.time() - start) * 1000))
    calls, text = [], ""
    for item in resp.output:
        if item.type == "function_call":
            calls.append({"id": item.call_id, "name": item.name, "arguments": item.arguments or "{}"})
        elif item.type == "message":
            text += "".join(getattr(c, "text", "") or "" for c in item.content)
    return {"content": text, "tool_calls": calls, "raw": [o.model_dump(exclude_none=True) for o in resp.output]}


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
