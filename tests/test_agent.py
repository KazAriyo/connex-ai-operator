import json
import os

import pytest

os.environ["OPERATOR_TODAY"] = "2026-11-17"

from ai_operator.agent import Operator
from ai_operator.clock import today
from ai_operator.providers import AnthropicProvider, ModelTurn, OpenAIProvider, ProviderError, ToolCall
from tests.fakes import ScriptedProvider
from ai_operator.seed import build_seed
from ai_operator.store import CRMStore


def store():
    return CRMStore(data=build_seed(today()))


def last_tool_result(history, name):
    for m in reversed(history):
        if m["role"] == "tool" and m["name"] == name:
            return json.loads(m["content"])
    raise AssertionError(f"no result for {name}")


def call(name, args, cid="c1"):
    return ModelTurn(tool_calls=[ToolCall(cid, name, args)])


def say(text):
    return ModelTurn(text=text)


def test_brief_transcript_end_to_end():
    s = store()
    tasks_before = len(s.tasks)

    def answer_insights(history):
        r = last_tool_result(history, "get_insights")
        return say(f"{r['new_leads']} new leads this week, {r['new_leads_unassigned']} are unassigned.")

    p = ScriptedProvider([
        call("get_insights", {"period": "this_week"}),
        answer_insights,
        call("create_task", {"title": "Call Aisha Khan", "due": "friday", "related_to": "Aisha Khan"}, "c2"),
        say("Done, the task has been created."),
    ])
    op = Operator(p, s)

    r1 = op.send("How many leads came in this week?")
    assert "3 new leads this week, 2 are unassigned." in r1.text
    assert len(s.tasks) == tasks_before

    r2 = op.send("Create a task to call Aisha Khan on Friday")
    assert r2.awaiting_confirmation
    confirm = r2.events[-1]
    assert confirm["summary"] == 'Create task "Call Aisha Khan", due Fri 20 Nov 2026, linked to Aisha Khan (L-001).'
    assert len(s.tasks) == tasks_before

    r3 = op.send("Yes")
    assert len(s.tasks) == tasks_before + 1
    new = s.tasks[-1]
    assert (new["title"], new["due"], new["related_to"]) == ("Call Aisha Khan", "2026-11-20", "L-001")
    assert "Done" in r3.text
    assert any(e["type"] == "executed" for e in r3.events)


def test_no_cancels_and_nothing_is_written():
    s = store()
    before = json.dumps(s.data)
    p = ScriptedProvider([
        call("update_lead_status", {"lead_id": "L-002", "status": "contacted"}),
        say("OK, I've left Tom Fletcher as new."),
    ])
    op = Operator(p, s)
    assert op.send("Mark Tom as contacted").awaiting_confirmation
    op.send("no")
    assert json.dumps(s.data) == before
    result = last_tool_result(op.history, "update_lead_status")
    assert result["status"] == "cancelled" and result["executed"] is False


def test_anything_other_than_yes_is_not_a_yes():
    s = store()
    p = ScriptedProvider([
        call("create_task", {"title": "Call Tom", "due": "friday"}),
        say("No problem, I haven't created it. When would you like it due?"),
    ])
    op = Operator(p, s)
    op.send("remind me to call Tom friday")
    n = len(s.tasks)
    op.send("hmm actually maybe thursday")
    assert len(s.tasks) == n
    assert last_tool_result(op.history, "create_task")["user_reply"] == "hmm actually maybe thursday"


def test_model_cannot_skip_confirmation_even_if_it_claims_done():
    s = store()
    n = len(s.tasks)
    p = ScriptedProvider([
        ModelTurn(text="Done! Task created.", tool_calls=[ToolCall("c1", "create_task", {"title": "X task", "due": "tomorrow"})]),
    ])
    op = Operator(p, s)
    r = op.send("make a task")
    assert r.awaiting_confirmation and len(s.tasks) == n


def test_two_writes_are_confirmed_one_at_a_time():
    s = store()
    p = ScriptedProvider([
        ModelTurn(tool_calls=[
            ToolCall("a", "update_lead_status", {"lead_id": "L-002", "status": "contacted"}),
            ToolCall("b", "create_task", {"title": "Send Tom listings", "due": "tomorrow", "related_to": "L-002"}),
        ]),
        say("Updated Tom; skipped the task."),
    ])
    op = Operator(p, s)
    r1 = op.send("mark Tom contacted and add a task to send listings")
    assert "Tom Fletcher" in r1.events[-1]["summary"]
    r2 = op.send("yes")
    assert r2.awaiting_confirmation and "Send Tom listings" in r2.events[-1]["summary"]
    n = len(s.tasks)
    op.send("no")
    assert s.lead_by_id("L-002")["status"] == "contacted"
    assert s.lead_by_id("L-002")["last_contacted"] == "2026-11-17"
    assert len(s.tasks) == n


