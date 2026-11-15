"""R17 exported SDK emergency stop: no network, durable marker, in-run halt."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from server.process_agent_project import build_runtime_project
from test_process_agent_project import packet


@pytest.fixture
def customer(tmp_path, monkeypatch):
    with ZipFile(BytesIO(build_runtime_project(packet()))) as archive:
        assert "stop.py" in archive.namelist()
        assert "execute.py" in archive.namelist()
        archive.extractall(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    def load(name):
        loader = importlib.util.spec_from_file_location(
            "test_owg_exported_" + name, tmp_path / (name + ".py"))
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        return module
    return tmp_path, load("execute"), load("stop")


def stop_cli(root, directory, *args):
    return subprocess.run([sys.executable, "-S", "stop.py",
                           "--journal-directory", str(directory), *args],
                          cwd=root, capture_output=True, text=True, timeout=15)


def test_opt_in_stop_is_local_private_durable_and_idempotent(customer):
    root, execute, stop = customer
    folder = root / "journal"
    status = stop_cli(root, folder, "--status")
    assert status.returncode == 0
    assert json.loads(status.stdout)["stopped"] is False
    denied = stop_cli(root, folder, "--stop")
    assert denied.returncode != 0
    assert not folder.exists()
    made = stop_cli(root, folder, "--stop", "--confirm-stop")
    assert made.returncode == 0, made.stderr
    result = json.loads(made.stdout)
    assert result["stopped"] is True and result["new_stop"] is True
    assert result["network_contacted"] is False
    assert result["in_flight_request_cancelled"] is False
    marker = folder / ".owg-stop"
    assert marker.is_file()
    assert json.loads(marker.read_text())["stop_requested"] is True
    if os.name == "posix":
        assert marker.stat().st_mode & 0o777 == 0o600
    repeated = stop_cli(root, folder, "--stop", "--confirm-stop")
    assert repeated.returncode == 0 and json.loads(repeated.stdout)["new_stop"] is False
    assert stop_cli(root, folder, "--status").returncode == 0
    intent = {"expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    with pytest.raises(execute.ImplementationError, match="operator stop"):
        execute.ensure_current(intent, stop_marker=marker)
    # The test controls this temporary directory. Production has no automatic
    # resume; stopping the process, checking provider receipts and new consent
    # must precede any operator's deliberate manual removal.
    marker.unlink()
    execute.ensure_current(intent, stop_marker=marker)


def test_malformed_marker_or_broken_symlink_still_stops(customer, tmp_path):
    _, execute, stop = customer
    directory = tmp_path / "safety"
    directory.mkdir()
    marker = directory / ".owg-stop"
    marker.write_text("not a valid JSON marker")
    intent = {"expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    with pytest.raises(execute.ImplementationError, match="operator stop"):
        execute.ensure_current(intent, stop_marker=marker)
    marker.unlink()
    if hasattr(os, "symlink"):
        try:
            marker.symlink_to(directory / "no-such-target")
        except OSError:
            return  # Some Windows CI configurations forbid symlinks.
        assert not marker.exists() and os.path.lexists(marker)
        with pytest.raises(execute.ImplementationError, match="operator stop"):
            execute.ensure_current(intent, stop_marker=marker)


def test_stop_before_start_never_opens_tool_client_or_journal(customer, monkeypatch):
    root, execute, stop = customer
    journal = root / "prelaunch"
    assert stop.stop(journal)["stopped"] is True
    actions = [{"activity_id": "A", "operation_kind": "read", "tool_name": "synthetic",
                "arguments": {}, "precondition": None, "result_pointer": "/ok",
                "result_equals": True}]
    prepared = {"actions": actions, "outcomes": [], "source_rules": [],
                "decision_tables": []}
    monkeypatch.setattr(execute, "prepare", lambda *args: prepared)
    monkeypatch.setattr(execute, "validate_intent", lambda *args, **kwargs: None)
    monkeypatch.setenv("OWG_EXECUTION_MCP_TOKEN", "synthetic-test-only")
    called = []
    def never(*args):
        called.append(args)
        raise AssertionError("Provider tool client must not be instantiated")
    spec = {"workflow_id": "synthetic", "revision": 1, "content_sha256": "a" * 64}
    intent = {"expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    with pytest.raises(execute.ImplementationError, match="operator stop"):
        asyncio.run(execute.execute_approved(
            spec, {}, {}, {"tool_server_url": "https://example.com"}, intent,
            journal_directory=journal, client_factory=never))
    assert called == []
    assert list(journal.glob("*.jsonl")) == []


def test_stop_after_first_tool_prevents_second_and_marks_journal(customer, monkeypatch):
    root, execute, stop = customer
    journal = root / "running"
    requests = []
    actions = [
        {"activity_id": name, "operation_kind": "read", "tool_name": "synthetic",
         "arguments": {}, "precondition": None, "result_pointer": "/ok",
         "result_equals": True}
        for name in ("first", "second")
    ]
    prepared = {"actions": actions, "outcomes": [], "source_rules": [],
                "decision_tables": []}
    monkeypatch.setattr(execute, "prepare", lambda *args: prepared)
    monkeypatch.setattr(execute, "validate_intent", lambda *args, **kwargs: None)
    monkeypatch.setenv("OWG_EXECUTION_MCP_TOKEN", "synthetic-test-only")
    class FakeClient:
        async def list_tools(self):
            return [{"name": "synthetic", "inputSchema": {"type": "object"}}]
        async def call_tool(self, name, args):
            requests.append((name, args))
            if len(requests) == 1:
                stop.stop(journal)  # operator stops during first in-flight call
            return {"structuredContent": {"ok": True}}
    @asynccontextmanager
    async def client_factory(*_):
        yield FakeClient()
    spec = {"workflow_id": "synthetic", "revision": 1, "content_sha256": "a" * 64}
    intent = {"expires_at": (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    result = asyncio.run(execute.execute_approved(
        spec, {}, {}, {"tool_server_url": "https://example.com"}, intent,
        journal_directory=journal, client_factory=client_factory))
    assert len(requests) == 1, "new tool attempts after operator stop are forbidden"
    assert result["operator_stopped"] is True
    assert result["in_flight_requests_cannot_be_cancelled"] is True
    assert result["declared_route_checks_satisfied"] is False
    assert result["all_tool_results_matched"] is False
    assert [r["activity_id"] for r in result["actions"]] == ["first"]
    journal_files = list(journal.glob("*.jsonl"))
    assert len(journal_files) == 1
    phases = [json.loads(line)["phase"] for line in journal_files[0].read_text().splitlines()]
    assert "attempt_pending" in phases
    assert "operator_stop_observed" in phases
    assert "operator_stop_active_at_completion" in phases
    assert "finished" in phases
