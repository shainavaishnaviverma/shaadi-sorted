"""Chat-agent tools. Every tool validates its arguments and mutates state in code.

Destructive actions (remove_guest) never run from the model: the tool only stores a
pending action, and the UI's Confirm button runs it via confirm_pending().
"""
import json
import re
from datetime import date
from urllib.parse import quote

from . import safety

STATUSES = ["yes", "no", "maybe", "pending"]
MAX_GUESTS = 500

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "add_guest",
        "description": "Add a guest or family and invite them to functions. Use functions=['all'] to invite to every function.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "party_size": {"type": "integer", "minimum": 1, "maximum": 20},
            "side": {"type": "string", "enum": ["a", "b"], "description": "Which family side (see side labels in context)"},
            "functions": {"type": "array", "items": {"type": "string"}, "description": "Function names or ['all']"},
            "phone": {"type": "string", "description": "Optional phone number with country code"}},
            "required": ["name", "party_size", "side"]}}},
    {"type": "function", "function": {
        "name": "update_rsvp",
        "description": "Set one guest's RSVP for ONE function.",
        "parameters": {"type": "object", "properties": {
            "guest": {"type": "string"}, "function": {"type": "string"},
            "status": {"type": "string", "enum": STATUSES}}, "required": ["guest", "function", "status"]}}},
    {"type": "function", "function": {
        "name": "set_rsvp",
        "description": "Set one RSVP status across ALL functions the guest is invited to (e.g. 'the Gills can't make it').",
        "parameters": {"type": "object", "properties": {
            "guest": {"type": "string"}, "status": {"type": "string", "enum": STATUSES}}, "required": ["guest", "status"]}}},
    {"type": "function", "function": {
        "name": "remove_guest",
        "description": "Request removal of a guest. This does NOT delete immediately: the user must press Confirm in the app.",
        "parameters": {"type": "object", "properties": {"guest": {"type": "string"}}, "required": ["guest"]}}},
    {"type": "function", "function": {
        "name": "update_budget",
        "description": "Either set the total budget (total) or set one category's share (category + pct). Other categories rebalance to keep 100%.",
        "parameters": {"type": "object", "properties": {
            "total": {"type": "number"}, "category": {"type": "string"}, "pct": {"type": "number", "minimum": 0, "maximum": 60}}}}},
    {"type": "function", "function": {
        "name": "complete_task",
        "description": "Mark a checklist task done (or not done).",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Task id (t3) or part of its title"}, "done": {"type": "boolean"}}, "required": ["task"]}}},
    {"type": "function", "function": {
        "name": "get_next_step",
        "description": "Return the most urgent unfinished checklist task.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "draft_invite_message",
        "description": "Create a short WhatsApp invitation text for a guest and function, plus a wa.me link. Nothing is sent. Do not put links in the text.",
        "parameters": {"type": "object", "properties": {
            "guest": {"type": "string"}, "function": {"type": "string"},
            "message": {"type": "string", "description": "Warm invitation text, max 500 characters, no URLs"}},
            "required": ["guest", "function", "message"]}}},
]


# ----------------------------------------------------------------------- lookups
def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def find_guest(state: dict, ref: str):
    guests = state.get("guests", [])
    n = _norm(ref)
    exact = [g for g in guests if g["id"] == ref or _norm(g["name"]) == n]
    hits = exact or [g for g in guests if n and (n in _norm(g["name"]) or _norm(g["name"]) in n)]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"No guest matches '{ref}'."
    return None, f"'{ref}' is ambiguous. Candidates: {', '.join(g['name'] for g in hits[:6])}."


def match_functions(state: dict, ref: str) -> list:
    n = _norm(ref)
    fns = state.get("functions", [])
    exact = [f for f in fns if f["id"] == ref or _norm(f["name"]) == n or _norm(f["key"]) == n]
    return exact or [f for f in fns if n and (n in _norm(f["name"]) or n in _norm(f["key"]))]


def find_function(state: dict, ref: str):
    hits = match_functions(state, ref)
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"No function matches '{ref}'. Functions: {', '.join(f['name'] for f in state['functions'])}."
    return None, f"'{ref}' is ambiguous. Candidates: {', '.join(f['name'] + ' (' + f['id'] + ')' for f in hits[:6])}."


def next_step(state: dict) -> dict:
    pending = sorted([t for t in state.get("tasks", []) if not t["done"]], key=lambda t: (t["due"], t["id"]))
    if not pending:
        return {"all_done": True}
    t = pending[0]
    days = (date.fromisoformat(t["due"]) - date.today()).days
    fn = next((f["name"] for f in state["functions"] if f["id"] == t["function_id"]), None)
    return {"task": t, "days_left": days, "overdue": days < 0, "function": fn, "remaining": len(pending)}


# ----------------------------------------------------------------------- tools
def add_guest(state, name, party_size, side, functions=None, phone=None, lenient=False):
    clean, err = safety.check_free_text("Name", name)
    if err:
        safety.log_event(state, "blocked_guest_name", f"{err} Input: {str(name)[:80]}")
        return {"error": err}
    if side not in ("a", "b"):
        return {"error": "side must be 'a' or 'b'."}
    try:
        party_size = int(party_size)
    except (TypeError, ValueError):
        return {"error": "party_size must be a number."}
    if not 1 <= party_size <= 20:
        return {"error": "party_size must be between 1 and 20."}
    if len(state["guests"]) >= MAX_GUESTS:
        return {"error": f"Guest list limit of {MAX_GUESTS} reached."}
    if any(_norm(g["name"]) == _norm(clean) and g["side"] == side for g in state["guests"]):
        return {"error": f"{clean} is already on the list. Use update_rsvp or set_rsvp instead."}
    digits = None
    if phone:
        digits = re.sub(r"\D", "", str(phone))
        if not 7 <= len(digits) <= 15:
            return {"error": "Phone number should have 7 to 15 digits including country code."}
    fns = functions or ["all"]
    chosen = []
    for ref in fns:
        if _norm(ref) == "all":
            chosen = list(state["functions"])
            break
        if lenient:
            chosen += match_functions(state, ref)
        else:
            f, e = find_function(state, ref)
            if e:
                return {"error": e}
            chosen.append(f)
    if not chosen:
        return {"error": "No matching functions."}
    guest = {"id": f"g{max([int(g['id'][1:]) for g in state['guests']] + [0]) + 1}", "name": clean, "party_size": party_size,
             "side": side, "phone": digits, "rsvps": {f["id"]: "pending" for f in chosen}}
    state["guests"].append(guest)
    return {"ok": True, "guest": guest["name"], "party_size": party_size, "side": state["couple"]["side_labels"][side],
            "invited_to": [f["name"] for f in chosen]}


def update_rsvp(state, guest, function, status):
    if status not in STATUSES:
        return {"error": f"status must be one of {STATUSES}"}
    g, err = find_guest(state, guest)
    if err:
        return {"error": err}
    f, err = find_function(state, function)
    if err:
        return {"error": err}
    if f["id"] not in g["rsvps"]:
        return {"error": f"{g['name']} is not invited to {f['name']}. Invited to: {', '.join(_fn_name(state, i) for i in g['rsvps'])}."}
    g["rsvps"][f["id"]] = status
    return {"ok": True, "guest": g["name"], "function": f["name"], "status": status}


def set_rsvp(state, guest, status):
    if status not in STATUSES:
        return {"error": f"status must be one of {STATUSES}"}
    g, err = find_guest(state, guest)
    if err:
        return {"error": err}
    for k in g["rsvps"]:
        g["rsvps"][k] = status
    return {"ok": True, "guest": g["name"], "status": status, "functions_updated": len(g["rsvps"])}


def remove_guest(state, guest):
    g, err = find_guest(state, guest)
    if err:
        return {"error": err}
    state["pending"] = {"id": f"p{len(state.get('safety_log', []))}-{g['id']}", "type": "remove_guest", "guest_id": g["id"],
                        "label": f"Remove {g['name']} ({g['party_size']} people) from the guest list"}
    return {"status": "pending_confirmation",
            "message": f"Asked the user to confirm removing {g['name']}. It has NOT been removed yet; tell the user to press Confirm in the app."}


def confirm_pending(state, approve: bool) -> dict:
    p = state.get("pending")
    if not p:
        return {"error": "Nothing is waiting for confirmation."}
    state["pending"] = None
    if not approve:
        return {"ok": True, "result": "Cancelled. Nothing was removed."}
    if p["type"] == "remove_guest":
        before = len(state["guests"])
        state["guests"] = [g for g in state["guests"] if g["id"] != p["guest_id"]]
        return {"ok": True, "result": p["label"] + " - done.", "removed": before - len(state["guests"])}
    return {"error": "Unknown pending action."}


def update_budget(state, total=None, category=None, pct=None):
    rows = state["budget"]
    if total is not None:
        try:
            total = float(total)
        except (TypeError, ValueError):
            return {"error": "total must be a number."}
        if not 0 < total <= 1e10:
            return {"error": "total must be positive and at most 10 billion."}
        state["budget_total"] = total
    elif category is not None and pct is not None:
        n = _norm(category)
        hits = [r for r in rows if _norm(r["category"]) == n] or [r for r in rows if n in _norm(r["category"])]
        if len(hits) != 1:
            return {"error": f"Category '{category}' not found or ambiguous. Categories: {', '.join(r['category'] for r in rows)}."}
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return {"error": "pct must be a number."}
        if not 0 <= pct <= 60:
            return {"error": "pct must be between 0 and 60."}
        target = hits[0]
        others = [r for r in rows if r is not target]
        rest = sum(r["pct"] for r in others)
        scale = (100 - pct) / rest if rest else 0
        target["pct"] = round(pct, 1)
        for r in others:
            r["pct"] = round(r["pct"] * scale, 1)
        others[-1]["pct"] = round(100 - sum(r["pct"] for r in rows if r is not others[-1]), 1)
    else:
        return {"error": "Provide either total, or category and pct."}
    for r in rows:
        r["amount"] = round(state["budget_total"] * r["pct"] / 100)
    rows[-1]["amount"] += round(state["budget_total"]) - sum(r["amount"] for r in rows)
    return {"ok": True, "total": state["budget_total"], "currency": state["currency"],
            "budget": [{"category": r["category"], "pct": r["pct"], "amount": r["amount"]} for r in rows]}


def complete_task(state, task, done=True):
    n = _norm(task)
    tasks = state["tasks"]
    hits = [t for t in tasks if t["id"] == task] or [t for t in tasks if n and n in _norm(t["title"])]
    if len(hits) != 1:
        if not hits:
            return {"error": f"No task matches '{task}'."}
        return {"error": f"'{task}' is ambiguous. Candidates: {'; '.join(t['id'] + ' ' + t['title'] for t in hits[:5])}"}
    hits[0]["done"] = bool(done)
    return {"ok": True, "task": hits[0]["title"], "done": hits[0]["done"]}


def get_next_step(state):
    r = next_step(state)
    if r.get("all_done"):
        return {"all_done": True}
    return {"task": r["task"]["title"], "task_id": r["task"]["id"], "due": r["task"]["due"], "days_left": r["days_left"],
            "overdue": r["overdue"], "function": r["function"], "tasks_remaining": r["remaining"]}


def draft_invite_message(state, guest, function, message):
    g, err = find_guest(state, guest)
    if err:
        return {"error": err}
    f, err = find_function(state, function)
    if err:
        return {"error": err}
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(message or "")).strip()
    if not text or len(text) > 600:
        return {"error": "Message must be 1 to 600 characters."}
    if safety.looks_like_injection(text):
        safety.log_event(state, "blocked_invite_text", "Invite text contained a link or instruction-like text")
        return {"error": "Invite text can't contain links or instruction-like text. Rewrite it as a plain invitation."}
    base = f"https://wa.me/{g['phone']}" if g.get("phone") else "https://wa.me/"
    return {"ok": True, "guest": g["name"], "function": f["name"], "text": text,
            "wa_link": f"{base}?text={quote(text)}", "note": "Draft only. Nothing was sent."}


