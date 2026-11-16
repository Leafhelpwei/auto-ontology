# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM client construction and structured-output invocation wrappers."""

import contextlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit
import logging
import os
import random
import threading
import time
import weakref
from typing import Iterator, Type, TypeVar

import requests as _requests
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from auto_ontology.utils.model_config import resolve
from auto_ontology.utils.chat_protocol import normalize_messages, text_content

logger = logging.getLogger(__name__)


class StrictLLMOutputModel(BaseModel):
    """Base for LLM structured-output schemas.

    Forbids genuine hallucinated fields (``extra="forbid"``) but first strips any
    key starting with ``$`` — some models leak tool-calling metadata (e.g. a stray
    ``$FUNCTION_NAME`` field echoing the tool name) into the arguments payload,
    which would otherwise fail validation and burn all retry attempts even though
    the real data is fine. Opt a schema into this by inheriting from it instead of
    ``BaseModel``.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _strip_provider_artifact_keys(cls, data):
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not k.startswith("$")}
        return data


RETRY_MAX_ATTEMPTS = 3
LLM_INVOKE_TIMEOUT_S = int(os.environ.get("LLM_INVOKE_TIMEOUT_S", "120"))

# Concurrent LLM requests are unbounded here by default. A process-wide cap used
# to live at this call site, defaulting to 6, to stop semantic compilation's
# nested fan-out (tables x terms) from saturating the endpoint's per-worker
# request cap and drawing HTTP 503s.
#
# The cost was paid by every caller. Callers that already bound their own
# concurrency -- a server handling N requests, an eval running N questions --
# gained nothing and hit a second, lower ceiling they could not see: measured on
# a 60-question benchmark at 12 workers, lifting this cap cut wall clock 25%
# with no change in error rate, because threads sat ~3s per node waiting on the
# semaphore before reaching the network.
#
# Fan-out that needs a bound now asks for one with `limit_inflight`, which
# applies it for the duration of a block. See `run_semantic_compilation`.
LLM_MAX_INFLIGHT = int(os.environ.get("LLM_MAX_INFLIGHT", "0"))

# The process-wide default, from the environment. None means unbounded. This is
# what the guard falls back to whenever no `limit_inflight` block is active.
_ENV_INFLIGHT: threading.BoundedSemaphore | None = (
    threading.BoundedSemaphore(LLM_MAX_INFLIGHT) if LLM_MAX_INFLIGHT > 0 else None
)

# Every `limit_inflight` block currently open, as (max_inflight, semaphore).
# Tracking the whole set rather than a save/restore of one global is what makes
# overlapping blocks safe: two blocks that overlap without nesting exit in the
# opposite order to a stack, and a save/restore would then have each one put
# back the value *it* captured -- dropping the bound of a block still running,
# and leaving a stale bound installed forever once both had exited.
_ACTIVE: list[tuple[int, threading.BoundedSemaphore]] = []
_ACTIVE_LOCK = threading.Lock()

# The bound in force. Rebound only under _ACTIVE_LOCK by `_refresh_inflight`,
# but read on the hot path without it: the read is a single attribute lookup,
# and a caller that reads a semaphore just as its block ends simply waits on a
# still-valid bound for that one invoke. That is a bounded staleness, not a
# correctness problem, and it keeps the lock off every LLM call.
_INFLIGHT: threading.BoundedSemaphore | None = _ENV_INFLIGHT


def _refresh_inflight() -> None:
    """Recompute the bound in force. Caller must hold ``_ACTIVE_LOCK``."""
    global _INFLIGHT
    # The most restrictive active block wins. Only one semaphore is ever
    # acquired, so nested blocks cannot deadlock against each other.
    _INFLIGHT = (
        min(_ACTIVE, key=lambda entry: entry[0])[1] if _ACTIVE else _ENV_INFLIGHT
    )


@contextlib.contextmanager
def limit_inflight(max_inflight: int) -> Iterator[None]:
    """Bound concurrent LLM invokes to *max_inflight* for the duration of the block.

    For call paths whose own parallelism multiplies -- semantic compilation runs
    tables in parallel and fans out again within each -- where the resulting
    request rate can exceed what the endpoint will serve and come back as 503.

    The bound is process-wide, because the fan-out it exists to limit spans
    worker threads the caller does not own. While several blocks overlap the
    most restrictive one applies, in any nesting or interleaving; once the last
    one exits the process returns to ``LLM_MAX_INFLIGHT``.

    A value of 0 or less registers nothing and leaves any other block's bound
    alone -- "I need no bound" must not mean "remove someone else's".
    """
    if max_inflight <= 0:
        yield
        return
    entry = (max_inflight, threading.BoundedSemaphore(max_inflight))
    with _ACTIVE_LOCK:
        _ACTIVE.append(entry)
        _refresh_inflight()
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            # Removal is by identity of the semaphore, so concurrent blocks
            # asking for the same size still remove their own entry.
            for index, candidate in enumerate(_ACTIVE):
                if candidate[1] is entry[1]:
                    del _ACTIVE[index]
                    break
            _refresh_inflight()


@contextlib.contextmanager
def _inflight_guard(timeout: float | None = None) -> Iterator[None]:
    """Include waiting for concurrency capacity in the request deadline."""
    semaphore = _INFLIGHT
    if semaphore is None:
        yield
        return
    acquired = (
        semaphore.acquire(timeout=timeout)
        if timeout is not None
        else semaphore.acquire()
    )
    if not acquired:
        raise TimeoutError("LLM concurrency deadline exceeded")
    try:
        yield
    finally:
        semaphore.release()


class LLMOutputError(ValueError):
    """The provider did not return a usable structured response."""


class LLMRefusalError(LLMOutputError):
    """A refusal must never trigger a second provider."""


@dataclass
class _Budget:
    deadline: float
    remaining: int = RETRY_MAX_ATTEMPTS


_BUDGET: ContextVar[_Budget | None] = ContextVar("llm_budget", default=None)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in {408, 429, 500, 502, 503, 504}
    return isinstance(exc, (_requests.exceptions.Timeout, TimeoutError)) or type(
        exc
    ).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "ConnectTimeout",
        "ReadTimeout",
    }


class _TimeoutSession(_requests.Session):
    """requests.Session that enforces a default timeout on every request."""

    def __init__(self, timeout: float = LLM_INVOKE_TIMEOUT_S, **kwargs):
        super().__init__(**kwargs)
        self._default_timeout = timeout

    def request(self, method, url, **kwargs):
        budget = _BUDGET.get()
        remaining = (
            max(0.001, budget.deadline - time.monotonic())
            if budget
            else self._default_timeout
        )
        kwargs["timeout"] = min(self._default_timeout, remaining)
        return super().request(method, url, **kwargs)


T = TypeVar("T", bound=BaseModel)

# Main (reasoning) model triplet. Each field falls back to DEFAULT_MODELS_<field>
# (and the API key additionally to the legacy NVIDIA_API_KEY) when unset.
_BASE_URL = resolve("REASONING", "ENDPOINT")
_MODEL_NAME = resolve("REASONING", "MODEL")
_API_KEY = resolve("REASONING", "API_KEY")

# Non-reasoning model. Kept fully separate (key/endpoint/model) so it can point at
# a different endpoint than the main model (e.g. inference vs integrate API). Each
# field falls back to DEFAULT_MODELS_<field> when unset.
_NON_REASONING_BASE_URL = resolve("NON_REASONING", "ENDPOINT")
_NON_REASONING_MODEL_NAME = resolve("NON_REASONING", "MODEL")
_NON_REASONING_API_KEY = resolve("NON_REASONING", "API_KEY")


def _capabilities(llm: object) -> dict:
    profiles = json.loads(os.getenv("LLM_MODEL_CAPABILITIES", "{}"))
    if not isinstance(profiles, dict):
        raise ValueError("LLM_MODEL_CAPABILITIES must be an object")
    model = getattr(llm, "model_name", None) or getattr(llm, "model", "")
    profile = profiles.get(model, {}) if isinstance(model, str) else {}
    if not isinstance(profile, dict):
        raise ValueError("Model capabilities must be an object")
    allowed = {
        "temperature",
        "structured_output_method",
        "tool_choice",
        "strict",
        "structured_output_format",
    }
    if set(profile) - allowed:
        raise ValueError("Unknown model capability")
    if "temperature" in profile and not isinstance(profile["temperature"], bool):
        raise ValueError("temperature capability must be boolean")
    if "strict" in profile and not isinstance(profile["strict"], bool):
        raise ValueError("strict capability must be boolean")
    return profile


def _provider(base_url: str, explicit: str) -> str:
    if explicit not in {"auto", "openai_compatible", "nvidia"}:
        raise ValueError("Invalid chat provider")
    if explicit != "auto":
        return explicit
    return (
        "nvidia"
        if urlsplit(base_url).hostname
        in {"integrate.api.nvidia.com", "ai.api.nvidia.com"}
        else "openai_compatible"
    )


def _build_client(
    *,
    model: str,
    api_key: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
    provider: str = "auto",
) -> BaseChatModel:
    if not model or not base_url or urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError("Configure an explicit model and HTTP(S) endpoint")
    profile = _capabilities(type("ModelConfiguration", (), {"model": model})())
    kwargs = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "max_tokens": max_tokens,
    }
    if profile.get("temperature", True):
        kwargs["temperature"] = temperature
    if _provider(base_url, provider) == "openai_compatible":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(**kwargs, timeout=LLM_INVOKE_TIMEOUT_S, max_retries=0)
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
    client = ChatNVIDIA(**kwargs)
    client._client.get_session_fn = lambda: _TimeoutSession(LLM_INVOKE_TIMEOUT_S)
    return client


def get_llm_client(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Create an LLM client for the main reasoning model.

    Parameters
    ----------
    model : str | None
        Override the default ``REASONING_MODEL`` env var for this client.
    """
    if not _API_KEY:
        raise EnvironmentError("REASONING_API_KEY is not set")

    return _build_client(
        model=model or _MODEL_NAME,
        api_key=_API_KEY,
        base_url=_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        provider=os.getenv(
            "REASONING_PROVIDER", os.getenv("DEFAULT_MODELS_PROVIDER", "auto")
        ),
    )


