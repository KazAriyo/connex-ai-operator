import json

import httpx
import openai
import pytest

from ai_operator.providers import GROQ, OpenAIProvider, ProviderError, make_provider

TOOLS = [{"name": "get_insights", "description": "Stats.", "schema": {"type": "object", "properties": {}}}]
HISTORY = [{"role": "user", "content": "How many leads came in this week?"}]


def provider_with(handler, retries=0):
    p = OpenAIProvider(GROQ, api_key="test-key")
    p.client = openai.OpenAI(api_key="test-key", base_url=GROQ.base_url, max_retries=retries,
                             http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return p


def completion(message):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}]}


def test_make_provider_picks_groq_from_key(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPERATOR_PROVIDER", "OPERATOR_MODEL"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    p = make_provider()
    assert (p.name, p.model) == ("groq", "openai/gpt-oss-120b")
    assert str(p.client.base_url).startswith("https://api.groq.com/openai/v1")


def test_no_key_message_mentions_free_option(monkeypatch):
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "OPERATOR_PROVIDER"):
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(ProviderError, match="GROQ_API_KEY"):
        make_provider()


def test_request_format_and_tool_call_parsing():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "get_insights", "arguments": "{\"period\": \"this_week\"}"}}]}))

    turn = provider_with(handler).complete("system text", HISTORY, TOOLS)
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "openai/gpt-oss-120b"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "system text"}
    assert seen["body"]["tools"][0]["function"]["name"] == "get_insights"
    assert [(c.name, c.args) for c in turn.tool_calls] == [("get_insights", {"period": "this_week"})]


def test_bad_key_is_reported_clearly():
    p = provider_with(lambda r: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    with pytest.raises(ProviderError, match="Groq rejected the API key. Check GROQ_API_KEY"):
        p.complete("s", HISTORY, TOOLS)


def test_rate_limit_waits_and_retries_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after-ms": "1"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=completion({"role": "assistant", "content": "3 new leads this week."}))

    turn = provider_with(handler, retries=6).complete("s", HISTORY, TOOLS)
    assert calls["n"] == 3 and turn.text == "3 new leads this week."


def test_rate_limit_that_never_clears_fails_loudly():
    p = provider_with(lambda r: httpx.Response(429, headers={"retry-after-ms": "1"},
                                               json={"error": {"message": "slow down"}}), retries=2)
    with pytest.raises(ProviderError, match="rate limit"):
        p.complete("s", HISTORY, TOOLS)


def test_malformed_tool_call_is_retried_once():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": {"message": "Failed to call a function",
                                                       "code": "tool_use_failed"}})
        return httpx.Response(200, json=completion({"role": "assistant", "content": "Done."}))

    assert provider_with(handler).complete("s", HISTORY, TOOLS).text == "Done."
    assert calls["n"] == 2


def test_invalid_json_arguments_become_a_tool_error():
    p = provider_with(lambda r: httpx.Response(200, json=completion({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c", "type": "function", "function": {"name": "get_insights", "arguments": "{oops"}}]})))
    call = p.complete("s", HISTORY, TOOLS).tool_calls[0]
    assert call.raw_error and call.args == {}


def test_malformed_tool_call_twice_fails_loudly():
    p = provider_with(lambda r: httpx.Response(400, json={"error": {"message": "Failed to call a function",
                                                                    "code": "tool_use_failed"}}))
    with pytest.raises(ProviderError, match="malformed tool call twice"):
        p.complete("s", HISTORY, TOOLS)


def test_brief_conversation_over_the_groq_wire_format(monkeypatch):
    monkeypatch.setenv("OPERATOR_TODAY", "2026-11-17")
    from ai_operator.agent import Operator
    from ai_operator.clock import today
    from ai_operator.seed import build_seed
    from ai_operator.store import CRMStore

    def tool_call(cid, name, args):
        return completion({"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})

    def text(t):
        return completion({"role": "assistant", "content": t})

    replies = [
        tool_call("c1", "get_insights", {"period": "this_week"}),
        text("3 new leads this week, 2 are unassigned."),
        tool_call("c2", "create_task", {"title": "Call Aisha Khan", "due": "friday", "related_to": "Aisha Khan"}),
        text("Done, the task has been created."),
    ]
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        for m in body["messages"]:
            assert m["role"] in ("system", "user", "assistant", "tool")
            if m["role"] == "tool":
                assert m["tool_call_id"] and isinstance(m["content"], str)
            if m["role"] == "assistant":
                assert m["content"] is not None or m.get("tool_calls")
        return httpx.Response(200, json=replies.pop(0))

    store = CRMStore(data=build_seed(today()))
    op = Operator(provider_with(handler), store)
    assert "3 new leads" in op.send("How many leads came in this week?").text
    assert op.send("Create a task to call Aisha Khan on Friday").awaiting_confirmation
    assert len(bodies) == 3
    assert "Done" in op.send("Yes").text
    assert store.tasks[-1]["due"] == "2026-11-20"
    last = bodies[-1]["messages"]
    assert [m["role"] for m in last[-2:]] == ["assistant", "tool"]
    assert json.loads(last[-1]["content"])["status"] == "executed"


def test_non_json_reply_is_reported_with_details():
    p = provider_with(lambda r: httpx.Response(200, headers={"content-type": "text/html"},
                                               text="<html><body>Please sign in</body></html>"))
    with pytest.raises(ProviderError) as exc:
        p.complete("s", HISTORY, TOOLS)
    message = str(exc.value)
    assert "not a model reply" in message and "text/html" in message and "Please sign in" in message
    assert "chat/completions" in message


def test_json_reply_with_wrong_content_type_still_works():
    body = json.dumps(completion({"role": "assistant", "content": "hello"}))
    p = provider_with(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=body))
    assert p.complete("s", HISTORY, TOOLS).text == "hello"


def test_error_object_in_a_200_reply_is_reported():
    p = provider_with(lambda r: httpx.Response(200, headers={"content-type": "text/plain"},
                                               text=json.dumps({"error": {"message": "Unknown model"}})))
    with pytest.raises(ProviderError, match="returned an error: Unknown model"):
        p.complete("s", HISTORY, TOOLS)


def test_bare_ok_text_reply_is_reported():
    p = provider_with(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text="OK"))
    with pytest.raises(ProviderError, match="not a model reply .*'OK'"):
        p.complete("s", HISTORY, TOOLS)
