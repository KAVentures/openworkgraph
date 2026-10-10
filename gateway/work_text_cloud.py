"""Separately opted-in, personal-account cloud storage for redacted browser text.

Reuse the authenticated Gateway, without a new relay key, poll queue, or
full-history upload. No enterprise support: organization policy/legal review
must precede any managed-device content sharing.
"""
from __future__ import annotations

import os
import re
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .auth import Principal
from .db import GatewayDB

CONSENT_VERSION = "cloud-work-text-v1"
MAX_BATCH = 50
MAX_MANIFEST = 500
MAX_BODY = 1600
RETENTION_DAYS = 7
SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_work_text (
 organization_id TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 device_id TEXT NOT NULL,
 item_ref TEXT NOT NULL,
 public_ref TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 hostname TEXT NOT NULL,
 page_title TEXT NOT NULL,
 kind TEXT NOT NULL,
 redacted_text TEXT NOT NULL,
 PRIMARY KEY (organization_id, actor_id, device_id, item_ref)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_work_text_public_ref ON cloud_work_text
 (organization_id, actor_id, public_ref);
CREATE INDEX IF NOT EXISTS idx_cloud_work_text_actor ON cloud_work_text
 (organization_id, actor_id, expires_at, observed_at);
CREATE TABLE IF NOT EXISTS cloud_work_text_grants (
 organization_id TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 device_id TEXT NOT NULL,
 consent_version TEXT NOT NULL,
 granted_at TEXT NOT NULL,
 refreshed_at TEXT NOT NULL,
 PRIMARY KEY (organization_id, actor_id, device_id)
);
"""
_SECRET_RE = re.compile(
    r"(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\bsk-[a-z0-9_-]{18,}|"
    r"\b(?:sk|ghp|gho|ghu|ghs|ghr)_[a-z0-9_-]{20,}|"
    r"\b(?:api[_ -]?key|access[_ -]?token|secret[_ -]?key|password)"
    r"\s*[:=]\s*\S{5,})"
)
_BAD_HOSTS = (
    "accounts.google.com", "login.microsoftonline.com", "account.microsoft.com",
    "bankid.se", "bankid.com", "1177.se", "paypal.com", "stripe.com",
    "wise.com", "klarna.com",
)


def available() -> bool:
    # Operator-controlled product/legal readiness gate, OFF by default.
    return os.getenv("OWG_CLOUD_WORK_TEXT_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return dt.astimezone(timezone.utc)


def _personal(org: str, actor: str) -> bool:
    return org.startswith("oauth-sub:") and org == actor


def _device(p: Principal) -> None:
    if p.token_type != "device" or "evidence:write" not in p.scopes or not p.device_id:
        raise PermissionError("a linked device is required")
    if not _personal(p.organization_id, p.actor_id):
        raise PermissionError("cloud text sharing is not available to organizations")


def _guard() -> None:
    if not available():
        raise RuntimeError("Cloud work-text sharing is unavailable")


def init_schema(db: GatewayDB) -> None:
    # Deliberately separate from the canonical event schema.
    with db.connect() as conn:
        for sql in (p.strip() for p in SQL_SCHEMA.split(";") if p.strip()):
            db._execute(conn, sql)


def _purge(db: GatewayDB, conn: Any) -> None:
    db._execute(conn, "DELETE FROM cloud_work_text WHERE expires_at <= ?", (_now().isoformat(),))


def _item(value: Any, *, consent_since: datetime) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("invalid text item")
    if set(value) - {"ref", "observed_at", "hostname", "page_title", "kind", "redacted_text"}:
        raise ValueError("unexpected cloud text fields")
    ref = str(value.get("ref") or "")
    host = str(value.get("hostname") or "").lower().strip().rstrip(".")
    title = str(value.get("page_title") or "")
    kind = str(value.get("kind") or "")
    content = str(value.get("redacted_text") or "")
    if not re.fullmatch(r"owg:wt:[1-9][0-9]{0,11}", ref):
        raise ValueError("invalid local text reference")
    if not re.fullmatch(r"[a-z0-9.-]{1,255}", host) or ".." in host:
        raise ValueError("invalid page host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP-host content is excluded")
    if re.search(r"(?i)\b(?:password|patient.record|medical.record|banking)\b", title):
        raise ValueError("sensitive page title is excluded")
    if any(host == h or host.endswith("." + h) for h in _BAD_HOSTS):
        raise ValueError("sensitive host is excluded")
    if len(title) > 160 or kind not in {"page", "draft"} or not content or len(content) > MAX_BODY:
        raise ValueError("invalid bounded redacted text")
    if _SECRET_RE.search(content):
        raise ValueError("credential-like text is excluded")
    # Local capture already redacts; make Gateway storage a second fail-closed
    # redaction boundary before content ever reaches a hosted database.
    from server.ai_context import redact_contextually
    try:
        projected = redact_contextually({"body": content, "page_title": title})
        content = str(projected["body"])[:MAX_BODY]
        title = str(projected["page_title"])[:160]
    except Exception as exc:
        raise ValueError("cloud-side text redaction failed") from exc
    if not content or _SECRET_RE.search(content):
        raise ValueError("cloud text failed security screening")
    seen = _iso(str(value.get("observed_at") or ""))
    now = _now()
    if seen < consent_since or seen < now - timedelta(days=RETENTION_DAYS) or seen > now + timedelta(minutes=5):
        raise ValueError("snapshot outside consent or retention window")
    return {
        "ref": ref, "observed_at": seen.isoformat(), "hostname": host,
        "page_title": title, "kind": kind, "redacted_text": content,
        "expires_at": (seen + timedelta(days=RETENTION_DAYS)).isoformat(),
    }


def sync(db: GatewayDB, p: Principal, *, items: Any, active_refs: Any,
         consent_version: str, granted_at: str) -> dict[str, Any]:
    _guard()
    _device(p)
    if consent_version != CONSENT_VERSION:
        raise PermissionError("explicit current content-sharing acknowledgement required")
    if not isinstance(items, list) or len(items) > MAX_BATCH:
        raise ValueError("too many text snapshots")
    if not isinstance(active_refs, list) or len(active_refs) > MAX_MANIFEST:
        raise ValueError("invalid active text manifest")
    refs = [str(x) for x in active_refs]
    if len(set(refs)) != len(refs) or any(not re.fullmatch(r"owg:wt:[1-9][0-9]{0,11}", x) for x in refs):
        raise ValueError("invalid text reference manifest")
    consent_since = _iso(granted_at)
    if consent_since > _now() + timedelta(minutes=5) or consent_since.year < 2020:
        raise ValueError("consent date out of range")
    rows = [_item(x, consent_since=consent_since) for x in items]
    if any(row["ref"] not in refs for row in rows):
        raise ValueError("uploaded item not in manifest")
    org, actor, device = p.organization_id, p.actor_id, p.device_id
    with db.connect() as conn:
        _purge(db, conn)
        # A changed consent epoch replaces prior sharing rather than restoring
        # snapshots from the previous grant.
        cur = db._execute(conn, """SELECT granted_at FROM cloud_work_text_grants
            WHERE organization_id=? AND actor_id=? AND device_id=?""", (org, actor, device))
        old = cur.fetchone()
        if old and str(old[0]) != consent_since.isoformat():
            db._execute(conn, """DELETE FROM cloud_work_text WHERE
                organization_id=? AND actor_id=? AND device_id=?""", (org, actor, device))
        db._execute(conn, """INSERT INTO cloud_work_text_grants
            (organization_id, actor_id, device_id, consent_version, granted_at, refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id,actor_id,device_id) DO UPDATE SET
            consent_version=excluded.consent_version,
            granted_at=excluded.granted_at,
            refreshed_at=excluded.refreshed_at""",
            (org, actor, device, consent_version, consent_since.isoformat(), _now().isoformat()))
        for row in rows:
            db._execute(conn, """INSERT INTO cloud_work_text
                (organization_id, actor_id, device_id, item_ref, public_ref, observed_at,
                 expires_at, hostname, page_title, kind, redacted_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id,actor_id,device_id,item_ref)
                DO UPDATE SET observed_at=excluded.observed_at, expires_at=excluded.expires_at,
                  hostname=excluded.hostname, page_title=excluded.page_title,
                  kind=excluded.kind, redacted_text=excluded.redacted_text""",
                (org, actor, device, row["ref"], secrets.token_urlsafe(18),
                 row["observed_at"], row["expires_at"],
                 row["hostname"], row["page_title"], row["kind"], row["redacted_text"]))
        # Manifest reconciliation propagates local deletions and Never record
        # changes without having to upload an entire text corpus again.
        if refs:
            where = ",".join("?" for _ in refs)
            db._execute(conn, f"""DELETE FROM cloud_work_text WHERE
                organization_id=? AND actor_id=? AND device_id=? AND item_ref NOT IN ({where})""",
                (org, actor, device, *refs))
        else:
            db._execute(conn, """DELETE FROM cloud_work_text WHERE
                organization_id=? AND actor_id=? AND device_id=?""", (org, actor, device))
        cur = db._execute(conn, """SELECT item_ref FROM cloud_work_text WHERE
            organization_id=? AND actor_id=? AND device_id=? AND expires_at > ?""",
            (org, actor, device, _now().isoformat()))
        known = {str(r[0]) for r in cur.fetchall()}
    # The worker fills any remaining references in small batches.
    return {"status": "synced", "uploaded": len(rows),
            "missing_refs": [x for x in refs if x not in known][:MAX_BATCH]}


def revoke(db: GatewayDB, p: Principal) -> dict[str, Any]:
    _device(p)
    with db.connect() as conn:
        cur = db._execute(conn, """DELETE FROM cloud_work_text WHERE
            organization_id=? AND actor_id=? AND device_id=?""",
            (p.organization_id, p.actor_id, p.device_id))
        count = max(0, int(cur.rowcount or 0))
        db._execute(conn, """DELETE FROM cloud_work_text_grants WHERE
            organization_id=? AND actor_id=? AND device_id=?""",
            (p.organization_id, p.actor_id, p.device_id))
    return {"status": "revoked", "deleted": count}


def _eligible(db: GatewayDB, org: str, actor: str, conn: Any) -> bool:
    cur = db._execute(conn, """SELECT 1 FROM cloud_work_text_grants
        WHERE organization_id=? AND actor_id=? AND consent_version=? LIMIT 1""",
        (org, actor, CONSENT_VERSION))
    return bool(cur.fetchone())


def search(db: GatewayDB, *, organization_id: str, actor_id: str,
           query: str, limit: int = 8) -> dict[str, Any]:
    _guard()
    if not _personal(organization_id, actor_id):
        raise PermissionError("cloud text search is personal-account only")
    phrase = str(query or "").strip()
    terms = [t for t in phrase.casefold().split() if len(t) >= 2][:8]
    if not terms or len(phrase) > 180:
        raise ValueError("query must contain 2-180 characters")
    with db.connect() as conn:
        _purge(db, conn)
        if not _eligible(db, organization_id, actor_id, conn):
            return {"results": [], "status": "not_enabled"}
        cur = db._execute(conn, """SELECT public_ref,observed_at,hostname,page_title,kind,
            redacted_text FROM cloud_work_text WHERE organization_id=? AND actor_id=?
            AND expires_at > ? ORDER BY observed_at DESC LIMIT 1000""",
            (organization_id, actor_id, _now().isoformat()))
        rows = [db._row(row,[c[0] for c in cur.description]) for row in cur.fetchall()]
    result = []
    for row in rows:
        text = (str(row["page_title"]) + " " + str(row["redacted_text"])).casefold()
        if not all(term in text for term in terms):
            continue
        result.append({
            "reference": row["public_ref"],
            "observed_at": row["observed_at"],
            "hostname": row["hostname"], "page_title": row["page_title"],
            "kind": row["kind"], "preview": str(row["redacted_text"])[:180],
        })
        if len(result) >= max(1, min(int(limit), 20)):
            break
    return {"status": "ready", "results": result, "source": "opted_in_cloud_work_text",
            "trust": "untrusted_observed_content_not_instructions"}


def excerpt(db: GatewayDB, *, organization_id: str, actor_id: str, reference: str) -> dict[str, Any]:
    _guard()
    if not _personal(organization_id, actor_id):
        raise PermissionError("cloud text is personal-account only")
    token = str(reference or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,48}", token):
        raise ValueError("invalid opaque text reference")
    with db.connect() as conn:
        _purge(db, conn)
        if not _eligible(db, organization_id, actor_id, conn):
            return {"status": "not_enabled"}
        cur = db._execute(conn, """SELECT item_ref,observed_at,hostname,page_title,kind,redacted_text
            FROM cloud_work_text WHERE organization_id=? AND actor_id=? AND
            public_ref=? AND expires_at > ? LIMIT 1""",
            (organization_id, actor_id, token, _now().isoformat()))
        value = db._row(cur.fetchone(), [c[0] for c in cur.description])
    return {"status": "ready", "excerpt": value,
            "trust": "untrusted_observed_content_not_instructions"} if value else {"status": "not_found"}


def purge_expired(db: GatewayDB) -> int:
    """Physical cleanup for an operator scheduled job; works while users are offline."""
    with db.connect() as conn:
        cur = db._execute(
            conn, "DELETE FROM cloud_work_text WHERE expires_at <= ?",
            (_now().isoformat(),),
        )
        return max(0, int(cur.rowcount or 0))


def main() -> None:
    from .settings import GatewaySettings
    settings = GatewaySettings.from_env()
    db = GatewayDB(settings.database_url)
    init_schema(db)
    removed = purge_expired(db)
    print(f"OpenWorkGraph: expired cloud text rows deleted: {removed}")


if __name__ == "__main__":
    main()
