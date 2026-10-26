"""Plan generation.

Division of labour:
  * Planner agent (LLM)        - picks and orders functions from traditions.json
  * Tradition-Merge agent (LLM)- only for two traditions: one timeline + clash flags
  * Code                       - dates, checklist deadlines, budget split, validation
If the LLM is unavailable or returns junk, deterministic fallbacks keep the app working.
"""
import json
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from . import llm, safety, traditions
from .config import DATA_DIR
from .store import empty_state

CURRENCIES = ["INR", "USD", "GBP", "CAD", "AUD", "AED"]
TIME_ORDER = {"morning": 0, "afternoon": 1, "evening": 2}
CONF_ORDER = {"low": 0, "medium": 1, "high": 2}
CLASH_TYPES = {"dietary", "dress", "timing", "who_pays"}


class TraditionSel(BaseModel):
    religion: str
    variant: Optional[str] = None


class Intake(BaseModel):
    partner_a: str = Field(min_length=1, max_length=40)
    partner_b: str = Field(min_length=1, max_length=40)
    side_a_label: str = Field(default="Bride's side", max_length=30)
    side_b_label: str = Field(default="Groom's side", max_length=30)
    city_a: str = Field(min_length=1, max_length=40)
    city_b: str = Field(min_length=1, max_length=40)
    wedding_city: str = Field(min_length=1, max_length=40)
    wedding_date: date
    budget_total: float = Field(gt=0, le=1e10)
    currency: str = "INR"
    guest_count: int = Field(ge=10, le=5000)
    traditions: list[TraditionSel] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _check(self):
        if self.currency not in CURRENCIES:
            raise ValueError(f"Currency must be one of {', '.join(CURRENCIES)}")
        if self.wedding_date < date.today() + timedelta(days=14):
            raise ValueError("Wedding date must be at least 14 days from today.")
        for field in ("partner_a", "partner_b", "side_a_label", "side_b_label", "city_a", "city_b", "wedding_city"):
            clean, err = safety.check_free_text("Text", getattr(self, field), 40)
            if err:
                raise ValueError(f"{field}: {err}")
            setattr(self, field, clean)
        for t in self.traditions:
            traditions.validate_selection(t.religion, t.variant)
        return self


def is_dual(intake: Intake) -> bool:
    if len(intake.traditions) < 2:
        return False
    a, b = intake.traditions
    return (a.religion, a.variant) != (b.religion, b.variant)


# --------------------------------------------------------------------------- helpers
def _side_host(host: str) -> str:
    return {"bride": "a", "groom": "b"}.get(host, "both")


def _pool(effs: dict) -> dict:
    pool = {}
    for side, eff in effs.items():
        for f in eff["functions"]:
            pool[f"{side}:{f['key']}"] = {**f, "id": f"{side}:{f['key']}", "side": side}
    return pool


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- Planner agent
PLANNER_SYSTEM = """You are the Planner agent for Shaadi Sorted, an Indian wedding planning app.
From the CANDIDATE functions provided, choose which to include and put them in sensible order for this couple.
Judgment calls only: use guest count, budget per guest and days until the wedding to decide which OPTIONAL functions fit.
Rules:
- Use only candidate ids exactly as given. Never invent functions.
- Always include every function where optional=false.
- You may adjust day_offset (days relative to the wedding day, negative = before) and guest_share (0.05 to 1.0, share of all guests invited).
- Never state or suggest auspicious dates, muhurat or lucky days. Those come from the family's religious authority.
- Keep notes to at most two short sentences.
Return ONLY a JSON object:
{"selected":[{"id":"a:haldi","day_offset":-1,"guest_share":0.4}],"dropped":[{"id":"a:mehndi","reason":"short reason"}],"notes":"..."}
The order of "selected" is the order of the plan."""


def _planner_fallback(pool: dict) -> list:
    chosen = [f for f in pool.values() if not f.get("optional")]
    optional = sorted([f for f in pool.values() if f.get("optional")], key=lambda f: -f["guest_share"])
    room = max(0, 8 - len(chosen))
    chosen += optional[:room]
    return [{"id": f["id"], "day_offset": f["day_offset"], "guest_share": f["guest_share"]} for f in chosen]


