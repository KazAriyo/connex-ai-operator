import os
from datetime import date

import pytest

os.environ["OPERATOR_TODAY"] = "2026-11-17"

from ai_operator.clock import DateError, resolve_date, resolve_range, today
from ai_operator.seed import build_seed
from ai_operator.store import CRMStore
from ai_operator.tools import TOOLS, ToolError, clean_schema, resolve_lead


@pytest.fixture
def s():
    return CRMStore(data=build_seed(today()))


def run(s, name, args):
    t = TOOLS[name]
    return t.run(s, t.validate(args))


def prep(s, name, args):
    t = TOOLS[name]
    return t.prepare(s, t.validate(args))


@pytest.mark.parametrize("expr,expected", [
    ("2026-11-20", date(2026, 11, 20)), ("today", date(2026, 11, 17)), ("tomorrow", date(2026, 11, 18)),
    ("friday", date(2026, 11, 20)), ("Fri", date(2026, 11, 20)), ("next friday", date(2026, 11, 20)),
    ("tuesday", date(2026, 11, 24)), ("in 3 days", date(2026, 11, 20)), ("start of week", date(2026, 11, 16)),
    ("7 days ago", date(2026, 11, 10)),
])
def test_resolve_date(expr, expected):
    assert resolve_date(expr) == expected


def test_resolve_date_rejects_nonsense():
    with pytest.raises(DateError):
        resolve_date("whenever")


def test_resolve_range():
    assert resolve_range("this_week") == (date(2026, 11, 16), date(2026, 11, 22))
    assert resolve_range("this month") == (date(2026, 11, 1), date(2026, 11, 30))


def test_insights_match_brief_example(s):
    r = run(s, "get_insights", {"period": "this_week"})
    assert (r["new_leads"], r["new_leads_unassigned"]) == (3, 2)


def test_search_not_contacted_this_week_excludes_closed(s):
    r = run(s, "search_leads", {"not_contacted_since": "start of week"})
    names = {l["name"] for l in r["leads"]}
    assert "Aisha Khan" in names and "Marcus Bell" in names
    assert "Fatima Rahman" not in names
    assert "Ravi Patel" not in names


def test_search_no_match_says_so(s):
    r = run(s, "search_leads", {"query": "Kensington"})
    assert r["total_matching"] == 0 and r["note"]


def test_calendar_tomorrow(s):
    r = run(s, "get_calendar", {"range": "tomorrow"})
    assert r["count"] == 2 and all(e["start"].startswith("2026-11-18") for e in r["events"])


def test_list_tasks_flags_overdue(s):
    r = run(s, "list_tasks", {"status": "open"})
    assert any(t["overdue"] for t in r["tasks"])


def test_read_tools_never_change_data(s):
    import json
    before = json.dumps(s.data, sort_keys=True)
    run(s, "search_leads", {"query": "hackney"})
    run(s, "get_insights", {"period": "all_time"})
    run(s, "get_calendar", {"range": "this_week"})
    run(s, "list_tasks", {"status": "all"})
    assert json.dumps(s.data, sort_keys=True) == before


def test_prepare_does_not_write(s):
    n = len(s.tasks)
    action = prep(s, "create_task", {"title": "Call Aisha Khan", "due": "friday", "related_to": "aisha"})
    assert len(s.tasks) == n
    action.apply()
    assert len(s.tasks) == n + 1


def test_update_status_same_status_rejected(s):
    with pytest.raises(ToolError, match="already"):
        prep(s, "update_lead_status", {"lead_id": "L-001", "status": "new"})


def test_update_status_invalid_status_rejected():
    with pytest.raises(ToolError, match="Invalid arguments"):
        TOOLS["update_lead_status"].validate({"lead_id": "L-001", "status": "sold_to_aliens"})


def test_stale_confirmation_is_refused(s):
    action = prep(s, "update_lead_status", {"lead_id": "L-002", "status": "contacted"})
    s.lead_by_id("L-002")["status"] = "lost"
    with pytest.raises(ToolError, match="while waiting"):
        action.apply()


def test_complete_task(s):
    prep(s, "complete_task", {"task_id": "T-001"}).apply()
    assert next(t for t in s.tasks if t["id"] == "T-001")["status"] == "done"


def test_resolve_lead_ambiguous(s):
    s.leads.append({**s.leads[0], "id": "L-099", "name": "Aisha Begum"})
    with pytest.raises(ToolError, match="more than one"):
        resolve_lead(s, "Aisha")


def test_resolve_lead_by_short_id(s):
    assert resolve_lead(s, "l1")["name"] == "Aisha Khan"


def test_schemas_are_clean():
    for t in TOOLS.values():
        sch = t.json_schema()
        assert sch["type"] == "object"
        assert "anyOf" not in str(sch)
        assert set(sch["properties"]) == set(t.input_model.model_fields)
        assert all("title" not in p for p in sch["properties"].values())
    assert "title" in TOOLS["create_task"].json_schema()["properties"]
    assert clean_schema({"properties": {}})["type"] == "object"


def test_store_persists_to_file(tmp_path):
    path = tmp_path / "crm.json"
    a = CRMStore(path=path)
    prep(a, "create_task", {"title": "Persisted", "due": "tomorrow"}).apply()
    b = CRMStore(path=path)
    assert b.tasks[-1]["title"] == "Persisted"


def test_complete_task_refuses_after_reset(s):
    action = prep(s, "complete_task", {"task_id": "T-001"})
    s.tasks.clear()
    with pytest.raises(ToolError, match="no longer exists"):
        action.apply()


def test_store_refreshes_stale_data(tmp_path, monkeypatch):
    path = tmp_path / "crm.json"
    CRMStore(path=path)
    monkeypatch.setenv("OPERATOR_TODAY", "2026-11-20")
    assert CRMStore(path=path).data["generated_on"] == "2026-11-20"
