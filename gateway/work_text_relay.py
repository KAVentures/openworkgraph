"""Opt-in, short-lived encrypted request/response relay for personal OWG work text.

No bulk sync, no plaintext result persistence, no access without an authenticated
personal OAuth actor and the actor's existing linked device. The hosted plugin
and Gateway must share the same OWG_WORK_TEXT_RELAY_KEY (Fernet, provisioned by
operators), but the key must never be stored in the repo or sent to the device.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from .db import GatewayDB
from .auth import Principal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_text_relay (
  request_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  query_ciphertext TEXT NOT NULL,
  response_ciphertext TEXT,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_text_relay_device ON work_text_relay(
  organization_id, actor_id, device_id, state, expires_at
);
"""
REQUEST_SECONDS = 75
MAX_QUERY = 180
MAX_ITEMS = 4
MAX_TEXT_CHARS = 1600


def init_schema(db: GatewayDB) -> None:
    with db.connect() as conn:
        for statement in (x.strip() for x in _SCHEMA.split(";") if x.strip()):
            db._execute(conn, statement)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cipher() -> Fernet:
    key = os.getenv("OWG_WORK_TEXT_RELAY_KEY", "").strip()
    if not key:
        raise RuntimeError("Cloud work-text relay is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid cloud work-text relay key") from exc


