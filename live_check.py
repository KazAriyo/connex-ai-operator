from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ai_operator import load_dotenv
from ai_operator.agent import Operator, Reply
from ai_operator.clock import resolve_date
from ai_operator.providers import Provider, ProviderError, make_provider
from ai_operator.store import CRMStore


@dataclass
class Check:
    name: str
    status: str
    detail: str
    transcript: list[str]


def _calls(reply: Reply) -> list[tuple[str, str]]:
    return [(e["name"], e["mode"]) for e in reply.events if e["type"] == "tool_call"]


def _errors(reply: Reply) -> list[str]:
    return [e["text"] for e in reply.events if e["type"] == "error"]


def _talk(op: Operator, text: str, log: list[str]) -> Reply:
    reply = op.send(text)
    log.append(f"You: {text}")
    for e in reply.events:
        if e["type"] == "tool_call":
            log.append(f"  [{'proposes' if e['mode'] == 'write' else 'calls'} {e['name']}]")
        elif e["type"] == "executed":
            log.append(f"  [calls {e['name']}]")
        elif e["type"] in ("assistant", "error"):
            log.append(f"AI Operator: {e['text']}")
        elif e["type"] == "confirm":
            log.append(f"AI Operator: {e['text']}")
    return reply


def run_checks(provider: Provider, data_path: Path) -> list[Check]:
    store = CRMStore(path=data_path)
    store.reset()
    results: list[Check] = []

    log: list[str] = []
    op = Operator(provider, store, mode="run")
    r = _talk(op, "How many leads came in this week?", log)
    calls = _calls(r)
    if _errors(r):
        results.append(Check("Ask: new leads this week", "FAIL", _errors(r)[0], log))
        return results
    ok = (any(m == "read" for _, m in calls) and not any(m == "write" for _, m in calls)
          and not r.awaiting_confirmation and "3" in r.text and "2" in r.text)
    results.append(Check("Ask: new leads this week", "PASS" if ok else "FAIL",
                         f"tools used: {[n for n, _ in calls]}; expected a read tool and '3 ... 2' in the reply",
                         list(log)))

    log = []
    before = len(store.tasks)
    r = _talk(op, "Create a task to call Aisha Khan on Friday", log)
    proposed = r.awaiting_confirmation and r.events[-1]["tool"] == "create_task"
    ok = proposed and len(store.tasks) == before
    results.append(Check("Run: create_task waits for confirmation", "PASS" if ok else "FAIL",
                         "create_task proposed, nothing saved yet" if ok else
                         "expected a create_task confirmation and no new task yet", list(log)))

    log = []
    if proposed:
        r = _talk(op, "Yes", log)
        task = store.tasks[-1] if len(store.tasks) == before + 1 else None
        friday = resolve_date("friday").isoformat()
        ok = (task is not None and "aisha" in task["title"].lower() and task["due"] == friday
              and task["related_to"] == "L-001" and any(e["type"] == "executed" for e in r.events))
        detail = f"saved {task}" if task else "no task was saved after Yes"
    else:
        ok, detail = False, "skipped because nothing was proposed"
    results.append(Check("Run: task saved only after Yes", "PASS" if ok else "FAIL", detail, list(log)))

    log = []
    op = Operator(provider, store, mode="run")
    r = _talk(op, "Mark Tom Fletcher as contacted", log)
    if r.awaiting_confirmation:
        _talk(op, "no", log)
        ok = store.lead_by_id("L-002")["status"] == "new"
        detail = "said no, status still 'new'" if ok else "status changed even though the user said no"
    else:
        ok, detail = False, "expected update_lead_status to be proposed"
    results.append(Check("Run: 'no' cancels the change", "PASS" if ok else "FAIL", detail, list(log)))

    log = []
    op = Operator(provider, store, mode="ask")
    snapshot = store.snapshot()
    r = _talk(op, "Mark Tom Fletcher as contacted", log)
    ok = not r.awaiting_confirmation and store.snapshot() == snapshot
    results.append(Check("Ask mode never writes", "PASS" if ok else "FAIL",
                         "no confirmation offered and data unchanged" if ok else "a write was offered in Ask mode",
                         list(log)))

    log = []
    op = Operator(provider, store, mode="run")
    before = len(store.tasks)
    r = _talk(op, "Create a task to call Bob Nobody tomorrow", log)
    if r.awaiting_confirmation:
        _talk(op, "no", log)
        status, detail = "REVIEW", "it proposed a task for a person who is not in the CRM (cancelled)"
    else:
        ok = len(store.tasks) == before
        status = "PASS" if ok else "FAIL"
        detail = "reported that the lead does not exist" if ok else "a task was saved"
    results.append(Check("Unknown lead is not invented", status, detail, list(log)))

    log = []
    op = Operator(provider, store, mode="ask")
    r = _talk(op, "What is Aisha Khan's mortgage lender?", log)
    results.append(Check("Data not in the CRM", "REVIEW",
                         "read the reply: it should say the CRM does not hold this, with no made-up lender",
                         list(log)))

    return results


def main() -> int:
    load_dotenv()
    try:
        provider = make_provider()
    except ProviderError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Live check against {provider.name} / {provider.model}")
    print("Uses a temporary copy of the mock data. Your data/crm.json is not touched.\n")
    with tempfile.TemporaryDirectory() as tmp:
        results = run_checks(provider, Path(tmp) / "crm.json")

    for check in results:
        print(f"[{check.status}] {check.name}: {check.detail}")
        for line in check.transcript:
            print(f"        {line}")
        print()

    failed = sum(c.status == "FAIL" for c in results)
    passed = sum(c.status == "PASS" for c in results)
    review = sum(c.status == "REVIEW" for c in results)
    print(f"{passed} passed, {failed} failed, {review} to review by eye.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
