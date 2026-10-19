"""Provider-independent types, the Provider interface, and error classification."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

RETRYABLE_KINDS = frozenset({"network", "timeout", "rate_limit", "overload"})
# Model-specific failures where trying another model can plausibly help.
# Account-wide problems (auth, billing, quota) are deliberately excluded.
FALLBACK_KINDS = frozenset({"rate_limit", "unavailable", "unsupported_tools", "overload", "context_overflow"})


class ProviderError(Exception):
    """A classified provider failure."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    @property
    def fallback_eligible(self) -> bool:
        return self.kind in FALLBACK_KINDS


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    provider: str = ""
    context_length: int | None = None
    prompt_price_per_m: float | None = None
    completion_price_per_m: float | None = None
    supports_tools: bool | None = None
    supports_streaming: bool | None = None
    supports_vision: bool | None = None
    description: str = ""

    @property
    def is_free(self) -> bool | None:
        if self.prompt_price_per_m is None or self.completion_price_per_m is None:
            return None
        return self.prompt_price_per_m == 0 and self.completion_price_per_m == 0

    def price_label(self) -> str:
        if self.prompt_price_per_m is None and self.completion_price_per_m is None:
            return "unknown"
        if self.is_free:
            return "free"
        p = "?" if self.prompt_price_per_m is None else f"${self.prompt_price_per_m:g}"
        c = "?" if self.completion_price_per_m is None else f"${self.completion_price_per_m:g}"
        return f"{p}/{c} per 1M"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "provider": self.provider,
            "context_length": self.context_length,
            "prompt_price_per_m": self.prompt_price_per_m,
            "completion_price_per_m": self.completion_price_per_m,
            "supports_tools": self.supports_tools,
            "supports_vision": self.supports_vision,
            "description": self.description,
        }


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class ProviderResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    model: str = ""


@dataclass
class ProviderConfig:
    name: str
    kind: str
    model: str = ""
    base_url: str = ""
    enabled: bool = True


def classify_http(status: int, payload: Any, headers: dict[str, str]) -> ProviderError:
    """Map an HTTP failure to a ProviderError kind."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    low = text.lower()
    message = _extract_message(payload) or f"HTTP {status}"
    retry_after: float | None = None
    raw_ra = headers.get("retry-after")
    if raw_ra:
        try:
            retry_after = float(raw_ra)
        except ValueError:
            retry_after = None

    if status in (401, 403):
        kind = "billing" if any(w in low for w in ("credit", "insufficient", "payment")) else "auth"
    elif status == 402:
        kind = "billing"
    elif status == 404:
        kind = "unavailable"
    elif status == 408:
        kind = "timeout"
    elif status == 429:
        kind = "quota" if any(w in low for w in ("quota", "exceeded your", "billing")) else "rate_limit"
    elif status == 413:
        kind = "context_overflow"
    elif status in (400, 422):
        if "context" in low and any(w in low for w in ("length", "maximum", "too long")):
            kind = "context_overflow"
        elif "tool" in low and any(w in low for w in ("not support", "unsupported")):
            kind = "unsupported_tools"
        else:
            kind = "bad_request"
    elif status in (500, 502, 503, 504, 529):
        kind = "overload"
    else:
        kind = "unknown"
    return ProviderError(kind, message, status=status, retry_after=retry_after)


def _extract_message(payload: Any) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
        if payload.get("message"):
            return str(payload["message"])[:300]
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:300]
    return ""


def call_json(transport, method, url, headers, body, timeout) -> Any:
    status, payload, resp_headers = transport.request(method, url, headers, body, timeout)
    if 200 <= status < 300:
        if not isinstance(payload, (dict, list)):
            raise ProviderError("malformed", "provider returned a non-JSON response", status=status)
        return payload
    raise classify_http(status, payload, resp_headers)


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class Provider(ABC):
    """Common interface every vendor adapter implements."""

    kind = "base"
    default_base_url = ""

    def __init__(self, config: ProviderConfig, api_key: str, transport=None, timeout: int = 120):
        if transport is None:
            from .transport import UrllibTransport

            transport = UrllibTransport()
        self.config = config
        self.name = config.name
        self._key = api_key
        self.transport = transport
        self.timeout = timeout
        self.base_url = (config.base_url or self.default_base_url).rstrip("/")
        self._model_cache: dict[str, ModelInfo] = {}

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Fetch the live catalogue from the vendor."""

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
        max_tokens: int = 2048,
        timeout: int | None = None,
    ) -> ProviderResponse:
        """Send a normalized conversation and return a normalized response."""

    def validate_credentials(self) -> None:
        self.list_models()

    def remember_models(self, models: list[ModelInfo]) -> None:
        self._model_cache = {m.id: m for m in models}

    def get_model_capabilities(self, model_id: str) -> ModelInfo | None:
        if not self._model_cache:
            self.remember_models(self.list_models())
        return self._model_cache.get(model_id)

    def _call(self, method: str, url: str, headers: dict[str, str], body=None, timeout=None):
        return call_json(self.transport, method, url, headers, body, timeout or self.timeout)
