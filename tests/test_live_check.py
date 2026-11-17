import json
import os

os.environ["OPERATOR_TODAY"] = "2026-11-17"

from ai_operator.providers import ModelTurn, ToolCall
from live_check import run_checks
from tests.fakes import ScriptedProvider


def _say(text):
    return ModelTurn(text=text)


def _call(name, args, cid):
    return ModelTurn(tool_calls=[ToolCall(cid, name, args)])


def _from_insights(history):
    r = json.loads([m for m in history if m["role"] == "tool"][-1]["content"])
    return _say(f"{r['new_leads']} new leads this week, {r['new_leads_unassigned']} are unassigned.")


def test_live_check_passes_with_a_well_behaved_model(tmp_path):
    provider = ScriptedProvider([
        _call("get_insights", {"period": "this_week"}, "a"),
        _from_insights,
        _call("create_task", {"title": "Call Aisha Khan", "due": "friday", "related_to": "Aisha Khan"}, "b"),
        _say("Done, the task has been created."),
        _call("update_lead_status", {"lead_id": "L-002", "status": "contacted"}, "c"),
        _say("OK, I have left Tom Fletcher as new."),
        _say("I can't make changes in Ask mode. Switch to Run a task."),
        _call("create_task", {"title": "Call Bob Nobody", "due": "tomorrow", "related_to": "Bob Nobody"}, "d"),
        _say("There is no lead called Bob Nobody in the CRM."),
        _say("The CRM does not hold mortgage lender details for Aisha Khan."),
    ])
    results = run_checks(provider, tmp_path / "crm.json")
    statuses = {c.name: c.status for c in results}
    assert all(s == "PASS" for n, s in statuses.items() if n != "Data not in the CRM"), statuses
    assert len(results) == 7


def test_live_check_catches_a_model_that_skips_tools(tmp_path):
    provider = ScriptedProvider([_say("You had 5 new leads.")] + [_say("ok")] * 10)
    results = run_checks(provider, tmp_path / "crm.json")
    assert results[0].status == "FAIL"
