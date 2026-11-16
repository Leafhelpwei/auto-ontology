# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for LLM invocation helpers."""

import contextlib
import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from auto_ontology.utils import llm_invoke
from auto_ontology.utils.llm_invoke import (
    _structured_output_kwargs,
    _system_messages_first,
)


@pytest.fixture(autouse=True)
def _isolate_fallback_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent client identities and cached fallback clients leaking across tests."""
    monkeypatch.setattr(llm_invoke, "_NON_REASONING_IDS", set())
    monkeypatch.setattr(llm_invoke, "_NON_REASONING_STRONG_REFS", {})
    monkeypatch.setattr(llm_invoke, "_REASONING_FALLBACK_CLIENT", None)
    monkeypatch.delenv("LLM_MODEL_CAPABILITIES", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_ENDPOINT_ALLOWLIST", raising=False)
    monkeypatch.setattr(
        llm_invoke, "_NON_REASONING_BASE_URL", "https://same.example/v1"
    )
    monkeypatch.setattr(llm_invoke, "_BASE_URL", "https://same.example/v1")


class _Model:
    """Stand-in exposing the model name the way langchain clients do."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


@pytest.mark.parametrize(
    "name", ["claude-opus", "ANTHROPIC/Claude", "qwen", "nvidia/nemotron", "gpt-4o"]
)
def test_model_name_does_not_implicitly_select_protocol(name: str) -> None:
    assert _structured_output_kwargs(_Model(name)) == {}


def test_explicit_tool_capabilities_support_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "LLM_MODEL_CAPABILITIES",
        json.dumps(
            {
                "gateway-model": {
                    "structured_output_method": "function_calling",
                    "tool_choice": "omit",
                }
            }
        ),
    )
    assert _structured_output_kwargs(_Model("gateway-model")) == {
        "method": "function_calling",
        "tool_choice": None,
    }

    class Alternate:
        model = "gateway-model"

    assert _structured_output_kwargs(Alternate())["tool_choice"] is None


def _interleaved() -> list[BaseMessage]:
    """The shape sql_from_semantic sends: system, user, system, system, user."""
    return [
        SystemMessage(content="role"),
        HumanMessage(content="question"),
        SystemMessage(content="evidence"),
        SystemMessage(content="anchors"),
        HumanMessage(content="prompt"),
    ]


@pytest.mark.parametrize("model", ["claude-opus", "qwen", "nvidia/nemotron"])
def test_every_model_gets_a_single_leading_system(model: str) -> None:
    ordered = _system_messages_first(_Model(model), _interleaved())
    assert [m.type for m in ordered] == ["system", "human"]
    assert ordered[0].content == "role\n\nevidence\n\nanchors"
    assert ordered[1].content == "question\n\nprompt"


class _SystemCheckModel(BaseModel):
    answer: str


def test_validation_correction_remains_in_user_role() -> None:
    """Output errors never become privileged system instructions."""
    sent: list[list[BaseMessage]] = []

    class _Structured:
        def invoke(self, messages: list[BaseMessage]) -> _SystemCheckModel:
            sent.append(messages)
            if len(sent) == 1:
                _SystemCheckModel.model_validate({})
            return _SystemCheckModel(answer="ok")

    class _Claude(_Model):
        def with_structured_output(self, *_args: Any, **_kwargs: Any) -> _Structured:
            return _Structured()

    llm_invoke.safe_invoke_with_structured_output(
        _Claude("azure/anthropic/claude-opus-5-5"),  # type: ignore[arg-type]
        [SystemMessage(content="role"), HumanMessage(content="question")],
        _SystemCheckModel,
    )

    roles = [m.type for m in sent[1]]
    assert roles == ["system", "human"]
    assert sent[1][0].content == "role"
    assert "schema" in sent[1][1].content


def _bound() -> int | None:
    """Capacity of the bound currently in force, or None when unbounded."""
    guard = llm_invoke._INFLIGHT
    return None if guard is None else guard._initial_value  # type: ignore[attr-defined]


