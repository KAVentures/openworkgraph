from __future__ import annotations

"""Endpoint verification and opt-in activation of signed enterprise policy."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from shared.policy_bundle import (
    PolicyBundleError,
    canonical_policy_manifest_bytes,
    verify_policy_bundle,
)
from server.declared_policy import DeclaredPolicyError, load_declared_policy_manifest

from .config import GatewaySyncSettings
from .state import SyncState


class ManagedDeclaredPolicyError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _secure_mode(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _secure_mode(tmp)
        os.replace(tmp, path)
        _secure_mode(path)
        _fsync_directory(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _target_file(settings: GatewaySyncSettings, *, data_dir: Path) -> Path:
    if settings.managed_declared_policy_target_file is not None:
        return settings.managed_declared_policy_target_file
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_FILE", "")).strip()
    return Path(configured).expanduser() if configured else data_dir / "declared_policies.json"


def _validate_manifest_bytes(raw: bytes, *, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".managed-policy-validate.", suffix=".json", dir=str(target.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            loaded = load_declared_policy_manifest(tmp)
        except DeclaredPolicyError as exc:
            raise ManagedDeclaredPolicyError("signed bundle contains an invalid declared-policy manifest") from exc
        if not loaded.get("manifest_present"):
            raise ManagedDeclaredPolicyError("signed bundle contains no declared-policy manifest")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def fetch_device_declared_policy(client: httpx.Client, gateway_url: str) -> dict[str, Any] | None:
    response = client.get(f"{gateway_url}/v1/device-declared-policy")
    response.raise_for_status()
    try:
        payload = response.json()
    except Exception as exc:
        raise ManagedDeclaredPolicyError("Gateway returned invalid managed policy JSON") from exc
    if not isinstance(payload, dict):
        raise ManagedDeclaredPolicyError("Gateway managed policy response must be an object")
    if not bool(payload.get("present")):
        return None
    bundle = payload.get("bundle")
    if not isinstance(bundle, dict):
        raise ManagedDeclaredPolicyError("Gateway managed policy response is missing a signed bundle")
    return payload


def refresh_managed_declared_policy(
    client: httpx.Client,
    gateway_url: str,
    *,
    settings: GatewaySyncSettings,
    state: SyncState,
    data_dir: Path,
) -> dict[str, Any]:
    """Fetch, verify and atomically activate one newer managed enterprise policy.

    This path is opt-in. Signature verification happens before any policy-file
    mutation. Revision state protects against ordinary server/network rollback
    relative to this endpoint's retained local state; it is not hardware-backed.
    """
    if not settings.managed_declared_policy_enabled:
        return {"status": "disabled", "changed": False}
    organization_id = str(settings.managed_declared_policy_organization_id or "").strip()
    if not organization_id:
        raise ManagedDeclaredPolicyError("managed declared policy requires organization_id")
    if not settings.managed_declared_policy_trusted_keys:
        raise ManagedDeclaredPolicyError("managed declared policy requires at least one pinned trusted public key")

    payload = fetch_device_declared_policy(client, gateway_url)
    if payload is None:
        state.set("managed_declared_policy_status", "not_published")
        state.set("managed_declared_policy_last_error", "")
        return {"status": "not_published", "changed": False}
    response_org = str(payload.get("organization_id") or "").strip()
    if response_org and response_org != organization_id:
        raise ManagedDeclaredPolicyError("Gateway returned managed policy for another organization")

    try:
        verified = verify_policy_bundle(
            payload["bundle"],
            trusted_public_keys=settings.managed_declared_policy_trusted_keys,
            expected_organization_id=organization_id,
        )
    except PolicyBundleError as exc:
        raise ManagedDeclaredPolicyError(str(exc)) from exc

    response_bundle_sha = str(payload.get("bundle_sha256") or "").strip().lower()
    if response_bundle_sha and response_bundle_sha != verified["bundle_sha256"]:
        raise ManagedDeclaredPolicyError("Gateway bundle metadata does not match the signed bundle")
    response_manifest_sha = str(payload.get("manifest_sha256") or "").strip().lower()
    if response_manifest_sha and response_manifest_sha != verified["manifest_sha256"]:
        raise ManagedDeclaredPolicyError("Gateway manifest metadata does not match the signed bundle")

    revision = int(verified["policy_revision"])
    bundle_sha = str(verified["bundle_sha256"])
    manifest_sha = str(verified["manifest_sha256"])
    previous_revision = state.get_int("managed_declared_policy_revision", 0)
    previous_bundle_sha = state.get("managed_declared_policy_bundle_sha256", "")
    if revision < previous_revision:
        raise ManagedDeclaredPolicyError("managed declared policy rollback rejected")
    if revision == previous_revision and previous_bundle_sha and previous_bundle_sha != bundle_sha:
        raise ManagedDeclaredPolicyError("managed declared policy revision changed signed content")

    target = _target_file(settings, data_dir=data_dir)
    manifest_raw = canonical_policy_manifest_bytes(verified["bundle"]["manifest"])
    if _sha256(manifest_raw) != manifest_sha:
        raise ManagedDeclaredPolicyError("verified manifest hash mismatch")
    _validate_manifest_bytes(manifest_raw, target=target)

    current_raw: bytes | None
    try:
        current_raw = target.read_bytes() if target.exists() else None
    except OSError as exc:
        raise ManagedDeclaredPolicyError("unable to read managed declared-policy target") from exc

    target_current = current_raw == manifest_raw
    newer = revision > previous_revision or not previous_bundle_sha
    changed = False
    repaired = False
    if newer or not target_current:
        _atomic_write(target, manifest_raw)
        changed = True
        repaired = bool(not newer and not target_current)

    # Persist rollback/provenance state only after the verified manifest is safely
    # on disk. A crash before these writes causes a harmless re-verification.
    state.set_int("managed_declared_policy_revision", revision)
    state.set("managed_declared_policy_bundle_sha256", bundle_sha)
    state.set("managed_declared_policy_manifest_sha256", manifest_sha)
    state.set("managed_declared_policy_key_id", str(verified["key_id"]))
    state.set("managed_declared_policy_issued_at", str(verified["issued_at"]))
    state.set("managed_declared_policy_organization_id", organization_id)
    state.set("managed_declared_policy_status", "repaired" if repaired else ("applied" if changed else "current"))
    state.set("managed_declared_policy_last_error", "")

    return {
        "status": "repaired" if repaired else ("applied" if changed else "current"),
        "changed": changed,
        "repaired_local_drift": repaired,
        "organization_id": organization_id,
        "policy_revision": revision,
        "bundle_sha256": bundle_sha,
        "manifest_sha256": manifest_sha,
        "key_id": verified["key_id"],
        "signature_verified": True,
        "rollback_protection": "endpoint_local_state",
        "hardware_backed_rollback_protection": False,
    }
