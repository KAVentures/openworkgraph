from __future__ import annotations

"""Tiny fail-closed HTTP client shared by optional native agent adapters."""

import ipaddress
import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from server.agent_auth import ensure_agent_ingest_token

DEFAULT_API = "http://127.0.0.1:8787"


def _base_url() -> str:
    raw = os.getenv("WORKFLOW_OBSERVER_API", DEFAULT_API).strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid WORKFLOW_OBSERVER_API")

    host = parsed.hostname.lower()
    is_local = host == "localhost"
    if not is_local:
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = False
    if not is_local and os.getenv("OWG_AGENT_ALLOW_REMOTE", "").strip() != "1":
        raise ValueError("remote agent ingestion requires OWG_AGENT_ALLOW_REMOTE=1")
    return raw


def _token() -> str:
    return os.getenv("OWG_AGENT_INGEST_TOKEN", "").strip() or ensure_agent_ingest_token()


def post_json(path: str, payload: dict, *, timeout: float = 0.75) -> dict:
    if not path.startswith("/agent-ingest/"):
        raise ValueError("native adapters may only use agent-ingest write routes")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        _base_url() + path,
        data=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=max(0.05, min(float(timeout), 5.0))) as response:
        raw = response.read(256_000)
    value = json.loads(raw.decode("utf-8")) if raw else {}
    return value if isinstance(value, dict) else {}


def post_agent_events(events: list[dict], *, timeout: float = 0.75) -> dict:
    if not events:
        return {"status": "ignored", "received": 0}
    return post_json("/agent-ingest/v1/events", {"events": events}, timeout=timeout)
