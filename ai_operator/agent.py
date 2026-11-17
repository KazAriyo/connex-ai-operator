from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .prompts import system_prompt
from .providers import ModelTurn, Provider, ProviderError, ToolCall
from .store import CRMStore
from .tools import TOOLS, PreparedAction, ToolError, ToolSpec, tools_for_mode

YES = {"y", "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "go ahead",
       "do it", "please do", "yes please", "go for it", "proceed", "yes go ahead", "approve", "approved"}
NO = {"n", "no", "nope", "cancel", "stop", "dont", "don't", "no thanks", "abort", "never mind", "nevermind"}


def _norm(text: str) -> str:
    return re.sub(r"[^\w' ]+", "", text.lower()).strip()


@dataclass
class Pending:
    call: ToolCall
    spec: ToolSpec
    action: PreparedAction


@dataclass
class Reply:
    events: list[dict] = field(default_factory=list)

    @property
    def awaiting_confirmation(self) -> bool:
        return bool(self.events) and self.events[-1]["type"] == "confirm"

    @property
    def text(self) -> str:
        return "\n".join(e["text"] for e in self.events if e["type"] in ("assistant", "error"))


class Operator:
    def __init__(self, provider: Provider, store: CRMStore, mode: str = "run", max_steps: int = 8):
        if mode not in ("ask", "run"):
            raise ValueError("mode must be 'ask' or 'run'")
        self.provider = provider
        self.store = store
        self.mode = mode
        self.max_steps = max_steps
        self.history: list[dict] = []
        self._queue: list[Pending] = []
        self._results: dict[str, dict] = {}
        self._call_order: list[str] = []

    @property
    def pending(self) -> Pending | None:
        return self._queue[0] if self._queue else None

    def set_mode(self, mode: str) -> str:
        if mode not in ("ask", "run"):
            raise ValueError("mode must be 'ask' or 'run'")
        if self._queue:
            raise ValueError("Answer the pending confirmation (yes or no) before switching mode.")
        self.mode = mode
        return mode

    def reset(self) -> None:
        self.history.clear()
        self._queue.clear()
        self._results.clear()
        self._call_order.clear()

    def send(self, text: str) -> Reply:
        reply = Reply()
        text = (text or "").strip()
        if not text:
            reply.events.append({"type": "error", "text": "Please type a question or a task."})
            return reply
        if self._queue:
            return self._answer_confirmation(text, reply)
        self.history.append({"role": "user", "content": text})
        return self._loop(reply)

    def _loop(self, reply: Reply) -> Reply:
        tools = [{"name": t.name, "description": t.description, "schema": t.json_schema()}
                 for t in tools_for_mode(self.mode)]
        system = system_prompt(self.mode, self.store.data.get("agency", "the agency"))

        for _ in range(self.max_steps):
            try:
                turn = self.provider.complete(system, self.history, tools)
            except ProviderError as exc:
                return self._fail(reply, str(exc))
            except Exception as exc:
                return self._fail(reply, f"Unexpected error talking to the model: {exc!r}")

            self.history.append({
                "role": "assistant", "content": turn.text,
                "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls],
            })
            if turn.text.strip():
                reply.events.append({"type": "assistant", "text": turn.text.strip()})

            if not turn.tool_calls:
                if not turn.text.strip():
                    reply.events.append({"type": "error", "text": "The model returned an empty reply. Please try rephrasing."})
                return reply

            self._handle_calls(turn, reply)
            if self._queue:
                self._ask_confirmation(reply)
                return reply
            self._flush_results()

        reply.events.append({"type": "error", "text": (
            f"Stopped after {self.max_steps} model steps without a final answer. "
            "Nothing else was changed. Try a simpler request.")})
        return reply

    def _handle_calls(self, turn: ModelTurn, reply: Reply) -> None:
        self._results.clear()
        self._call_order = [c.id for c in turn.tool_calls]
        for call in turn.tool_calls:
            spec = TOOLS.get(call.name)
            mode = spec.mode if spec else "unknown"
            reply.events.append({"type": "tool_call", "name": call.name, "args": call.args, "mode": mode})
            try:
                if call.raw_error:
                    raise ToolError(call.raw_error)
                if spec is None:
                    raise ToolError(f"There is no tool called '{call.name}'.")
                if spec.is_write and self.mode != "run":
                    raise ToolError("Write tools are disabled in Ask mode. Nothing was changed. "
                                    "Tell the user to switch to Run a task mode.")
                args = spec.validate(call.args)
                if spec.is_write:
                    self._queue.append(Pending(call, spec, spec.prepare(self.store, args)))
                else:
                    self._result(call, spec.check_output(spec.run(self.store, args)), reply)
            except ToolError as exc:
                self._result(call, {"error": str(exc)}, reply, is_error=True)
            except Exception as exc:
                self._result(call, {"error": f"Internal error in {call.name}: {exc!r}"}, reply, is_error=True)

    def _ask_confirmation(self, reply: Reply) -> None:
        p = self._queue[0]
        reply.events.append({
            "type": "confirm", "tool": p.spec.name, "args": p.call.args,
            "summary": p.action.summary,
            "text": f"I'll do this: {p.action.summary} Shall I go ahead? (yes / no)",
        })

    def _answer_confirmation(self, text: str, reply: Reply) -> Reply:
        p = self._queue.pop(0)
        said = _norm(text)

        if said in YES:
            try:
                result = p.spec.check_output(p.action.apply())
                self._result(p.call, {"status": "executed", **result}, reply)
                reply.events.append({"type": "executed", "name": p.spec.name, "args": p.call.args,
                                     "summary": p.action.summary})
            except ToolError as exc:
                self._result(p.call, {"status": "failed", "executed": False, "error": str(exc)}, reply, is_error=True)
            except Exception as exc:
                self._result(p.call, {"status": "failed", "executed": False,
                                      "error": f"Internal error: {exc!r}"}, reply, is_error=True)
        else:
            cancelled = [p]
            if said not in NO:
                cancelled += self._queue
                self._queue = []
            for c in cancelled:
                self._result(c.call, {
                    "status": "cancelled", "executed": False,
                    "message": "The user did not confirm, so nothing was changed.",
                    "user_reply": text,
                }, reply)
                reply.events.append({"type": "cancelled", "summary": c.action.summary})

        if self._queue:
            self._ask_confirmation(reply)
            return reply
        self._flush_results()
        return self._loop(reply)

    def _result(self, call: ToolCall, data: dict, reply: Reply, is_error: bool = False) -> None:
        content = json.dumps(data, ensure_ascii=False, default=str)
        self._results[call.id] = {"role": "tool", "tool_call_id": call.id, "name": call.name,
                                  "content": content, "is_error": is_error}
        reply.events.append({"type": "tool_result", "name": call.name, "ok": not is_error, "data": data})

    def _flush_results(self) -> None:
        for cid in self._call_order:
            if cid in self._results:
                self.history.append(self._results.pop(cid))
        self._call_order = []

    def _fail(self, reply: Reply, message: str) -> Reply:
        if self.history and self.history[-1]["role"] == "user":
            self.history.pop()
        reply.events.append({"type": "error", "text": message})
        return reply


def describe_args(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
