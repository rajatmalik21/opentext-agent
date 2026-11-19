import json

import pytest

from ota.agent.claude import API_VERSION, ClaudeClient
from ota.agent.model import ModelError
from ota.agent.tools import ToolRegistry  # noqa: F401  (import check)
from ota.agent.types import ToolCall, ToolSpec

from conftest import TIMEOUT, FakeResponse, ScriptedSession

KEY = "sk-test-SECRET-1234567890"
OK = {
    "content": [
        {"type": "text", "text": "thinking"},
        {"type": "tool_use", "id": "toolu_1", "name": "search_support_content", "input": {"query": "x"}},
        {"type": "unknown_future_block", "x": 1},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 11, "output_tokens": 4, "cache_read_input_tokens": 3},
}
SPEC = ToolSpec("search_support_content", "d", {"type": "object", "properties": {}})


def client(script, **kw):
    sleeps = []
    return ClaudeClient(KEY, session=ScriptedSession(script), sleep=sleeps.append, **kw), sleeps


def test_request_shape_and_response_parsing():
    c, _ = client([FakeResponse(200, OK)])
    turn = c.generate("SYS", [{"role": "user", "content": "hi"}], [SPEC], max_tokens=123)
    url, headers, data, timeout = c._session.calls[0]
    body = json.loads(data)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == KEY and headers["anthropic-version"] == API_VERSION == "2023-06-01"
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 123 and body["system"] == "SYS"
    assert "temperature" not in body
    assert body["tools"] == [{"name": "search_support_content", "description": "d",
                              "input_schema": {"type": "object", "properties": {}}}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert turn.text == "thinking" and turn.stop_reason == "tool_use"
    assert turn.tool_calls == [ToolCall("toolu_1", "search_support_content", {"query": "x"})]
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (11, 4)


def test_convert_messages_groups_tool_results_and_merges_following_user_turn():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "text": "", "tool_calls": [ToolCall("a", "t", {"k": 1}), ToolCall("b", "t", {})]},
        {"role": "tool", "tool_call_id": "a", "name": "t", "content": "res-a"},
        {"role": "tool", "tool_call_id": "b", "name": "t", "content": "res-b"},
        {"role": "user", "content": "repair"},
    ]
    out = ClaudeClient.convert_messages(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] == [
        {"type": "tool_use", "id": "a", "name": "t", "input": {"k": 1}},
        {"type": "tool_use", "id": "b", "name": "t", "input": {}},
    ]
    assert out[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": "res-a"},
        {"type": "tool_result", "tool_use_id": "b", "content": "res-b"},
        {"type": "text", "text": "repair"},
    ]


def test_retries_transient_then_succeeds_and_gives_up():
    c, sleeps = client([FakeResponse(529), TIMEOUT, FakeResponse(200, OK)])
    assert c.generate("s", [{"role": "user", "content": "q"}], []).text == "thinking"
    assert sleeps == [1.0, 2.0]
    c2, _ = client([FakeResponse(500)] * 3, max_retries=2)
    with pytest.raises(ModelError, match="unavailable after 3 attempts"):
        c2.generate("s", [{"role": "user", "content": "q"}], [])


def test_client_errors_not_retried_and_never_leak_key():
    c, _ = client([FakeResponse(401, {"error": {"message": "invalid x-api-key"}}), FakeResponse(200, OK)])
    with pytest.raises(ModelError) as e:
        c.generate("s", [{"role": "user", "content": "q"}], [])
    assert "HTTP 401" in str(e.value) and KEY not in str(e.value)
    assert len(c._session.calls) == 1
    assert KEY not in repr(c) and "claude-sonnet-5-5" in repr(c)


def test_requires_key():
    with pytest.raises(ValueError):
        ClaudeClient("")
