"""Tests for controller OpenRouter env fallback behavior."""

from __future__ import annotations

from app.config import Settings


def test_settings_fall_back_to_plain_openrouter_api_key(monkeypatch) -> None:
    monkeypatch.delenv("SIM_CTRL_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LAUNCHER_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-plain")

    settings = Settings()

    assert settings.openrouter_api_key == "sk-plain"


def test_settings_fall_back_to_agent_launcher_openrouter_api_key(monkeypatch) -> None:
    monkeypatch.delenv("SIM_CTRL_OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_LAUNCHER_OPENROUTER_API_KEY", "sk-agent")

    settings = Settings()

    assert settings.openrouter_api_key == "sk-agent"
