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
import stat
from zipfile import ZipFile, BadZipFile, ZIP_STORED, ZIP_DEFLATED

FORMAT = "openworkgraph.customer-runtime-file-manifest.v1"
MANIFEST_NAME = "release-manifest.json"
MAX_FILES = 100
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_EXPANDED_BYTES = 64 * 1024 * 1024
REQUIRED_GENERATED_FILES = frozenset({
    "workflow.spec.json", "verify_release.py", "execute.py", "runtime.py",
    "agent.py", "README.md", "AGENTS.md", "test_execution.py",
})
_HEX = re.compile(r"^[0-9a-f]{64}$")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
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
    with manifest_path.open("rb") as source:
        raw = source.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ReleaseIntegrityError("Release manifest is too large")
    return _verify_inventory(raw, lambda name: _directory_file(root, name), expected_manifest_sha256)[0]


def _directory_file(root, name):
    path = root
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ReleaseIntegrityError('Generated file inventory contains a symbolic link')
    if not path.is_file():
        raise ReleaseIntegrityError('Generated file missing or changed: ' + name)
    with path.open('rb') as source:
        return source.read(MAX_FILE_BYTES + 1)


def _verify_inventory(raw, read_file, expected_manifest_sha256):
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
    verified_spec_bytes = None
    for name, digest in hashes.items():
        if not _safe_name(name) or not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ReleaseIntegrityError("Invalid or unsafe inventory path/digest")
        data = read_file(name)
        if len(data) > MAX_FILE_BYTES or _hash(data) != digest:
            raise ReleaseIntegrityError("Generated file missing or changed: " + name)
        if name == "workflow.spec.json":
            verified_spec_bytes = data
    try:
        spec = json.loads(verified_spec_bytes, object_pairs_hook=_unique)
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
    result = {
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
    return result, manifest, spec


def _archive_snapshot(path, expected_manifest_sha256):
    """Read and verify a closed generated ZIP inventory without extracting it.

    A trusted verifier must be used. Pins are independent of the ZIP, not
    fetched from it. Member payloads are read only within bounded sizes.
    """
    if not isinstance(expected_manifest_sha256, str) or not _HEX.fullmatch(expected_manifest_sha256):
        raise ReleaseIntegrityError("Archive review requires an independently pinned manifest digest")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseIntegrityError("Select a regular runtime ZIP file")
    try:
        with path.open("rb") as source:
            # Bound the central directory and entire archive before ZipFile
            # parses metadata. One descriptor, no extraction or temp writes.
            raw = source.read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ReleaseIntegrityError("Runtime ZIP exceeds the archive byte limit")
        from io import BytesIO
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 2 <= len(entries) <= MAX_FILES + 1:
                raise ReleaseIntegrityError("Runtime ZIP exceeds the member count limit")
            members, aliases = {}, set()
            total = 0
            for member in entries:
                name = member.filename
                # Reject NUL truncation, directory entries, traversal, case
                # aliases, Windows device names and nonregular Unix types.
                if (member.orig_filename != name or member.is_dir()
                        or (name != MANIFEST_NAME and not _safe_name(name))):
                    raise ReleaseIntegrityError("Unsafe runtime ZIP member path")
                for part in name.split("/"):
                    stem=part.split(".")[0].upper()
                    if part.endswith(".") or stem in {"CON","PRN","AUX","NUL",
                        *("COM"+str(i) for i in range(1,10)),
                        *("LPT"+str(i) for i in range(1,10))}:
                        raise ReleaseIntegrityError("Unsafe cross-platform runtime ZIP member path")
                folded=name.casefold()
                if folded in aliases:
                    raise ReleaseIntegrityError("Duplicate or case-aliased runtime ZIP member")
                aliases.add(folded)
                mode=(member.external_attr >> 16) & 0xffff
                if stat.S_IFMT(mode) not in (0,stat.S_IFREG) or member.external_attr & 0x10:
                    raise ReleaseIntegrityError("Nonregular runtime ZIP member")
                if member.flag_bits & 1 or member.compress_type not in (ZIP_STORED,ZIP_DEFLATED):
                    raise ReleaseIntegrityError("Encrypted or unsupported runtime ZIP compression")
                cap=MAX_MANIFEST_BYTES if name==MANIFEST_NAME else MAX_FILE_BYTES
                if member.file_size>cap:
                    raise ReleaseIntegrityError("Runtime ZIP member exceeds the expanded byte limit")
                total+=member.file_size
                if total>MAX_TOTAL_EXPANDED_BYTES:
                    raise ReleaseIntegrityError("Runtime ZIP exceeds the total expanded byte limit")
                members[name]=member
            if MANIFEST_NAME not in members:
                raise ReleaseIntegrityError("Release manifest is missing from runtime ZIP")
            # A path cannot be both a regular file and an ancestor.
            for name in aliases:
                parts=name.split("/")
                if any("/".join(parts[:i]) in aliases for i in range(1,len(parts))):
                    raise ReleaseIntegrityError("Conflicting runtime ZIP member paths")
            def read(name,cap=MAX_FILE_BYTES):
                if name not in members:
                    raise ReleaseIntegrityError("Generated file missing or changed: "+name)
                with archive.open(members[name]) as stream:
                    data=stream.read(cap+1)
                if len(data)>cap or len(data)!=members[name].file_size:
                    raise ReleaseIntegrityError("Runtime ZIP member has inconsistent size")
                return data
            result,manifest,spec=_verify_inventory(read(MANIFEST_NAME,MAX_MANIFEST_BYTES),
                read,expected_manifest_sha256)
            if set(members)!=(set(manifest["files"])|{MANIFEST_NAME}):
                raise ReleaseIntegrityError("Runtime ZIP has untracked members; review a clean generated archive")
        return {**result,"archive_sha256":_hash(raw),"archive_bytes":len(raw),
                "archive_inventory_closed":True,"archive_extracted":False,
                "execution_authorized":False},manifest,spec
    except (BadZipFile,RuntimeError,NotImplementedError,OSError,EOFError) as exc:
        raise ReleaseIntegrityError("Runtime ZIP could not be checked") from None


def verify_archive(path, expected_manifest_sha256):
    """Return exact generated archive verification, with no extraction."""
    return _archive_snapshot(path,expected_manifest_sha256)[0]


def compare_archives(baseline_path,candidate_path,baseline_manifest_sha256,candidate_manifest_sha256):
    """Compare two independently pinned generated archives before extraction."""
    left,before,old_spec=_archive_snapshot(baseline_path,baseline_manifest_sha256)
    right,after,new_spec=_archive_snapshot(candidate_path,candidate_manifest_sha256)
    result=_compare_verified(before,after,old_spec,new_spec,baseline_manifest_sha256,candidate_manifest_sha256)
    return {**result,"baseline_archive_sha256":left["archive_sha256"],
            "candidate_archive_sha256":right["archive_sha256"],
            "archive_bytes_changed":left["archive_sha256"]!=right["archive_sha256"],
            "both_archive_inventories_closed":True,"archives_extracted":False}


def _pinned_inventory(root: Path | str, pin: str) -> dict:
    if not isinstance(pin, str) or not _HEX.fullmatch(pin):
        raise ReleaseIntegrityError("Both releases require separately pinned manifest SHA-256 digests")
    verify_directory(root, pin)
    # Retain exactly the manifest that was verified, not an unpinned second
    # read. This remains a local snapshot, not a live filesystem lock.
    path = Path(root) / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise ReleaseIntegrityError("Release manifest changed during comparison")
    with path.open("rb") as source:
        raw = source.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES or _hash(raw) != pin:
        raise ReleaseIntegrityError("Release manifest changed during comparison")
    return json.loads(raw, object_pairs_hook=_unique)


def _pinned_spec(root: Path | str, manifest: dict) -> dict:
    """Read bounded spec bytes and verify the digest again before using them."""
    path = Path(root) / "workflow.spec.json"
    if path.is_symlink() or not path.is_file():
        raise ReleaseIntegrityError("Runtime specification changed during comparison")
    with path.open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES or _hash(raw) != manifest["workflow_spec_sha256"]:
        raise ReleaseIntegrityError("Runtime specification changed during comparison")
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ReleaseIntegrityError("Nonfinite JSON value")))


