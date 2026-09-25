from __future__ import annotations

"""Read-only policy-source drift and provenance analysis.

This module compares the currently active declared-policy manifest with the current
content of explicitly configured local policy sources. It never creates proposals,
never activates policy, never fetches remote content, and treats source/receipt
uncertainty as unknown rather than as evidence of freshness or drift.
"""

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .declared_policy import DeclaredPolicyError, load_declared_policy_manifest, policy_manifest_path
from .policy_proposals import list_policy_proposals, proposal_diff
from .policy_sources import (
    PolicySourceError,
    _canonical_candidate,
    _read_source,
    load_policy_sources_config,
    policy_source_receipt_dir,
)


_DRIFT_SCHEMA = "1.0"
_RECEIPT_SCHEMA = "1.0"
_MAX_RECEIPTS = 4096
_MAX_RECEIPT_BYTES = 128 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_RE = re.compile(r"^receipt-[0-9a-f]{20}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{20}$")
_LOCATION_HASH_RE = re.compile(r"^location:[0-9a-f]{16}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class PolicySourceDriftError(ValueError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _candidate_public(canonical: bytes) -> dict[str, Any]:
    fd, tmp_name = tempfile.mkstemp(prefix="openworkgraph-policy-drift-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb", closefd=True) as handle:
            handle.write(canonical)
            handle.flush()
        try:
            return load_declared_policy_manifest(tmp)
        except DeclaredPolicyError as exc:
            raise PolicySourceDriftError("configured policy source is not a valid declared-policy manifest") from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _active_state() -> tuple[dict[str, Any], str | None]:
    try:
        public = load_declared_policy_manifest()
    except DeclaredPolicyError as exc:
        raise PolicySourceDriftError("active declared-policy manifest is invalid") from exc
    path = policy_manifest_path()
    if not path.exists():
        return public, None
    try:
        raw = path.read_bytes()
        canonical = _canonical_candidate(raw)
    except (OSError, PolicySourceError) as exc:
        raise PolicySourceDriftError("unable to canonicalize active declared-policy manifest") from exc
    return public, _sha256(canonical)


def _receipt_core(payload: dict[str, Any]) -> dict[str, Any]:
    source_id = str(payload.get("source_id") or "")
    proposal_id = str(payload.get("proposal_id") or "")
    source_sha = str(payload.get("source_sha256") or "")
    candidate_sha = str(payload.get("candidate_manifest_sha256") or "")
    source_type = payload.get("source_type")
    location_hash = payload.get("source_location_hash")
    git_commit = payload.get("git_commit_sha")
    dirty = payload.get("working_tree_differs_from_committed_source")
    committed_only = payload.get("committed_content_only")

    if not source_id or len(source_id) > 64:
        raise PolicySourceDriftError("invalid source provenance receipt")
    if not _PROPOSAL_ID_RE.fullmatch(proposal_id):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if not _SHA256_RE.fullmatch(source_sha) or not _SHA256_RE.fullmatch(candidate_sha):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if source_type not in {"local_file", "git_file"}:
        raise PolicySourceDriftError("invalid source provenance receipt")
    if not isinstance(location_hash, str) or not _LOCATION_HASH_RE.fullmatch(location_hash):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if git_commit is not None and (not isinstance(git_commit, str) or not _GIT_SHA_RE.fullmatch(git_commit)):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if dirty is not None and not isinstance(dirty, bool):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if committed_only is not None and not isinstance(committed_only, bool):
        raise PolicySourceDriftError("invalid source provenance receipt")
    if payload.get("automatic_activation") is not False:
        raise PolicySourceDriftError("invalid source provenance receipt")

    return {
        "schema_version": _RECEIPT_SCHEMA,
        "source_id": source_id,
        "proposal_id": proposal_id,
        "source_sha256": source_sha,
        "candidate_manifest_sha256": candidate_sha,
        "source_type": source_type,
        "source_location_hash": location_hash,
        "git_commit_sha": git_commit,
        "working_tree_differs_from_committed_source": dirty,
        "committed_content_only": committed_only,
        "automatic_activation": False,
    }


def _validate_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicySourceDriftError("unable to read source provenance receipt") from exc
    if not raw or len(raw) > _MAX_RECEIPT_BYTES:
        raise PolicySourceDriftError("invalid source provenance receipt")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicySourceDriftError("invalid source provenance receipt") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _RECEIPT_SCHEMA:
        raise PolicySourceDriftError("invalid source provenance receipt")
    receipt_id = str(payload.get("receipt_id") or "")
    if not _RECEIPT_ID_RE.fullmatch(receipt_id) or path.name != f"{receipt_id}.json":
        raise PolicySourceDriftError("invalid source provenance receipt")
    core = _receipt_core(payload)
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_id = "receipt-" + hashlib.sha256(canonical).hexdigest()[:20]
    if receipt_id != expected_id:
        raise PolicySourceDriftError("source provenance receipt integrity mismatch")
    return {**core, "receipt_id": receipt_id}


def _load_receipts() -> dict[str, Any]:
    root = policy_source_receipt_dir()
    if not root.exists():
        return {"receipts": [], "invalid_receipt_count": 0, "integrity_ok": True, "truncated": False}
    paths = sorted(root.glob("receipt-*.json"))
    truncated = len(paths) > _MAX_RECEIPTS
    paths = paths[:_MAX_RECEIPTS]
    valid: list[dict[str, Any]] = []
    invalid = 0
    for path in paths:
        try:
            valid.append(_validate_receipt(path))
        except PolicySourceDriftError:
            invalid += 1
    return {
        "receipts": valid,
        "invalid_receipt_count": invalid,
        "integrity_ok": invalid == 0 and not truncated,
        "truncated": truncated,
    }


def _safe_provenance(provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": provenance.get("source_type"),
        "source_location_hash": provenance.get("source_location_hash"),
        "git_commit_sha": provenance.get("git_commit_sha"),
        "working_tree_differs_from_committed_source": provenance.get("working_tree_differs_from_committed_source"),
        "committed_content_only": provenance.get("committed_content_only"),
    }


def _active_receipt_matches(receipts: list[dict[str, Any]], active_canonical_sha: str | None) -> list[dict[str, Any]]:
    if not active_canonical_sha:
        return []
    return [item for item in receipts if item.get("candidate_manifest_sha256") == active_canonical_sha]


def _proposal_for_candidate(proposals: list[dict[str, Any]], candidate_sha: str) -> dict[str, Any] | None:
    matches = [
        item for item in proposals
        if item.get("candidate_manifest_sha256") == candidate_sha and item.get("stale") is False
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return matches[0]


def policy_source_drift_status(config_path: Path | None = None) -> dict[str, Any]:
    """Return read-only semantic/provenance freshness for configured sources."""
    try:
        config = load_policy_sources_config(config_path)
    except PolicySourceError as exc:
        raise PolicySourceDriftError(str(exc)) from exc

    active_public, active_canonical_sha = _active_state()
    receipt_state = _load_receipts()
    receipts = list(receipt_state["receipts"])
    active_receipts = _active_receipt_matches(receipts, active_canonical_sha)
    proposals = list_policy_proposals()

    items: list[dict[str, Any]] = []
    for source in config.get("sources") or []:
        source_id = source["source_id"]
        source_type = source["type"]
        try:
            raw, provenance = _read_source(source)
            source_sha = _sha256(raw)
            canonical = _canonical_candidate(raw)
            candidate_sha = _sha256(canonical)
            candidate_public = _candidate_public(canonical)
            diff = proposal_diff(active_public, candidate_public)
            semantic_drift = int(diff.get("change_count") or 0) > 0
            matching_proposal = _proposal_for_candidate(proposals, candidate_sha) if semantic_drift else None
            if not semantic_drift:
                status = "in_sync"
            elif matching_proposal is not None:
                status = "drifted_proposal_ready"
            else:
                status = "drifted_no_proposal"

            origin_matches = [item for item in active_receipts if item.get("source_id") == source_id]
            origin_provenance = [
                {
                    "receipt_id": item.get("receipt_id"),
                    "proposal_id": item.get("proposal_id"),
                    "source_sha256": item.get("source_sha256"),
                    "git_commit_sha": item.get("git_commit_sha"),
                    "source_location_hash": item.get("source_location_hash"),
                }
                for item in origin_matches[:8]
            ]
            source_advanced = None
            if origin_matches:
                if source_type == "git_file":
                    source_advanced = all(
                        item.get("git_commit_sha") != provenance.get("git_commit_sha")
                        for item in origin_matches
                    )
                else:
                    source_advanced = all(item.get("source_sha256") != source_sha for item in origin_matches)

            items.append({
                "source_id": source_id,
                "source_type": source_type,
                "status": status,
                "active_policy_stale_relative_to_source": semantic_drift,
                "semantic_change_count": int(diff.get("change_count") or 0),
                "semantic_diff": diff,
                "active_manifest_sha256": active_public.get("manifest_sha256"),
                "active_manifest_canonical_sha256": active_canonical_sha,
                "current_source_sha256": source_sha,
                "current_candidate_manifest_sha256": candidate_sha,
                "current_source_provenance": _safe_provenance(provenance),
                "active_origin_provenance_known_for_source": bool(origin_matches),
                "active_origin_provenance": origin_provenance,
                "source_advanced_since_active_origin": source_advanced,
                "source_content_differs_from_active_canonical": (
                    active_canonical_sha is None or candidate_sha != active_canonical_sha
                ),
                "source_content_differs_but_policy_semantics_equal": (
                    not semantic_drift and active_canonical_sha is not None and candidate_sha != active_canonical_sha
                ),
                "proposal_available": matching_proposal is not None,
                "proposal_id": matching_proposal.get("proposal_id") if matching_proposal else None,
                "proposal_candidate_manifest_sha256": (
                    matching_proposal.get("candidate_manifest_sha256") if matching_proposal else None
                ),
                "automatic_activation": False,
                "remote_fetch_performed": False,
            })
        except (PolicySourceError, PolicySourceDriftError, DeclaredPolicyError, OSError, ValueError):
            items.append({
                "source_id": source_id,
                "source_type": source_type,
                "status": "source_unavailable",
                "active_policy_stale_relative_to_source": None,
                "semantic_change_count": None,
                "semantic_diff": None,
                "proposal_available": False,
                "proposal_id": None,
                "automatic_activation": False,
                "remote_fetch_performed": False,
            })

    active_provenance = [
        {
            "receipt_id": item.get("receipt_id"),
            "source_id": item.get("source_id"),
            "source_type": item.get("source_type"),
            "source_sha256": item.get("source_sha256"),
            "git_commit_sha": item.get("git_commit_sha"),
            "source_location_hash": item.get("source_location_hash"),
            "proposal_id": item.get("proposal_id"),
        }
        for item in active_receipts[:16]
    ]

    return {
        "schema_version": _DRIFT_SCHEMA,
        "config_present": bool(config.get("config_present")),
        "source_count": int(config.get("source_count") or 0),
        "active_manifest_present": bool(active_public.get("manifest_present")),
        "active_manifest_sha256": active_public.get("manifest_sha256"),
        "active_manifest_canonical_sha256": active_canonical_sha,
        "active_policy_provenance_known": bool(active_receipts),
        "active_policy_provenance_ambiguous": len(active_receipts) > 1,
        "active_policy_provenance": active_provenance,
        "receipt_integrity_ok": bool(receipt_state.get("integrity_ok")),
        "invalid_receipt_count": int(receipt_state.get("invalid_receipt_count") or 0),
        "receipt_scan_truncated": bool(receipt_state.get("truncated")),
        "sources": items,
        "in_sync_count": sum(1 for item in items if item.get("status") == "in_sync"),
        "drifted_count": sum(1 for item in items if str(item.get("status") or "").startswith("drifted_")),
        "source_unavailable_count": sum(1 for item in items if item.get("status") == "source_unavailable"),
        "automatic_activation": False,
        "remote_fetch_performed": False,
        "read_only": True,
        "interpretation": (
            "semantic drift compares the active declared-policy manifest with the current configured source; "
            "provenance freshness is reported separately and uncertainty is never treated as proof of freshness"
        ),
    }