def test_limit_inflight_applies_and_releases_a_bound() -> None:
    assert _bound() is None
    with llm_invoke.limit_inflight(6):
        assert _bound() == 6
    assert _bound() is None


def test_overlapping_blocks_do_not_leak_a_bound() -> None:
    """Two blocks that overlap without nesting exit in non-stack order.

    A save/restore of one global has each block put back the value it captured,
    which drops the bound of a block still running and then leaves a stale bound
    installed with nothing active. Both are silent, and the second permanently
    re-imposes the ceiling this module exists to avoid.
    """
    a_inside = threading.Event()
    b_inside = threading.Event()
    seen_by_b: list[int | None] = []

    def first() -> None:
        with llm_invoke.limit_inflight(6):
            a_inside.set()
            b_inside.wait(5)  # leave only once B is also inside

    def second() -> None:
        a_inside.wait(5)
        with llm_invoke.limit_inflight(6):
            b_inside.set()
            time.sleep(0.2)  # outlive A
            seen_by_b.append(_bound())

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert seen_by_b == [6], "B lost its bound when A exited"
    assert _bound() is None, "a bound survived with no block active"


def test_the_most_restrictive_overlapping_bound_wins() -> None:
    with llm_invoke.limit_inflight(8):
        assert _bound() == 8
        with llm_invoke.limit_inflight(2):
            assert _bound() == 2
        # Leaving the tighter block must not leave the process unbounded.
        assert _bound() == 8
    assert _bound() is None


def test_a_non_positive_limit_leaves_another_blocks_bound_alone() -> None:
    """ "I need no bound" must not mean "remove someone else's"."""
    with llm_invoke.limit_inflight(4):
        with llm_invoke.limit_inflight(0):
            assert _bound() == 4
        assert _bound() == 4
    assert _bound() is None


def test_an_exception_inside_the_block_still_releases_the_bound() -> None:
    with contextlib.suppress(RuntimeError):
        with llm_invoke.limit_inflight(3):
            assert _bound() == 3
            raise RuntimeError("boom")
    assert _bound() is None


class _Schema:
    """Stand-in schema; the structured-invoke mock never validates it."""


class _Binding:
    """Stand-in for ``llm.bind()``, which wraps the client as ``bound``."""

    def __init__(self, bound: object) -> None:
        self.bound = bound


def test_non_reasoning_client_is_marked(monkeypatch: Any) -> None:
    sentinel = object()
    monkeypatch.setattr(llm_invoke, "_NON_REASONING_API_KEY", "sk-test")
    monkeypatch.setattr(llm_invoke, "_build_client", lambda **_kwargs: sentinel)

    client = llm_invoke.get_non_reasoning_llm_client()

    assert client is sentinel
    assert llm_invoke._is_non_reasoning_client(client)
    assert llm_invoke._is_non_reasoning_client(_Binding(client))
    assert not llm_invoke._is_non_reasoning_client(object())
    assert not llm_invoke._is_non_reasoning_client(_Binding(object()))


def test_endless_bound_chain_is_not_followed_forever() -> None:
    """A ``MagicMock`` has a fresh ``bound`` on every access; the walk must stop."""
    assert not llm_invoke._is_non_reasoning_client(MagicMock())


def test_non_weakref_client_id_cannot_match_an_unrelated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = object()
    registered = object()
    monkeypatch.setattr(llm_invoke, "_NON_REASONING_IDS", {id(candidate)})
    monkeypatch.setattr(
        llm_invoke,
        "_NON_REASONING_STRONG_REFS",
        {id(candidate): registered},
    )

    assert not llm_invoke._is_non_reasoning_client(candidate)