def run_planner(state: dict, intake: Intake, pool: dict, use_llm: bool) -> dict:
    info = {"mode": "fallback", "notes": "", "error": None}
    selected = None
    if use_llm:
        days_left = (intake.wedding_date - date.today()).days
        payload = {
            "couple": {"guests": intake.guest_count, "budget": intake.budget_total, "currency": intake.currency,
                       "budget_per_guest": round(intake.budget_total / intake.guest_count), "days_until_wedding": days_left,
                       "traditions": [t.model_dump() for t in intake.traditions]},
            "candidates": [{"id": f["id"], "name": f["name"], "optional": bool(f.get("optional")),
                            "host": f["host_side"], "day_offset": f["day_offset"], "guest_share": f["guest_share"],
                            "confidence": f["confidence"]} for f in pool.values()],
        }
        try:
            out = llm.chat(state, "planner", [{"role": "system", "content": PLANNER_SYSTEM},
                                              {"role": "user", "content": json.dumps(payload)}], json_mode=True)
            data = llm.parse_json(out["content"])
            selected = [s for s in data.get("selected", []) if isinstance(s, dict) and s.get("id") in pool]
            info.update(mode="ai", notes=str(data.get("notes", ""))[:300])
        except (llm.LLMUnavailable, llm.BudgetExceeded, ValueError, json.JSONDecodeError) as exc:
            info["error"] = str(exc)[:200]
            selected = None
    if not selected:
        selected = _planner_fallback(pool)
        info["mode"] = "fallback"
    # Code enforces: no duplicates, all non-optional present, values in range
    seen, clean = set(), []
    for s in selected:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        f = pool[s["id"]]
        clean.append({"id": s["id"], "day_offset": int(_clamp(s.get("day_offset"), -60, 3, f["day_offset"])),
                      "guest_share": _clamp(s.get("guest_share"), 0.05, 1.0, f["guest_share"])})
    for f in pool.values():
        if not f.get("optional") and f["id"] not in seen:
            clean.append({"id": f["id"], "day_offset": f["day_offset"], "guest_share": f["guest_share"]})
    return {"selected": clean, "info": info}


# --------------------------------------------------------------------------- Tradition-Merge agent
MERGE_SYSTEM = """You are the Tradition-Merge agent for Shaadi Sorted. Two families with different wedding traditions are planning ONE wedding.
You get the selected functions from each side (ids like "a:wedding", "b:sangeet") and each side's constraints.
Tasks:
1. Build ONE timeline. Combine overlapping functions (e.g. two engagements, two receptions) into one shared entry when that makes sense;
   keep distinct ceremonies (e.g. two different wedding rituals) as separate entries, usually the same day at different times of day.
2. Flag clashes between the two traditions in: dietary, dress, timing, who_pays. For each give a practical suggestion and ONE-LINE rationale.
Rules:
- "sources" must use only ids you were given. Every given id must appear in at least one entry's sources.
- origin is "a", "b" or "shared" (shared = combines both sides).
- time_of_day is morning, afternoon or evening. day_offset is days from the wedding day (negative = before).
- Never state or suggest auspicious dates, muhurat or lucky days. Say to ask the family's religious authority instead.
- Be neutral and respectful to both traditions. No more than 6 clashes.
Return ONLY JSON:
{"timeline":[{"name":"...","origin":"shared","sources":["a:engagement","b:engagement"],"day_offset":-45,"time_of_day":"afternoon","rationale":"..."}],
 "clashes":[{"type":"dietary","functions":["Reception"],"description":"...","suggestion":"...","rationale":"..."}],"notes":"..."}"""


