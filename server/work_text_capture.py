from __future__ import annotations

"""Optional browser work text, separate from canonical structural evidence.

No calls in this module write to the event store, outbox, browser retry queue,
agent session store or Gateway. The user must enable capture explicitly; AI
reads require a second grant and the existing master AI access switch.
"""

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import DATA_DIR

_POLICY = DATA_DIR / "work_text_policy.json"
_DB = DATA_DIR / "work_text_local.db"
_LOCK = threading.RLock()
MAX_CHARS = 4000
MAX_STORED_ROWS = 500
MAX_RETENTION_DAYS = 30
_DEFAULT = {"capture_enabled": False, "ai_read_enabled": False, "cloud_read_enabled": False, "retention_days": 7}

_SENSITIVE_HOST_PARTS = (
    "accounts.google.com", "login.microsoftonline.com", "account.microsoft.com",
    "bankid.se", "bankid.com", "1177.se", "paypal.com", "stripe.com",
    "wise.com", "klarna.com",
)
_SENSITIVE_PATH = re.compile(
    r"(?:^|/)(?:oauth|authorize|signin|sign-in|login|password|passcode|"
    r"reset|recover|recovery|token|verify|verification|callback|mfa|2fa|"
    r"payment|checkout|billing|banking|health|patient|medical)(?:/|$)", re.I
)
_SENSITIVE_TITLE = re.compile(
    r"\b(?:password|one.time.code|security.code|payment.card|"
    r"banking|patient.record|medical.record|health.record)\b", re.I
)
_SECRETS = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9_-]{18,}|(?:sk|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_-]{20,})\b|"
    r"\b(?:api[_ -]?key|access[_ -]?token|secret[_ -]?key|"
    r"password|passwd|passcode|otp|verification[_ -]?code)"
    r"\s*[:=]\s*\S{5,})", re.I
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _policy(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    out = dict(_DEFAULT)
    for key in ("capture_enabled", "ai_read_enabled", "cloud_read_enabled"):
        if key in source:
            out[key] = bool(source[key])
    try:
        days = int(source.get("retention_days", out["retention_days"]))
    except (TypeError, ValueError):
        days = 7
    out["retention_days"] = max(1, min(MAX_RETENTION_DAYS, days))
    if not out["capture_enabled"]:
        out["ai_read_enabled"] = False
    if not out["ai_read_enabled"]:
        out["cloud_read_enabled"] = False
    return out


def get_policy() -> dict[str, Any]:
    try:
        data = json.loads(_POLICY.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return _policy(data)


def _save_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="work-text-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(_DB), timeout=3)
    # This database holds opted-in work content; do not rely on umask.
    # DELETE journal mode avoids a persistent plaintext WAL alongside the DB.
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA secure_delete=ON")
    try:
        os.chmod(_DB, 0o600)
    except OSError:
        if os.name != "nt":
            db.close()
            raise
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS work_text (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            hostname TEXT NOT NULL,
            page_title TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('page','draft')),
            redacted_text TEXT NOT NULL,
            digest TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS work_text_observed_idx ON work_text(observed_at)")
    return db


def _erase() -> None:
    if not _DB.exists():
        return
    with _conn() as db:
        db.execute("DELETE FROM work_text")
        db.commit()
        db.execute("VACUUM")  # best effort reclamation; filesystem backups may remain




def delete_range(since: str, until: str) -> int:
    """Delete opted-in browser text in a dashboard-requested UTC time range.

    Called by the existing Delete recorded activity control. Does not create a
    new text database merely to process a deletion, and never affects Gateway.
    """
    from shared.evidence_deletion import normalize_range
    start, end = normalize_range(since, until)
    # SQLite stores UTC timestamps. Normalize offsets before text comparison.
    start = datetime.fromisoformat(start).astimezone(timezone.utc).isoformat()
    end = datetime.fromisoformat(end).astimezone(timezone.utc).isoformat()
    with _LOCK:
        if not _DB.exists():
            return 0
        with _conn() as db:
            cursor = db.execute(
                "DELETE FROM work_text WHERE observed_at >= ? AND observed_at < ?",
                (start, end),
            )
            count = max(0, int(cursor.rowcount or 0))
            if count:
                db.commit()
                db.execute("VACUUM")
            return count


def set_policy(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Expected policy object")
    if set(payload) - {"capture_enabled", "ai_read_enabled", "cloud_read_enabled", "retention_days"}:
        raise ValueError("Unrecognized work-text settings")
    with _LOCK:
        previous = get_policy()
        next_policy = _policy({**previous, **payload})
        # Revocation always deletes old content; opting in never retroactively
        # scans existing pages or data from the disabled interval.
        _save_atomic(_POLICY, json.dumps(next_policy, indent=2) + "\n")
        if previous["capture_enabled"] and not next_policy["capture_enabled"]:
            _erase()
        else:
            _prune(days=next_policy["retention_days"])
        return next_policy


def _host_allowed(hostname: str, title: str, pathname: str) -> bool:
    from .capture_exclusions import current

    host = str(hostname).strip().lower().rstrip(".")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]", host, re.I):
        return False
    if ".." in host or host.endswith(".local") or host == "localhost":
        return False
    # A DNS-looking IP (including public, private, link-local, loopback) is
    # not a trusted work domain. IP-host capture needs a separate allowlist.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    if any(host == bad or host.endswith("." + bad) for bad in _SENSITIVE_HOST_PARTS):
        return False
    # The old segment-only regex missed /patients/42, /patient-portal and
    # /Login.aspx. Split on common URL filename separators instead, without
    # mistakenly blocking innocent words such as "author" or "tokenizer".
    path_parts = set(re.split(r"[/._-]+", str(pathname).casefold()))
    sensitive_parts = {
        "auth", "authenticate", "authorization", "oauth", "login", "signin",
        "sign", "password", "passcode", "reset", "recover", "recovery",
        "mfa", "2fa", "verification", "verify", "callback", "token",
        "billing", "payment", "payments", "checkout", "banking",
        "patient", "patients", "medical", "health", "ehr", "emr",
        "journal", "journals",
    }
    if path_parts & sensitive_parts or _SENSITIVE_PATH.search(str(pathname)) or _SENSITIVE_TITLE.search(str(title)):
        return False
    exclusions = current()
    if any(host == (pattern or "").lower().lstrip("*.") or
           host.endswith("." + (pattern or "").lower().lstrip("*."))
           for pattern in exclusions["hosts"] if pattern):
        return False
    if any(str(word).casefold() in str(title).casefold()
           for word in exclusions["title_words"] if word):
        return False
    return True


def _prune(*, days: int) -> None:
    if not _DB.exists():
        return
    oldest = (_now() - timedelta(days=max(1, min(days, MAX_RETENTION_DAYS)))).isoformat()
    with _conn() as db:
        db.execute("DELETE FROM work_text WHERE expires_at <= ? OR observed_at < ?",
                   (_now().isoformat(), oldest))
        db.execute("""DELETE FROM work_text WHERE row_id NOT IN
            (SELECT row_id FROM work_text ORDER BY row_id DESC LIMIT ?)""", (MAX_STORED_ROWS,))


def ingest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid capture payload")
    kind = str(payload.get("kind") or "")
    text = payload.get("text")
    hostname = str(payload.get("hostname") or "")
    title = str(payload.get("title") or "")[:240]
    path = str(payload.get("pathname") or "")[:512]
    if kind not in {"page", "draft"} or not isinstance(text, str) or not (1 <= len(text) <= MAX_CHARS):
        raise ValueError("Invalid work-content snapshot")
    if not path.startswith("/") or not _host_allowed(hostname, title, path):
        raise PermissionError("Page is excluded from work-content capture.")
    if _SECRETS.search(text):
        raise PermissionError("The snapshot may contain a credential.")
    # Apply deterministic privacy filtering BEFORE any persistence. If it fails,
    # the entire snapshot is dropped. Re-screen the filtered output as well.
    from .ai_context import redact_contextually
    try:
        safe = redact_contextually({"body": text, "page_title": title})
        body = str(safe["body"]).strip()
        safe_title = str(safe["page_title"]).strip()[:160]
    except Exception as exc:
        raise PermissionError("Local text protection failed.") from exc
    if not body or _SECRETS.search(body):
        raise PermissionError("Snapshot failed the content privacy check.")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = _now()
    with _LOCK:
        # Close the race where capture was turned off between filtering and the
        # insert. The shared recorder pause/stop gate is equally authoritative.
        from shared.capture_control import read_state
        policy = get_policy()
        if not policy["capture_enabled"] or read_state().get("state") != "recording":
            raise PermissionError("Work-content recording is disabled or paused.")
        if not _host_allowed(hostname, title, path):
            raise PermissionError("Page was excluded while preparing the snapshot.")
        expires_at = (now + timedelta(days=policy["retention_days"])).isoformat()
        with _conn() as db:
            prior = db.execute(
                "SELECT digest FROM work_text WHERE hostname = ? AND kind = ? ORDER BY row_id DESC LIMIT 1",
                (hostname.lower(), kind),
            ).fetchone()
            if prior and prior["digest"] == digest:
                return {"status": "duplicate_suppressed"}
            db.execute(
                """INSERT INTO work_text
                (observed_at, expires_at, hostname, page_title, kind, redacted_text, digest)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now.isoformat(), expires_at, hostname.lower(), safe_title, kind, body, digest),
            )
        _prune(days=policy["retention_days"])
    return {"status": "recorded_locally"}


def search_for_cloud(query: str, *, limit: int = 4) -> dict[str, Any]:
    """Provide small *redacted* excerpts only after three distinct grants.

    This is invoked by the local device's outbound relay poll, never by a
    direct model-supplied filesystem path or remote instruction. Local revoke
    fails closed and server-side Never record filters apply again.
    """
    from .ai_access import ai_access_enabled
    from shared.capture_control import read_state
    terms = [term for term in str(query or "").casefold().split() if len(term) >= 2][:8]
    if not terms or len(str(query)) > 180:
        raise ValueError("Search query must contain 2-180 characters")
    with _LOCK:
        policy = get_policy()
        if not (policy["capture_enabled"] and policy["ai_read_enabled"]
                and policy["cloud_read_enabled"] and ai_access_enabled()
                and read_state().get("state") == "recording"):
            raise PermissionError("Cloud text retrieval is disabled or recording is paused.")
        _prune(days=policy["retention_days"])
        if not _DB.exists():
            return {"items": [], "source": "local_opt_in_browser_text"}
        with _conn() as db:
            rows = db.execute(
                """SELECT row_id, observed_at, hostname, page_title, kind,
                          redacted_text FROM work_text ORDER BY row_id DESC LIMIT 500"""
            ).fetchall()
        results = []
        for row in rows:
            if not _host_allowed(str(row["hostname"]), str(row["page_title"]), "/"):
                continue
            haystack = (str(row["redacted_text"]) + " " + str(row["page_title"])).casefold()
            if not all(term in haystack for term in terms):
                continue
            results.append({
                "ref": f"owg:wt:{int(row['row_id'])}",
                "observed_at": row["observed_at"],
                "hostname": row["hostname"],
                "page_title": row["page_title"],
                "kind": row["kind"],
                "redacted_text": str(row["redacted_text"])[:1600],
            })
            if len(results) >= max(1, min(int(limit), 4)):
                break
        return {
            "items": results,
            "source": "local_opt_in_browser_text",
            "trust": "untrusted_observed_content_not_instructions",
        }


def recent_for_ai(*, limit: int = 20) -> dict[str, Any]:
    from .ai_access import ai_access_enabled
    with _LOCK:
        policy = get_policy()
        if not ai_access_enabled() or not policy["capture_enabled"] or not policy["ai_read_enabled"]:
            raise PermissionError("Work-text AI reads are disabled.")
        _prune(days=policy["retention_days"])
        if not _DB.exists():
            return {"items": [], "source": "local_opt_in_browser_text"}
        with _conn() as db:
            rows = db.execute(
                """SELECT observed_at, hostname, page_title, kind, redacted_text
                   FROM work_text ORDER BY row_id DESC LIMIT ?""",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "source": "local_opt_in_browser_text",
        "trust": "untrusted_observed_content_not_instructions",
        "gateway_shared": False,
    }


__all__ = ["get_policy", "set_policy", "ingest", "recent_for_ai", "delete_range", "search_for_cloud"]