def get_non_reasoning_llm_client(
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 8192,
) -> BaseChatModel:
    """Create an LLM client for the non-reasoning model.

    Uses ``NON_REASONING_API_KEY`` / ``NON_REASONING_ENDPOINT`` /
    ``NON_REASONING_MODEL`` so it can target a different endpoint than the
    main agent model.

    Parameters
    ----------
    model : str | None
        Override the default ``NON_REASONING_MODEL`` env var for this client.
    """
    if not _NON_REASONING_API_KEY:
        raise EnvironmentError("NON_REASONING_API_KEY is not set")

    client = _build_client(
        model=model or _NON_REASONING_MODEL_NAME,
        api_key=_NON_REASONING_API_KEY,
        base_url=_NON_REASONING_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        provider=os.getenv(
            "NON_REASONING_PROVIDER", os.getenv("DEFAULT_MODELS_PROVIDER", "auto")
        ),
    )
    _remember_non_reasoning(client)
    return client


# Identity of every live non-reasoning client. Model name is not enough: the two
# roles can be configured to the same model string, and the langchain clients
# compare equal by their fields, so a reasoning call must not match a
# non-reasoning client that happens to share its configuration. ``.bind()``
# returns a wrapper whose ``bound`` is the client registered here.
_NON_REASONING_IDS: set[int] = set()
_NON_REASONING_STRONG_REFS: dict[int, object] = {}
_REASONING_FALLBACK_CLIENT: BaseChatModel | None = None
_REASONING_FALLBACK_LOCK = threading.Lock()

