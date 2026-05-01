"""Tests for apodornot.chat — scorecard-grounded streaming chat.

Mocks the Anthropic AsyncAnthropic client to avoid hitting the real API. The
streaming protocol is exercised end-to-end (text deltas, tool_use events, the
tool-execution loop) against simulated event sequences.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("anthropic")

from apodornot.chat import _execute_tool, _system_prompt, stream_chat


# ---------------------------------------------------------------------------- #
# Shape of the system prompt
# ---------------------------------------------------------------------------- #


def test_system_prompt_includes_scorecard_json():
    sc = {"overall_score": 72.0, "reference_category": "rosette"}
    prompt = _system_prompt(sc, [])
    assert "72.0" in prompt
    assert "rosette" in prompt
    assert "reference set" in prompt.lower()


def test_system_prompt_lists_reference_titles():
    refs = [
        {"date": "2024-02-14", "title": "Rosette Deep Field"},
        {"date": "2025-07-16", "title": "The Rosette Nebula from DECam"},
    ]
    prompt = _system_prompt({}, refs)
    assert "Rosette Deep Field" in prompt
    assert "DECam" in prompt


def test_system_prompt_truncates_long_reference_lists():
    refs = [{"date": f"2020-01-{i:02d}", "title": f"r{i}"} for i in range(1, 21)]
    prompt = _system_prompt({}, refs)
    assert "and 10 more" in prompt


# ---------------------------------------------------------------------------- #
# Local tool dispatch
# ---------------------------------------------------------------------------- #


def test_execute_tool_known_metric():
    out = _execute_tool("get_diagnostic_context", {"metric_name": "median_fwhm_px"})
    assert out["metric"] == "median_fwhm_px"
    assert "what_it_measures" in out


def test_execute_tool_unknown_metric_returns_error():
    out = _execute_tool("get_diagnostic_context", {"metric_name": "made_up_metric"})
    assert "error" in out
    assert "available_metrics" in out


def test_execute_tool_unknown_tool_name():
    out = _execute_tool("not_a_tool", {})
    assert "error" in out


# ---------------------------------------------------------------------------- #
# stream_chat: mock Anthropic stream
# ---------------------------------------------------------------------------- #


def _text_event(text):
    e = MagicMock()
    e.type = "text"
    e.text = text
    return e


def _content_block_stop_text():
    e = MagicMock()
    e.type = "content_block_stop"
    e.content_block = MagicMock(type="text")
    return e


def _content_block_stop_tool(name, tool_input):
    e = MagicMock()
    e.type = "content_block_stop"
    block = MagicMock(type="tool_use", input=tool_input)
    block.name = name  # MagicMock(name=...) is reserved; set after construction
    e.content_block = block
    return e


def _make_stream(events, final_content):
    """Mimic the AsyncAnthropic stream context manager."""

    class _AsyncIter:
        def __init__(self, items):
            self._items = list(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop(0)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    cm.__aiter__ = lambda self: _AsyncIter(events)
    final_msg = MagicMock(content=final_content)
    cm.get_final_message = AsyncMock(return_value=final_msg)
    return cm


def _make_client(turn_streams):
    """Build a mock AsyncAnthropic client whose .messages.stream() returns each
    of ``turn_streams`` in order on successive calls."""
    client = MagicMock()
    client.messages = MagicMock()
    iterator = iter(turn_streams)
    client.messages.stream = MagicMock(side_effect=lambda **_kw: next(iterator))
    return client


@pytest.mark.asyncio
async def test_stream_chat_simple_text_response():
    """Single turn that emits text only and ends — should yield tokens then done."""
    text_block = MagicMock(type="text", text="Hello!")
    stream = _make_stream(
        events=[_text_event("Hello"), _text_event("!"), _content_block_stop_text()],
        final_content=[text_block],
    )
    client = _make_client([stream])

    events = []
    async for ev in stream_chat(
        scorecard={"overall_score": 50, "reference_category": "global"},
        messages=[{"role": "user", "content": "Hi"}],
        archive_dir="/no/such/dir",
        client=client,
    ):
        events.append(ev)

    assert events == [
        ("token", {"text": "Hello"}),
        ("token", {"text": "!"}),
        ("done", {}),
    ]


@pytest.mark.asyncio
async def test_stream_chat_tool_use_loop():
    """Turn 1 emits a tool_use; turn 2 emits the final text + ends."""
    # Turn 1: claude says "Let me check..." then tool_use(get_diagnostic_context)
    tool_use_block = MagicMock(
        type="tool_use",
        id="toolu_1",
        input={"metric_name": "median_fwhm_px"},
    )
    tool_use_block.name = "get_diagnostic_context"
    turn1 = _make_stream(
        events=[
            _text_event("Let me check your FWHM. "),
            _content_block_stop_tool("get_diagnostic_context", {"metric_name": "median_fwhm_px"}),
        ],
        final_content=[
            MagicMock(type="text", text="Let me check your FWHM. "),
            tool_use_block,
        ],
    )
    # Turn 2: Claude finishes after seeing the tool result
    final_text_block = MagicMock(type="text", text="Your FWHM is fine.")
    turn2 = _make_stream(
        events=[_text_event("Your FWHM is fine."), _content_block_stop_text()],
        final_content=[final_text_block],
    )
    client = _make_client([turn1, turn2])

    events = []
    async for ev in stream_chat(
        scorecard={"reference_category": "global"},
        messages=[{"role": "user", "content": "How are my stars?"}],
        archive_dir="/no/such/dir",
        client=client,
    ):
        events.append(ev)

    types = [t for t, _ in events]
    assert "token" in types
    assert "tool_use" in types
    assert types[-1] == "done"
    # Tool_use payload should include name + input
    tool_event = next(p for t, p in events if t == "tool_use")
    assert tool_event["name"] == "get_diagnostic_context"
    assert tool_event["input"] == {"metric_name": "median_fwhm_px"}


@pytest.mark.asyncio
async def test_stream_chat_handles_anthropic_exception():
    """If the SDK raises, we yield an error event and stop, not crash."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.stream = MagicMock(side_effect=RuntimeError("api down"))

    events = []
    async for ev in stream_chat(
        scorecard={},
        messages=[{"role": "user", "content": "hi"}],
        archive_dir="/no/such/dir",
        client=client,
    ):
        events.append(ev)

    assert len(events) == 1
    assert events[0][0] == "error"
    assert "api down" in events[0][1]["message"]


# ---------------------------------------------------------------------------- #
# /chat endpoint via TestClient
# ---------------------------------------------------------------------------- #


def test_chat_endpoint_streams_sse(monkeypatch):
    """Smoke test the FastAPI /chat route — mock stream_chat to a fixed sequence."""
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    from apodornot import web

    async def fake_stream(*, scorecard, messages, image_path=None, archive_dir, max_tokens=None):
        yield ("token", {"text": "Hi "})
        yield ("token", {"text": "there."})
        yield ("done", {})

    monkeypatch.setattr(web, "stream_chat", fake_stream)
    client = TestClient(web.app)

    body = {
        "scorecard": {"overall_score": 50, "reference_category": "global"},
        "messages": [{"role": "user", "content": "Hello"}],
    }
    with client.stream("POST", "/chat", json=body) as r:
        assert r.status_code == 200
        text = "".join(chunk for chunk in r.iter_text())

    # SSE blocks separated by \n\n
    blocks = [b for b in text.split("\n\n") if b.strip()]
    types = []
    for b in blocks:
        for line in b.split("\n"):
            if line.startswith("event: "):
                types.append(line.removeprefix("event: ").strip())
    assert types == ["token", "token", "done", "close"]
