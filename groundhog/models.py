#!/usr/bin/env python3
"""A very small provider-agnostic chat client with tool calling.

Deliberately stdlib-only. Groundhog is meant to be runnable five minutes after
someone clones it, and every dependency is another reason a benchmark run dies
on somebody's laptop.

Models are named `provider:model`, e.g. `anthropic:claude-opus-5` or
`openai:gpt-5.2`. Anything exposing an OpenAI-compatible API (OpenRouter,
Together, Groq, vLLM, Ollama) works through the `openai` provider with
GROUNDHOG_OPENAI_BASE_URL pointed at it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Approximate USD per million tokens. Used for reporting only -- wrong numbers
# here change the cost column, never the pass/fail result. Override with
# GROUNDHOG_PRICING=/path/to/prices.json
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-5.2": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
}


def load_pricing() -> dict[str, tuple[float, float]]:
    prices = dict(DEFAULT_PRICING)
    path = os.environ.get("GROUNDHOG_PRICING")
    if path and os.path.exists(path):
        with open(path) as fh:
            prices.update({k: tuple(v) for k, v in json.load(fh).items()})
    return prices


def price_of(model: str, usage: "Usage") -> float | None:
    prices = load_pricing()
    for name, (inp, out) in prices.items():
        if name in model:
            return usage.input_tokens / 1e6 * inp + usage.output_tokens / 1e6 * out
    return None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    done: bool


class ProviderError(RuntimeError):
    pass


def post(url: str, headers: dict[str, str], payload: dict, timeout: int = 180) -> dict:
    """POST JSON with a few retries on the failures that are worth retrying."""
    body = json.dumps(payload).encode()
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:400]
            if exc.code in (408, 429, 500, 502, 503, 529):
                last = ProviderError(f"HTTP {exc.code}: {detail}")
                time.sleep(2**attempt * 2)
                continue
            raise ProviderError(f"HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            last = ProviderError(str(exc))
            time.sleep(2**attempt * 2)
    raise last or ProviderError("request failed")


class Provider:
    """Holds one conversation. Subclasses own their wire format."""

    def __init__(self, model: str, system: str):
        self.model = model
        self.system = system
        self.messages: list[dict] = []
        self.usage = Usage()

    def say(self, text: str) -> None:
        raise NotImplementedError

    def reply(self, tools: list[dict]) -> Reply:
        raise NotImplementedError

    def give_results(self, results: list[tuple[str, str]]) -> None:
        raise NotImplementedError


class Anthropic(Provider):
    API = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, system: str):
        super().__init__(model, system)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.headers = {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        self.url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    def say(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def reply(self, tools: list[dict]) -> Reply:
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "system": self.system,
            "messages": self.messages,
            "tools": [
                {"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
                for t in tools
            ],
        }
        data = post(self.url, self.headers, payload)
        blocks = data.get("content", [])
        self.messages.append({"role": "assistant", "content": blocks})

        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            ToolCall(id=b["id"], name=b["name"], args=b.get("input", {}))
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        u = data.get("usage", {})
        step = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0))
        self.usage = self.usage + step
        return Reply(text, calls, step, done=not calls)

    def give_results(self, results: list[tuple[str, str]]) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": cid, "content": out[:20000]}
                    for cid, out in results
                ],
            }
        )


class OpenAI(Provider):
    def __init__(self, model: str, system: str):
        super().__init__(model, system)
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GROUNDHOG_OPENAI_KEY")
        if not key:
            raise ProviderError("OPENAI_API_KEY is not set")
        base = os.environ.get("GROUNDHOG_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.url = f"{base}/chat/completions"
        self.headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
        self.messages = [{"role": "system", "content": system}]

    def say(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def reply(self, tools: list[dict]) -> Reply:
        payload = {
            "model": self.model,
            "messages": self.messages,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["schema"],
                    },
                }
                for t in tools
            ],
        }
        data = post(self.url, self.headers, payload)
        choice = data["choices"][0]["message"]
        self.messages.append(choice)

        calls = []
        for tc in choice.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], args=args))

        u = data.get("usage", {})
        step = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        self.usage = self.usage + step
        return Reply(choice.get("content") or "", calls, step, done=not calls)

    def give_results(self, results: list[tuple[str, str]]) -> None:
        for cid, out in results:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": out[:20000]})


PROVIDERS = {"anthropic": Anthropic, "openai": OpenAI}


def connect(spec: str, system: str) -> Provider:
    """`anthropic:claude-opus-5` -> a ready conversation."""
    if ":" not in spec:
        raise ProviderError(f"model must look like provider:model, got {spec!r}")
    provider, model = spec.split(":", 1)
    if provider not in PROVIDERS:
        raise ProviderError(f"unknown provider {provider!r}; try one of {sorted(PROVIDERS)}")
    return PROVIDERS[provider](model, system)
