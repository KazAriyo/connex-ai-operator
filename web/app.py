from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ai_operator import load_dotenv
from ai_operator.agent import Operator
from ai_operator.providers import Provider, ProviderError, make_provider
from ai_operator.store import CRMStore

load_dotenv()

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="ConneX AI Operator (prototype)")
store = CRMStore()
_sessions: dict[str, Operator] = {}
_lock = threading.Lock()
_provider: Provider | None = None


def provider() -> Provider:
    global _provider
    if _provider is None:
        _provider = make_provider()
    return _provider


def session(sid: str | None) -> tuple[str, Operator]:
    sid = sid or uuid.uuid4().hex
    if sid not in _sessions:
        _sessions[sid] = Operator(provider(), store)
    return sid, _sessions[sid]


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str


class ModeIn(BaseModel):
    session_id: str | None = None
    mode: str


class SessionIn(BaseModel):
    session_id: str | None = None


def _state(sid: str, op: Operator) -> dict:
    return {"session_id": sid, "mode": op.mode, "pending": op.pending is not None}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/info")
def info():
    try:
        p = provider()
        return {"ok": True, "provider": p.name, "model": p.model, "agency": store.data.get("agency")}
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        with _lock:
            sid, op = session(body.session_id)
            reply = op.send(body.message)
            return {**_state(sid, op), "events": reply.events}
    except ProviderError as exc:
        return {"session_id": body.session_id, "mode": "run", "pending": False,
                "events": [{"type": "error", "text": str(exc)}]}


@app.post("/api/mode")
def set_mode(body: ModeIn):
    try:
        with _lock:
            sid, op = session(body.session_id)
            op.set_mode(body.mode)
            return {**_state(sid, op), "ok": True}
    except (ValueError, ProviderError) as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/data")
def data():
    return store.snapshot()


@app.post("/api/reset")
def reset(body: SessionIn):
    with _lock:
        store.reset()
        if body.session_id in _sessions:
            _sessions[body.session_id].reset()
    return {"ok": True}
