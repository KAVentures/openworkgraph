from __future__ import annotations

"""Canonical signed enterprise declared-policy bundles.

The Gateway may store and transport these bundles, but only endpoints holding a
pinned organization public key can establish policy authenticity. Private signing
keys are never required by the Gateway or ordinary OpenWorkGraph runtimes.
"""

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .time_utils import normalize_timestamp


POLICY_BUNDLE_SCHEMA_VERSION = "1.0"
POLICY_BUNDLE_ALGORITHM = "Ed25519"
MAX_POLICY_BUNDLE_BYTES = 512 * 1024
MAX_POLICY_MANIFEST_BYTES = 256 * 1024
MAX_POLICY_REVISION = 9_223_372_036_854_775_807
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PolicyBundleError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PolicyBundleError("policy bundle must contain canonical JSON values") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: Any, *, field: str, expected_bytes: int) -> bytes:
    text = str(value or "").strip()
    if not text or not _B64URL_RE.fullmatch(text) or len(text) > 512:
        raise PolicyBundleError(f"invalid {field}")
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except Exception as exc:
        raise PolicyBundleError(f"invalid {field}") from exc
    if len(raw) != expected_bytes:
        raise PolicyBundleError(f"invalid {field}")
    return raw


def canonical_policy_manifest_bytes(manifest: Any) -> bytes:
    if not isinstance(manifest, dict):
        raise PolicyBundleError("policy manifest must be a JSON object")
    raw = _canonical_json(manifest)
    if not raw or len(raw) > MAX_POLICY_MANIFEST_BYTES:
        raise PolicyBundleError("policy manifest exceeds 256 KiB")
    return raw


def policy_manifest_sha256(manifest: Any) -> str:
    return _sha256(canonical_policy_manifest_bytes(manifest))


def _organization_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 512 or any(ord(ch) < 32 for ch in text):
        raise PolicyBundleError("invalid organization_id")
    return text


def _key_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _KEY_ID_RE.fullmatch(text):
        raise PolicyBundleError("invalid key_id")
    return text


def _revision(value: Any) -> int:
    if isinstance(value, bool):
        raise PolicyBundleError("invalid policy_revision")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyBundleError("invalid policy_revision") from exc
    if revision < 1 or revision > MAX_POLICY_REVISION or str(value).strip() != str(revision):
        raise PolicyBundleError("invalid policy_revision")
    return revision


def _issued_at(value: Any) -> str:
    try:
        return normalize_timestamp(str(value or ""))
    except ValueError as exc:
        raise PolicyBundleError("invalid issued_at") from exc


def _signing_core(bundle: dict[str, Any]) -> dict[str, Any]:
    manifest = bundle.get("manifest")
    manifest_sha = policy_manifest_sha256(manifest)
    declared_sha = str(bundle.get("manifest_sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(declared_sha) or declared_sha != manifest_sha:
        raise PolicyBundleError("manifest_sha256 does not match manifest")
    schema = str(bundle.get("schema_version") or "").strip()
    if schema != POLICY_BUNDLE_SCHEMA_VERSION:
        raise PolicyBundleError("unsupported policy bundle schema_version")
    algorithm = str(bundle.get("signature_algorithm") or "").strip()
    if algorithm != POLICY_BUNDLE_ALGORITHM:
        raise PolicyBundleError("unsupported policy bundle signature_algorithm")
    core = {
        "schema_version": schema,
        "organization_id": _organization_id(bundle.get("organization_id")),
        "policy_revision": _revision(bundle.get("policy_revision")),
        "issued_at": _issued_at(bundle.get("issued_at")),
        "key_id": _key_id(bundle.get("key_id")),
        "signature_algorithm": algorithm,
        "manifest_sha256": manifest_sha,
        "manifest": manifest,
    }
    raw = _canonical_json(core)
    if len(raw) > MAX_POLICY_BUNDLE_BYTES:
        raise PolicyBundleError("policy bundle signing payload exceeds 512 KiB")
    return core


def policy_bundle_signing_bytes(bundle: dict[str, Any]) -> bytes:
    return _canonical_json(_signing_core(bundle))


def inspect_policy_bundle(bundle: Any) -> dict[str, Any]:
    """Validate envelope structure without claiming signature authenticity."""
    if not isinstance(bundle, dict):
        raise PolicyBundleError("policy bundle must be a JSON object")
    core = _signing_core(bundle)
    signature = str(bundle.get("signature") or "").strip()
    _b64url_decode(signature, field="signature", expected_bytes=64)
    normalized = {**core, "signature": signature}
    raw = _canonical_json(normalized)
    if len(raw) > MAX_POLICY_BUNDLE_BYTES:
        raise PolicyBundleError("policy bundle exceeds 512 KiB")
    return {
        "bundle": normalized,
        "bundle_sha256": _sha256(raw),
        "manifest_sha256": core["manifest_sha256"],
        "organization_id": core["organization_id"],
        "policy_revision": core["policy_revision"],
        "issued_at": core["issued_at"],
        "key_id": core["key_id"],
        "signature_algorithm": POLICY_BUNDLE_ALGORITHM,
        "signature_present": True,
        "signature_verified": False,
    }


def verify_policy_bundle(
    bundle: Any,
    *,
    trusted_public_keys: dict[str, str],
    expected_organization_id: str | None = None,
) -> dict[str, Any]:
    inspected = inspect_policy_bundle(bundle)
    normalized = inspected["bundle"]
    organization_id = normalized["organization_id"]
    if expected_organization_id is not None and organization_id != _organization_id(expected_organization_id):
        raise PolicyBundleError("policy bundle belongs to another organization")
    key_id = normalized["key_id"]
    encoded_key = str((trusted_public_keys or {}).get(key_id) or "").strip()
    if not encoded_key:
        raise PolicyBundleError("policy bundle key_id is not trusted")
    public_raw = _b64url_decode(encoded_key, field="trusted public key", expected_bytes=32)
    signature_raw = _b64url_decode(normalized["signature"], field="signature", expected_bytes=64)
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature_raw,
            policy_bundle_signing_bytes(normalized),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PolicyBundleError("policy bundle signature verification failed") from exc
    return {
        **inspected,
        "signature_verified": True,
        "trusted_key_id": key_id,
    }


def sign_policy_bundle(
    manifest: dict[str, Any],
    *,
    organization_id: str,
    policy_revision: int,
    key_id: str,
    private_key_pem: bytes,
    issued_at: str | None = None,
) -> dict[str, Any]:
    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except Exception as exc:
        raise PolicyBundleError("unable to load Ed25519 private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PolicyBundleError("private key must be Ed25519")
    timestamp = issued_at or datetime.now(timezone.utc).isoformat()
    core = {
        "schema_version": POLICY_BUNDLE_SCHEMA_VERSION,
        "organization_id": _organization_id(organization_id),
        "policy_revision": _revision(policy_revision),
        "issued_at": _issued_at(timestamp),
        "key_id": _key_id(key_id),
        "signature_algorithm": POLICY_BUNDLE_ALGORITHM,
        "manifest_sha256": policy_manifest_sha256(manifest),
        "manifest": manifest,
    }
    signature = key.sign(policy_bundle_signing_bytes(core))
    bundle = {**core, "signature": _b64url_encode(signature)}
    return inspect_policy_bundle(bundle)["bundle"]


def generate_ed25519_keypair() -> tuple[bytes, str]:
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_pem, _b64url_encode(public_raw)
