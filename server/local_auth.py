from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.RLock()
_DASHBOARD_SESSIONS: set[str] = set()
_CONSUMED_DASHBOARD_BOOTSTRAPS: set[str] = set()
_EXPORT_TICKETS: dict[str, tuple[float, str, str, bool]] = {}
_PAIRING_CODE: tuple[str, float, int] | None = None
_SEEN_BROWSER_NONCES: dict[str, float] = {}


def data_dir() -> Path:
    path = Path(os.getenv("WORKFLOW_OBSERVER_DATA", ROOT / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def auth_dir() -> Path:
    path = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", ROOT / "data" / "auth"))
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass
    return path


def _secret_file(name: str, *, directory: Path | None = None) -> Path:
    return (directory or auth_dir()) / name


def _read_or_create_secret(name: str, *, directory: Path | None = None) -> str:
    """Return a stable high-entropy installation secret.

    Creation uses O_EXCL so concurrent startup processes cannot create different
    credentials. POSIX permissions are tightened to 0600. On Windows the file
    inherits the user's application-data ACL; this is intentionally documented
    as a same-user hardening measure, not a malware boundary.
    """
    path = _secret_file(name, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="ascii").strip()
        if value:
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            return value
    except FileNotFoundError:
        pass
    except Exception:
        pass

    value = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, (value + "\n").encode("ascii"))
        finally:
            os.close(fd)
    except FileExistsError:
        value = path.read_text(encoding="ascii").strip()
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return value


def ensure_api_token(*, directory: Path | None = None) -> str:
    return _read_or_create_secret(".api_token", directory=directory)


def ensure_mcp_token(*, directory: Path | None = None) -> str:
    return _read_or_create_secret(".mcp_token", directory=directory)


def ensure_browser_secret(*, directory: Path | None = None) -> str:
    return _read_or_create_secret(".browser_pairing_secret", directory=directory)


def write_browser_pairing_bundle(extension_dir: Path, *, directory: Path | None = None) -> Path | None:
    """Write the browser's local pairing secret into the unpacked extension folder.

    This avoids asking normal users to type a code after every upgrade. The file
    is local-only, generated at runtime, and is not part of the repository or
    release artifact. Manual one-time code pairing remains available as fallback.
    """
    try:
        extension_dir.mkdir(parents=True, exist_ok=True)
        path = extension_dir / "pairing.json"
        tmp = path.with_name(path.name + ".tmp")
        payload = {"version": 1, "secret": ensure_browser_secret(directory=directory)}
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return path
    except Exception:
        return None


def bearer_matches(header: str | None, expected: str | None = None) -> bool:
    raw = str(header or "")
    if not raw.lower().startswith("bearer "):
        return False
    supplied = raw[7:].strip()
    target = expected or ensure_api_token()
    return bool(supplied) and hmac.compare_digest(supplied, target)


def mcp_bearer_matches(header: str | None) -> bool:
    # The managed v0.49+ HTTP bridge receives an ephemeral per-bridge token.
    # Retain the installation token only as a compatibility fallback for an
    # explicitly/manual-launched HTTP MCP process.
    bridge_token = os.getenv("WORKFLOW_OBSERVER_MCP_BRIDGE_TOKEN", "").strip()
    if bridge_token:
        return bearer_matches(header, bridge_token)
    return bearer_matches(header, ensure_mcp_token())


def dashboard_bootstrap_matches(candidate: str) -> bool:
    expected = os.getenv("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", "")
    return bool(expected and candidate) and hmac.compare_digest(candidate, expected)


def create_dashboard_session() -> str:
    """Create a dashboard capability valid for this local server process.

    Dashboard sessions intentionally have no wall-clock expiry. They live only in
    process memory, so stopping or restarting OpenWorkGraph revokes every session
    automatically while long-running observers remain usable for their full run.
    """
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _DASHBOARD_SESSIONS.add(token)
    return token


def exchange_dashboard_bootstrap(candidate: str) -> str | None:
    """Consume the launch bootstrap exactly once and return a process-scoped session."""
    expected = os.getenv("WORKFLOW_OBSERVER_DASHBOARD_BOOTSTRAP", "")
    if not expected or not candidate:
        return None
    with _LOCK:
        if expected in _CONSUMED_DASHBOARD_BOOTSTRAPS:
            return None
        if not hmac.compare_digest(candidate, expected):
            return None
        token = secrets.token_urlsafe(32)
        _DASHBOARD_SESSIONS.add(token)
        _CONSUMED_DASHBOARD_BOOTSTRAPS.add(expected)
        return token


def dashboard_session_valid(token: str | None) -> bool:
    if not token:
        return False
    with _LOCK:
        return str(token) in _DASHBOARD_SESSIONS


def issue_export_ticket(
    export_format: str,
    scope: str,
    include_raw: bool,
    ttl_seconds: int = 30,
) -> dict[str, int | str]:
    token = secrets.token_urlsafe(32)
    expires = time.time() + ttl_seconds
    with _LOCK:
        now = time.time()
        for key, value in list(_EXPORT_TICKETS.items()):
            if value[0] < now:
                _EXPORT_TICKETS.pop(key, None)
        _EXPORT_TICKETS[token] = (expires, str(export_format), str(scope), bool(include_raw))
    return {"ticket": token, "expires_in_seconds": ttl_seconds}


def consume_export_ticket(token: str, export_format: str, scope: str, include_raw: bool) -> bool:
    if not token:
        return False
    with _LOCK:
        current = _EXPORT_TICKETS.pop(str(token), None)
        if not current:
            return False
        expiry, expected_format, expected_scope, expected_raw = current
        if expiry < time.time():
            return False
        return (
            hmac.compare_digest(str(export_format), expected_format)
            and hmac.compare_digest(str(scope), expected_scope)
            and bool(include_raw) is expected_raw
        )


def new_pairing_code(ttl_seconds: int = 120) -> dict[str, int | str]:
    global _PAIRING_CODE
    code = f"{secrets.randbelow(100_000_000):08d}"
    expires = time.time() + ttl_seconds
    with _LOCK:
        _PAIRING_CODE = (code, expires, 0)
    return {"code": code, "expires_in_seconds": ttl_seconds}


def consume_pairing_code(candidate: str, *, max_attempts: int = 5) -> bool:
    global _PAIRING_CODE
    with _LOCK:
        current = _PAIRING_CODE
        if not current:
            return False
        code, expiry, attempts = current
        if expiry < time.time() or attempts >= max_attempts:
            _PAIRING_CODE = None
            return False
        if not hmac.compare_digest(str(candidate or ""), code):
            attempts += 1
            _PAIRING_CODE = None if attempts >= max_attempts else (code, expiry, attempts)
            return False
        _PAIRING_CODE = None
        return True


def browser_server_proof(nonce: str) -> str:
    message = f"openworkgraph-server-proof:{nonce}".encode("utf-8")
    return hmac.new(ensure_browser_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()


def _cleanup_nonces(now: float) -> None:
    for nonce, seen_at in list(_SEEN_BROWSER_NONCES.items()):
        if now - seen_at > 180:
            _SEEN_BROWSER_NONCES.pop(nonce, None)


def verify_browser_authorization(
    header: str | None,
    *,
    method: str,
    path: str,
    body: bytes,
    max_clock_skew_seconds: int = 90,
) -> bool:
    """Verify a replay-resistant HMAC request from the paired browser sensor."""
    raw = str(header or "")
    if not raw.startswith("OWG-HMAC "):
        return False
    value = raw[len("OWG-HMAC "):].strip()
    parts = value.split(".", 2)
    if len(parts) != 3:
        return False
    ts_raw, nonce, supplied = parts
    try:
        ts = int(ts_raw)
    except ValueError:
        return False
    now = int(time.time())
    if abs(now - ts) > max_clock_skew_seconds or not nonce or len(nonce) > 200:
        return False

    body_hash = hashlib.sha256(body or b"").hexdigest()
    canonical = f"{ts}\n{nonce}\n{method.upper()}\n{path}\n{body_hash}".encode("utf-8")
    expected = hmac.new(ensure_browser_secret().encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return False

    with _LOCK:
        _cleanup_nonces(time.time())
        if nonce in _SEEN_BROWSER_NONCES:
            return False
        _SEEN_BROWSER_NONCES[nonce] = time.time()
    return True


def local_security_note() -> str:
    return (
        "Local capability tokens and browser pairing reduce accidental localhost exposure and "
        "cross-process access. They are not a security boundary against malware already running "
        "with the same operating-system user privileges."
    )