def _merge_fallback(sel_pool: list, effs: dict, labels: dict) -> tuple:
    by_key = {}
    for f in sel_pool:
        by_key.setdefault(f["key"], []).append(f)
    timeline = []
    for key, items in by_key.items():
        sides = {i["side"] for i in items}
        if len(items) == 2 and items[0]["name"] == items[1]["name"]:
            timeline.append({"name": items[0]["name"], "origin": "shared", "sources": [i["id"] for i in items],
                             "day_offset": items[0]["day_offset"], "time_of_day": items[0]["time_of_day"],
                             "rationale": "Both traditions have this function, so it is combined."})
        elif len(sides) == 2:
            for n, i in enumerate(sorted(items, key=lambda x: x["side"])):
                timeline.append({"name": i["name"], "origin": i["side"], "sources": [i["id"]], "day_offset": i["day_offset"],
                                 "time_of_day": i["time_of_day"] if n == 0 else ("afternoon" if i["time_of_day"] == "morning" else i["time_of_day"]),
                                 "rationale": "Each tradition keeps its own ceremony; they run back to back."})
        else:
            for i in items:
                timeline.append({"name": i["name"], "origin": i["side"], "sources": [i["id"]], "day_offset": i["day_offset"],
                                 "time_of_day": i["time_of_day"], "rationale": f"Specific to {labels[i['side']]}'s tradition."})
    clashes = []
    for t in traditions.CONSTRAINT_TYPES:
        ra = " ".join(c["rule"] for c in effs["a"]["constraints"][t])
        rb = " ".join(c["rule"] for c in effs["b"]["constraints"][t])
        if ra and rb and ra != rb:
            who = " and ".join(effs["a"]["confirm_with"][:1] + effs["b"]["confirm_with"][:1])
            clashes.append({"type": t, "functions": [], "description": f"{effs['a']['label']}: {ra}  |  {effs['b']['label']}: {rb}",
                            "suggestion": f"Agree one rule for shared functions and check it with your {who}.",
                            "rationale": "The two traditions state different rules."})
    return timeline, clashes[:6], ""


def run_merge(state: dict, intake: Intake, effs: dict, sel_pool: list, use_llm: bool) -> dict:
    labels = {"a": intake.side_a_label, "b": intake.side_b_label}
    info = {"mode": "fallback", "notes": "", "error": None}
    timeline = clashes = None
    if use_llm:
        payload = {
            "side_labels": labels,
            "traditions": {s: effs[s]["label"] for s in effs},
            "selected": [{"id": f["id"], "name": f["name"], "day_offset": f["day_offset"], "time_of_day": f["time_of_day"],
                          "host": f["host_side"], "rituals": f["rituals"]} for f in sel_pool],
            "constraints": {s: {t: [c["rule"] for c in effs[s]["constraints"][t]] for t in traditions.CONSTRAINT_TYPES} for s in effs},
        }
        try:
            out = llm.chat(state, "merge", [{"role": "system", "content": MERGE_SYSTEM},
                                            {"role": "user", "content": json.dumps(payload)}], json_mode=True)
            data = llm.parse_json(out["content"])
            valid = {f["id"] for f in sel_pool}
            timeline = []
            for e in data.get("timeline", []):
                srcs = [s for s in e.get("sources", []) if s in valid]
                if srcs:
                    timeline.append({"name": str(e.get("name", ""))[:70], "sources": srcs, "origin": None,
                                     "day_offset": int(_clamp(e.get("day_offset"), -60, 3, 0)),
                                     "time_of_day": e.get("time_of_day") if e.get("time_of_day") in TIME_ORDER else "afternoon",
                                     "rationale": str(e.get("rationale", ""))[:200]})
            clashes = []
            for c in data.get("clashes", [])[:6]:
                t = "who_pays" if c.get("type") in ("payer", "who_pays") else c.get("type")
                if t in CLASH_TYPES:
                    clashes.append({"type": t, "functions": [str(x)[:60] for x in c.get("functions", [])][:4],
                                    "description": str(c.get("description", ""))[:300],
                                    "suggestion": str(c.get("suggestion", ""))[:300],
                                    "rationale": str(c.get("rationale", ""))[:200]})
            if not timeline:
                raise ValueError("empty timeline")
            info.update(mode="ai", notes=str(data.get("notes", ""))[:300])
        except (llm.LLMUnavailable, llm.BudgetExceeded, ValueError, TypeError, json.JSONDecodeError) as exc:
            info["error"] = str(exc)[:200]
            timeline = None
    if timeline is None:
        timeline, clashes, _ = _merge_fallback(sel_pool, effs, labels)
        info["mode"] = "fallback"
    # Code enforces: all selected ids covered, origin derived from sources
    covered = {s for e in timeline for s in e["sources"]}
    for f in sel_pool:
        if f["id"] not in covered:
            timeline.append({"name": f["name"], "sources": [f["id"]], "origin": None, "day_offset": f["day_offset"],
                             "time_of_day": f["time_of_day"], "rationale": "Kept from " + labels[f["side"]] + "'s tradition."})
    for e in timeline:
        sides = {s.split(":")[0] for s in e["sources"]}
        e["origin"] = "shared" if len(sides) == 2 else sides.pop()
    return {"timeline": timeline, "clashes": clashes, "info": info}


