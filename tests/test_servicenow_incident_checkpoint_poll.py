"""Finite ServiceNow checkpoint polling against disposable actual OWG HTTP storage.

Fake provider only. The local HTTP append invokes the repository's real
transactional event-log engine; no external tenant, model or business write.
"""
from __future__ import annotations

import json
import os

import pytest

# Reuse the real local HTTP/SQLite engine fixture and fake HTTPS provider
# from the checkpoint acceptance suite; do not replace append with a mock.
from test_servicenow_incident_checkpoint import (
    environment, owg_server, Provider, HOST, INCIDENT, NOW, TOKEN, SESSION,
)
from server import event_log_workspace as workspace
from server.servicenow_incident_checkpoint_poll import main


def args(env, port, *, prefix="round", runs=2, initial=True, consent=True,
         tenant="team-a", interval=60):
    root, grant, log, _ = env
    argv = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(root / "journal.sqlite"),
        "--checkpoint", str(root / "checkpoint.json"),
        "--output-dir", str(root), "--output-prefix", prefix,
        "--runs", str(runs), "--interval-seconds", str(interval),
        "--log-id", log, "--confirm-log-id", log, "--port", str(port),
    ]
    if initial:
        argv += ["--initial-since", "2026-10-20 00:00:00"]
    if consent:
        argv += ["--consent-selected-read", "--consent-bounded-poll",
                 "--consent-local-journal-read", "--consent-local-append",
                 "--consent-incremental-checkpoint"]
    return argv


def test_two_poll_checkpoint_advances_and_replays_real_owg_http(environment, capsys):
    provider = Provider()
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 1:
            provider.updated = "2026-10-31 04:10:00"
            provider.revision = 4

    with owg_server(environment[3]) as (port, posts):
        assert main(args(environment, port), provider_opener=provider,
                    sleep_fn=sleep, now_fn=lambda: NOW) == 0
        report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["completed_polls"] == 2
    assert report["checkpoint_commits"] == 2
    assert report["checkpoint_advances"] == 2
    assert report["added_events"] == 2
    assert report["source_writes_performed"] is False
    assert len(posts) == 2
    assert provider.calls == 2
    assert provider.since_values == [
        "2026-10-20 00:00:00", "2026-10-28 03:10:00",
    ]
    assert slept == [5] * 12
    assert workspace.summary(environment[2])["events"] == 3
    root = environment[0]
    assert (root / "round-001.csv").exists()
    assert (root / "round-002.csv").exists()
    state = json.loads((root / "checkpoint.json").read_text())
    assert state["latest_source_utc"] == "2026-10-31T04:10:00+00:00"
    assert INCIDENT not in json.dumps(report) + json.dumps(state)
    assert TOKEN not in json.dumps(report) + json.dumps(state)
    assert SESSION not in json.dumps(report) + json.dumps(state)


