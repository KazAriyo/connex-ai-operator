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
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                                          max_retries=6, timeout=60)

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


@dataclass(frozen=True)
class CompatibleService:
    name: str
    label: str
    key_env: str
    default_model: str
    base_url: str | None


OPENAI = CompatibleService("openai", "OpenAI", "OPENAI_API_KEY", "gpt-4.1-mini", None)
GROQ = CompatibleService("groq", "Groq", "GROQ_API_KEY", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1")


class OpenAIProvider(Provider):
    def __init__(self, service: CompatibleService = OPENAI, model: str | None = None, api_key: str | None = None):
        import openai

        self.service = service
        self.name = service.name
        self.model = model or os.environ.get("OPERATOR_MODEL") or service.default_model
        self._openai = openai
        self.client = openai.OpenAI(
            api_key=api_key or os.environ.get(service.key_env),
            base_url=service.base_url,
            max_retries=6,
            timeout=60,
        )

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

    def _create(self, system: str, history: list[dict], tools: list[dict]):
        return self.client.chat.completions.create(
            model=self.model,
            messages=self.to_messages(system, history),
            tools=[{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["schema"]}}
                for t in tools],
        )

    def complete(self, system, history, tools):
        label, key_env, errors = self.service.label, self.service.key_env, self._openai
        resp = None
        for attempt in range(2):
            try:
                resp = self._create(system, history, tools)
                break
            except errors.AuthenticationError:
                raise ProviderError(f"{label} rejected the API key. Check {key_env} in your .env file.") from None
            except errors.NotFoundError:
                raise ProviderError(f"{label} does not recognise the model '{self.model}'. Set OPERATOR_MODEL.") from None
            except errors.RateLimitError:
                raise ProviderError(f"{label} rate limit reached, even after waiting and retrying. "
                                    "Free plans allow only a few requests per minute: wait a minute and try again.") from None
            except errors.APIConnectionError:
                raise ProviderError(f"Could not reach the {label} API. Check your internet connection.") from None
            except errors.BadRequestError as exc:
                if getattr(exc, "code", None) != "tool_use_failed":
                    raise ProviderError(f"{label} API error: {exc}") from None
                if attempt == 1:
                    raise ProviderError("The model produced a malformed tool call twice. Try rephrasing.") from None
            except errors.APIError as exc:
                raise ProviderError(f"{label} API error: {exc}") from None

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


SERVICES = {"openai": OPENAI, "groq": GROQ}
KEY_ENVS = {"anthropic": "ANTHROPIC_API_KEY", "openai": OPENAI.key_env, "groq": GROQ.key_env}


def make_provider() -> Provider:
    choice = (os.environ.get("OPERATOR_PROVIDER") or "").strip().lower()
    if not choice:
        choice = next((name for name, env in KEY_ENVS.items() if os.environ.get(env)), "")
        if not choice:
            raise ProviderError(
                "No API key found. Copy .env.example to .env and set one of "
                "GROQ_API_KEY (free), ANTHROPIC_API_KEY or OPENAI_API_KEY.")
    if choice not in KEY_ENVS:
        raise ProviderError(f"Unknown OPERATOR_PROVIDER '{choice}'. Use 'anthropic', 'openai' or 'groq'.")
    if not os.environ.get(KEY_ENVS[choice]):
        raise ProviderError(f"OPERATOR_PROVIDER is {choice} but {KEY_ENVS[choice]} is not set.")
    if choice == "anthropic":
        return AnthropicProvider()
    return OpenAIProvider(SERVICES[choice])
