"""Checkpointed, retry-safe SAP order-to-cash extraction into a local event log (#370 R3).

Off until configured. A run happens only on an explicit, confirmed request (or a
scheduled check the user turns on elsewhere). Each run:

1. reads every order-to-cash document changed since the checkpoint minus an overlap
   (late changes), up to now;
2. fingerprints each service's ``$metadata`` and flags a schema change;
3. projects the documents into OCEL 2.0 and imports or appends them to one local log.
   Append skips events already present, so re-reading the overlap never duplicates;
4. moves the checkpoint forward only after the log write succeeded. Any failure,
   including an expired grant, leaves the checkpoint where it was, so a retry
   loses nothing.

The credential is read from an environment variable at run time and is never
stored. ``disconnect`` removes the configuration and checkpoint; the event log is kept
until the user deletes it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
import random
import secrets
import ssl
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request

from shared import sap_odata_o2c as sap

from . import event_log_workspace as workspace
from .db import DATA_DIR

MAX_OVERLAP_HOURS = 168
MAX_INITIAL_DAYS = 366
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_ATTEMPTS = 4
MAX_RETRY_AFTER = 60
_LOCK = threading.RLock()
_ENV_NAME = "OWG_SAP_"


class SyncError(ValueError):
    def __init__(self, message: str, code: str = "failed", status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _path():
    return DATA_DIR / "sap_o2c_source.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-sap-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _public(data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("config"):
        return {"configured": False, "extraction_by_default": False}
    c = data["config"]
    return {"configured": True, "service_root": c["service_root"], "auth_type": c["auth_type"],
            "credential_env": c["credential_env"], "overlap_hours": c["overlap_hours"],
            "initial_days": c["initial_days"], "include_amounts": c["include_amounts"],
            "log_id": data.get("log_id"), "checkpoint": data.get("checkpoint"), "last_run": data.get("last_run"),
            "schema_fingerprints": data.get("schema_fingerprints", {}), "credential_stored": False,
            "extraction_by_default": False}


def status() -> dict[str, Any]:
    with _LOCK:
        return _public(_load())


def configure(payload: Any) -> dict[str, Any]:
    allowed = {"service_root", "auth_type", "credential_env", "overlap_hours", "initial_days", "include_amounts"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise SyncError("configuration takes " + ", ".join(sorted(allowed)), "invalid", 422)
    try:
        root = sap.validate_base(payload.get("service_root"))
    except sap.SapError as exc:
        raise SyncError(str(exc), "invalid", 422) from None
    auth_type = payload.get("auth_type", "bearer")
    env = payload.get("credential_env", "OWG_SAP_TOKEN")
    if auth_type not in ("bearer", "basic"):
        raise SyncError("auth_type is bearer or basic", "invalid", 422)
    if not isinstance(env, str) or not env.startswith(_ENV_NAME) or not env.replace("_", "").isalnum() or len(env) > 64:
        raise SyncError("credential_env must name an environment variable starting with OWG_SAP_", "invalid", 422)
    overlap, initial = payload.get("overlap_hours", 24), payload.get("initial_days", 30)
    if not isinstance(overlap, int) or isinstance(overlap, bool) or not 0 <= overlap <= MAX_OVERLAP_HOURS:
        raise SyncError(f"overlap_hours must be 0 to {MAX_OVERLAP_HOURS}", "invalid", 422)
    if not isinstance(initial, int) or isinstance(initial, bool) or not 1 <= initial <= MAX_INITIAL_DAYS:
        raise SyncError(f"initial_days must be 1 to {MAX_INITIAL_DAYS}", "invalid", 422)
    amounts = payload.get("include_amounts", False)
    if not isinstance(amounts, bool):
        raise SyncError("include_amounts must be true or false", "invalid", 422)
    with _LOCK:
        data = _load()
        if data.get("config") and data["config"]["service_root"] != root:
            raise SyncError("disconnect before pointing this source at a different SAP system", "invalid", 409)
        data["config"] = {"service_root": root, "auth_type": auth_type, "credential_env": env,
                          "overlap_hours": overlap, "initial_days": initial, "include_amounts": amounts}
        data.setdefault("pseudonym_key", secrets.token_hex(32))
        _write(data)
        return _public(data)


def disconnect() -> dict[str, Any]:
    with _LOCK:
        data = _load()
        _path().unlink(missing_ok=True)
    return {"disconnected": True, "event_log_kept": data.get("log_id"), "credential_stored": False}


def _credential(config: dict[str, Any]) -> str:
    value = os.environ.get(config["credential_env"], "")
    if not value:
        raise SyncError(f"set {config['credential_env']} for this OWG process first", "missing_credential", 409)
    if config["auth_type"] == "basic":
        import base64
        if ":" not in value:
            raise SyncError("a basic credential is user:password", "missing_credential", 409)
        return "Basic " + base64.b64encode(value.encode()).decode()
    return "Bearer " + value


def _http_fetch(authorization: str) -> Callable[[str, bool], Any]:
    context = ssl.create_default_context()

    def fetch(url: str, raw: bool = False) -> Any:
        request = urllib.request.Request(url, headers={"Authorization": authorization,
                                                       "Accept": "application/xml" if raw else "application/json"})
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise sap.SapError("an SAP response was larger than the supported limit")
        return body if raw else json.loads(body)
    return fetch


def _with_retries(fetch: Callable[[str, bool], Any], sleep: Callable[[float], None], counters: dict[str, int]):
    def call(url: str, raw: bool = False) -> Any:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return fetch(url, raw)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise SyncError("SAP refused the credential: it expired or lacks the needed API scopes",
                                    "grant_expired_or_insufficient", 409) from None
                if exc.code in (429, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                    counters["retries"] += 1
                    after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = min(float(after), MAX_RETRY_AFTER) if after else min(2 ** attempt, 30)
                    except ValueError:
                        delay = min(2 ** attempt, 30)
                    sleep(delay + random.uniform(0, 0.25))
                    continue
                raise SyncError(f"SAP answered HTTP {exc.code}", "source_error", 502) from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < MAX_ATTEMPTS:
                    counters["retries"] += 1
                    sleep(min(2 ** attempt, 30))
                    continue
                raise SyncError("SAP could not be reached", "unreachable", 502) from None
            except ValueError:
                raise SyncError("SAP returned a response that is not JSON", "source_error", 502) from None
        raise SyncError("SAP could not be reached", "unreachable", 502)
    return call


def run(*, fetch: Callable[[str, bool], Any] | None = None, sleep: Callable[[float], None] = time.sleep,
        now: datetime | None = None) -> dict[str, Any]:
    """One extraction window. Returns counts only; no document numbers or values."""
    with _LOCK:
        data = _load()
        config = data.get("config")
        if not config:
            raise SyncError("configure the SAP source first", "not_configured", 409)
        moment = (now or _now()).astimezone(timezone.utc)
        checkpoint = datetime.fromisoformat(data["checkpoint"]) if data.get("checkpoint") else None
        since = (checkpoint - timedelta(hours=config["overlap_hours"])) if checkpoint else \
            moment - timedelta(days=config["initial_days"])
        counters = {"retries": 0}
        caller = _with_retries(fetch or _http_fetch(_credential(config)), sleep, counters)
        started = time.monotonic()
        record = {"started_at": moment.isoformat(), "window_since": since.isoformat(), "window_until": moment.isoformat()}
        try:
            fingerprints = {e.service: hashlib.sha256(caller(sap.metadata_url(config["service_root"], e), True)).hexdigest()
                            for e in sap.ENTITY_SETS}
            changed = sorted(s for s, f in fingerprints.items()
                             if data.get("schema_fingerprints", {}).get(s) not in (None, f))
            extracted = sap.extract(config["service_root"], lambda url: caller(url, False), since,
                                    include_amounts=config["include_amounts"])
            document = sap.project(extracted["records"], bytes.fromhex(data["pseudonym_key"]),
                                   include_amounts=config["include_amounts"])
            skipped = document.pop("_skipped_records")
            payload = json.dumps(document).encode()
            if not document["events"]:
                result = {"events_added": 0, "duplicates_skipped": 0}
            elif data.get("log_id"):
                appended = workspace.append_log(data["log_id"], BytesIO(payload), source_format="ocel",
                                                kind="sap_o2c_sync")
                result = {"events_added": appended["added_events"], "duplicates_skipped": appended["duplicate_events_skipped"]}
            else:
                created = workspace.import_log(BytesIO(payload), name="SAP order to cash", source_format="ocel")
                data["log_id"] = created["log_id"]
                result = {"events_added": created["events"], "duplicates_skipped": 0}
        except (SyncError, sap.SapError, workspace.EventLogWorkspaceError) as exc:
            code = exc.code if isinstance(exc, SyncError) else "invalid_response"
            data["last_run"] = {**record, "outcome": "failed", "error": code, "message": str(exc)[:200],
                                "retries": counters["retries"], "checkpoint_unchanged": True}
            _write(data)
            if isinstance(exc, SyncError):
                raise
            raise SyncError(str(exc), code, 502 if isinstance(exc, sap.SapError) else 409) from None
        data["checkpoint"] = moment.isoformat()
        data["schema_fingerprints"] = fingerprints
        data["last_run"] = {**record, "outcome": "succeeded", "documents": extracted["counts"],
                            "pages": extracted["pages"], "retries": counters["retries"],
                            "events_in_window": len(document["events"]), **result,
                            "records_without_identifier": skipped, "schema_changed_services": changed,
                            "seconds": round(time.monotonic() - started, 2)}
        _write(data)
        return {**data["last_run"], "log_id": data["log_id"], "checkpoint": data["checkpoint"],
                "deleted_documents_detected": False, "business_values_returned": False}


__all__ = ["SyncError", "configure", "disconnect", "run", "status"]