def _fn_name(state, fid):
    return next((f["name"] for f in state["functions"] if f["id"] == fid), fid)


REGISTRY = {
    "add_guest": add_guest, "update_rsvp": update_rsvp, "set_rsvp": set_rsvp, "remove_guest": remove_guest,
    "update_budget": update_budget, "complete_task": complete_task, "get_next_step": get_next_step,
    "draft_invite_message": draft_invite_message,
}


def run_tool(state: dict, name: str, raw_args) -> dict:
    fn = REGISTRY.get(name)
    if not fn:
        return {"error": f"Unknown tool '{name}'."}
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        return {"error": "Tool arguments were not valid JSON."}
    allowed = set(TOOL_SCHEMAS[[t["function"]["name"] for t in TOOL_SCHEMAS].index(name)]["function"]["parameters"]["properties"])
    args = {k: v for k, v in args.items() if k in allowed}  # drop unexpected arguments (e.g. 'lenient')
    try:
        return fn(state, **args)
    except TypeError as exc:
        return {"error": f"Bad arguments: {exc}"}


def load_demo_guests(state: dict, guests: list) -> None:
    for g in guests:
        res = add_guest(state, g["name"], g["party_size"], g["side"], g["functions"], lenient=True)
        guest = next((x for x in state["guests"] if x["name"] == g["name"]), None)
        if guest and g.get("rsvp"):
            for k in guest["rsvps"]:
                guest["rsvps"][k] = g["rsvp"]