class ScriptedModel(_Model):
    def __init__(self, responses: list[object]) -> None:
        super().__init__("configured-model")
        self.responses = iter(responses)
        self.calls = 0

    def with_structured_output(
        self, *_args: object, **_kwargs: object
    ) -> "ScriptedModel":
        return self

    def invoke(self, _messages: object) -> object:
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("structured", [True, False])
@pytest.mark.parametrize("binding", [True, False])
def test_same_endpoint_transient_fallback_shares_three_call_budget(
    monkeypatch: pytest.MonkeyPatch, structured: bool, binding: bool
) -> None:
    error = TimeoutError("temporary")
    primary = ScriptedModel([error, error])
    target = ScriptedModel([_SystemCheckModel(answer="ok") if structured else "ok"])
    llm_invoke._remember_non_reasoning(primary)
    client = primary
    if binding:

        class Binding(ScriptedModel):
            def invoke(self, messages: object) -> object:
                return primary.invoke(messages)

        client = Binding([])
        client.bound = primary
    monkeypatch.setattr(llm_invoke, "get_llm_client", lambda **_: target)
    monkeypatch.setattr(llm_invoke.time, "sleep", lambda _: None)
    if structured:
        result = llm_invoke.invoke_with_structured_output(
            client, [HumanMessage(content="q")], _SystemCheckModel
        )
        assert result.answer == "ok"
    else:
        assert llm_invoke.safe_invoke_text(client, "q") == "ok"
    assert (primary.calls, target.calls) == (2, 1)


def test_reasoning_fallback_client_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, target = object(), object(), object()
    builds = []
    llm_invoke._remember_non_reasoning(first)
    llm_invoke._remember_non_reasoning(second)
    monkeypatch.setattr(
        llm_invoke, "get_llm_client", lambda **_: builds.append(target) or target
    )
    assert llm_invoke._reasoning_fallback_client(first) is target
    assert llm_invoke._reasoning_fallback_client(second) is target
    assert builds == [target]


@pytest.mark.parametrize("structured", [True, False])
def test_exhaustion_is_bounded_for_both_public_apis(
    monkeypatch: pytest.MonkeyPatch, structured: bool
) -> None:
    primary = ScriptedModel([TimeoutError("temporary")] * 2)
    target = ScriptedModel([TimeoutError("temporary")])
    llm_invoke._remember_non_reasoning(primary)
    monkeypatch.setattr(llm_invoke, "get_llm_client", lambda **_: target)
    monkeypatch.setattr(llm_invoke.time, "sleep", lambda _: None)
    if structured:
        assert (
            llm_invoke.invoke_with_structured_output(
                primary, [HumanMessage(content="q")], _SystemCheckModel
            )
            is None
        )
    else:
        with pytest.raises(TimeoutError):
            llm_invoke.invoke_text(primary, "q")
    assert (primary.calls, target.calls) == (2, 1)


def test_missing_configuration_never_redirects_private_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(**_kwargs: object) -> object:
        raise EnvironmentError("missing key")

    monkeypatch.setattr(llm_invoke, "get_non_reasoning_llm_client", unavailable)
    monkeypatch.setattr(
        llm_invoke, "get_llm_client", lambda **_: pytest.fail("unexpected fallback")
    )
    assert (
        llm_invoke.safe_invoke_structured_nr(
            [HumanMessage(content="q")], _SystemCheckModel
        )
        is None
    )
    with pytest.raises(EnvironmentError):
        llm_invoke.safe_invoke_text_nr("q")


def test_reasoning_client_never_switches_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ScriptedModel([TimeoutError("temporary")] * 3)
    monkeypatch.setattr(
        llm_invoke, "get_llm_client", lambda **_: pytest.fail("unexpected fallback")
    )
    monkeypatch.setattr(llm_invoke.time, "sleep", lambda _: None)
    assert (
        llm_invoke.invoke_with_structured_output(
            client, [HumanMessage(content="q")], _SystemCheckModel
        )
        is None
    )
    assert client.calls == 3


def test_waiting_for_capacity_obeys_the_same_deadline() -> None:
    with llm_invoke.limit_inflight(1):
        with llm_invoke._inflight_guard():
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                with llm_invoke._inflight_guard(timeout=0.02):
                    pytest.fail("capacity already held")
            assert time.monotonic() - started < 0.5
