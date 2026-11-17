from __future__ import annotations

import argparse
import json
import sys

from ai_operator import load_dotenv
from ai_operator.agent import Operator, Reply, describe_args
from ai_operator.providers import ProviderError, make_provider
from ai_operator.store import CRMStore

USE_COLOUR = sys.stdout.isatty()

HELP = """Commands:
  /ask    switch to Ask mode (read-only)
  /run    switch to Run a task mode (changes need your confirmation)
  /data   show the current CRM data
  /reset  rebuild the mock data and clear the conversation
  /quit   exit"""


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOUR else text


def show(reply: Reply, verbose: bool) -> None:
    for e in reply.events:
        t = e["type"]
        if t == "tool_call":
            tag = "write" if e["mode"] == "write" else "read"
            print(c(f"  [calls {e['name']}({describe_args(e['args'])}) · {tag}]", "2"))
        elif t == "tool_result":
            if not e["ok"]:
                print(c(f"  [{e['name']} error: {e['data'].get('error')}]", "33"))
            elif verbose:
                print(c("  " + json.dumps(e["data"], ensure_ascii=False)[:600], "2"))
        elif t == "assistant":
            print(c("AI Operator: ", "1;36") + e["text"])
        elif t == "confirm":
            print(c("AI Operator: ", "1;36") + c(e["text"], "1;33"))
        elif t == "executed":
            print(c(f"  ✓ executed: {e['summary']}", "32"))
        elif t == "cancelled":
            print(c(f"  ✗ not executed: {e['summary']}", "31"))
        elif t == "error":
            print(c("Error: ", "1;31") + e["text"])


def show_data(store: CRMStore) -> None:
    d = store.snapshot()
    print(c(f"Leads ({len(d['leads'])})", "1"))
    for l in d["leads"]:
        print(f"  {l['id']}  {l['name']:<15} {l['status']:<15} {l['assigned_to'] or 'unassigned':<14} "
              f"created {l['created_on']}  last contact {l['last_contacted'] or '-'}")
    print(c(f"Tasks ({len(d['tasks'])})", "1"))
    for t in d["tasks"]:
        print(f"  {t['id']}  {t['status']:<5} due {t['due']}  {t['title']}  {t['related_to'] or ''}")
    print(c(f"Events ({len(d['events'])})", "1"))
    for e in d["events"]:
        print(f"  {e['id']}  {e['start'].replace('T', ' ')}  {e['title']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="ConneX AI Operator (prototype)")
    ap.add_argument("--mode", choices=["ask", "run"], default="run")
    ap.add_argument("--reset", action="store_true", help="rebuild the mock data with today's dates")
    ap.add_argument("--verbose", "-v", action="store_true", help="print raw tool results")
    args = ap.parse_args()

    load_dotenv()
    store = CRMStore()
    if args.reset:
        store.reset()
    try:
        provider = make_provider()
    except ProviderError as exc:
        print(c("Error: ", "1;31") + str(exc))
        return 1

    op = Operator(provider, store, mode=args.mode)
    label = {"ask": "Ask (read-only)", "run": "Run a task (writes need your yes)"}
    print(c("ConneX AI Operator", "1") + f"  ·  {provider.name}/{provider.model}  ·  data: {store.path}")
    print(f"Mode: {label[op.mode]}.  Commands: /ask /run /data /reset /help /quit")
    print(c("AI Operator: ", "1;36") + "How can I help you today?")

    while True:
        try:
            text = input(c("You: ", "1;35")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        cmd = text.lower()
        if cmd in ("/quit", "/exit", "quit", "exit"):
            return 0
        if cmd in ("/ask", "/run"):
            try:
                op.set_mode(cmd[1:])
                print(f"Mode: {label[op.mode]}.")
            except ValueError as exc:
                print(c("Error: ", "1;31") + str(exc))
            continue
        if cmd == "/data":
            show_data(store)
            continue
        if cmd == "/reset":
            store.reset()
            op.reset()
            print("Mock data rebuilt and conversation cleared.")
            continue
        if cmd == "/help":
            print(HELP)
            continue
        show(op.send(text), args.verbose)


if __name__ == "__main__":
    sys.exit(main())