def _structural_changes(before: dict, after: dict) -> dict:
    """Describe exact saved model changes by stable IDs, never disclose values."""
    old, new = before["process_model"], after["process_model"]
    def differs(a, b):
        # Python equality treats True, 1 and 1.0 as equal. Their JSON
        # representations can carry distinct customer rule semantics.
        return json.dumps(a, sort_keys=True, allow_nan=False) != json.dumps(b, sort_keys=True, allow_nan=False)
    groups = {
        "activities": ("id",),
        "transitions": ("source", "target"),
        "business_rules": ("id",),
        "decision_tables": ("id",),
        "outcome_expectations": ("terminal_activity_id", "operation"),
    }
    changes = {}
    for name, keys in groups.items():
        def index(model):
            rows = model.get(name, [])
            if not isinstance(rows, list) or len(rows) > 100:
                raise ReleaseIntegrityError("Invalid bounded model inventory")
            result = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ReleaseIntegrityError("Invalid model inventory row")
                identity = tuple(row.get(key) for key in keys)
                if any(not isinstance(value, str) or not value or len(value) > 200 for value in identity) or identity in result:
                    raise ReleaseIntegrityError("Invalid or duplicate model inventory identity")
                result[identity] = row
            return result
        a, b = index(old), index(new)
        def identity(k):
            return dict(zip(keys, k))
        changes[name] = {
            "added": [identity(k) for k in sorted(b.keys() - a.keys())],
            "removed": [identity(k) for k in sorted(a.keys() - b.keys())],
            "changed": [{**identity(k), "changed_fields": sorted(
                field for field in a[k].keys() | b[k].keys()
                if field not in a[k] or field not in b[k] or differs(a[k][field], b[k][field]))}
                for k in sorted(a.keys() & b.keys()) if differs(a[k], b[k])],
            "retained_identity_order_changed": ([k for k in a if k in b] != [k for k in b if k in a]),
        }
    def terminals(model):
        values = model.get("terminal_activity_ids", [])
        if (not isinstance(values, list) or len(values) > 40
                or any(not isinstance(value, str) or not value or len(value) > 200 for value in values)
                or len(set(values)) != len(values)):
            raise ReleaseIntegrityError("Invalid terminal activity inventory")
        return set(values)
    return {
        "keyed_model_changes": changes,
        "entry_activity_changed": old.get("entry_activity_id") != new.get("entry_activity_id"),
        "terminal_activity_ids_changed": terminals(old) != terminals(new),
        "other_changed_model_fields": sorted(
            k for k in old.keys() | new.keys() if k not in groups
            and (k not in old or k not in new or differs(old[k], new[k]))),
        "changed_runtime_contract_fields": sorted(
            k for k in before.keys() | after.keys()
            if k not in {"workflow_id", "revision", "content_sha256", "process_model", "process_model_sha256"}
            and (k not in before or k not in after or differs(before[k], after[k]))),
        "model_field_values_returned": False,
        "structural_changes_establish_compatibility": False,
    }


