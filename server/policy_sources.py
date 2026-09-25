from __future__ import annotations

"""Trusted local policy-source synchronization.

This module detects policy-manifest changes in explicitly configured local sources
and prepares immutable policy proposals. It never activates policy, never fetches
remote content, and never trusts its sync-state file to suppress source validation.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from mcp_server.security import _looks_instruction_like

from .db import DATA_DIR
from .policy_proposals import PolicyProposalError, create_policy_proposal


_SOURCE_SCHEMA = "1.0"
_STATE_SCHEMA = "1.0"
_RECEIPT_SCHEMA = "1.0"
_MAX_CONFIG_BYTES = 128 * 1024
_MAX_SOURCE_BYTES = 256 * 1024
_MAX_SOURCES = 32
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_PROPOSAL_ID_RE = re.compile(r"^proposal-[0-9a-f]{20}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_GIT_PATH_PART_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SOURCE_TYPES = frozenset({"local_file", "git_file"})


class PolicySourceError(ValueError):
    pass


def policy_sources_config_path() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_SOURCES_FILE", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "policy_sources.json"


def policy_source_state_path() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_SOURCE_STATE", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "policy_source_state.json"


def policy_source_receipt_dir() -> Path:
    configured = str(os.getenv("WORKFLOW_OBSERVER_POLICY_SOURCE_RECEIPT_DIR", "")).strip()
    return Path(configured).expanduser() if configured else DATA_DIR / "policy_source_receipts"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_location(value: str) -> str:
    return "location:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


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


def _instruction_like(value: str) -> bool:
    return _looks_instruction_like(re.sub(r"[_:.-]+", " ", value))


def _validate_source_id(value: Any) -> str:
    source_id = str(value or "").strip().lower()
    if not _SOURCE_ID_RE.fullmatch(source_id) or _instruction_like(source_id):
        raise PolicySourceError("invalid policy source_id")
    return source_id


def _read_json_file(path: Path, *, max_bytes: int, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicySourceError(f"unable to read {label}") from exc
    if not raw or len(raw) > max_bytes:
        raise PolicySourceError(f"{label} has invalid size")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicySourceError(f"invalid {label} JSON") from exc
    if not isinstance(payload, dict):
        raise PolicySourceError(f"{label} must be a JSON object")
    return payload


def _validate_git_relative_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 512 or "\\" in text or text.startswith("/"):
        raise PolicySourceError("invalid git relative_path")
    path = PurePosixPath(text)
    parts = path.parts
    if not parts or len(parts) > 16 or any(part in {"", ".", ".."} or not _GIT_PATH_PART_RE.fullmatch(part) for part in parts):
        raise PolicySourceError("invalid git relative_path")
    return path.as_posix()


def load_policy_sources_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or policy_sources_config_path()
    if not config_path.exists():
        return {
            "schema_version": _SOURCE_SCHEMA,
            "config_present": False,
            "source_count": 0,
            "sources": [],
            "remote_fetch_available": False,
            "automatic_activation": False,
        }
    payload = _read_json_file(config_path, max_bytes=_MAX_CONFIG_BYTES, label="policy source configuration")
    if payload.get("schema_version") != _SOURCE_SCHEMA:
        raise PolicySourceError("unsupported policy source schema_version")
    sources_raw = payload.get("sources")
    if not isinstance(sources_raw, list) or len(sources_raw) > _MAX_SOURCES:
        raise PolicySourceError("policy sources must be a list with at most 32 items")
    sources: list[dict[str, Any]] = []
    ids: list[str] = []
    for raw in sources_raw:
        if not isinstance(raw, dict):
            raise PolicySourceError("policy source must be an object")
        source_id = _validate_source_id(raw.get("source_id"))
        source_type = str(raw.get("type") or "").strip().lower()
        if source_type not in _SOURCE_TYPES:
            raise PolicySourceError("unsupported policy source type")
        if source_type == "local_file":
            value = str(raw.get("path") or "").strip()
            if not value or len(value) > 1000:
                raise PolicySourceError("invalid local policy source path")
            source = {"source_id": source_id, "type": source_type, "path": value}
        else:
            root = str(raw.get("repo_root") or "").strip()
            if not root or len(root) > 1000:
                raise PolicySourceError("invalid git repo_root")
            source = {
                "source_id": source_id,
                "type": source_type,
                "repo_root": root,
                "relative_path": _validate_git_relative_path(raw.get("relative_path")),
            }
        sources.append(source)
        ids.append(source_id)
    if len(ids) != len(set(ids)):
        raise PolicySourceError("duplicate policy source_id")
    return {
        "schema_version": _SOURCE_SCHEMA,
        "config_present": True,
        "source_count": len(sources),
        "sources": sources,
        "remote_fetch_available": False,
        "automatic_activation": False,
    }


def _canonical_candidate(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_SOURCE_BYTES:
        raise PolicySourceError("policy source must be 1..256 KiB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicySourceError("policy source is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise PolicySourceError("policy source manifest must be a JSON object")
    canonical = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(canonical) > _MAX_SOURCE_BYTES:
        raise PolicySourceError("canonical policy source exceeds 256 KiB")
    return canonical


def _read_local_source(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    configured = Path(source["path"]).expanduser()
    try:
        resolved = configured.resolve(strict=True)
    except OSError as exc:
        raise PolicySourceError("local policy source does not exist") from exc
    if not resolved.is_file():
        raise PolicySourceError("local policy source is not a regular file")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise PolicySourceError("unable to read local policy source") from exc
    if len(raw) > _MAX_SOURCE_BYTES:
        raise PolicySourceError("local policy source exceeds 256 KiB")
    return raw, {
        "source_type": "local_file",
        "source_location_hash": _hash_location(str(resolved)),
        "git_commit_sha": None,
        "working_tree_differs_from_committed_source": None,
    }


def _git(repo: Path, *args: str) -> bytes:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=8,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PolicySourceError("unable to inspect configured git policy source") from exc
    if proc.returncode != 0:
        raise PolicySourceError("configured git policy source is unavailable")
    return bytes(proc.stdout)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _read_git_source(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    try:
        repo = Path(source["repo_root"]).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PolicySourceError("configured git repository does not exist") from exc
    if not repo.is_dir():
        raise PolicySourceError("configured git repo_root is not a directory")
    relative = _validate_git_relative_path(source["relative_path"])
    try:
        top = Path(_git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeDecodeError, OSError) as exc:
        raise PolicySourceError("unable to resolve configured git repository") from exc
    if not _same_path(top, repo):
        raise PolicySourceError("git repo_root must be the repository toplevel")
    try:
        commit = _git(repo, "rev-parse", "HEAD").decode("ascii").strip().lower()
    except UnicodeDecodeError as exc:
        raise PolicySourceError("invalid git commit identity") from exc
    if not _GIT_SHA_RE.fullmatch(commit):
        raise PolicySourceError("invalid git commit identity")
    # Pin the blob lookup to the exact commit we just resolved. Using HEAD again
    # would allow a concurrent local ref move to pair content with the wrong SHA.
    raw = _git(repo, "show", f"{commit}:{relative}")
    if not raw or len(raw) > _MAX_SOURCE_BYTES:
        raise PolicySourceError("committed git policy source must be 1..256 KiB")
    dirty = bool(_git(repo, "status", "--porcelain", "--", relative).strip())
    return raw, {
        "source_type": "git_file",
        "source_location_hash": _hash_location(f"{repo}:{relative}"),
        "git_commit_sha": commit,
        "working_tree_differs_from_committed_source": dirty,
        "committed_content_only": True,
    }


def _read_source(source: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    if source["type"] == "local_file":
        return _read_local_source(source)
    return _read_git_source(source)


def _load_state() -> dict[str, Any]:
    path = policy_source_state_path()
    if not path.exists():
        return {"schema_version": _STATE_SCHEMA, "sources": {}}
    try:
        payload = _read_json_file(path, max_bytes=_MAX_CONFIG_BYTES, label="policy source state")
    except PolicySourceError:
        return {"schema_version": _STATE_SCHEMA, "sources": {}, "state_recovered_from_invalid_file": True}
    if payload.get("schema_version") != _STATE_SCHEMA or not isinstance(payload.get("sources"), dict):
        return {"schema_version": _STATE_SCHEMA, "sources": {}, "state_recovered_from_invalid_file": True}
    return payload


def _write_state(state: dict[str, Any]) -> None:
    payload = {
        "schema_version": _STATE_SCHEMA,
        "sources": state.get("sources") if isinstance(state.get("sources"), dict) else {},
    }
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > _MAX_CONFIG_BYTES:
        raise PolicySourceError("policy source state exceeds 128 KiB")
    _atomic_write(policy_source_state_path(), raw)


def _receipt_core(*, source_id: str, proposal_id: str, source_sha: str, candidate_sha: str, provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _RECEIPT_SCHEMA,
        "source_id": source_id,
        "proposal_id": proposal_id,
        "source_sha256": source_sha,
        "candidate_manifest_sha256": candidate_sha,
        "source_type": provenance.get("source_type"),
        "source_location_hash": provenance.get("source_location_hash"),
        "git_commit_sha": provenance.get("git_commit_sha"),
        "working_tree_differs_from_committed_source": provenance.get("working_tree_differs_from_committed_source"),
        "committed_content_only": provenance.get("committed_content_only"),
        "automatic_activation": False,
    }


def _write_source_receipt(*, source_id: str, proposal_id: str, source_sha: str, candidate_sha: str, provenance: dict[str, Any]) -> str:
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")):
        raise PolicySourceError("invalid proposal identity for source receipt")
    core = _receipt_core(
        source_id=source_id,
        proposal_id=proposal_id,
        source_sha=source_sha,
        candidate_sha=candidate_sha,
        provenance=provenance,
    )
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt_id = "receipt-" + hashlib.sha256(canonical).hexdigest()[:20]
    path = policy_source_receipt_dir() / f"{receipt_id}.json"
    payload = {**core, "receipt_id": receipt_id}
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists():
        existing = _read_json_file(path, max_bytes=_MAX_CONFIG_BYTES, label="policy source receipt")
        if existing != payload:
            raise PolicySourceError("policy source receipt integrity mismatch")
        return receipt_id
    _atomic_write(path, raw)
    return receipt_id


def _proposal_from_bytes(canonical: bytes) -> dict[str, Any]:
    root = policy_source_state_path().parent
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".policy-source-candidate.", suffix=".json", dir=str(root))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical)
            handle.flush()
            os.fsync(handle.fileno())
        _secure_mode(tmp)
        return create_policy_proposal(tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def sync_policy_sources(path: Path | None = None) -> dict[str, Any]:
    """Scan approved sources and prepare proposals; never activate policy."""
    config = load_policy_sources_config(path)
    previous_state = _load_state()
    previous_sources = previous_state.get("sources") if isinstance(previous_state.get("sources"), dict) else {}
    next_sources: dict[str, Any] = {}
    results: list[dict[str, Any]] = []

    for source in config["sources"]:
        source_id = source["source_id"]
        prior = previous_sources.get(source_id) if isinstance(previous_sources.get(source_id), dict) else {}
        try:
            raw, provenance = _read_source(source)
            source_sha = _sha256(raw)
            canonical = _canonical_candidate(raw)
            candidate_sha = _sha256(canonical)
            source_changed = prior.get("source_sha256") != source_sha
            deduplicated = prior.get("candidate_manifest_sha256") == candidate_sha and bool(prior.get("proposal_id"))
            receipt_id: str | None = None
            try:
                proposal = _proposal_from_bytes(canonical)
                status = "proposal_ready"
                proposal_id = str(proposal.get("proposal_id") or "")
                candidate_manifest_sha = str(proposal.get("candidate_manifest_sha256") or "")
                base_manifest_sha = proposal.get("base_manifest_sha256")
                receipt_id = _write_source_receipt(
                    source_id=source_id,
                    proposal_id=proposal_id,
                    source_sha=source_sha,
                    candidate_sha=candidate_manifest_sha,
                    provenance=provenance,
                )
            except PolicyProposalError as exc:
                if "no policy changes" not in str(exc).lower():
                    raise
                status = "up_to_date"
                proposal_id = None
                candidate_manifest_sha = candidate_sha
                base_manifest_sha = None
                deduplicated = False

            record = {
                "source_sha256": source_sha,
                "candidate_manifest_sha256": candidate_manifest_sha,
                "proposal_id": proposal_id,
                "receipt_id": receipt_id,
                "status": status,
                "provenance": provenance,
            }
            next_sources[source_id] = record
            results.append({
                "source_id": source_id,
                "source_type": source["type"],
                "status": status,
                "source_changed_since_previous_scan": source_changed,
                "proposal_deduplicated_from_previous_scan": bool(deduplicated and proposal_id == prior.get("proposal_id")),
                "source_sha256": source_sha,
                "candidate_manifest_sha256": candidate_manifest_sha,
                "base_manifest_sha256": base_manifest_sha,
                "proposal_id": proposal_id,
                "receipt_id": receipt_id,
                "provenance": provenance,
                "automatic_activation": False,
            })
        except (PolicySourceError, PolicyProposalError) as exc:
            previous_good = prior.copy() if prior else {}
            previous_good["status"] = "error"
            previous_good["last_error"] = type(exc).__name__
            next_sources[source_id] = previous_good
            results.append({
                "source_id": source_id,
                "source_type": source["type"],
                "status": "error",
                "error_type": type(exc).__name__,
                "automatic_activation": False,
            })

    _write_state({"schema_version": _STATE_SCHEMA, "sources": next_sources})
    return {
        "schema_version": _SOURCE_SCHEMA,
        "config_present": config["config_present"],
        "source_count": config["source_count"],
        "results": results,
        "proposal_count": sum(1 for item in results if item.get("status") == "proposal_ready"),
        "up_to_date_count": sum(1 for item in results if item.get("status") == "up_to_date"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
        "remote_fetch_performed": False,
        "automatic_activation": False,
        "state_is_authoritative": False,
        "provenance_receipts_immutable": True,
    }


def policy_source_status(path: Path | None = None) -> dict[str, Any]:
    config = load_policy_sources_config(path)
    state = _load_state()
    state_sources = state.get("sources") if isinstance(state.get("sources"), dict) else {}
    items = []
    for source in config["sources"]:
        stored = state_sources.get(source["source_id"]) if isinstance(state_sources.get(source["source_id"]), dict) else {}
        items.append({
            "source_id": source["source_id"],
            "source_type": source["type"],
            "last_status": stored.get("status") or "never_scanned",
            "last_source_sha256": stored.get("source_sha256"),
            "last_candidate_manifest_sha256": stored.get("candidate_manifest_sha256"),
            "last_proposal_id": stored.get("proposal_id"),
            "last_receipt_id": stored.get("receipt_id"),
            "provenance": stored.get("provenance"),
        })
    return {
        "schema_version": _SOURCE_SCHEMA,
        "config_present": config["config_present"],
        "source_count": config["source_count"],
        "sources": items,
        "remote_fetch_available": False,
        "automatic_activation": False,
        "state_is_authoritative": False,
        "provenance_receipts_immutable": True,
    }