# How many ``bound`` links to follow. Real ``.bind()`` chains are a level or two
# deep; the cap stops the walk on objects whose ``bound`` never ends — a
# ``MagicMock`` returns a fresh mock for every attribute access.
_MAX_BINDING_DEPTH = 8


def _remember_non_reasoning(client: object) -> None:
    """Record *client* so a failed call on it can fall back to the reasoning model."""
    client_id = id(client)
    _NON_REASONING_IDS.add(client_id)
    try:
        weakref.finalize(client, _NON_REASONING_IDS.discard, client_id)
    except TypeError:
        # Keep non-weak-referenceable clients alive so Python cannot reuse their
        # registered id for an unrelated reasoning client.
        _NON_REASONING_STRONG_REFS[client_id] = client


def _is_non_reasoning_client(llm: object) -> bool:
    """Whether *llm* is a non-reasoning client, including a ``.bind()`` wrapper."""
    current = llm
    for _ in range(_MAX_BINDING_DEPTH):
        if id(current) in _NON_REASONING_IDS:
            registered = _NON_REASONING_STRONG_REFS.get(id(current))
            if registered is None or registered is current:
                return True
        bound = getattr(current, "bound", None)
        if bound is None or isinstance(bound, (str, bytes, int, float, bool)):
            return False
        current = bound
    return False


