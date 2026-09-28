from __future__ import annotations

"""A short, bounded buffer for native agent events while OpenWorkGraph is busy.

Native agent hooks (Claude Code) run outside OpenWorkGraph and POST each event
once. If that POST fails, the event may be kept on disk and delivered later,
but only under a recording lease:

* Only a running, recording, non-demo OpenWorkGraph issues the lease. It is short
  (``LEASE_TTL_SECONDS``), renewed while recording, and revoked immediately on
  Pause, Stop and shutdown. A crashed OpenWorkGraph stops granting it within the
  TTL.
* A hook spools only while the lease is valid *and* the capture state file of
  the data folder that issued it says "recording" (read directly, never
  defaulted). So after the user presses Stop or quits OpenWorkGraph, agent
  events are dropped, never quietly collected for later.
* Flushing goes through the normal ingest path, which applies the Observe
  switches, deletion tombstones, retention and pause/stop windows again.
* The spool is bounded by file count, file size and age.

Spooled events are the same allowlisted structural events the hook would have
sent; no prompt, response, argument or result content exists in them.
"""

import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .local_auth import auth_dir

LEASE_NAME = "agent_spool_lease.json"
SPOOL_DIR_NAME = "agent_spool"
LEASE_TTL_SECONDS = 90.0
MAX_SPOOL_FILES = 2000
MAX_FILE_BYTES = 256_000
MAX_AGE_SECONDS = 24 * 3600.0


def _lease_path() -> Path:
    return auth_dir() / LEASE_NAME


def spool_dir() -> Path:
    path = auth_dir() / SPOOL_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------- lease (server side)

def issue_lease(data_dir: Path, *, lease_id: str, ttl_seconds: float = LEASE_TTL_SECONDS, now: float | None = None) -> dict[str, Any]:
    issued = time.time() if now is None else now
    lease = {
        "lease_id": lease_id,
        "data_dir": str(Path(data_dir).resolve()),
        "issued_at": issued,
        "valid_until": issued + float(ttl_seconds),
    }
    _atomic_write(_lease_path(), (json.dumps(lease) + "\n").encode("utf-8"))
    return lease


def revoke_lease(*, lease_id: str | None = None) -> None:
    """Remove the lease (only our own, when an id is given)."""
    path = _lease_path()
    try:
        if lease_id is not None:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("lease_id") != lease_id:
                return
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        try:
            path.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------- lease (hook side)

def _capture_state(data_dir: Path) -> str:
    try:
        value = json.loads((data_dir / "capture_control.json").read_text(encoding="utf-8"))
        return str(value.get("state") or "") if isinstance(value, dict) else ""
    except Exception:
        return ""


def valid_lease(now: float | None = None) -> dict[str, Any] | None:
    try:
        lease = json.loads(_lease_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(lease, dict):
        return None
    current = time.time() if now is None else now
    try:
        if float(lease.get("valid_until") or 0) <= current:
            return None
    except Exception:
        return None
    data_dir = Path(str(lease.get("data_dir") or ""))
    if not str(lease.get("data_dir") or "") or _capture_state(data_dir) != "recording":
        return None
    return lease


def spool_events(events: list[dict[str, Any]], *, now: float | None = None) -> bool:
    """Keep events for later delivery if, and only if, recording is leased."""
    if not events:
        return False
    lease = valid_lease(now)
    if lease is None:
        return False
    directory = spool_dir()
    try:
        if sum(1 for p in directory.iterdir() if p.suffix == ".json") >= MAX_SPOOL_FILES:
            return False
    except Exception:
        return False
    payload = json.dumps({
        "lease_id": lease["lease_id"],
        "data_dir": lease["data_dir"],
        "spooled_at": datetime.now(timezone.utc).isoformat(),
        "spooled_epoch": time.time() if now is None else now,
        "events": events,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        return False
    try:
        _atomic_write(directory / f"{time.time_ns()}-{uuid.uuid4().hex}.json", payload)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------- flush (server side)

def flush_spool(
    data_dir: Path,
    ingest: Callable[[list[dict[str, Any]]], Any],
    *,
    now: float | None = None,
) -> dict[str, int]:
    """Deliver spooled events that belong to this data folder, oldest first.

    Files older than ``MAX_AGE_SECONDS`` are discarded unread. An ingest error
    that is about the payload discards that file; any other error stops the
    flush so the file is retried next time.
    """
    from shared.agent_evidence import AgentEvidenceError

    counts = {"delivered_files": 0, "delivered_events": 0, "expired_files": 0, "rejected_files": 0}
    current = time.time() if now is None else now
    ours = str(Path(data_dir).resolve())
    try:
        files = sorted(p for p in spool_dir().iterdir() if p.suffix == ".json" and not p.name.startswith(".tmp-"))
    except Exception:
        return counts
    for path in files:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)
            counts["rejected_files"] += 1
            continue
        age = current - float(item.get("spooled_epoch") or 0)
        if age > MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            counts["expired_files"] += 1
            continue
        if str(item.get("data_dir") or "") != ours:
            continue  # another OpenWorkGraph data folder (e.g. demo) owns it
        events = [e for e in item.get("events") or [] if isinstance(e, dict)]
        try:
            if events:
                ingest(events)
        except (AgentEvidenceError, ValueError):
            path.unlink(missing_ok=True)
            counts["rejected_files"] += 1
            continue
        except Exception:
            break
        path.unlink(missing_ok=True)
        counts["delivered_files"] += 1
        counts["delivered_events"] += len(events)
    return counts


def pending_count() -> int:
    try:
        return sum(1 for p in spool_dir().iterdir() if p.suffix == ".json" and not p.name.startswith(".tmp-"))
    except Exception:
        return 0
