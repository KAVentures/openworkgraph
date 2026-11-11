"""Offline integrity manifest for portable customer runtime ZIPs (R17).

This file is also shipped as verify_release.py inside the exported bundle:
stdlib only, read-only, no provider calls, no optional OWG install required.

Integrity is not authenticity: an attacker who can replace both the generated
files and this unsigned manifest can forge a self-consistent bundle. Pin the
manifest SHA-256 independently to detect that kind of replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

FORMAT = "openworkgraph.customer-runtime-file-manifest.v1"
MANIFEST_NAME = "release-manifest.json"
MAX_FILES = 100
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
REQUIRED_GENERATED_FILES = frozenset({
    "workflow.spec.json", "verify_release.py", "execute.py", "runtime.py",
    "agent.py", "README.md", "AGENTS.md", "test_execution.py",
})
_HEX = re.compile(r"^[0-9a-f]{64}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")
_EXACT_FIELDS = {
    "format", "source", "workflow_reference", "workflow_spec_sha256",
    "process_model_sha256", "files", "generated_files_verified_as_remote_deployment",
    "remote_business_outcomes_verified", "independent_signature_present",
}


class ReleaseIntegrityError(ValueError):
    """Safe local error; do not include file contents or embedded customer goals."""


def _safe_name(name: object) -> bool:
    return (isinstance(name, str) and len(name) <= 180 and _FILE.fullmatch(name) is not None
            and all(part not in {".", ".."} for part in name.split("/"))
            and name != MANIFEST_NAME)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_model_hash(model: object) -> str:
    if not isinstance(model, dict):
        raise ReleaseIntegrityError("The process model is missing")
    try:
        return _hash(json.dumps(model, sort_keys=True, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ReleaseIntegrityError("Invalid process model JSON") from None


def create_manifest(files: dict[str, str], spec: dict) -> str:
    """Build an unsigned inventory of the exact output bytes, excluding the manifest itself."""
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES or any(
        not _safe_name(name) or not isinstance(value, str)
        or len(value.encode("utf-8")) > MAX_FILE_BYTES for name, value in files.items()
    ):
        raise ReleaseIntegrityError("Invalid generated runtime file set")
    if not isinstance(spec, dict) or spec.get("format") != "openworkgraph.agent-runtime-project.v1" or (
        spec.get("execution_authorization") is not False or spec.get("source_records_included") is not False
    ):
        raise ReleaseIntegrityError("Only an unchanged non-authorizing runtime specification can be inventoried")
    if not REQUIRED_GENERATED_FILES.issubset(files):
        raise ReleaseIntegrityError("Required generated runtime files are missing")
    try:
        if json.loads(files["workflow.spec.json"]) != spec:
            raise ReleaseIntegrityError("Runtime spec content differs from the requested version")
    except (ValueError, TypeError):
        raise ReleaseIntegrityError("Runtime spec content is not valid JSON") from None
    expected_model = _canonical_model_hash(spec.get("process_model"))
    if spec.get("process_model_sha256") != expected_model:
        raise ReleaseIntegrityError("Runtime process-model digest is inconsistent")
    reference = {name: spec.get(name) for name in ("workflow_id", "revision", "content_sha256")}
    if (not isinstance(reference["workflow_id"], str) or
        type(reference["revision"]) is not int or reference["revision"] < 1 or
        not isinstance(reference["content_sha256"], str) or
        not _HEX.fullmatch(reference["content_sha256"])):
        raise ReleaseIntegrityError("Invalid versioned process reference")
    data = {
        "format": FORMAT,
        "source": "generated_bundle_file_inventory_not_a_remote_attestation",
        "workflow_reference": reference,
        "workflow_spec_sha256": _hash(files["workflow.spec.json"].encode("utf-8")),
        "process_model_sha256": expected_model,
        "files": {name: _hash(files[name].encode("utf-8")) for name in sorted(files)},
        "generated_files_verified_as_remote_deployment": False,
        "remote_business_outcomes_verified": False,
        "independent_signature_present": False,
    }
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def _unique(pairs: list[tuple[str, object]]) -> dict:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ReleaseIntegrityError("Duplicate field in release manifest")
        output[key] = value
    return output


def verify_directory(root: Path | str, expected_manifest_sha256: str | None = None) -> dict:
    """Inspect the archive *after extraction* without executing any generated code."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ReleaseIntegrityError("The extracted runtime root must be a real directory")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseIntegrityError("Release manifest is missing or is a symbolic link")
    raw = manifest_path.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ReleaseIntegrityError("Release manifest is too large")
    actual_manifest_sha256 = _hash(raw)
    if expected_manifest_sha256 is not None and (
        not _HEX.fullmatch(expected_manifest_sha256) or expected_manifest_sha256 != actual_manifest_sha256
    ):
        raise ReleaseIntegrityError("The manifest differs from the independently pinned digest")
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ReleaseIntegrityError("Nonfinite JSON value")))
    except ReleaseIntegrityError:
        raise
    except (ValueError, UnicodeError):
        raise ReleaseIntegrityError("Invalid release manifest JSON") from None
    if not isinstance(manifest, dict) or set(manifest) != _EXACT_FIELDS or (
        manifest.get("format") != FORMAT or
        manifest.get("generated_files_verified_as_remote_deployment") is not False or
        manifest.get("remote_business_outcomes_verified") is not False or
        manifest.get("independent_signature_present") is not False or
        manifest.get("source") != "generated_bundle_file_inventory_not_a_remote_attestation"
    ):
        raise ReleaseIntegrityError("The release manifest does not match the documented contract")
    hashes = manifest["files"]
    if (not isinstance(hashes, dict) or not 1 <= len(hashes) <= MAX_FILES
        or not REQUIRED_GENERATED_FILES.issubset(hashes)):
        raise ReleaseIntegrityError("Invalid release file inventory")
    for name, digest in hashes.items():
        if not _safe_name(name) or not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ReleaseIntegrityError("Invalid or unsafe inventory path/digest")
        path = root
        for part in name.split("/"):
            path = path / part
            if path.is_symlink():
                raise ReleaseIntegrityError("Generated file inventory contains a symbolic link")
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES or _hash(path.read_bytes()) != digest:
            raise ReleaseIntegrityError("Generated file missing or changed: " + name)
    try:
        spec = json.loads((root / "workflow.spec.json").read_bytes(), object_pairs_hook=_unique)
    except ReleaseIntegrityError:
        raise
    except (ValueError, UnicodeError):
        raise ReleaseIntegrityError("Invalid exported workflow spec") from None
    if not isinstance(spec, dict) or (
        spec.get("format") != "openworkgraph.agent-runtime-project.v1"
        or spec.get("execution_authorization") is not False
        or spec.get("source_records_included") is not False
    ):
        raise ReleaseIntegrityError("The exported workflow has an invalid authorization boundary")
    reference = {name: spec.get(name) for name in ("workflow_id", "revision", "content_sha256")}
    if manifest["workflow_reference"] != reference or manifest["workflow_spec_sha256"] != hashes["workflow.spec.json"]:
        raise ReleaseIntegrityError("Release identity differs from its exported workflow")
    model_hash = _canonical_model_hash(spec.get("process_model"))
    if manifest["process_model_sha256"] != model_hash or spec.get("process_model_sha256") != model_hash:
        raise ReleaseIntegrityError("The exported process model does not match its release identity")
    return {
        "format": "openworkgraph.customer-runtime-file-verification.v1",
        "manifest_sha256": actual_manifest_sha256,
        "workflow_reference": reference,
        "verified_generated_file_count": len(hashes),
        "generated_file_integrity_ok": True,
        "independently_signed": False,
        "remote_deployment_verified": False,
        "remote_execution_or_outcome_verified": False,
        "interpretation": ("Generated bundle file bytes match this unsigned manifest. "
                           "To detect forged replacement, compare its SHA-256 to a digest pinned "
                           "outside the archive. This does not verify remote deployment or business results."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify generated OWG runtime file integrity (offline, read-only)")
    parser.add_argument("--root", default=".", help="Extracted runtime project directory")
    parser.add_argument("--manifest-sha256", default=None,
                        help="SHA-256 pinned separately from this unsigned exported ZIP")
    args = parser.parse_args(argv)
    try:
        result = verify_directory(args.root, args.manifest_sha256)
    except ReleaseIntegrityError as exc:
        print("Release file verification FAILED: " + str(exc), file=sys.stderr)
        return 2
    print("Generated file integrity passed (" + str(result["verified_generated_file_count"]) + " files).")
    print("Manifest SHA-256: " + result["manifest_sha256"])
    print("No independent signature, remote deployment or business outcome was verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
