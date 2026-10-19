"""HTTP transport built on urllib (no third-party dependency)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from .base import ProviderError


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any | None,
        timeout: int,
    ) -> tuple[int, Any, dict[str, str]]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        all_headers = {"Accept": "application/json", "User-Agent": "WorkFlaskCode/0.1"}
        if data is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers)
        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = resp.status
                resp_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderError("timeout", f"request timed out after {timeout}s") from exc
            raise ProviderError("network", f"network error: {exc.reason}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError("timeout", f"request timed out after {timeout}s") from exc

        text = raw.decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            payload = text
        return status, payload, {k.lower(): v for k, v in resp_headers.items()}
