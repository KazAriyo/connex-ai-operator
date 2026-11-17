from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    args: Any
    raw_error: str | None = None


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ProviderError(RuntimeError):
    pass


class Provider:
    name = "base"
    model = ""

    def complete(self, system: str, history: list[dict], tools: list[dict]) -> ModelTurn:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic

        self.model = model or os.environ.get("OPERATOR_MODEL") or "claude-sonnet-5-5"
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    @staticmethod
    def to_messages(history: list[dict]) -> list[dict]:
        msgs: list[dict] = []
        for m in history:
            if m["role"] == "user":
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append({"type": "text", "text": m["content"]})
                else:
                    msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for c in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                                   "input": c["args"] if isinstance(c["args"], dict) else {}})
                msgs.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                         "content": m["content"], "is_error": bool(m.get("is_error"))}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)
                else:
                    msgs.append({"role": "user", "content": [block]})
        return msgs

    def complete(self, system, history, tools):
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system,
                tools=[{"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
                       for t in tools],
                messages=self.to_messages(history),
            )
        except self._anthropic.AuthenticationError:
            raise ProviderError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in your .env file.") from None
        except self._anthropic.NotFoundError:
            raise ProviderError(f"Anthropic does not recognise the model '{self.model}'. Set OPERATOR_MODEL.") from None
        except self._anthropic.RateLimitError:
            raise ProviderError("Anthropic rate limit reached. Wait a moment and try again.") from None
        except self._anthropic.APIConnectionError:
            raise ProviderError("Could not reach the Anthropic API. Check your internet connection.") from None
        except self._anthropic.APIError as exc:
            raise ProviderError(f"Anthropic API error: {exc}") from None

        turn = ModelTurn()
        for block in resp.content:
            if block.type == "text":
                turn.text += block.text
            elif block.type == "tool_use":
                turn.tool_calls.append(ToolCall(block.id, block.name, block.input))
        return turn


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        import openai

        self.model = model or os.environ.get("OPERATOR_MODEL") or "gpt-4.1-mini"
        self._openai = openai
        self.client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    @staticmethod
    def to_messages(system: str, history: list[dict]) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                out: dict = {"role": "assistant",
                             "content": m.get("content") or (None if m.get("tool_calls") else "(no reply)")}
                if m.get("tool_calls"):
                    out["tool_calls"] = [{
                        "id": c["id"], "type": "function",
                        "function": {"name": c["name"],
                                     "arguments": json.dumps(c["args"] if isinstance(c["args"], dict) else {})},
                    } for c in m["tool_calls"]]
                msgs.append(out)
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return msgs

    def complete(self, system, history, tools):
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=self.to_messages(system, history),
                tools=[{"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["schema"]}}
                    for t in tools],
            )
        except self._openai.AuthenticationError:
            raise ProviderError("OpenAI rejected the API key. Check OPENAI_API_KEY in your .env file.") from None
        except self._openai.NotFoundError:
            raise ProviderError(f"OpenAI does not recognise the model '{self.model}'. Set OPERATOR_MODEL.") from None
        except self._openai.RateLimitError:
            raise ProviderError("OpenAI rate limit or quota reached. Check your billing or wait.") from None
        except self._openai.APIConnectionError:
            raise ProviderError("Could not reach the OpenAI API. Check your internet connection.") from None
        except self._openai.APIError as exc:
            raise ProviderError(f"OpenAI API error: {exc}") from None

        msg = resp.choices[0].message
        turn = ModelTurn(text=msg.content or "")
        for c in msg.tool_calls or []:
            try:
                args = json.loads(c.function.arguments or "{}")
                err = None
            except json.JSONDecodeError:
                args, err = {}, "The model sent arguments that were not valid JSON."
            turn.tool_calls.append(ToolCall(c.id, c.function.name, args, raw_error=err))
        return turn


def make_provider() -> Provider:
    choice = (os.environ.get("OPERATOR_PROVIDER") or "").strip().lower()
    if not choice:
        if os.environ.get("ANTHROPIC_API_KEY"):
            choice = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            choice = "openai"
        else:
            raise ProviderError(
                "No API key found. Copy .env.example to .env and set ANTHROPIC_API_KEY "
                "(or OPENAI_API_KEY with OPERATOR_PROVIDER=openai).")
    if choice == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("OPERATOR_PROVIDER is anthropic but ANTHROPIC_API_KEY is not set.")
        return AnthropicProvider()
    if choice == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError("OPERATOR_PROVIDER is openai but OPENAI_API_KEY is not set.")
        return OpenAIProvider()
    raise ProviderError(f"Unknown OPERATOR_PROVIDER '{choice}'. Use 'anthropic' or 'openai'.")
