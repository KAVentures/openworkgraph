from __future__ import annotations

"""Opt-in local Open Work preview for GitHub PRs in one selected repository.

Uses the existing gh CLI authentication and OWG's separately consented
GitHub outcome-tracking toggle. Never scans all organizations, changes a PR,
reads comments/description, or asserts review obligation from an open status.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import subprocess
from typing import Any, Callable

from . import outcome_tracker
from .gh_cli import find_gh_cli

_REPO = re.compile(r"^([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})$")
_DECISIONS = {"APPROVED", "REVIEW_REQUIRED", "CHANGES_REQUESTED", ""}
_PROCESS_SECRET = secrets.token_bytes(32)
_MAX_ROWS = 100


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "format": "openworkgraph.open-work-preview.v1",
        "provider": "github",
        "availability": "unknown", "reason": reason,
        "candidates": [], "source_coverage_complete": False,
        "source_reported_not_human_observed": True,
        "business_obligation_verified": False,
        "read_only": True, "persisted": False,
    }


def _valid_repo(repo: str) -> bool:
    m = _REPO.fullmatch(repo)
    if not m:
        return False
    return all(value not in {".", ".."} and
               not value.startswith("-") and not value.endswith(".")
               for value in m.groups())


def _item_ref(repository: str, number: int) -> str:
    material = f"github:{repository}:pull:{number}".encode("utf-8")
    return "owg:w:" + hmac.new(_PROCESS_SECRET, material, hashlib.sha256).hexdigest()[:32]


def github_open_work_preview(
    repository: str, *, explicit_scan_authorized: bool = False,
    limit: int = 50, runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = find_gh_cli,
    consent: Callable[[], bool] = outcome_tracker.enabled,
) -> dict[str, Any]:
    if not explicit_scan_authorized:
        return _unknown("explicit_inventory_scan_not_authorized")
    if not _valid_repo(repository):
        raise ValueError("repository must be an exact owner/name slug")
    if type(limit) is not int or not 1 <= limit <= _MAX_ROWS:
        raise ValueError("limit must be between 1 and 100")
    # A GitHub CLI login is NOT sufficient by itself to grant an OWG scan:
    # the separate existing GitHub source-status consent must also be enabled.
    if not consent():
        return _unknown("existing_github_outcome_tracking_opt_in_off")
    binary = which("gh")
    if not binary:
        return _unknown("github_cli_not_installed")

    command = [
        binary, "pr", "list", "--repo", repository, "--state", "open",
        "--limit", str(limit), "--json",
        "number,state,isDraft,reviewDecision,updatedAt",
    ]
    try:
        response = runner(command, capture_output=True, text=True, timeout=15)
        if response.returncode != 0 or len(response.stdout or "") > 250_000:
            return _unknown("github_cli_read_failed")
        values = json.loads(response.stdout or "")
        if not isinstance(values, list) or len(values) > limit:
            return _unknown("invalid_github_open_pr_inventory")
    except (OSError, ValueError, subprocess.SubprocessError):
        return _unknown("github_cli_read_failed")
    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            return _unknown("invalid_github_pull_request_entry")
        number = value.get("number")
        decision = value.get("reviewDecision")
        if decision is None:
            decision = ""
        if (type(number) is not int or not 1 <= number <= 1_000_000_000
            or number in seen or value.get("state") != "OPEN"
            or type(value.get("isDraft")) is not bool
            or decision not in _DECISIONS):
            return _unknown("conflicting_github_pull_request_status")
        stamp = value.get("updatedAt")
        try:
            when = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
            if when.tzinfo is None:
                return _unknown("invalid_github_update_timestamp")
            iso = when.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            return _unknown("invalid_github_update_timestamp")
        seen.add(number)
        rows.append({
            "work_item_ref": _item_ref(repository, number),
            "kind": "open_pull_request",
            "source_status": "open",
            "source_last_updated_at": iso,
            "attention_reason": (
                "source_review_required" if decision == "REVIEW_REQUIRED"
                else "open_draft_review_candidate" if value["isDraft"]
                else "open_pr_review_candidate"
            ),
            "review_status": decision.lower() if decision else "unknown",
            "priority": "unassigned",
            "historically_observed_in_owg": "not_checked",
            "business_obligation_verified": False,
            "pr_merge_succeeded": False,
            "agent_execution_authorized": False,
        })
    return {
        "format": "openworkgraph.open-work-preview.v1",
        "provider": "github",
        "availability": "source_reported",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "candidates": rows,
        "returned": len(rows),
        "source_coverage_complete": len(values) < limit,
        "source_limit_reached": len(values) >= limit,
        "source_reported_not_human_observed": True,
        "business_obligation_verified": False,
        "native_repo_and_pr_numbers_included": False,
        "comments_and_titles_included": False,
        "read_only": True,
        "persisted": False,
        "automatic_actions": False,
        "ephemeral_references_reset_when_service_restarts": True,
    }


__all__ = ["github_open_work_preview"]
