from __future__ import annotations

from typing import Callable

from ai_operator.providers import ModelTurn, Provider, ProviderError


class ScriptedProvider(Provider):
    name = "scripted"
    model = "scripted"

    def __init__(self, steps: list[ModelTurn | Callable[[list[dict]], ModelTurn] | Exception]):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def complete(self, system, history, tools):
        self.calls.append({"system": system, "history": [dict(m) for m in history],
                           "tools": [t["name"] for t in tools]})
        if not self.steps:
            raise ProviderError("Scripted provider ran out of steps.")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(history) if callable(step) else step
