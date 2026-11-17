import os

os.environ["OPERATOR_TODAY"] = "2026-11-17"

from fastapi.testclient import TestClient

import web.app as webapp
from ai_operator.clock import today
from ai_operator.providers import ModelTurn, ToolCall
from tests.fakes import ScriptedProvider
from ai_operator.seed import build_seed
from ai_operator.store import CRMStore


def setup(steps):
    webapp.store = CRMStore(data=build_seed(today()))
    webapp._sessions.clear()
    webapp._provider = ScriptedProvider(steps)
    return TestClient(webapp.app)


def test_web_confirm_flow():
    c = setup([
        ModelTurn(tool_calls=[ToolCall("c1", "create_task", {"title": "Call Aisha Khan", "due": "friday", "related_to": "L-001"})]),
        ModelTurn(text="Done, the task has been created."),
    ])
    n = len(webapp.store.tasks)
    r = c.post("/api/chat", json={"message": "Create a task to call Aisha Khan on Friday"}).json()
    assert r["pending"] and r["events"][-1]["type"] == "confirm"
    assert len(c.get("/api/data").json()["tasks"]) == n
    r = c.post("/api/chat", json={"session_id": r["session_id"], "message": "yes"}).json()
    assert not r["pending"]
    assert len(c.get("/api/data").json()["tasks"]) == n + 1


def test_web_mode_switch_blocked_while_pending():
    c = setup([ModelTurn(tool_calls=[ToolCall("c1", "create_task", {"title": "A task", "due": "tomorrow"})])])
    sid = c.post("/api/chat", json={"message": "task"}).json()["session_id"]
    r = c.post("/api/mode", json={"session_id": sid, "mode": "ask"}).json()
    assert r["ok"] is False and "pending" in r["error"]


def test_index_served():
    c = setup([])
    assert "How can I help you today?" in c.get("/").text
