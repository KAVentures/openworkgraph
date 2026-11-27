"""Portable acceptance checker on actual generated release archives."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from test_process_agent_release_integrity import _extract


def _pin(root):
    return hashlib.sha256((root / "release-manifest.json").read_bytes()).hexdigest()


def _run(root, code, *args, env=None):
    return subprocess.run(
        [sys.executable, "-c", code, *args], cwd=root,
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_acceptance_requires_confirmation_and_exact_manifest(tmp_path):
    _extract(tmp_path)
    pin = _pin(tmp_path)
    command = [sys.executable, "check_runtime.py", "--manifest-sha256", pin]
    denied = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert denied.returncode == 2
    (tmp_path / "runtime.py").write_text("# tampered")
    denied = subprocess.run(command + ["--confirm-trusted-synthetic-tests"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert denied.returncode == 2
    assert not denied.stdout
    assert "Acceptance refused" in denied.stderr


@pytest.mark.parametrize("suite_status,exit_code", [
    ("passed", 0), ("incomplete", 3), ("failed", 1), ("timed_out", 1), ("runner_error", 1),
])
def test_aggregate_status_and_staging_excludes_customer_data(tmp_path, suite_status, exit_code):
    _extract(tmp_path)
    (tmp_path / "customer-bindings.json").write_text("SECRET_CUSTOMER_RECORD")
    code = r"""
import check_runtime as check
import sys
from pathlib import Path
from verify_release import verify_directory
def fake(module, root, home):
    assert root != Path.cwd()
    assert not (root / "customer-bindings.json").exists()
    assert home.is_dir()
    assert "OWG_EXECUTION_MCP_TOKEN" not in check._environment(home)
    assert "OPENAI_API_KEY" not in check._environment(home)
    assert check._environment(home)["HOME"] == str(home)
    verify_directory(root, sys.argv[1])
    return {"suite": module, "status": sys.argv[2], "counts": None}
check._run_suite = fake
raise SystemExit(check.main(["--manifest-sha256", sys.argv[1],
                             "--confirm-trusted-synthetic-tests"]))
"""
    env = {**os.environ, "OWG_EXECUTION_MCP_TOKEN": "SECRET_TOKEN", "OPENAI_API_KEY": "SECRET_KEY"}
    run = _run(tmp_path, code, _pin(tmp_path), suite_status, env=env)
    assert run.returncode == exit_code, run.stderr
    data = json.loads(run.stdout)
    assert len(data["suites"]) == 10
    assert {"test_run_queue", "test_binding_rehearsal", "test_heldout"} <= {suite["suite"] for suite in data["suites"]}
    assert data["manifest_sha256"] == _pin(tmp_path)
    assert data["status"] == ("failed" if exit_code == 1 else suite_status)
    assert not data["business_outcomes_verified"]
    assert not data["execution_authorized"]
    assert not data["os_sandbox_enforced"]
    assert "SECRET_" not in run.stdout
    assert (tmp_path / "customer-bindings.json").read_text() == "SECRET_CUSTOMER_RECORD"


def test_actual_generated_shadow_suite_and_skip_detection(tmp_path):
    _extract(tmp_path)
    code = r"""
import check_runtime as check
import json
from pathlib import Path
root = Path.cwd()
home = root / "test-home"
home.mkdir()
# Execute the actual generated structural tests in the isolated child.
result = check._run_suite("test_shadow", root, home)
assert result["status"] == "passed", result
assert result["counts"]["tests_run"] >= 5
# Test status handling with synthetic fixtures, without installing SDKs.
(root / "test_probe.py").write_text(
    "import unittest\nclass Probe(unittest.TestCase):\n"
    " @unittest.skip('dependency unavailable')\n"
    " def test_missing(self): pass\n")
skipped = check._run_suite("test_probe", root, home)
assert skipped["status"] == "incomplete", skipped
assert skipped["counts"]["skipped"] == 1
(root / "test_probe.py").write_text(
    "import unittest\nclass Probe(unittest.TestCase):\n"
    " def test_failure(self): self.fail('PRIVATE_TEST_DETAIL')\n")
failed = check._run_suite("test_probe", root, home)
assert failed["status"] == "failed", failed
assert failed["counts"]["failures"] == 1
print(json.dumps([result, skipped, failed]))
"""
    run = _run(tmp_path, code)
    assert run.returncode == 0, run.stderr
    assert "PRIVATE_TEST_DETAIL" not in run.stdout
    assert "PRIVATE_TEST_DETAIL" not in run.stderr
