from __future__ import annotations

"""Local-only proposal/review/apply workflow for declared policy manifests.

This module deliberately does not expose network mutation. Candidate manifests are
validated through the existing declared-policy validator, stored as immutable local
proposal material, and can be activated only by the separate interactive CLI.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import DATA_DIR
from .declared_policy import DeclaredPolicyError, load_declared_policy_manifest, policy_manifest_path


_PROPOSAL_SCHEMA = "1.0"
_MAX_CANDIDATE_BYTES = 256 * 1024
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{20}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PolicyProposalError(ValueError):
    pass


def policy_proposal_dir() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_PROPOSAL_DIR", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "policy_proposals"


def policy_history_dir() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_HISTORY_DIR", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "policy_history"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _proposal_id_for(base_sha: str | None, candidate_sha: str) -> str:
    if base_sha is not None and not _SHA256_RE.fullmatch(base_sha):
        raise PolicyProposalError("invalid proposal base digest")
    if not _SHA256_RE.fullmatch(candidate_sha):
        raise PolicyProposalError("invalid proposal candidate digest")
    seed = f"{base_sha or 'none'}:{candidate_sha}".encode("utf-8")
    return "proposal-" + hashlib.sha256(seed).hexdigest()[:20]


def _secure_mode(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


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
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _canonical_manifest_bytes(source: Path) -> bytes:
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise PolicyProposalError("unable to read candidate policy manifest") from exc
    if not raw or len(raw) > _MAX_CANDIDATE_BYTES:
        raise PolicyProposalError("candidate policy manifest must be 1..256 KiB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyProposalError("candidate policy manifest is not valid UTF-8 JSON") from exc
    canonical = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(canonical) > _MAX_CANDIDATE_BYTES:
        raise PolicyProposalError("canonical candidate policy manifest exceeds 256 KiB")
    return canonical


def _validate_manifest_file(path: Path) -> dict[str, Any]:
    try:
        return load_declared_policy_manifest(path)
    except DeclaredPolicyError as exc:
        raise PolicyProposalError(str(exc)) from exc


def _current_manifest_state() -> tuple[str | None, dict[str, Any]]:
    path = policy_manifest_path()
    if not path.exists():
        return None, {
            "schema_version": "1.0",
            "manifest_present": False,
            "manifest_sha256": None,
            "policy_count": 0,
            "policies": [],
        }
    public = _validate_manifest_file(path)
    return str(public.get("manifest_sha256") or "") or None, public


def _proposal_paths(proposal_id: str) -> tuple[Path, Path]:
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")):
        raise PolicyProposalError("invalid proposal_id")
    root = policy_proposal_dir()
    return root / f"{proposal_id}.proposal.json", root / f"{proposal_id}.candidate.json"


def _policy_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("policy_id") or ""), str(item.get("version") or "")


def _policy_compare_shape(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("policy_id", "version", "status", "family_key", "source_type", "source_ref_hash", "rules")
    }


def _policy_label(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("policy_id", "version", "status", "family_key", "source_type")
    }


def proposal_diff(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    before = {_policy_key(item): item for item in current.get("policies") or []}
    after = {_policy_key(item): item for item in candidate.get("policies") or []}
    added = [_policy_label(after[key]) for key in sorted(after.keys() - before.keys())]
    removed = [_policy_label(before[key]) for key in sorted(before.keys() - after.keys())]
    modified = [
        {
            "policy_id": key[0],
            "version": key[1],
            "before": _policy_label(before[key]),
            "after": _policy_label(after[key]),
        }
        for key in sorted(before.keys() & after.keys())
        if _policy_compare_shape(before[key]) != _policy_compare_shape(after[key])
    ]
    return {
        "current_policy_count": len(before),
        "candidate_policy_count": len(after),
        "added": added,
        "removed": removed,
        "modified": modified,
        "change_count": len(added) + len(removed) + len(modified),
        "requires_human_review": True,
    }


def _candidate_public(candidate_path: Path) -> dict[str, Any]:
    public = _validate_manifest_file(candidate_path)
    return {
        "schema_version": public.get("schema_version"),
        "manifest_present": public.get("manifest_present"),
        "manifest_sha256": public.get("manifest_sha256"),
        "policy_count": public.get("policy_count"),
        "policies": public.get("policies") or [],
    }


def create_policy_proposal(candidate_source: Path) -> dict[str, Any]:
    source = Path(candidate_source).expanduser()
    canonical = _canonical_manifest_bytes(source)
    candidate_sha = _sha256(canonical)
    base_sha, _current_public = _current_manifest_state()
    proposal_id = _proposal_id_for(base_sha, candidate_sha)
    meta_path, candidate_path = _proposal_paths(proposal_id)

    if meta_path.exists() or candidate_path.exists():
        if meta_path.exists() and candidate_path.exists():
            existing = load_policy_proposal(proposal_id)
            if existing.get("candidate_manifest_sha256") == candidate_sha and existing.get("base_manifest_sha256") == base_sha:
                return existing
        raise PolicyProposalError("proposal path collision or incomplete existing proposal")

    _atomic_write(candidate_path, canonical)
    try:
        candidate_public = _candidate_public(candidate_path)
        if candidate_public.get("manifest_sha256") != candidate_sha:
            raise PolicyProposalError("candidate manifest digest mismatch after validation")
        metadata = {
            "schema_version": _PROPOSAL_SCHEMA,
            "proposal_id": proposal_id,
            "created_at": _utc_now(),
            "base_manifest_sha256": base_sha,
            "candidate_manifest_sha256": candidate_sha,
            "candidate_file": candidate_path.name,
        }
        _atomic_write(meta_path, (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    except Exception:
        try:
            candidate_path.unlink()
        except OSError:
            pass
        raise
    return load_policy_proposal(proposal_id)


def _read_metadata(meta_path: Path) -> dict[str, Any]:
    try:
        raw = meta_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyProposalError("invalid policy proposal metadata") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _PROPOSAL_SCHEMA:
        raise PolicyProposalError("unsupported policy proposal schema")
    proposal_id = str(payload.get("proposal_id") or "")
    if not _PROPOSAL_ID_RE.fullmatch(proposal_id) or meta_path.name != f"{proposal_id}.proposal.json":
        raise PolicyProposalError("proposal metadata identity mismatch")
    base_sha = payload.get("base_manifest_sha256")
    if base_sha is not None and (not isinstance(base_sha, str) or not _SHA256_RE.fullmatch(base_sha)):
        raise PolicyProposalError("invalid proposal base digest")
    candidate_sha = payload.get("candidate_manifest_sha256")
    if not isinstance(candidate_sha, str) or not _SHA256_RE.fullmatch(candidate_sha):
        raise PolicyProposalError("invalid proposal candidate digest")
    expected_id = _proposal_id_for(base_sha, candidate_sha)
    if proposal_id != expected_id:
        raise PolicyProposalError("proposal metadata integrity mismatch")
    return payload


def _activation_contract() -> dict[str, bool]:
    return {
        "network_write_available": False,
        "mcp_write_available": False,
        "interactive_local_apply_required": True,
    }


def load_policy_proposal(proposal_id: str) -> dict[str, Any]:
    meta_path, candidate_path = _proposal_paths(proposal_id)
    if not meta_path.exists() or not candidate_path.exists():
        raise PolicyProposalError("policy proposal not found")
    metadata = _read_metadata(meta_path)
    if metadata.get("candidate_file") != candidate_path.name:
        raise PolicyProposalError("proposal candidate filename mismatch")
    try:
        candidate_raw = candidate_path.read_bytes()
    except OSError as exc:
        raise PolicyProposalError("unable to read proposal candidate") from exc
    actual_sha = _sha256(candidate_raw)
    if actual_sha != metadata.get("candidate_manifest_sha256"):
        raise PolicyProposalError("proposal candidate digest mismatch")
    candidate_public = _candidate_public(candidate_path)
    if candidate_public.get("manifest_sha256") != actual_sha:
        raise PolicyProposalError("proposal candidate validation digest mismatch")
    current_sha, current_public = _current_manifest_state()
    stale = current_sha != metadata.get("base_manifest_sha256")
    current_diff = proposal_diff(current_public, candidate_public)
    return {
        "schema_version": metadata["schema_version"],
        "proposal_id": metadata["proposal_id"],
        "created_at": metadata.get("created_at"),
        "base_manifest_sha256": metadata.get("base_manifest_sha256"),
        "candidate_manifest_sha256": metadata["candidate_manifest_sha256"],
        "current_manifest_sha256": current_sha,
        "stale": stale,
        "candidate": candidate_public,
        "diff": current_diff,
        "current_diff": current_diff,
        "activation": _activation_contract(),
        "proposal_material_local_only": True,
        "integrity": {
            "proposal_id_pins_base_and_candidate": True,
            "candidate_digest_verified": True,
        },
    }


def list_policy_proposals() -> list[dict[str, Any]]:
    root = policy_proposal_dir()
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("proposal-*.proposal.json")):
        proposal_id = path.name.removesuffix(".proposal.json")
        try:
            item = load_policy_proposal(proposal_id)
        except PolicyProposalError:
            continue
        items.append({
            key: item.get(key)
            for key in (
                "proposal_id", "created_at", "base_manifest_sha256", "candidate_manifest_sha256", "stale", "current_diff",
            )
        })
    return items


def _archive_manifest(raw: bytes) -> Path:
    digest = _sha256(raw)
    path = policy_history_dir() / f"manifest-{digest}.json"
    if path.exists():
        if _sha256(path.read_bytes()) != digest:
            raise PolicyProposalError("history archive digest collision")
        return path
    _atomic_write(path, raw)
    return path


def _append_audit(record: dict[str, Any]) -> None:
    path = policy_history_dir() / "policy_admin_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "ab", closefd=True) as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        _secure_mode(path)


def _confirmation_text(proposal: dict[str, Any]) -> str:
    digest = str(proposal.get("candidate_manifest_sha256") or "")
    return f"APPLY {proposal['proposal_id']} {digest[:12]}"


def apply_policy_proposal(
    proposal_id: str,
    *,
    interactive: bool,
    prompt: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Activate one proposal after local interactive confirmation.

    `interactive` must be supplied by the CLI after checking that stdin/stdout are
    attached to a TTY. There is intentionally no force/noninteractive bypass.
    """
    if not interactive:
        raise PolicyProposalError("policy activation requires an interactive local terminal")
    proposal = load_policy_proposal(proposal_id)
    if proposal.get("stale"):
        raise PolicyProposalError("proposal is stale because the active manifest changed after proposal creation")

    expected = _confirmation_text(proposal)
    entered = str(prompt(f"Type exactly '{expected}' to activate this policy manifest: ") or "").strip()
    if entered != expected:
        raise PolicyProposalError("policy activation confirmation did not match")

    # Re-read after confirmation so review time cannot hide an intervening change.
    proposal = load_policy_proposal(proposal_id)
    if proposal.get("stale"):
        raise PolicyProposalError("proposal became stale before activation")
    _meta_path, candidate_path = _proposal_paths(proposal_id)
    candidate_raw = candidate_path.read_bytes()
    candidate_sha = _sha256(candidate_raw)
    if candidate_sha != proposal.get("candidate_manifest_sha256"):
        raise PolicyProposalError("proposal candidate changed before activation")

    active_path = policy_manifest_path()
    previous_raw: bytes | None = None
    previous_sha: str | None = None
    if active_path.exists():
        previous_raw = active_path.read_bytes()
        previous_sha = _sha256(previous_raw)
        if previous_sha != proposal.get("base_manifest_sha256"):
            raise PolicyProposalError("proposal became stale before activation")
        _validate_manifest_file(active_path)
        _archive_manifest(previous_raw)
    elif proposal.get("base_manifest_sha256") is not None:
        raise PolicyProposalError("proposal became stale before activation")

    _archive_manifest(candidate_raw)
    _atomic_write(active_path, candidate_raw)
    activated = _validate_manifest_file(active_path)
    if activated.get("manifest_sha256") != candidate_sha:
        raise PolicyProposalError("activated policy manifest digest mismatch")

    audit = {
        "event": "policy_proposal_applied",
        "at": _utc_now(),
        "proposal_id": proposal_id,
        "previous_manifest_sha256": previous_sha,
        "new_manifest_sha256": candidate_sha,
        "network_activation": False,
        "interactive_local_confirmation": True,
    }
    _append_audit(audit)
    return {
        "status": "applied",
        "proposal_id": proposal_id,
        "previous_manifest_sha256": previous_sha,
        "active_manifest_sha256": candidate_sha,
        "history_archive_present": True,
        "audit_recorded": True,
    }
