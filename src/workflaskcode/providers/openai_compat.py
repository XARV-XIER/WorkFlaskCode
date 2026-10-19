"""OpenAI chat-completions adapters: OpenRouter, OpenAI, and custom compatible endpoints."""

from __future__ import annotations

import json
from typing import Any

from .base import ModelInfo, Provider, ProviderError, ProviderResponse, ToolCall, Usage, as_int


def _per_million(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, dict):
        return raw, None
    if not isinstance(raw, str):
        return {}, "arguments were neither a string nor an object"
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON arguments: {exc.msg}"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


def to_openai_messages(messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": msg["tool_call_id"], "content": msg["content"]})
        elif role == "assistant" and msg.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": msg.get("content") or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in msg["tool_calls"]
                ],
            })
        else:
            out.append({"role": role, "content": msg.get("content", "")})
    return out


def parse_openai_response(payload: Any, model: str) -> ProviderResponse:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("malformed", "response contained no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        text = json.dumps(text)
    calls: list[ToolCall] = []
    for index, tc in enumerate(message.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args, error = parse_arguments(fn.get("arguments") or "{}")
        calls.append(ToolCall(
            id=str(tc.get("id") or f"call_{index}"),
            name=str(fn.get("name") or ""),
            arguments=args,
            parse_error=error,
        ))
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cost = usage.get("cost")
    cost_value = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return ProviderResponse(
        text=text,
        tool_calls=calls,
        usage=Usage(
            input_tokens=as_int(usage.get("prompt_tokens")),
            output_tokens=as_int(usage.get("completion_tokens")),
            cached_tokens=as_int(details.get("cached_tokens")),
            cost_usd=cost_value,
        ),
        finish_reason=str(choice.get("finish_reason") or ""),
        model=str(payload.get("model") or model),
    )


class OpenAICompatibleProvider(Provider):
    kind = "openai_compatible"
    default_base_url = ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def list_models(self) -> list[ModelInfo]:
        payload = self._call("GET", f"{self.base_url}/models", self._headers())
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ProviderError("malformed", "model list response has no 'data' array")
        models = [self.parse_model(m) for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)]
        self.remember_models(models)
        return models

    def parse_model(self, raw: dict[str, Any]) -> ModelInfo:
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        top = raw.get("top_provider") if isinstance(raw.get("top_provider"), dict) else {}
        context = raw.get("context_length") or top.get("context_length")
        params = raw.get("supported_parameters")
        supports_tools = ("tools" in params) if isinstance(params, list) and params else None
        arch = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
        modalities = arch.get("input_modalities")
        vision = ("image" in modalities) if isinstance(modalities, list) and modalities else None
        return ModelInfo(
            id=raw["id"],
            name=str(raw.get("name") or raw["id"]),
            provider=self.name,
            context_length=as_int(context) or None,
            prompt_price_per_m=_per_million(pricing.get("prompt")),
            completion_price_per_m=_per_million(pricing.get("completion")),
            supports_tools=supports_tools,
            supports_vision=vision,
            description=str(raw.get("description") or "")[:300],
        )

    def generate(self, messages, *, model, tools=None, system="", max_tokens=2048, timeout=None):
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages, system),
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["parameters"],
                }}
                for t in tools
            ]
            body["tool_choice"] = "auto"
        payload = self._call("POST", f"{self.base_url}/chat/completions", self._headers(), body, timeout)
        return parse_openai_response(payload, model)


class OpenRouterProvider(OpenAICompatibleProvider):
    kind = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers.update({"HTTP-Referer": "https://github.com/workflaskcode", "X-Title": "WorkFlaskCode"})
        return headers


class OpenAIProvider(OpenAICompatibleProvider):
    kind = "openai"
    default_base_url = "https://api.openai.com/v1"