def _get_reasoning_fallback_client() -> BaseChatModel:
    """Build the default reasoning fallback once and reuse its connection pool."""
    global _REASONING_FALLBACK_CLIENT
    if _REASONING_FALLBACK_CLIENT is None:
        with _REASONING_FALLBACK_LOCK:
            if _REASONING_FALLBACK_CLIENT is None:
                _REASONING_FALLBACK_CLIENT = get_llm_client()
    return _REASONING_FALLBACK_CLIENT


def _reasoning_fallback_client(llm: object) -> BaseChatModel | None:
    if not _is_non_reasoning_client(llm):
        return None
    source = (_NON_REASONING_BASE_URL or "").rstrip("/")
    destination = (_BASE_URL or "").rstrip("/")
    allowed = json.loads(os.getenv("LLM_FALLBACK_ENDPOINT_ALLOWLIST", "[]"))
    if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
        raise ValueError("Fallback endpoint allowlist must be an array of exact URLs")
    if (
        not source
        or not destination
        or (
            source != destination
            and destination not in {x.rstrip("/") for x in allowed}
        )
    ):
        return None
    try:
        return _get_reasoning_fallback_client()
    except Exception as exc:
        logger.warning("Fallback unavailable error_class=%s", type(exc).__name__)
        return None


def _ensure_non_system_message(messages: list[BaseMessage]) -> list[BaseMessage]:
    return normalize_messages(messages)


def _system_messages_first(
    llm: object, messages: list[BaseMessage]
) -> list[BaseMessage]:
    return normalize_messages(messages)


def _structured_output_kwargs(llm: object) -> dict:
    profile = _capabilities(llm)
    method = profile.get("structured_output_method")
    if method not in {None, "json_schema", "function_calling", "json_mode", "prompt"}:
        raise ValueError("Unsupported structured output method")
    kwargs = {"method": method} if method and method != "prompt" else {}
    if "tool_choice" in profile:
        choice = profile["tool_choice"]
        if method != "function_calling" or choice not in {"omit", "auto", "required"}:
            raise ValueError("tool_choice requires function_calling")
        kwargs["tool_choice"] = None if choice == "omit" else choice
    if "strict" in profile:
        if method not in {None, "json_schema", "function_calling"}:
            raise ValueError("strict is supported only for schema or tools")
        kwargs["strict"] = profile["strict"]
    if "structured_output_format" in profile:
        fmt = profile["structured_output_format"]
        if fmt not in {"openai", "guided_json", "nvext"}:
            raise ValueError("Unsupported NVIDIA schema format")
        kwargs["structured_output_format"] = fmt
    return kwargs


