"""Model routing: primary model, bounded retries, fallback chain, budgets, usage records."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import ConfigStore
from .credentials import CredentialStore
from .providers import ProviderConfig, ProviderError, ProviderResponse, build_provider
from .state import StateStore


class Cancelled(Exception):
    """The user cancelled the operation."""


class NotConfigured(Exception):
    """A provider or model is missing from configuration."""


class BudgetExceeded(Exception):
    """The configured estimated-cost limit for this session was reached."""


@dataclass(frozen=True)
class Target:
    provider: str
    model: str

    def label(self) -> str:
        return f"{self.provider}::{self.model}"


class AllTargetsFailed(Exception):
    def __init__(self, failures: list[tuple[Target | None, str, str]]) -> None:
        self.failures = failures
        self.kind = failures[-1][1] if failures else "unknown"
        detail = "; ".join(
            f"{(t.label() if t else 'config')}: {kind}: {msg}" for t, kind, msg in failures
        )
        super().__init__(detail or "no model targets available")


def parse_target(spec: str, default_provider: str) -> Target:
    """'model-id' uses the default provider; 'provider::model-id' selects another."""
    if "::" in spec:
        provider, model = spec.split("::", 1)
        return Target(provider.strip(), model.strip())
    return Target(default_provider, spec.strip())


def backoff_delay(attempt: int, retry_after: float | None = None, *, base: float = 1.0,
                  cap: float = 30.0, rand: Callable[[], float] = random.random) -> float:
    if retry_after is not None:
        return min(max(float(retry_after), 0.0), 120.0)
    delay = min(cap, base * (2 ** attempt))
    return delay * (0.5 + rand() / 2)


def call_with_retry(fn: Callable[[], Any], max_retries: int, *, sleep: Callable[[float], None],
                    on_retry: Callable[[int, ProviderError, float], None] | None = None) -> Any:
    attempt = 0
    while True:
        try:
            return fn()
        except ProviderError as err:
            if not err.retryable or attempt >= max_retries:
                raise
            delay = backoff_delay(attempt, err.retry_after)
            if on_retry is not None:
                on_retry(attempt + 1, err, delay)
            sleep(delay)
            attempt += 1


def _sleep_cancellable(cancel: threading.Event, seconds: float) -> None:
    if cancel.wait(seconds):
        raise Cancelled()


class ModelRouter:
    def __init__(self, config: ConfigStore, creds: CredentialStore, state: StateStore,
                 transport=None) -> None:
        self.config = config
        self.creds = creds
        self.state = state
        self.transport = transport
        self.on_retry: Callable[[str], None] | None = None
        self._providers: dict[str, Any] = {}
        self._lock = threading.Lock()

    def provider(self, name: str):
        with self._lock:
            if name in self._providers:
                return self._providers[name]
            entry = self.config.providers().get(name)
            if not entry:
                raise NotConfigured(f"provider '{name}' is not configured; run workflaskcode setup")
            key = self.creds.get(name)
            if not key:
                raise NotConfigured(f"no API key for '{name}'; set its environment variable or run setup")
            cfg = ProviderConfig(
                name=name, kind=entry["kind"], model=entry.get("model", ""),
                base_url=entry.get("base_url", ""), enabled=entry.get("enabled", True),
            )
            timeout = self.config.data["limits"]["request_timeout_s"]
            prov = build_provider(cfg, key, transport=self.transport, timeout=timeout)
            self._providers[name] = prov
            return prov

    def invalidate(self) -> None:
        with self._lock:
            self._providers.clear()

    def targets(self, role: str) -> list[Target]:
        general = self.config.data["general"]
        default_provider = general["default_provider"]
        spec = general["chat_model"] if role == "chat" and general["chat_model"] else general["coding_model"]
        if not spec:
            raise NotConfigured("no model selected; run workflaskcode setup or use @changemodel")
        if not default_provider and "::" not in spec:
            raise NotConfigured("no default provider configured")
        targets = [parse_target(spec, default_provider)]
        targets += [parse_target(fb, default_provider) for fb in general["fallback_models"]]
        return targets

    def _check_budget(self, session_id: str | None) -> None:
        limit = float(self.config.data["limits"]["max_estimated_cost_usd"])
        if limit > 0 and session_id:
            spent = self.state.session_cost(session_id)
            if spent >= limit:
                raise BudgetExceeded(f"estimated spend ${spent:.4f} reached the ${limit:.2f} limit")

    def generate(self, role: str, messages: list[dict[str, Any]], *, system: str = "",
                 tools: list[dict[str, Any]] | None = None, cancel: threading.Event | None = None,
                 session_id: str | None = None, max_tokens: int = 2048,
                 ) -> tuple[ProviderResponse, Target]:
        cancel = cancel or threading.Event()
        limits = self.config.data["limits"]
        failures: list[tuple[Target | None, str, str]] = []

        for target in self.targets(role):
            if cancel.is_set():
                raise Cancelled()
            try:
                provider = self.provider(target.provider)
            except NotConfigured as exc:
                failures.append((target, "not_configured", str(exc)))
                continue
            self._check_budget(session_id)

            def attempt(provider=provider, target=target) -> ProviderResponse:
                if cancel.is_set():
                    raise Cancelled()
                return provider.generate(messages, model=target.model, tools=tools,
                                         system=system, max_tokens=max_tokens)

            def announce(n: int, err: ProviderError, delay: float, target=target) -> None:
                if self.on_retry:
                    self.on_retry(f"{target.label()} {err.kind}; retry {n} in {delay:.1f}s")

            started = time.monotonic()
            try:
                response = call_with_retry(
                    attempt, limits["max_retries"],
                    sleep=lambda d: _sleep_cancellable(cancel, d),
                    on_retry=announce,
                )
            except ProviderError as err:
                elapsed = int((time.monotonic() - started) * 1000)
                self.state.add_usage(session_id, target.provider, target.model, None, elapsed,
                                     False, err.kind)
                failures.append((target, err.kind, err.message))
                if err.fallback_eligible:
                    continue
                raise AllTargetsFailed(failures) from err

            elapsed = int((time.monotonic() - started) * 1000)
            self.state.add_usage(session_id, target.provider, target.model, response.usage,
                                 elapsed, True, None)
            return response, target

        raise AllTargetsFailed(failures)
