from __future__ import annotations

"""Offline enterprise declared-policy signing administration.

This CLI deliberately has no Gateway credentials or publishing capability. The
private signing key can remain on a separate administrator machine; the Gateway
only needs the signed JSON bundle and endpoints only need pinned public keys.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from shared.policy_bundle import (
    POLICY_BUNDLE_ALGORITHM,
    POLICY_BUNDLE_SCHEMA_VERSION,
    PolicyBundleError,
    generate_ed25519_keypair,
    inspect_policy_bundle,
    sign_policy_bundle,
    verify_policy_bundle,
)

from .declared_policy import DeclaredPolicyError, load_declared_policy_manifest


class EnterprisePolicyAdminError(ValueError):
    pass


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False))


def _write_new(path: Path, raw: bytes, *, private: bool) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(path), flags, 0o600 if private else 0o644)
    except FileExistsError as exc:
        raise EnterprisePolicyAdminError(f"refusing to overwrite existing file: {path}") from exc
    except OSError as exc:
        raise EnterprisePolicyAdminError(f"unable to create file: {path}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            try:
                path.chmod(0o600)
            except OSError:
                pass
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _read_json(path: Path, *, label: str, max_bytes: int = 512 * 1024) -> dict[str, Any]:
    try:
        raw = path.expanduser().read_bytes()
    except OSError as exc:
        raise EnterprisePolicyAdminError(f"unable to read {label}") from exc
    if not raw or len(raw) > max_bytes:
        raise EnterprisePolicyAdminError(f"invalid {label} size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnterprisePolicyAdminError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise EnterprisePolicyAdminError(f"{label} must be a JSON object")
    return value


def _validated_manifest(path: Path) -> dict[str, Any]:
    try:
        loaded = load_declared_policy_manifest(path.expanduser())
    except DeclaredPolicyError as exc:
        raise EnterprisePolicyAdminError("invalid declared-policy manifest") from exc
    if not loaded.get("manifest_present"):
        raise EnterprisePolicyAdminError("declared-policy manifest is missing")
    return _read_json(path, label="declared-policy manifest", max_bytes=256 * 1024)


def _public_key_document(path: Path) -> dict[str, str]:
    value = _read_json(path, label="public key document", max_bytes=16 * 1024)
    if value.get("schema_version") != POLICY_BUNDLE_SCHEMA_VERSION:
        raise EnterprisePolicyAdminError("unsupported public key document schema_version")
    if value.get("algorithm") != POLICY_BUNDLE_ALGORITHM:
        raise EnterprisePolicyAdminError("unsupported public key algorithm")
    key_id = str(value.get("key_id") or "").strip()
    public_key = str(value.get("public_key") or "").strip()
    if not key_id or not public_key:
        raise EnterprisePolicyAdminError("public key document is incomplete")
    return {"key_id": key_id, "public_key": public_key}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m server.enterprise_policy_admin")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="generate a local Ed25519 enterprise policy signing keypair")
    keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)

    sign = sub.add_parser("sign", help="sign a validated declared-policy manifest into a portable bundle")
    sign.add_argument("manifest", type=Path)
    sign.add_argument("--organization-id", required=True)
    sign.add_argument("--revision", required=True, type=int)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--private-key", type=Path, required=True)
    sign.add_argument("--output", type=Path, required=True)

    inspect_cmd = sub.add_parser("inspect", help="inspect a signed bundle without claiming signature authenticity")
    inspect_cmd.add_argument("bundle", type=Path)

    verify = sub.add_parser("verify", help="verify a bundle against one pinned public key document")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--organization-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "keygen":
            private_pem, public_key = generate_ed25519_keypair()
            public_document = {
                "schema_version": POLICY_BUNDLE_SCHEMA_VERSION,
                "algorithm": POLICY_BUNDLE_ALGORITHM,
                "key_id": str(args.key_id),
                "public_key": public_key,
            }
            # Validate key_id through the same signed-envelope parser by creating
            # no secondary permissive key-ID grammar in the admin CLI.
            try:
                dummy_private = private_pem
                sign_policy_bundle(
                    {"schema_version": "1.0", "policies": []},
                    organization_id="keygen-validation",
                    policy_revision=1,
                    key_id=str(args.key_id),
                    private_key_pem=dummy_private,
                )
            except PolicyBundleError as exc:
                raise EnterprisePolicyAdminError(str(exc)) from exc
            _write_new(args.private_key, private_pem, private=True)
            try:
                _write_new(
                    args.public_key,
                    (json.dumps(public_document, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                    private=False,
                )
            except Exception:
                try:
                    args.private_key.expanduser().unlink()
                except OSError:
                    pass
                raise
            _print_json({
                "status": "created",
                "algorithm": POLICY_BUNDLE_ALGORITHM,
                "key_id": str(args.key_id),
                "private_key_path": str(args.private_key),
                "public_key_path": str(args.public_key),
                "private_key_printed": False,
                "gateway_private_key_required": False,
            })
            return 0

        if args.command == "sign":
            manifest = _validated_manifest(args.manifest)
            try:
                private_pem = args.private_key.expanduser().read_bytes()
            except OSError as exc:
                raise EnterprisePolicyAdminError("unable to read private signing key") from exc
            bundle = sign_policy_bundle(
                manifest,
                organization_id=args.organization_id,
                policy_revision=args.revision,
                key_id=args.key_id,
                private_key_pem=private_pem,
            )
            raw = (json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            _write_new(args.output, raw, private=False)
            inspected = inspect_policy_bundle(bundle)
            _print_json({
                "status": "signed",
                "organization_id": inspected["organization_id"],
                "policy_revision": inspected["policy_revision"],
                "key_id": inspected["key_id"],
                "manifest_sha256": inspected["manifest_sha256"],
                "bundle_sha256": inspected["bundle_sha256"],
                "output": str(args.output),
                "private_key_printed": False,
            })
            return 0

        if args.command == "inspect":
            inspected = inspect_policy_bundle(_read_json(args.bundle, label="policy bundle"))
            _print_json({key: value for key, value in inspected.items() if key != "bundle"})
            return 0

        if args.command == "verify":
            public = _public_key_document(args.public_key)
            verified = verify_policy_bundle(
                _read_json(args.bundle, label="policy bundle"),
                trusted_public_keys={public["key_id"]: public["public_key"]},
                expected_organization_id=args.organization_id,
            )
            _print_json({
                **{key: value for key, value in verified.items() if key != "bundle"},
                "status": "verified",
            })
            return 0
    except (EnterprisePolicyAdminError, PolicyBundleError) as exc:
        print(f"enterprise policy admin error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