def _refused(raw: object) -> bool:
    extras = getattr(raw, "additional_kwargs", {})
    metadata = getattr(raw, "response_metadata", {})
    if isinstance(extras, dict) and extras.get("refusal"):
        return True
    if isinstance(metadata, dict) and metadata.get("finish_reason") in {
        "content_filter",
        "refusal",
    }:
        return True
    content = getattr(raw, "content", None)
    return isinstance(content, list) and any(
        isinstance(x, dict) and x.get("type") == "refusal" for x in content
    )


def _rpc(
    llm: object, messages: list[BaseMessage], schema: Type[T] | None, remaining: float
) -> T | str:
    messages = normalize_messages(messages)
    if sum(len(text_content(x.content)) for x in messages) > int(
        os.getenv("LLM_MAX_PROMPT_CHARACTERS", "120000")
    ):
        raise ValueError("LLM prompt budget exceeded")
    method = _capabilities(llm).get("structured_output_method")
    _structured_output_kwargs(
        llm
    )  # Reject invalid capability combinations before any RPC.
    sdk = isinstance(llm, BaseChatModel)
    # ChatOpenAI forwards timeout to the SDK request; NVIDIA's requests.Session
    # reads the same ContextVar deadline. Test doubles do not receive SDK kwargs.
    kwargs = (
        {"timeout": remaining}
        if sdk and type(llm).__module__.startswith("langchain_openai")
        else {}
    )
    if schema is None:
        response = llm.invoke(messages, **kwargs)
        if _refused(response):
            raise LLMRefusalError("Model refused the request")
        return text_content(getattr(response, "content", response))
    if method in {"json_mode", "prompt"}:
        messages.append(
            HumanMessage(
                content="Return only JSON matching this schema: "
                + json.dumps(schema.model_json_schema())
            )
        )
        messages = normalize_messages(messages)
        if sum(len(text_content(x.content)) for x in messages) > int(
            os.getenv("LLM_MAX_PROMPT_CHARACTERS", "120000")
        ):
            raise ValueError("LLM prompt budget exceeded")
    nvidia = sdk and type(llm).__module__.startswith("langchain_nvidia")
    if nvidia and method != "prompt":
        if method == "function_calling":
            raise ValueError("Use the OpenAI-compatible adapter for tool output")
        profile = _capabilities(llm)
        if (
            profile.get("strict") is False
            and profile.get("structured_output_format", "guided_json") != "openai"
        ):
            raise ValueError("strict=false requires NVIDIA openai schema format")
        if method == "json_mode":
            binding = {"response_format": {"type": "json_object"}}
        else:
            public = urlsplit(str(llm.base_url)).hostname in {
                "integrate.api.nvidia.com",
                "ai.api.nvidia.com",
            }
            fmt = profile.get(
                "structured_output_format", "openai" if public else "guided_json"
            )
            definition = schema.model_json_schema()
            if fmt == "openai":
                binding = {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "schema": definition,
                            "strict": profile.get("strict", True),
                        },
                    }
                }
            elif fmt == "guided_json":
                binding = {"guided_json": definition}
            elif fmt == "nvext":
                binding = {"nvext": {"guided_json": definition}}
            else:
                raise ValueError("Unsupported NVIDIA schema format")
        # The SDK's structured helper silently retries three different wire
        # formats and cannot expose refusals. Choose one declared format and
        # validate its raw response with the shared request budget instead.
        response = llm.bind(**binding).invoke(messages)
        if _refused(response):
            raise LLMRefusalError("Model refused the request")
        result = json.loads(text_content(response.content))
    elif method == "prompt":
        response = llm.invoke(messages, **kwargs)
        if _refused(response):
            raise LLMRefusalError("Model refused the request")
        result = json.loads(text_content(getattr(response, "content", response)))
    else:
        options = _structured_output_kwargs(llm)
        if sdk:
            options["include_raw"] = True
        response = llm.with_structured_output(schema, **options).invoke(
            messages, **kwargs
        )
        if isinstance(response, dict) and "raw" in response and "parsed" in response:
            if _refused(response["raw"]):
                raise LLMRefusalError("Model refused the request")
            result = response["parsed"]
        else:
            result = response
    if result is None:
        raise LLMOutputError("Model returned no structured output")
    return result if isinstance(result, schema) else schema.model_validate(result)