def test_explicit_new_prefix_resumes_persisted_checkpoint_and_dedupes(environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(args(environment, port, prefix="first", runs=1),
                    provider_opener=provider, now_fn=lambda: NOW) == 0
        capsys.readouterr()
        assert main(args(environment, port, prefix="resumed", runs=1, initial=False),
                    provider_opener=provider, now_fn=lambda: NOW) == 0
        report = json.loads(capsys.readouterr().out)
    assert report["added_events"] == 0
    assert report["duplicate_events_skipped"] >= 1
    assert report["checkpoint_advances"] == 0
    assert provider.calls == 2
    assert len(posts) == 2
    assert workspace.summary(environment[2])["events"] == 2


def test_revocation_during_wait_stops_before_next_provider_get(environment, capsys):
    provider = Provider()
    root, grant, _, mapping = environment

    def revoke(_seconds):
        value = json.loads(grant.read_text())
        value["enabled"] = False
        grant.write_text(json.dumps(value))

    with owg_server(mapping) as (port, posts):
        assert main(args(environment, port), provider_opener=provider,
                    sleep_fn=revoke, now_fn=lambda: NOW) == 2
        report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert report["completed_polls"] == 1
    assert report["possible_local_append"] is False
    assert provider.calls == 1
    assert len(posts) == 1
    assert (root / "round-001.csv").exists()
    assert not (root / "round-002.csv").exists()


def test_uncertain_local_append_stops_without_checkpoint_or_second_read(environment, capsys):
    provider = Provider()
    with owg_server(environment[3], refuse_post=True) as (port, posts):
        assert main(args(environment, port), provider_opener=provider,
                    sleep_fn=lambda _: None, now_fn=lambda: NOW) == 3
        report = json.loads(capsys.readouterr().out)
    assert report["status"] == "append_or_checkpoint_unverified"
    assert report["completed_polls"] == 0
    assert report["possible_local_append"] is True
    assert provider.calls == 1
    assert len(posts) == 1
    assert not (environment[0] / "checkpoint.json").exists()


@pytest.mark.parametrize("case", [
    "no_consent", "wrong_tenant", "output_collision", "checkpoint_collision",
    "no_initial", "bad_interval", "too_many", "too_long", "wrong_log",
    "stale_lock", "bad_prefix", "wrong_checkpoint_binding",
])
def test_preflight_refuses_without_provider_request(environment, capsys, case):
    provider = Provider()
    root, grant, log, mapping = environment
    argv = args(environment, 8765)
    if case == "no_consent":
        argv.remove("--consent-incremental-checkpoint")
    elif case == "wrong_tenant":
        argv[argv.index("--tenant") + 1] = "team-b"
    elif case == "output_collision":
        (root / "round-002.csv").write_text("do not overwrite")
    elif case == "checkpoint_collision":
        argv[argv.index("--output-prefix") + 1] = "checkpoint"
        (root / "checkpoint-001.csv").write_text("do not overwrite")
    elif case == "no_initial":
        argv = args(environment, 8765, initial=False)
    elif case == "bad_interval":
        argv[argv.index("--interval-seconds") + 1] = "5"
    elif case == "too_many":
        argv[argv.index("--runs") + 1] = "9"
    elif case == "too_long":
        argv[argv.index("--runs") + 1] = "8"
        argv[argv.index("--interval-seconds") + 1] = "1800"
    elif case == "wrong_log":
        argv[argv.index("--confirm-log-id") + 1] = "wrong"
    elif case == "stale_lock":
        (root / "checkpoint.json.lock").write_text("busy")
    elif case == "bad_prefix":
        argv[argv.index("--output-prefix") + 1] = "../outside"
    elif case == "wrong_checkpoint_binding":
        (root / "checkpoint.json").write_text(json.dumps({
            "format": "openworkgraph.servicenow-incremental-checkpoint.v1",
            "binding": "0" * 64, "initial_since": "2026-10-20 00:00:00",
            "latest_source_utc": None,
        }))
        if os.name == "posix":
            (root / "checkpoint.json").chmod(0o600)
    assert main(argv, provider_opener=provider, now_fn=lambda: NOW) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert report["completed_polls"] == 0
    assert provider.calls == 0
    assert not (root / "journal.sqlite").exists()
    assert INCIDENT not in json.dumps(report)
    assert TOKEN not in json.dumps(report)


def test_forged_child_success_without_checkpoint_cannot_continue(environment, capsys, monkeypatch):
    import server.servicenow_incident_checkpoint_poll as module
    provider = Provider()

    def fake_success(_args, **_kwargs):
        print(json.dumps({
            "format": "openworkgraph.servicenow-incremental-checkpoint.v1",
            "status": "completed", "checkpoint_committed": True,
            "checkpoint_advanced": True, "local_owg_append_performed": True,
            "source_writes_performed": False, "historical_completeness": "unknown",
            "added_events": 1, "duplicate_events_skipped": 0,
        }))
        return 0

    monkeypatch.setattr(module, "checkpoint_main", fake_success)
    with owg_server(environment[3]) as (port, posts):
        assert main(args(environment, port), provider_opener=provider,
                    sleep_fn=lambda _: None, now_fn=lambda: NOW) == 3
        report = json.loads(capsys.readouterr().out)
    assert report["completed_polls"] == 0
    assert report["possible_local_append"] is True
    assert provider.calls == 0
    assert not posts
