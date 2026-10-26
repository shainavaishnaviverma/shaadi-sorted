"""Safety helpers: prompt-injection screening, muhurat scrubbing, event log."""
import re
from datetime import datetime

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier)",
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|instructions|rules)",
    r"(system|developer)\s*(prompt|message|instruction)",
    r"you\s+are\s+now\b",
    r"\bact\s+as\b",
    r"(delete|remove|wipe|erase)\s+(all|every|everything)",
    r"new\s+instructions?",
    r"reveal\s+(your|the)\s+(prompt|instructions|key|secret)",
    r"api[_\s-]?key",
    r"<\s*/?\s*(system|assistant|tool|plan_data)",
    r"\bassistant\s*:",
    r"tool_call",
    r"https?://",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)
_NAME_RE = re.compile(r"^[\w .,'&()/-]{1,60}$", re.UNICODE)

_MUHURAT_RE = re.compile(r"m[au]h[uo]r+a?t|shubh\s*(din|tithi|samay)|auspicious\s+(date|time|day|moment|window)|lucky\s+(date|day)", re.IGNORECASE)


def log_event(state: dict, kind: str, detail: str) -> None:
    state.setdefault("safety_log", []).append(
        {"time": datetime.now().strftime("%H:%M:%S"), "kind": kind, "detail": detail[:200]}
    )
    state["safety_log"] = state["safety_log"][-30:]


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def check_free_text(label: str, text: str, max_len: int = 60):
    """Validate guest-supplied text (names, notes). Returns (clean_text, error_or_None)."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "")).strip()
    if not text:
        return "", f"{label} is empty."
    if len(text) > max_len:
        return "", f"{label} is too long (max {max_len} characters)."
    if looks_like_injection(text):
        return "", f"{label} looks like an instruction to the AI, so it was blocked."
    if label.lower() == "name" and not _NAME_RE.match(text):
        return "", "Name has unsupported characters."
    return text, None


def scrub_muhurat(text: str, confirm_with: list, state: dict = None) -> str:
    """The app never states auspicious dates. Replace any sentence that does."""
    if not text or not _MUHURAT_RE.search(text):
        return text
    who = " or ".join(confirm_with[:2]) if confirm_with else "your religious authority"
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [p for p in parts if not _MUHURAT_RE.search(p)]
    kept.append(f"For auspicious dates and timings, please ask your {who}.")
    if state is not None:
        log_event(state, "muhurat_scrubbed", "Removed an auspicious-date statement from AI output")
    return " ".join(kept)
