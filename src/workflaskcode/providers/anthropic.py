"""Anthropic Messages API adapter (native format, not OpenAI-compatible)."""

from __future__ import annotations

from typing import Any

from .base import ModelInfo, Provider, ProviderError, ProviderResponse, ToolCall, Usage, as_int

API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    kind = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": API_VERSION}

    def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        after: str | None = None
        for _ in range(20):  # bounded pagination
            url = f"{self.base_url}/models?limit=100"
            if after:
                url += f"&after_id={after}"
            payload = self._call("GET", url, self._headers())
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ProviderError("malformed", "model list response has no 'data' array")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(ModelInfo(
                        id=item["id"],
                        name=str(item.get("display_name") or item["id"]),
                        provider=self.name,
                        supports_tools=True,
                        supports_streaming=True,
                        description="Anthropic Messages API model",
                    ))
            if not payload.get("has_more") or not payload.get("last_id"):
                break
            after = str(payload["last_id"])
        self.remember_models(models)
        return models

    def generate(self, messages, *, model, tools=None, system="", max_tokens=2048, timeout=None):
        system_parts = [system] if system else []
        wire: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            if role == "system":
                system_parts.append(msg["content"])
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}
                if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block]})
            elif role == "assistant" and msg.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if msg.get("content"):
                    blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                wire.append({"role": "assistant", "content": blocks})
            else:
                wire.append({"role": role, "content": msg.get("content", "")})

        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": wire}
        joined_system = "\n\n".join(p for p in system_parts if p)
        if joined_system:
            body["system"] = joined_system
        if tools:
            body["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ]
        payload = self._call("POST", f"{self.base_url}/messages", self._headers(), body, timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            raise ProviderError("malformed", "messages response has no content blocks")

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in payload["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                calls.append(ToolCall(id=str(block.get("id", "")), name=str(block.get("name", "")),
                                      arguments=arguments))
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResponse(
            text="".join(texts),
            tool_calls=calls,
            usage=Usage(
                input_tokens=as_int(usage.get("input_tokens")),
                output_tokens=as_int(usage.get("output_tokens")),
                cached_tokens=as_int(usage.get("cache_read_input_tokens")),
                cost_usd=None,
            ),
            finish_reason=str(payload.get("stop_reason") or ""),
            model=str(payload.get("model") or model),
        )
