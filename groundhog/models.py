#!/usr/bin/env python3
"""A very small provider-agnostic chat client with tool calling.

Deliberately stdlib-only. Groundhog is meant to be runnable five minutes after
someone clones it, and every dependency is another reason a benchmark run dies
on somebody's laptop.

Models are named `provider:model`, e.g. `anthropic:claude-opus-5`,
`openai:gpt-5.2` or `ollama:qwen3:8b`. Every OpenAI-compatible host has a
short name in PROVIDERS below with its base URL and key variable filled in,
so a local model needs no environment at all:

    groundhog run . --models ollama:qwen3:8b

Anything not listed still works through `openai:` with
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
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "grok-4": (3.0, 15.0),
    "llama-3.3-70b": (0.59, 0.79),
    "mistral-large": (2.0, 6.0),
    "codestral": (0.30, 0.90),
}

# A model served from the user's own machine costs nothing per token. Recording
# $0 rather than "unknown" keeps --max-spend honest: it means "this attempt
# was free", not "we could not tell".
FREE_PROVIDERS = {"ollama", "vllm", "lmstudio", "llamacpp"}


def load_pricing() -> dict[str, tuple[float, float]]:
    prices = dict(DEFAULT_PRICING)
    path = os.environ.get("GROUNDHOG_PRICING")
    if path and os.path.exists(path):
        with open(path) as fh:
            prices.update({k: tuple(v) for k, v in json.load(fh).items()})
    return prices


def price_of(model: str, usage: "Usage") -> float | None:
    provider = model.split(":", 1)[0] if ":" in model else ""
    if provider in FREE_PROVIDERS:
        return 0.0
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


TOOL_CALL_KEYS = ("arguments", "parameters", "input", "args")


def parse_text_tool_calls(text: str, known: set[str]) -> list[ToolCall]:
    """Recover tool calls from models that emit them as plain text.

    Several strong local models (qwen2.5-coder among them) ship Ollama templates
    with no native tool support, so a perfectly good tool call arrives as a JSON
    blob in the message body. Without this the model looks incapable when it is
    merely differently packaged -- exactly the kind of harness artifact that
    silently corrupts a benchmark.
    """
    if not text or "{" not in text:
        return []

    blobs: list[str] = []
    fence = text.split("```")
    for i, chunk in enumerate(fence):
        if i % 2 == 1:  # inside a fence
            blobs.append(chunk.split("\n", 1)[-1] if chunk[:20].strip() in ("json", "tool_call") else chunk)
    blobs.append(text)

    calls: list[ToolCall] = []
    seen: set[str] = set()
    for blob in blobs:
        for start in (i for i, c in enumerate(blob) if c == "{"):
            depth = 0
            for end in range(start, len(blob)):
                if blob[end] == "{":
                    depth += 1
                elif blob[end] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = blob[start : end + 1]
                        try:
                            obj = json.loads(candidate)
                        except json.JSONDecodeError:
                            break
                        name = obj.get("name")
                        if not isinstance(name, str) or name not in known:
                            break
                        args = next((obj[k] for k in TOOL_CALL_KEYS if k in obj), {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        if not isinstance(args, dict):
                            args = {}
                        key = name + json.dumps(args, sort_keys=True)
                        if key not in seen:
                            seen.add(key)
                            calls.append(ToolCall(id=f"text_{len(calls)}", name=name, args=args))
                        break
        if calls:
            break
    return calls


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


@dataclass(frozen=True)
class Host:
    """One OpenAI-compatible endpoint: where it lives and how it is unlocked."""

    name: str
    base_url: str
    key_var: str | None          # environment variable holding the API key
    key_required: bool = True
    local: bool = False          # served from this machine; no key, no cost
    signup: str = ""             # where to get a key, for the error message

    def key(self) -> str | None:
        if self.key_var is None:
            return None
        return os.environ.get(self.key_var) or (
            os.environ.get("GROUNDHOG_OPENAI_KEY") if self.name == "openai" else None
        )


HOSTS: dict[str, Host] = {
    "openai": Host("openai", "https://api.openai.com/v1", "OPENAI_API_KEY",
                   signup="https://platform.openai.com/api-keys"),
    "ollama": Host("ollama", "http://localhost:11434/v1", "OLLAMA_API_KEY",
                   key_required=False, local=True),
    "vllm": Host("vllm", "http://localhost:8000/v1", "VLLM_API_KEY",
                 key_required=False, local=True),
    "lmstudio": Host("lmstudio", "http://localhost:1234/v1", None,
                     key_required=False, local=True),
    "llamacpp": Host("llamacpp", "http://localhost:8080/v1", None,
                     key_required=False, local=True),
    "openrouter": Host("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                       signup="https://openrouter.ai/keys"),
    "groq": Host("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
                 signup="https://console.groq.com/keys"),
    "together": Host("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
                     signup="https://api.together.ai/settings/api-keys"),
    "deepseek": Host("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
                     signup="https://platform.deepseek.com/api_keys"),
    "gemini": Host("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                   "GEMINI_API_KEY", signup="https://aistudio.google.com/apikey"),
    "xai": Host("xai", "https://api.x.ai/v1", "XAI_API_KEY",
                signup="https://console.x.ai"),
    "mistral": Host("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
                    signup="https://console.mistral.ai/api-keys"),
    "fireworks": Host("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
                      signup="https://fireworks.ai/account/api-keys"),
    "cerebras": Host("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                     signup="https://cloud.cerebras.ai"),
}


def base_url_for(host: Host) -> str:
    """The endpoint, honouring per-host and legacy overrides.

    GROUNDHOG_<NAME>_BASE_URL overrides any host. GROUNDHOG_OPENAI_BASE_URL is
    kept for the `openai:` provider because the study scripts use it.
    """
    override = os.environ.get(f"GROUNDHOG_{host.name.upper()}_BASE_URL")
    if override:
        return override.rstrip("/")
    if host.name == "ollama" and os.environ.get("OLLAMA_HOST"):
        # Ollama's own convention, e.g. OLLAMA_HOST=0.0.0.0:11434 or http://gpu-box:11434
        raw = os.environ["OLLAMA_HOST"]
        if not raw.startswith("http"):
            raw = "http://" + raw
        return raw.rstrip("/") + "/v1"
    return host.base_url


def check_reachable(host: Host, url: str) -> None:
    """A local server that is not running should say so before any worktree is built.

    Without this the first symptom is a connection error four retries and a
    minute later, per task, once per model.
    """
    if not host.local:
        return
    probe = url.rsplit("/v1", 1)[0] + ("/api/tags" if host.name == "ollama" else "/v1/models")
    try:
        with urllib.request.urlopen(probe, timeout=3):
            return
    except Exception as exc:
        hint = "start it with `ollama serve`" if host.name == "ollama" else "start the server"
        raise ProviderError(
            f"{host.name} is not answering at {url} ({type(exc).__name__}); {hint}, "
            f"or point GROUNDHOG_{host.name.upper()}_BASE_URL at it"
        ) from None


class OpenAI(Provider):
    """Any OpenAI-compatible chat endpoint. The Host says which one."""

    host: Host = HOSTS["openai"]

    def __init__(self, model: str, system: str):
        super().__init__(model, system)
        host = self.host
        key = host.key()
        if not key and host.key_required:
            where = f" (get one at {host.signup})" if host.signup else ""
            raise ProviderError(f"{host.key_var} is not set{where}")
        base = base_url_for(host)
        check_reachable(host, base)
        self.url = f"{base}/chat/completions"
        self.headers = {"content-type": "application/json",
                        "authorization": f"Bearer {key or 'none'}"}
        if host.name == "openrouter":
            self.headers["HTTP-Referer"] = "https://github.com/abhinavsv3/groundhog"
            self.headers["X-Title"] = "Groundhog"
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

        text = choice.get("content") or ""
        if not calls:
            calls = parse_text_tool_calls(text, {t["name"] for t in tools})
            if calls:
                # Keep the transcript coherent for the next request.
                self.messages[-1] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.args)},
                        }
                        for c in calls
                    ],
                }

        u = data.get("usage", {})
        step = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        self.usage = self.usage + step
        return Reply(text, calls, step, done=not calls)

    def give_results(self, results: list[tuple[str, str]]) -> None:
        for cid, out in results:
            self.messages.append({"role": "tool", "tool_call_id": cid, "content": out[:20000]})


def _compatible(host: Host) -> type:
    return type(host.name.capitalize(), (OpenAI,), {"host": host})


PROVIDERS: dict[str, type] = {"anthropic": Anthropic}
PROVIDERS.update({name: _compatible(host) for name, host in HOSTS.items()})


def describe_providers() -> list[tuple[str, str, str]]:
    """(spec prefix, endpoint, what unlocks it) for --list-providers and doctor."""
    rows = [("anthropic:", "https://api.anthropic.com", "ANTHROPIC_API_KEY")]
    for name, host in HOSTS.items():
        unlock = "no key needed" if not host.key_required else (host.key_var or "")
        rows.append((f"{name}:", base_url_for(host), unlock))
    return rows


def connect(spec: str, system: str) -> Provider:
    """`anthropic:claude-opus-5` -> a ready conversation."""
    if ":" not in spec:
        raise ProviderError(f"model must look like provider:model, got {spec!r}")
    provider, model = spec.split(":", 1)
    if provider not in PROVIDERS:
        raise ProviderError(f"unknown provider {provider!r}; try one of {sorted(PROVIDERS)}")
    return PROVIDERS[provider](model, system)
