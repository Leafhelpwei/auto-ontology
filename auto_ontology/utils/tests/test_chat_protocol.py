# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture actual SDK HTTP messages, not only a mocked invoke call."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from auto_ontology.utils.chat_protocol import normalize_messages, text_content
from auto_ontology.utils import llm_invoke as inv


class Answer(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_CAPABILITIES", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_ENDPOINT_ALLOWLIST", raising=False)
    monkeypatch.setattr(inv, "_NON_REASONING_IDS", set())
    monkeypatch.setattr(inv, "_NON_REASONING_STRONG_REFS", {})
    monkeypatch.setattr(inv.time, "sleep", lambda _: None)
    for name in [
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_single_leading_system_without_promoting_user_data():
    out = normalize_messages(
        [
            HumanMessage(content="QUESTION_SENTINEL"),
            SystemMessage(content="policy"),
            HumanMessage(content="EVIDENCE_SENTINEL"),
            SystemMessage(content="schema policy"),
            AIMessage(content="prior answer"),
            HumanMessage(content="follow-up"),
        ]
    )
    assert [x.type for x in out] == ["system", "human", "ai", "human"]
    assert out[0].content == "policy\n\nschema policy"
    assert "SENTINEL" not in out[0].content
    assert out[1].content == "QUESTION_SENTINEL\n\nEVIDENCE_SENTINEL"


def test_system_only_keeps_privilege_and_adds_user_request():
    out = normalize_messages([SystemMessage(content="trusted policy")])
    assert out[0].type == "system"
    assert out[0].content == "trusted policy"
    assert out[1].type == "human"


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [HumanMessage(content=" ")],
        [ToolMessage(content="tool", tool_call_id="t")],
        [HumanMessage(content=[{"type": "image_url", "image_url": "data:bad"}])],
    ],
)
def test_invalid_input_fails_before_a_request(messages):
    with pytest.raises(ValueError):
        normalize_messages(messages)


def test_response_extracts_only_visible_text():
    assert (
        text_content(
            [
                {"type": "reasoning", "text": "private"},
                {"type": "text", "text": "visible"},
            ]
        )
        == "visible"
    )


class Scripted:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
        self.model_name = "any-model"

    def with_structured_output(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("invalid", [None, {}, {"answer": []}])
def test_structured_failures_get_one_bounded_correction_owner(invalid):
    client = Scripted([invalid, {"answer": "ok"}])
    assert (
        inv.safe_invoke_with_structured_output(
            client, [SystemMessage(content="policy")], Answer
        ).answer
        == "ok"
    )
    assert len(client.calls) == 2
    assert [m.type for m in client.calls[-1]] == ["system", "human"]


def test_exhausted_none_raises_instead_of_implicitly_returning_none():
    client = Scripted([None] * 3)
    with pytest.raises(inv.LLMOutputError):
        inv.safe_invoke_with_structured_output(
            client, [HumanMessage(content="q")], Answer
        )
    assert len(client.calls) == 3


def test_permanent_failure_is_not_retried_or_sent_elsewhere(monkeypatch, caplog):
    error = RuntimeError("429 litellm secret question")
    error.status_code = 401
    client = Scripted([error])
    monkeypatch.setattr(
        inv, "_reasoning_fallback_client", lambda _: pytest.fail("permanent fallback")
    )
    assert (
        inv.invoke_with_structured_output(client, [HumanMessage(content="q")], Answer)
        is None
    )
    assert len(client.calls) == 1
    assert "secret question" not in caplog.text


def test_fallback_requires_an_exact_endpoint_allowlist(monkeypatch):
    source = Scripted([])
    destination = Scripted([])
    inv._remember_non_reasoning(source)
    monkeypatch.setattr(inv, "_NON_REASONING_BASE_URL", "https://source.example/v1")
    monkeypatch.setattr(inv, "_BASE_URL", "https://target.example/v1")
    monkeypatch.setattr(inv, "_get_reasoning_fallback_client", lambda: destination)
    assert inv._reasoning_fallback_client(source) is None
    monkeypatch.setenv(
        "LLM_FALLBACK_ENDPOINT_ALLOWLIST", '["https://target.example/v1"]'
    )
    assert inv._reasoning_fallback_client(source) is destination


def test_fallback_shares_total_request_budget(monkeypatch):
    error = RuntimeError("transient")
    error.status_code = 503
    source = Scripted([error, error])
    target = Scripted([{"answer": "ok"}])
    monkeypatch.setattr(inv, "_reasoning_fallback_client", lambda _: target)
    assert (
        inv.safe_invoke_with_structured_output(
            source, [HumanMessage(content="q")], Answer
        ).answer
        == "ok"
    )
    assert len(source.calls) + len(target.calls) == 3


def test_refusal_is_terminal(monkeypatch):
    raw = AIMessage(content="", additional_kwargs={"refusal": "No"})
    client = Scripted([{"raw": raw, "parsed": None}])
    monkeypatch.setattr(
        inv, "_reasoning_fallback_client", lambda _: pytest.fail("refusal fallback")
    )
    with pytest.raises(inv.LLMRefusalError):
        inv.safe_invoke_with_structured_output(
            client, [HumanMessage(content="q")], Answer
        )
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "provider,model,capabilities",
    [
        ("openai_compatible", "qwen-compatible", {}),
        (
            "openai_compatible",
            "claude-via-gateway",
            {"structured_output_method": "function_calling", "tool_choice": "omit"},
        ),
        ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b", {}),
    ],
)
def test_actual_sdk_wire_protocol(provider, model, capabilities, monkeypatch):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self._reply(
                {
                    "object": "list",
                    "data": [{"id": model, "object": "model", "owned_by": "test"}],
                }
            )

        def _reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append(payload)
            message = {"role": "assistant", "content": '{"answer":"ok"}'}
            if payload.get("tools"):
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": payload["tools"][0]["function"]["name"],
                                "arguments": '{"answer":"ok"}',
                            },
                        }
                    ],
                }
            self._reply(
                {
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls"
                            if payload.get("tools")
                            else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("LLM_MODEL_CAPABILITIES", json.dumps({model: capabilities}))
        client = inv._build_client(
            model=model,
            api_key="test-key",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            provider=provider,
            temperature=0.4,
            max_tokens=128,
        )
        answer = inv.safe_invoke_with_structured_output(
            client,
            [
                HumanMessage(content="QUESTION_SENTINEL"),
                SystemMessage(content="trusted policy"),
                HumanMessage(content="EVIDENCE_SENTINEL COLUMN_SENTINEL SQL_SENTINEL"),
            ],
            Answer,
        )
        assert answer.answer == "ok"
        assert len(captured) == 1
        payload = captured[0]
        assert payload["messages"][0]["role"] == "system"
        assert sum(m["role"] == "system" for m in payload["messages"]) == 1
        assert "SENTINEL" not in json.dumps(payload["messages"][0])
        assert payload["temperature"] == 0.4
        if capabilities:
            assert "tool_choice" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