def compare_directories(baseline_root: Path | str, candidate_root: Path | str,
                        baseline_manifest_sha256: str, candidate_manifest_sha256: str) -> dict:
    """Review a proposed runtime upgrade without executing either release.

    Distinguish the saved design identity, specification bytes and generated
    file changes. All comparisons are between independently pinned snapshots.
    Customer-managed files, dependencies and provider behavior are outside
    the generated manifest and cannot be declared compatible here.
    """
    before = _pinned_inventory(baseline_root, baseline_manifest_sha256)
    after = _pinned_inventory(candidate_root, candidate_manifest_sha256)
    return _compare_verified(before,after,_pinned_spec(baseline_root,before),
        _pinned_spec(candidate_root,after),baseline_manifest_sha256,candidate_manifest_sha256)


def _compare_verified(before,after,old_spec,new_spec,baseline_manifest_sha256,candidate_manifest_sha256):
    for inventory in (before, after):
        ref = inventory["workflow_reference"]
        if (not isinstance(ref, dict) or set(ref) != {"workflow_id", "revision", "content_sha256"}
                or not isinstance(ref["workflow_id"], str) or not ref["workflow_id"]
                or type(ref["revision"]) is not int or ref["revision"] < 1
                or not isinstance(ref["content_sha256"], str) or not _HEX.fullmatch(ref["content_sha256"])):
            raise ReleaseIntegrityError("Invalid versioned release identity")
    if before["workflow_reference"]["workflow_id"] != after["workflow_reference"]["workflow_id"]:
        raise ReleaseIntegrityError("Compare releases of the same saved workflow ID")
    old, new = before["files"], after["files"]
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(name for name in old.keys() & new.keys() if old[name] != new[name])
    unchanged = sorted(name for name in old.keys() & new.keys() if old[name] == new[name])
    old_ref, new_ref = before["workflow_reference"], after["workflow_reference"]
    direction = ("newer_revision" if new_ref["revision"] > old_ref["revision"]
                 else "older_revision" if new_ref["revision"] < old_ref["revision"]
                 else "same_revision_same_content" if new_ref == old_ref
                 else "same_revision_different_content")
    spec_changed = before["workflow_spec_sha256"] != after["workflow_spec_sha256"]
    model_changed = before["process_model_sha256"] != after["process_model_sha256"]
    model_delta = _structural_changes(old_spec,new_spec)
    return {
        "format": "openworkgraph.customer-runtime-release-comparison.v1",
        "baseline": {"manifest_sha256": baseline_manifest_sha256, "workflow_reference": old_ref,
                     "workflow_spec_sha256": before["workflow_spec_sha256"],
                     "process_model_sha256": before["process_model_sha256"]},
        "candidate": {"manifest_sha256": candidate_manifest_sha256, "workflow_reference": new_ref,
                      "workflow_spec_sha256": after["workflow_spec_sha256"],
                      "process_model_sha256": after["process_model_sha256"]},
        "revision_direction": direction,
        "specification_bytes_changed": spec_changed,
        "process_model_changed": model_changed,
        "design_changes": model_delta,
        "generated_file_changes": {
            "added": [{"path": n, "candidate_sha256": new[n]} for n in added],
            "removed": [{"path": n, "baseline_sha256": old[n]} for n in removed],
            "changed": [{"path": n, "baseline_sha256": old[n], "candidate_sha256": new[n]} for n in changed],
            "unchanged_count": len(unchanged),
        },
        "generated_files_changed": bool(added or removed or changed),
        "requires_review_before_upgrade": bool(added or removed or changed or old_ref != new_ref),
        "both_generated_inventories_verified_against_pins": True,
        "customer_managed_files_compared": False,
        "dependency_compatibility_verified": False,
        "runtime_behavior_compatibility_verified": False,
        "execution_authorized": False,
        "independent_signature_verified": False,
        "remote_deployment_verified": False,
        "business_outcomes_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify generated OWG runtime file integrity (offline, read-only)")
    source=parser.add_mutually_exclusive_group()
    source.add_argument("--root", default=".", help="Extracted runtime project directory")
    source.add_argument("--archive", help="Verify generated ZIP directly, without extraction")
    parser.add_argument("--manifest-sha256", default=None,
                        help="SHA-256 pinned separately from this unsigned exported ZIP")
    candidate=parser.add_mutually_exclusive_group()
    candidate.add_argument("--compare-root", default=None, help="Candidate extracted release directory")
    candidate.add_argument("--compare-archive", default=None, help="Candidate generated ZIP (requires --archive)")
    parser.add_argument("--compare-manifest-sha256", default=None,
                        help="Separately pinned candidate manifest SHA-256")
    args = parser.parse_args(argv)
    if args.archive is not None:
        if args.compare_root is not None or (args.compare_manifest_sha256 is not None and args.compare_archive is None):
            parser.error("Archive comparison requires --archive and --compare-archive")
        try:
            result=(compare_archives(args.archive,args.compare_archive,args.manifest_sha256,args.compare_manifest_sha256)
                    if args.compare_archive is not None else verify_archive(args.archive,args.manifest_sha256))
        except (ReleaseIntegrityError,OSError,ValueError,RecursionError) as exc:
            print("Archive review FAILED: "+(str(exc) if isinstance(exc,ReleaseIntegrityError) else "local archive could not be checked"),file=sys.stderr)
            return 2
        print(json.dumps(result,sort_keys=True,indent=2,allow_nan=False))
        return 0
    if args.compare_archive is not None:
        parser.error("--compare-archive requires --archive")
    if args.compare_root is not None:
        try:
            comparison = compare_directories(args.root, args.compare_root,
                                             args.manifest_sha256, args.compare_manifest_sha256)
        except (ReleaseIntegrityError, OSError, ValueError, RecursionError) as exc:
            print("Release comparison FAILED: " + (str(exc) if isinstance(exc, ReleaseIntegrityError)
                                                   else "local release could not be checked"), file=sys.stderr)
            return 2
        print(json.dumps(comparison, sort_keys=True, indent=2, allow_nan=False))
        return 0
    if args.compare_manifest_sha256 is not None:
        parser.error("--compare-manifest-sha256 requires --compare-root")
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
