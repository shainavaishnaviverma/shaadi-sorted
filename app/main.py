"""FastAPI app: JSON API + static single-page UI."""
import json
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import agent, config, llm, planner, store, tools, traditions

app = FastAPI(title="Shaadi Sorted")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


class IntakeRequest(planner.Intake):
    use_llm: bool = True


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    approve: bool


class RsvpRequest(BaseModel):
    guest_id: str
    function_id: str
    status: str


class TaskRequest(BaseModel):
    task_id: str
    done: bool


def view(state: dict) -> dict:
    """State plus derived fields for the UI."""
    out = dict(state)
    if state.get("status") == "ready":
        out["next_step"] = tools.next_step(state)
    u = state["usage"]
    out["meter"] = {"total": llm.total_tokens(state), "cost_usd": round(llm.estimate_cost(u), 4), "cap": config.TOKEN_CAP}
    return out


def _require_ready(state: dict) -> None:
    if state.get("status") != "ready":
        raise HTTPException(409, "No plan yet. Create one first.")


@app.exception_handler(ValidationError)
async def _pyd(_, exc: ValidationError):
    msgs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse({"detail": "; ".join(msgs)}, status_code=422)


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta():
    return {**traditions.options(), "currencies": planner.CURRENCIES, "model": config.MODEL, "has_key": config.has_key(),
            "token_cap": config.TOKEN_CAP, "price_per_m": config.PRICE_PER_M, "env_files": config.ENV_FILES_LOADED}


@app.get("/api/state")
def get_state():
    return view(store.load())


@app.post("/api/intake")
def create_plan(req: IntakeRequest):
    with store.LOCK:
        old = store.load()
        use_llm = req.use_llm and config.has_key()
        state = planner.build_state(planner.Intake(**req.model_dump(exclude={"use_llm"})), old, use_llm=use_llm)
        store.save(state)
        return view(state)


@app.post("/api/demo")
def load_demo():
    """Instant demo plan from data/sample_*.json. Uses no tokens."""
    with store.LOCK:
        raw = json.loads((config.DATA_DIR / "sample_intake.json").read_text())
        days = raw.pop("wedding_date_days_from_today", 240)
        raw["wedding_date"] = (date.today() + timedelta(days=days)).isoformat()
        old = store.load()
        state = planner.build_state(planner.Intake(**raw), old, use_llm=False)
        tools.load_demo_guests(state, json.loads((config.DATA_DIR / "sample_guests.json").read_text()))
        store.save(state)
        return view(state)


@app.get("/api/sample-intake")
def sample_intake():
    raw = json.loads((config.DATA_DIR / "sample_intake.json").read_text())
    days = raw.pop("wedding_date_days_from_today", 240)
    raw["wedding_date"] = (date.today() + timedelta(days=days)).isoformat()
    return raw


@app.post("/api/reset")
def reset():
    with store.LOCK:
        old = store.load()
        state = store.empty_state(old["usage"])
        store.save(state)
        return view(state)


@app.post("/api/usage/reset")
def reset_usage():
    with store.LOCK:
        state = store.load()
        state["usage"] = store.empty_usage()
        store.save(state)
        return view(state)


@app.post("/api/chat")
def chat(req: ChatRequest):
    with store.LOCK:
        state = store.load()
        _require_ready(state)
        try:
            result = agent.run_chat(state, req.message)
        finally:
            store.save(state)
        return {**result, "state": view(state)}


@app.post("/api/confirm")
def confirm(req: ConfirmRequest):
    with store.LOCK:
        state = store.load()
        _require_ready(state)
        result = tools.confirm_pending(state, req.approve)
        store.save(state)
        return {**result, "state": view(state)}


@app.post("/api/rsvp")
def set_rsvp(req: RsvpRequest):
    with store.LOCK:
        state = store.load()
        _require_ready(state)
        res = tools.update_rsvp(state, req.guest_id, req.function_id, req.status)
        if "error" in res:
            raise HTTPException(400, res["error"])
        store.save(state)
        return view(state)


@app.post("/api/task")
def set_task(req: TaskRequest):
    with store.LOCK:
        state = store.load()
        _require_ready(state)
        res = tools.complete_task(state, req.task_id, req.done)
        if "error" in res:
            raise HTTPException(400, res["error"])
        store.save(state)
        return view(state)
