"""Unit tests for heartbeat multi-message LLM client behavior."""

from pathlib import Path

import pytest


from _bootstrap import PROJECT_ROOT

from app.llm_client import LLMClient, LLMError


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_rejects_empty_messages() -> None:
    client = LLMClient(provider="dummy", api_key="dummy")
    with pytest.raises(LLMError):
        await client.chat_completion_messages([])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dummy_provider_multi_round_progression() -> None:
    client = LLMClient(provider="dummy", api_key="dummy")

    round0, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
        ]
    )
    assert '"method":"GET"' in round0

    round1, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": round0},
            {"role": "user", "content": "Action results from your previous response"},
        ]
    )
    assert '"method":"POST"' in round1

    round2, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": round0},
            {"role": "user", "content": "obs0"},
            {"role": "assistant", "content": round1},
            {"role": "user", "content": "obs1"},
        ]
    )
    assert round2 == "HEARTBEAT_OK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dummy_provider_requires_observation_context_for_followup_write() -> None:
    client = LLMClient(provider="dummy", api_key="dummy")

    round0, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
        ]
    )
    assert '"method":"GET"' in round0

    round1_no_obs, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": round0},
            {"role": "user", "content": "heartbeat"},
        ]
    )
    assert round1_no_obs == "HEARTBEAT_OK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dummy_provider_respects_feed_vote_tool_surface() -> None:
    client = LLMClient(provider="dummy", api_key="dummy")
    feed_vote_tools = [
        {"type": "function", "function": {"name": "get_feed"}},
        {"type": "function", "function": {"name": "upvote_post"}},
        {"type": "function", "function": {"name": "downvote_post"}},
        {"type": "function", "function": {"name": "heartbeat_ok"}},
    ]

    round0, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
        ],
        tools=feed_vote_tools,
        tool_choice="auto",
    )
    assert round0 == 'get_feed({"limit":10,"sort":"new"})'

    round1, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": round0},
            {
                "role": "user",
                "content": (
                    "Action results from your previous response:\n\n"
                    '1. http:get_feed (success)\n   Request: GET /api/v1/feed?sort=new&limit=10\n'
                    '   Status code: 200\n   Preview: [{"id":"post-1","title":"Visible item"}]'
                ),
            },
        ],
        tools=feed_vote_tools,
        tool_choice="auto",
    )
    assert round1 == 'upvote_post({"post_id":"post-1"})'

    round2, _ = await client.chat_completion_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": round0},
            {"role": "user", "content": "obs0"},
            {"role": "assistant", "content": round1},
            {"role": "user", "content": "obs1"},
        ],
        tools=feed_vote_tools,
        tool_choice="auto",
    )
    assert round2 == "HEARTBEAT_OK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_openrouter_returns_content_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(provider="openrouter", api_key="dummy")

    async def _fake_openrouter(messages, tools=None, tool_choice=None):
        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert tools is None
        assert tool_choice is None
        return '{"ok":true}', 0.123

    monkeypatch.setattr(client, "_openrouter_chat_completion_messages", _fake_openrouter)
    content, cost = await client.chat_completion_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
    )
    assert content == '{"ok":true}'
    assert cost == pytest.approx(0.123, rel=1e-9)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_openrouter_passes_native_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(provider="openrouter", api_key="dummy")
    seen = {}

    async def _fake_openrouter(messages, tools=None, tool_choice=None):
        seen["tools"] = tools
        seen["tool_choice"] = tool_choice
        return 'request({"method":"GET","path":"/contract"})', 0.123

    monkeypatch.setattr(client, "_openrouter_chat_completion_messages", _fake_openrouter)
    content, cost = await client.chat_completion_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "request",
                    "description": "Send HTTP request",
                    "parameters": {"type": "object"},
                },
            }
        ],
        tool_choice="auto",
    )

    assert content == 'request({"method":"GET","path":"/contract"})'
    assert cost == pytest.approx(0.123, rel=1e-9)
    assert seen["tools"][0]["function"]["name"] == "request"
    assert seen["tool_choice"] == "auto"


@pytest.mark.unit
def test_extract_reasoning_from_openrouter_message() -> None:
    choice = {
        "message": {
            "content": "",
            "reasoning": "The feed is empty, so no write is clearly warranted yet.",
        }
    }

    payload = LLMClient._extract_reasoning_from_choice(choice, choice["message"])  # type: ignore[attr-defined]

    assert payload["text"] == "The feed is empty, so no write is clearly warranted yet."
    assert payload["source"] == "message.reasoning"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_openrouter_preserves_reasoning_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(provider="openrouter", api_key="dummy")

    async def _fake_post(*args, **kwargs):
        class _FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "read",
                                            "arguments": '{"path":"HEARTBEAT.md"}',
                                        },
                                    }
                                ],
                                "reasoning": "Need to inspect HEARTBEAT first.",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_cost": 0.001},
                }

        return _FakeResponse()

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return await _fake_post(*args, **kwargs)

    monkeypatch.setattr("app.llm_client.httpx.AsyncClient", lambda timeout=None: _FakeClient())

    content, cost = await client._openrouter_chat_completion_messages(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tools=None,
        tool_choice=None,
    )

    assert content == 'read({"path":"HEARTBEAT.md"})'
    assert cost == pytest.approx(0.001, rel=1e-9)
    assert client.get_last_reasoning()["text"] == "Need to inspect HEARTBEAT first."


@pytest.mark.unit
def test_render_provider_message_content_converts_tool_calls_to_direct_text() -> None:
    message = {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "request",
                    "arguments": '{"method":"GET","path":"/contract"}',
                },
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": '{"path":"HEARTBEAT.md"}',
                },
            },
        ]
    }

    rendered = LLMClient._render_provider_message_content(message)

    assert rendered == (
        'request({"method":"GET","path":"/contract"})\n'
        'read({"path":"HEARTBEAT.md"})'
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_openai_returns_tuple_with_zero_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(provider="openai", api_key="dummy")

    async def _fake_openai(messages, tools=None, tool_choice=None):
        assert isinstance(messages, list)
        assert tools is None
        assert tool_choice is None
        return "openai-content"

    monkeypatch.setattr(client, "_openai_chat_completion_messages", _fake_openai)
    content, cost = await client.chat_completion_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
    )
    assert content == "openai-content"
    assert cost == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_completion_messages_anthropic_returns_tuple_with_zero_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(provider="anthropic", api_key="dummy")

    async def _fake_anthropic(messages, tools=None, tool_choice=None):
        assert isinstance(messages, list)
        assert tools is None
        assert tool_choice is None
        return "anthropic-content"

    monkeypatch.setattr(client, "_anthropic_chat_completion_messages", _fake_anthropic)
    content, cost = await client.chat_completion_messages(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
    )
    assert content == "anthropic-content"
    assert cost == 0.0
