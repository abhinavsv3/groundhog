"""Provider table: short names for every OpenAI-compatible host."""

import os
from unittest import mock

import pytest

from groundhog import models


def test_every_host_is_a_provider():
    for name in models.HOSTS:
        assert name in models.PROVIDERS
    assert "anthropic" in models.PROVIDERS


def test_local_models_are_free():
    usage = models.Usage(input_tokens=500_000, output_tokens=500_000)
    assert models.price_of("ollama:qwen3:8b", usage) == 0.0
    assert models.price_of("vllm:whatever", usage) == 0.0
    # A hosted model with a known price is still priced.
    assert models.price_of("deepseek:deepseek-chat", usage) > 0
    # Unknown hosted model: unknown, not zero.
    assert models.price_of("groq:mystery-model", usage) is None


def test_missing_key_names_the_variable_and_where_to_get_one(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(models.ProviderError) as info:
        models.connect("groq:llama-3.3-70b-versatile", "sys")
    assert "GROQ_API_KEY" in str(info.value)
    assert "console.groq.com" in str(info.value)


def test_base_url_override_per_host(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_GROQ_BASE_URL", "http://proxy.local/v1/")
    assert models.base_url_for(models.HOSTS["groq"]) == "http://proxy.local/v1"
    monkeypatch.delenv("GROUNDHOG_GROQ_BASE_URL")
    assert models.base_url_for(models.HOSTS["groq"]) == "https://api.groq.com/openai/v1"


def test_ollama_honours_its_own_host_variable(monkeypatch):
    monkeypatch.delenv("GROUNDHOG_OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "gpu-box:11434")
    assert models.base_url_for(models.HOSTS["ollama"]) == "http://gpu-box:11434/v1"


def test_local_server_down_is_explained_up_front(monkeypatch):
    # Port 9 is the discard port; nothing answers HTTP there.
    monkeypatch.setenv("GROUNDHOG_OLLAMA_BASE_URL", "http://127.0.0.1:9/v1")
    with pytest.raises(models.ProviderError) as info:
        models.connect("ollama:qwen3:8b", "sys")
    assert "ollama serve" in str(info.value)


def test_hosted_provider_needs_no_reachability_probe(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    with mock.patch.object(models.urllib.request, "urlopen") as opened:
        chat = models.connect("deepseek:deepseek-chat", "sys")
    opened.assert_not_called()
    assert chat.url == "https://api.deepseek.com/v1/chat/completions"
    assert chat.headers["authorization"] == "Bearer test"


def test_openai_legacy_overrides_still_work(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GROUNDHOG_OPENAI_KEY", "legacy")
    monkeypatch.setenv("GROUNDHOG_OPENAI_BASE_URL", "http://localhost:11434/v1")
    with mock.patch.object(models.urllib.request, "urlopen"):
        chat = models.connect("openai:qwen3:8b", "sys")
    assert chat.url == "http://localhost:11434/v1/chat/completions"


def test_describe_providers_lists_everything():
    rows = models.describe_providers()
    prefixes = {r[0] for r in rows}
    assert {"anthropic:", "openai:", "ollama:", "openrouter:"} <= prefixes
    ollama = next(r for r in rows if r[0] == "ollama:")
    assert ollama[2] == "no key needed"