# --------------------------------------------------------------------------- code: dates, tasks, budget
def _function_from_entry(entry: dict, pool: dict, intake: Intake, today: date) -> dict:
    srcs = [pool[s] for s in entry["sources"]]
    first = srcs[0]
    hosts = {_side_host(s["host_side"]) for s in srcs}
    host = hosts.pop() if len(hosts) == 1 else "both"
    d = intake.wedding_date + timedelta(days=entry["day_offset"])
    if entry["day_offset"] < 0 and d < today + timedelta(days=3):
        d = today + timedelta(days=3)
    rituals = []
    for s in srcs:
        for r in s["rituals"]:
            if r not in rituals:
                rituals.append(r)
    return {
        "key": first["key"], "name": entry.get("name") or first["name"], "date": d.isoformat(),
        "time_of_day": entry["time_of_day"], "day_offset": entry["day_offset"],
        "city": {"a": intake.city_a, "b": intake.city_b}.get(host, intake.wedding_city),
        "host_side": host, "origin": entry["origin"], "merge_note": entry.get("rationale") or None,
        "est_guests": max(5, round(intake.guest_count * max(s["guest_share"] for s in srcs))),
        "rituals": rituals[:6], "optional": all(s.get("optional") for s in srcs),
        "confidence": min((s["confidence"] for s in srcs), key=lambda c: CONF_ORDER[c]),
        "sources": entry["sources"], "main": any(s.get("main") for s in srcs),
    }


def build_tasks(functions: list, effs: dict, intake: Intake, today: date) -> list:
    src_to_fn = {s: f for f in functions for s in f["sources"]}
    wedding = intake.wedding_date
    tasks, seen = [], set()
    for side, eff in effs.items():
        for t in eff["task_templates"]:
            fn = src_to_fn.get(f"{side}:{t['function']}")
            if not fn or t["title"] in seen:
                continue
            seen.add(t["title"])
            due = date.fromisoformat(fn["date"]) - timedelta(days=t["lead_days"])
            owner = fn["host_side"] if fn["host_side"] != "both" else (side if len(effs) > 1 else "both")
            tasks.append({"title": t["title"], "function_id": fn["id"], "due": due, "owner": owner})
    confirm = []
    for eff in effs.values():
        for c in eff["confirm_with"][:1]:
            if c not in confirm:
                confirm.append(c)
    for g in traditions.generic_tasks():
        tasks.append({"title": g["title"].replace("{confirm_with}", " / ".join(confirm)), "function_id": None,
                      "due": wedding - timedelta(days=g["lead_days_before_wedding"]), "owner": "both"})
    tasks.sort(key=lambda t: (t["due"], t["title"]))
    # Overdue tasks are spread out starting today instead of all landing on one day.
    i = 0
    fn_dates = {f["id"]: date.fromisoformat(f["date"]) for f in functions}
    for t in tasks:
        t["compressed"] = False
        if t["due"] < today:
            limit = (fn_dates.get(t["function_id"], wedding)) - timedelta(days=1)
            t["due"] = max(today, min(today + timedelta(days=2 * i), limit))
            t["compressed"] = True
            i += 1
    tasks.sort(key=lambda t: (t["due"], t["title"]))
    for n, t in enumerate(tasks, 1):
        t.update(id=f"t{n}", due=t["due"].isoformat(), done=False)
    return tasks