def _invoke(
    llm: object, messages: list[BaseMessage], schema: Type[T] | None = None
) -> T | str:
    current = normalize_messages(messages)
    budget = _BUDGET.get()
    token = None
    if budget is None:
        budget = _Budget(time.monotonic() + LLM_INVOKE_TIMEOUT_S)
        token = _BUDGET.set(budget)
    client = llm
    last: Exception | None = None
    try:
        while budget.remaining > 0:
            remaining = budget.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LLM total deadline exceeded")
            budget.remaining -= 1
            try:
                with _inflight_guard(timeout=remaining):
                    remaining = budget.deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("LLM total deadline exceeded")
                    return _rpc(client, current, schema, remaining)
            except LLMRefusalError:
                raise
            except Exception as exc:
                last = exc
                correctable = isinstance(
                    exc, (ValidationError, LLMOutputError, json.JSONDecodeError)
                )
                retryable = _is_retryable(exc)
                logger.warning(
                    "LLM attempt failed error_class=%s remaining_calls=%d",
                    type(exc).__name__,
                    budget.remaining,
                )
                if not (correctable or retryable) or budget.remaining <= 0:
                    raise
                if correctable:
                    # Never elevate provider output, validation inputs or error text
                    # into system instructions or retransmit them in a correction.
                    current = normalize_messages(
                        messages
                        + [
                            HumanMessage(
                                content="The response did not match the required schema. Return a complete valid object without extra fields."
                            )
                        ]
                    )
                elif budget.remaining == 1:
                    fallback = _reasoning_fallback_client(client)
                    if fallback is not None:
                        client = fallback
                if retryable:
                    delay = min(
                        0.5 + random.uniform(0, 0.25),
                        max(0, budget.deadline - time.monotonic()),
                    )
                    time.sleep(delay)
        raise LLMOutputError("LLM call budget exhausted") from last
    finally:
        if token is not None:
            _BUDGET.reset(token)


def invoke_text(llm: BaseChatModel, prompt: str) -> str:
    return _invoke(llm, [HumanMessage(content=prompt)])


def _safe_invoke_text(llm: BaseChatModel, prompt: str) -> str:
    return invoke_text(llm, prompt)


def safe_invoke_text(llm: BaseChatModel, prompt: str) -> str:
    return invoke_text(llm, prompt)


def safe_invoke_with_structured_output(
    llm: BaseChatModel, messages: list[BaseMessage], schema: Type[T]
) -> T:
    return _invoke(llm, messages, schema)


def invoke_with_structured_output(
    llm: BaseChatModel, messages: list[BaseMessage], schema: Type[T]
) -> T | None:
    try:
        return safe_invoke_with_structured_output(llm, messages, schema)
    except Exception as exc:
        logger.warning(
            "Structured call unavailable schema=%s error_class=%s",
            schema.__name__,
            type(exc).__name__,
        )
        return None


def safe_invoke_text_nr(prompt: str) -> str:
    # Missing configuration is permanent and must not disclose prompts elsewhere.
    return safe_invoke_text(get_non_reasoning_llm_client(), prompt)


def safe_invoke_structured_nr(messages: list[BaseMessage], schema: Type[T]) -> T | None:
    try:
        client = get_non_reasoning_llm_client()
    except Exception as exc:
        logger.warning(
            "Non-reasoning client unavailable error_class=%s", type(exc).__name__
        )
        return None
    return invoke_with_structured_output(client, messages, schema)
