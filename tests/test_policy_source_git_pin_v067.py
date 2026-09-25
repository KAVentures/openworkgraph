from __future__ import annotations

import json
from pathlib import Path

from server import policy_sources as ps


def _manifest() -> bytes:
    payload = {
        "schema_version": "1.0",
        "policies": [{
            "policy_id": "email-approval",
            "version": "2",
            "status": "active",
            "family_key": "human:email.send",
            "source_type": "repository_policy",
            "source_ref": "repo://policies/openworkgraph.json",
            "rules": [{
                "rule_id": "approval-before-submit",
                "type": "required_predecessor",
                "required_before": "approval_received:success",
                "trigger_step": "action:submit",
            }],
        }],
    }
    return (json.dumps(payload) + "\n").encode("utf-8")


def test_git_source_blob_is_read_from_exact_resolved_commit(monkeypatch, tmp_path: Path):
    repo = tmp_path / "policy-repo"
    repo.mkdir()
    commit = "a" * 40
    relative = "policies/openworkgraph.json"
    calls: list[tuple[str, ...]] = []

    def fake_git(repo_arg: Path, *args: str) -> bytes:
        assert repo_arg == repo.resolve()
        calls.append(tuple(args))
        if args == ("rev-parse", "--show-toplevel"):
            return str(repo.resolve()).encode("utf-8")
        if args == ("rev-parse", "HEAD"):
            return commit.encode("ascii")
        if args == ("show", f"{commit}:{relative}"):
            return _manifest()
        if args == ("status", "--porcelain", "--", relative):
            return b""
        raise AssertionError(f"unexpected git invocation: {args!r}")

    monkeypatch.setattr(ps, "_git", fake_git)
    raw, provenance = ps._read_git_source({
        "source_id": "repo-policy",
        "type": "git_file",
        "repo_root": str(repo),
        "relative_path": relative,
    })

    assert raw == _manifest()
    assert provenance["git_commit_sha"] == commit
    assert provenance["committed_content_only"] is True
    assert ("show", f"{commit}:{relative}") in calls
    assert ("show", f"HEAD:{relative}") not in calls