def build_budget(total: float, n_functions: int) -> list:
    templates = json.loads((DATA_DIR / "budget_templates.json").read_text())
    tpl = templates["compact"] if n_functions <= 3 else templates["grand"] if n_functions >= 8 else templates["standard"]
    rows = [{"category": r["category"], "pct": float(r["pct"]), "amount": round(total * r["pct"] / 100)} for r in tpl]
    rows[-1]["amount"] += round(total) - sum(r["amount"] for r in rows)  # rounding remainder
    return rows


# --------------------------------------------------------------------------- orchestration
def build_state(intake: Intake, usage_state: dict, use_llm: bool = True, today: date = None) -> dict:
    """Runs Planner -> (Merge if two traditions) -> code. `usage_state` is the current state (usage is carried over)."""
    today = today or date.today()
    state = empty_state(usage_state["usage"])
    state["safety_log"] = usage_state.get("safety_log", [])
    dual = is_dual(intake)
    sels = [intake.traditions[0], intake.traditions[1] if dual else intake.traditions[0]]
    effs = {"a": traditions.effective(sels[0].religion, sels[0].variant)}
    if dual:
        effs["b"] = traditions.effective(sels[1].religion, sels[1].variant)
    pool = _pool(effs)

    planner = run_planner(state, intake, pool, use_llm)
    sel_pool = []
    for s in planner["selected"]:
        f = dict(pool[s["id"]])
        f["day_offset"], f["guest_share"] = s["day_offset"], s["guest_share"]
        sel_pool.append(f)
    pool = {f["id"]: f for f in sel_pool} | {k: v for k, v in pool.items() if k not in {f["id"] for f in sel_pool}}

    if dual:
        merge = run_merge(state, intake, effs, sel_pool, use_llm)
        timeline, clashes, merge_info = merge["timeline"], merge["clashes"], merge["info"]
    else:
        timeline = [{"name": f["name"], "sources": [f["id"]], "origin": "shared", "day_offset": f["day_offset"],
                     "time_of_day": f["time_of_day"], "rationale": None} for f in sel_pool]
        clashes, merge_info = [], None

    functions = [_function_from_entry(e, pool, intake, today) for e in timeline]
    functions.sort(key=lambda f: (f["date"], TIME_ORDER[f["time_of_day"]], f["name"]))
    for n, f in enumerate(functions, 1):
        f["id"] = f"f{n}"
    # Safety: never keep auspicious-date statements from AI text
    confirm_all = [c for e in effs.values() for c in e["confirm_with"]]
    for f in functions:
        if f["merge_note"]:
            f["merge_note"] = safety.scrub_muhurat(f["merge_note"], confirm_all, state)
    for c in clashes:
        for k in ("description", "suggestion", "rationale"):
            c[k] = safety.scrub_muhurat(c[k], confirm_all, state)

    state.update({
        "status": "ready",
        "created": datetime.now().isoformat(timespec="seconds"),
        "couple": {"partner_a": intake.partner_a, "partner_b": intake.partner_b,
                   "side_labels": {"a": intake.side_a_label, "b": intake.side_b_label},
                   "cities": {"a": intake.city_a, "b": intake.city_b, "wedding": intake.wedding_city}},
        "wedding_date": intake.wedding_date.isoformat(),
        "budget_total": intake.budget_total, "currency": intake.currency,
        "guest_count_estimate": intake.guest_count,
        "traditions": [{"side": s, "religion": e["religion"], "variant": e["variant"], "label": e["label"]} for s, e in effs.items()],
        "dual": dual,
        "traditions_info": [{"side": s, "label": e["label"], "confirm_with": e["confirm_with"], "constraints": e["constraints"]} for s, e in effs.items()],
        "functions": functions,
        "tasks": build_tasks(functions, effs, intake, today),
        "budget": build_budget(intake.budget_total, len(functions)),
        "clashes": clashes,
        "generation": {"planner": planner["info"], "merge": merge_info},
        "guests": [],
    })
    return state