def test_read_and_write_in_same_turn_keeps_history_valid():
    s = store()
    p = ScriptedProvider([
        ModelTurn(tool_calls=[
            ToolCall("r", "search_leads", {"query": "Aisha"}),
            ToolCall("w", "update_lead_status", {"lead_id": "L-001", "status": "contacted"}),
        ]),
        say("Done."),
    ])
    op = Operator(p, s)
    op.send("find Aisha and mark her contacted")
    op.send("yes")
    roles = [m["role"] for m in op.history]
    assert roles == ["user", "assistant", "tool", "tool", "assistant"]
    a = AnthropicProvider.to_messages(op.history)
    assert [b["type"] for b in a[2]["content"]] == ["tool_result", "tool_result"]
    o = OpenAIProvider.to_messages("sys", op.history)
    assert [m["role"] for m in o] == ["system", "user", "assistant", "tool", "tool", "assistant"]


def test_ask_mode_hides_and_blocks_write_tools():
    s = store()
    before = json.dumps(s.data)
    p = ScriptedProvider([
        call("create_task", {"title": "Sneaky", "due": "tomorrow"}),
        say("I can't do that in Ask mode."),
    ])
    op = Operator(p, s, mode="ask")
    r = op.send("create a task")
    assert "create_task" not in p.calls[0]["tools"]
    assert not r.awaiting_confirmation
    assert "disabled in Ask mode" in last_tool_result(op.history, "create_task")["error"]
    assert json.dumps(s.data) == before


def test_cannot_switch_mode_with_pending_confirmation():
    op = Operator(ScriptedProvider([call("create_task", {"title": "A task", "due": "tomorrow"})]), store())
    op.send("task")
    with pytest.raises(ValueError):
        op.set_mode("ask")


def test_unknown_lead_is_reported_not_invented():
    s = store()
    p = ScriptedProvider([
        call("create_task", {"title": "Call Bob", "due": "friday", "related_to": "Bob Nobody"}),
        lambda h: say(last_tool_result(h, "create_task")["error"]),
    ])
    op = Operator(p, s)
    r = op.send("call Bob Nobody friday")
    assert not r.awaiting_confirmation
    assert "No lead called 'Bob Nobody'" in r.text


def test_bad_arguments_go_back_to_model_not_crash():
    p = ScriptedProvider([
        call("get_calendar", {"range": "fortnight"}),
        say("Sorry, I couldn't read that range."),
    ])
    op = Operator(p, store())
    r = op.send("what's on this fortnight")
    assert any(e["type"] == "tool_result" and not e["ok"] for e in r.events)
    assert "couldn't" in r.text


def test_past_due_date_rejected():
    p = ScriptedProvider([call("create_task", {"title": "Old", "due": "2026-01-01"}), say("That date is in the past.")])
    op = Operator(p, store())
    r = op.send("task for january")
    assert not r.awaiting_confirmation
    assert "in the past" in last_tool_result(op.history, "create_task")["error"]


def test_unknown_tool():
    p = ScriptedProvider([call("delete_everything", {}), say("I can't do that.")])
    op = Operator(p, store())
    op.send("delete everything")
    assert "no tool called" in last_tool_result(op.history, "delete_everything")["error"]


def test_provider_error_is_loud_and_retryable():
    p = ScriptedProvider([ProviderError("Anthropic rejected the API key."), say("Hello again.")])
    op = Operator(p, store())
    r = op.send("hi")
    assert r.events[-1]["type"] == "error" and "API key" in r.text
    assert op.history == []
    assert op.send("hi").text == "Hello again."


def test_loop_guard():
    p = ScriptedProvider([call("get_insights", {}, f"c{i}") for i in range(10)])
    op = Operator(p, store(), max_steps=3)
    r = op.send("loop forever")
    assert r.events[-1]["type"] == "error" and "Stopped after 3" in r.text


def test_empty_model_reply_is_flagged():
    op = Operator(ScriptedProvider([say("")]), store())
    assert "empty reply" in op.send("hi").text


def test_openai_empty_assistant_message_is_valid():
    msgs = OpenAIProvider.to_messages("sys", [{"role": "user", "content": "hi"},
                                              {"role": "assistant", "content": "", "tool_calls": []}])
    assert msgs[-1]["content"] == "(no reply)"
