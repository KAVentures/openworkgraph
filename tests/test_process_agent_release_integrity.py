"""Customer runtime release bytes and offline verifier (R17).

These synthetic tests are intentionally self-contained: no real provider,
customer credentials, agent tool calls or model inference are used.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from shared.process_agent_project import build_runtime_project
from shared.process_agent_release import ReleaseIntegrityError, verify_directory
from shared.process_model import ProcessModel


def _packet():
    model = ProcessModel(
        goal="Review synthetic approval",
        baseline_source="user_declared",
        baseline_path=["Receive", "Approve", "Close"],
        activities=[{"id": "receive", "label": "Receive"},
                    {"id": "approve", "label": "Approve", "kind": "end"}],
        transitions=[{"source": "receive", "target": "approve"}],
        entry_activity_id="receive", terminal_activity_ids=["approve"],
    )
    return {"format": "openworkgraph.versioned-process-export.v1",
            "workflow_id": "synthetic_release", "revision": 3,
            "content_sha256": "b" * 64, "process_model": model.model_dump(),
            "execution_authorization": False}


def _extract(root: Path):
    archive = build_runtime_project(_packet())
    assert archive == build_runtime_project(_packet()), "exported ZIP must be deterministic"
    with ZipFile(BytesIO(archive)) as z:
        names = z.namelist()
        assert names.count("release-manifest.json") == 1
        assert names.count("verify_release.py") == 1
        assert len(names) == len(set(names))
        z.extractall(root)
    return archive


def test_export_includes_offline_manifest_verifier_and_exact_workflow_identity(tmp_path):
    _extract(tmp_path)
    digest = hashlib.sha256((tmp_path / "release-manifest.json").read_bytes()).hexdigest()
    result = verify_directory(tmp_path, expected_manifest_sha256=digest)
    assert result["generated_file_integrity_ok"] is True
    assert result["manifest_sha256"] == digest
    assert result["workflow_reference"] == {
        "workflow_id": "synthetic_release", "revision": 3, "content_sha256": "b" * 64}
    assert result["verified_generated_file_count"] == 28
    assert result["independently_signed"] is False
    assert result["remote_deployment_verified"] is False
    assert result["remote_execution_or_outcome_verified"] is False
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    assert "release-manifest.json" not in manifest["files"]
    assert manifest["files"]["workflow.spec.json"] == hashlib.sha256(
        (tmp_path / "workflow.spec.json").read_bytes()).hexdigest()
    assert manifest["files"]["verify_release.py"] == hashlib.sha256(
        (tmp_path / "verify_release.py").read_bytes()).hexdigest()
    # Standard library only; --manifest-sha256 pins externally observed bytes.
    run = subprocess.run([sys.executable, "-S", "verify_release.py",
                          "--root", ".", "--manifest-sha256", digest],
                         cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert "No independent signature, remote deployment or business outcome" in run.stdout
    assert digest in run.stdout
    assert "verify_release.py --root ." in (tmp_path / "README.md").read_text()


@pytest.mark.parametrize("filename", ["agent.py", "workflow.spec.json", "verify_release.py"])
def test_modified_generated_file_is_refused_even_if_the_rest_is_intact(tmp_path, filename):
    _extract(tmp_path)
    target = tmp_path / filename
    target.write_bytes(target.read_bytes() + b"\n# corrupted")
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        verify_directory(tmp_path)
    run = subprocess.run([sys.executable, "-S", "verify_release.py", "--root", "."],
                         cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 2
    assert "FAILED" in run.stderr


def test_missing_file_replacement_and_malformed_manifest_are_refused(tmp_path):
    _extract(tmp_path)
    (tmp_path / "AGENTS.md").unlink()
    with pytest.raises(ReleaseIntegrityError, match="missing or changed"):
        verify_directory(tmp_path)
    _extract(tmp_path)
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(manifest.read_text().replace('"format": ', '"format": "fake", "format": ', 1))
    with pytest.raises(ReleaseIntegrityError, match="Duplicate field"):
        verify_directory(tmp_path)


def test_manifest_cannot_follow_parent_paths_or_untracked_symlinks(tmp_path):
    _extract(tmp_path)
    manifest = tmp_path / "release-manifest.json"
    data = json.loads(manifest.read_text())
    data["files"]["../private.txt"] = "a" * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ReleaseIntegrityError, match="unsafe inventory"):
        verify_directory(tmp_path)
    _extract(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside-verifier")
    outside.write_bytes((tmp_path / "agent.py").read_bytes())
    (tmp_path / "agent.py").unlink()
    try:
        (tmp_path / "agent.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This operating system cannot create a file symlink")
    with pytest.raises(ReleaseIntegrityError, match="symbolic link"):
        verify_directory(tmp_path)


def test_external_manifest_digest_rejects_forged_unsigned_self_consistent_archive(tmp_path):
    _extract(tmp_path)
    original = hashlib.sha256((tmp_path / "release-manifest.json").read_bytes()).hexdigest()
    agent = tmp_path / "agent.py"
    agent.write_bytes(agent.read_bytes() + b"\n# attacker edited the runner")
    manifest = tmp_path / "release-manifest.json"
    changed = json.loads(manifest.read_text())
    changed["files"]["agent.py"] = hashlib.sha256(agent.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(changed))
    # This documents the fundamental limit: an unsigned mutable manifest
    # cannot establish origin when both code and manifest are replaced.
    assert verify_directory(tmp_path)["generated_file_integrity_ok"] is True
    with pytest.raises(ReleaseIntegrityError, match="independently pinned digest"):
        verify_directory(tmp_path, expected_manifest_sha256=original)
    # Additional customer-managed files are not claimed to be covered.
    (tmp_path / "customer-bindings.json").write_text('{"enabled":false}')
    assert verify_directory(tmp_path)["generated_file_integrity_ok"] is True


def test_release_verification_does_not_claim_authorized_runtime(tmp_path):
    _extract(tmp_path)
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    assert manifest["independent_signature_present"] is False
    assert manifest["generated_files_verified_as_remote_deployment"] is False
    assert manifest["remote_business_outcomes_verified"] is False
    assert json.loads((tmp_path / "workflow.spec.json").read_text())["execution_authorization"] is False