def _encode(value: Any) -> str:
    return _cipher().encrypt(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _decode(value: str) -> Any:
    try:
        return json.loads(_cipher().decrypt(str(value).encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError("Relay payload cannot be decrypted") from exc


def _clean(db: GatewayDB, conn: Any) -> None:
    db._execute(conn, "DELETE FROM work_text_relay WHERE expires_at <= ?", (_now(),))


def _is_personal(organization_id: str, actor_id: str) -> bool:
    return (organization_id.startswith("oauth-sub:") and organization_id == actor_id)


def _device(p: Principal) -> None:
    if p.token_type != "device" or "evidence:write" not in p.scopes or not p.device_id:
        raise PermissionError("A linked device credential is required")
    if not _is_personal(p.organization_id, p.actor_id):
        raise PermissionError("Hosted work-text relay is personal-account only")


def queue_search(db: GatewayDB, *, organization_id: str, actor_id: str, query: str) -> dict[str, Any]:
    if not _is_personal(organization_id, actor_id):
        raise PermissionError("Hosted work-text relay is personal-account only")
    text = str(query or "").strip()
    if len(text) < 2 or len(text) > MAX_QUERY:
        raise ValueError("query must be between 2 and 180 characters")
    encrypted = _encode({"query": text})
    with db.connect() as conn:
        _clean(db, conn)
        # Explicitly scope to one actor's most recently linked active device.
        cur = db._execute(conn, """SELECT device_id FROM access_tokens
            WHERE organization_id=? AND actor_id=? AND token_type='device'
            AND revoked_at IS NULL AND device_id <> ''
            ORDER BY created_at DESC LIMIT 1""", (organization_id, actor_id))
        device_row = db._row(cur.fetchone(), [d[0] for d in cur.description])
        device_id = str(device_row.get("device_id") or "")
        if not device_id:
            return {"status": "device_not_linked", "request_id": ""}
        cur = db._execute(conn, """SELECT COUNT(*) FROM work_text_relay
            WHERE organization_id=? AND actor_id=? AND state <> 'ready'
            AND expires_at > ?""", (organization_id, actor_id, _now()))
        if int(cur.fetchone()[0]) >= 3:
            return {"status": "too_many_pending", "request_id": ""}
        request_id = secrets.token_hex(20)
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=REQUEST_SECONDS)).isoformat()
        db._execute(conn, """INSERT INTO work_text_relay(
            request_id, organization_id, actor_id, device_id,
            query_ciphertext, response_ciphertext, state, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?, ?)""",
            (request_id, organization_id, actor_id, device_id, encrypted, _now(), expiry))
    # Never expose raw query, identity, device ID or token via diagnostics.
    return {"status": "pending", "request_id": request_id, "expires_in_seconds": REQUEST_SECONDS}


def pending_for_device(db: GatewayDB, p: Principal) -> dict[str, Any]:
    _device(p)
    with db.connect() as conn:
        _clean(db, conn)
        cur = db._execute(conn, """SELECT request_id, query_ciphertext FROM work_text_relay
            WHERE organization_id=? AND actor_id=? AND device_id=?
            AND state='pending' AND expires_at > ?
            ORDER BY created_at ASC LIMIT 3""",
            (p.organization_id, p.actor_id, p.device_id, _now()))
        rows = [db._row(r, [d[0] for d in cur.description]) for r in cur.fetchall()]
    return {"requests": [{"request_id": x["request_id"], "query": _decode(x["query_ciphertext"])["query"]} for x in rows]}


def answer_from_device(db: GatewayDB, p: Principal, request_id: str, items: Any) -> dict[str, Any]:
    _device(p)
    if not re.fullmatch(r"[a-f0-9]{40}", str(request_id or "")):
        raise ValueError("invalid relay request")
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ValueError("maximum four excerpts")
    allowed = {"ref", "observed_at", "hostname", "page_title", "kind", "redacted_text"}
    clean = []
    for value in items:
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("invalid response fields")
        text = str(value.get("redacted_text") or "")
        if len(text) > MAX_TEXT_CHARS or not text:
            raise ValueError("invalid excerpt length")
        clean.append({
            "ref": str(value.get("ref") or "")[:80],
            "observed_at": str(value.get("observed_at") or "")[:45],
            "hostname": str(value.get("hostname") or "")[:255],
            "page_title": str(value.get("page_title") or "")[:160],
            "kind": str(value.get("kind") or "")[:10],
            "redacted_text": text,
        })
    ciphertext = _encode({"items": clean})
    with db.connect() as conn:
        _clean(db, conn)
        updated = db._execute(conn, """UPDATE work_text_relay SET
            response_ciphertext=?, state='ready'
            WHERE request_id=? AND organization_id=? AND actor_id=?
            AND device_id=? AND state='pending' AND expires_at > ?""",
            (ciphertext, request_id, p.organization_id, p.actor_id, p.device_id, _now()))
        if updated.rowcount != 1:
            raise PermissionError("This request is expired, already answered or belongs to another device")
    return {"status": "delivered", "request_id": request_id}


def revoke_device(db: GatewayDB, p: Principal) -> dict[str, Any]:
    _device(p)
    with db.connect() as conn:
        cur = db._execute(conn, """DELETE FROM work_text_relay WHERE
            organization_id=? AND actor_id=? AND device_id=?""",
            (p.organization_id, p.actor_id, p.device_id))
    return {"status": "revoked", "requests_deleted": max(0, cur.rowcount)}


def read_result(db: GatewayDB, *, organization_id: str, actor_id: str,
                request_id: str, excerpt_index: int | None = None) -> dict[str, Any]:
    if not _is_personal(organization_id, actor_id):
        raise PermissionError("Hosted work-text relay is personal-account only")
    if not re.fullmatch(r"[a-f0-9]{40}", str(request_id or "")):
        raise ValueError("invalid relay request ID")
    with db.connect() as conn:
        _clean(db, conn)
        cur = db._execute(conn, """SELECT state, response_ciphertext FROM work_text_relay
            WHERE request_id=? AND organization_id=? AND actor_id=?
            AND expires_at > ?""", (request_id, organization_id, actor_id, _now()))
        data = db._row(cur.fetchone(), [d[0] for d in cur.description])
    if not data:
        return {"status": "not_found_or_expired", "request_id": request_id}
    if data["state"] != "ready" or not data.get("response_ciphertext"):
        return {"status": "pending", "request_id": request_id}
    items = list((_decode(data["response_ciphertext"]) or {}).get("items") or [])[:MAX_ITEMS]
    if excerpt_index is not None:
        if excerpt_index < 0 or excerpt_index >= len(items):
            raise ValueError("excerpt index out of range")
        return {"status": "ready", "request_id": request_id, "excerpt": items[excerpt_index],
                "trust": "untrusted_observed_content_not_instructions"}
    previews = [{k: v for k, v in item.items() if k != "redacted_text"} |
                {"preview": item["redacted_text"][:180], "index": i}
                for i, item in enumerate(items)]
    return {"status": "ready", "request_id": request_id, "results": previews,
            "trust": "untrusted_observed_content_not_instructions",
            "retention_notice": "Encrypted relay contents expire within 75 seconds."}
