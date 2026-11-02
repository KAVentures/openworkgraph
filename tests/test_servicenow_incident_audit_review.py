"""Two-phase ServiceNow audit review acceptance against real local OWG HTTP/storage.

The provider is synthetic; no customer ServiceNow tenant is contacted.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from server import event_log_workspace as workspace
from server.servicenow_incident_audit_review import main
from test_servicenow_incident_audit import (
    HOST, INCIDENT, SINCE, UNTIL, Provider, audit_row, environment, owg_server,
)


def cli(env, action, *, port, sha=None, csv_name="review.csv",
        manifest_name="review.json", tenant="team-a", log_id=None, consent=True):
    root, grant, audit_grant, saved_log, _ = env
    log = saved_log if log_id is None else log_id
    args = [
        action, "--instance", HOST, "--tenant", tenant,
        "--incident-id", INCIDENT, "--field", "state", "--field", "priority",
        "--since", SINCE, "--until", UNTIL, "--timezone", "UTC",
        "--grant-file", str(grant), "--audit-grant-file", str(audit_grant),
        "--csv", str(root / csv_name), "--manifest", str(root / manifest_name),
        "--log-id", log, "--confirm-log-id", log, "--port", str(port),
    ]
    if action == "prepare":
        if consent:
            args += ["--consent-audit-read", "--consent-recovery-export"]
    else:
        args += ["--confirm-sha256", sha or "0" * 64]
        if consent:
            args += ["--consent-prepared-read", "--consent-local-append",
                     "--consent-reviewed-apply"]
    return args


def prepare(env, port, capsys, provider=None):
    provider = provider or Provider([audit_row(1), audit_row(2, field="priority")])
    assert main(cli(env, "prepare", port=port), provider_opener=provider) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "prepared"
    assert report["local_owg_append_performed"] is False
    return report, provider


def test_real_local_reviewed_append_and_idempotent_replay(environment, capsys):
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        before = workspace.summary(environment[3])["events"]
        report, provider = prepare(environment, port, capsys)
        assert report["audit_observations"] == 2
        assert len(posts) == 0
        assert workspace.summary(environment[3])["events"] == before
        assert report["csv_sha256"] == hashlib.sha256(
            (environment[0] / "review.csv").read_bytes()).hexdigest()
        assert main(cli(environment, "apply", port=port,
                        sha=report["csv_sha256"])) == 0
        applied = json.loads(capsys.readouterr().out)
        assert applied["added_events"] == 2
        assert applied["duplicate_events_skipped"] == 0
        assert applied["local_owg_append_performed"] is True
        assert provider.calls == 1  # apply makes no provider GET
        assert main(cli(environment, "apply", port=port,
                        sha=report["csv_sha256"])) == 0
        replay = json.loads(capsys.readouterr().out)
        assert replay["added_events"] == 0
        assert replay["duplicate_events_skipped"] == 2
        assert len(posts) == 2
        assert workspace.summary(environment[3])["events"] == before + 2
        assert INCIDENT not in json.dumps(report) + json.dumps(applied)


@pytest.mark.parametrize("failure", [
    "wrong-digest", "modified-csv", "modified-manifest", "revoked-audit",
    "revoked-incident", "wrong-tenant", "missing-consent", "symlink-csv",
])
def test_refuse_unreviewed_tampered_or_revoked_without_post(
        environment, capsys, failure):
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        report, _ = prepare(environment, port, capsys)
        sha = report["csv_sha256"]
        if failure == "wrong-digest":
            sha = "f" * 64
        elif failure == "modified-csv":
            with (environment[0] / "review.csv").open("ab") as output:
                output.write(b"unexpected,unreviewed,event,row\n")
        elif failure == "modified-manifest":
            manifest = environment[0] / "review.json"
            data = json.loads(manifest.read_text())
            data["log_id"] = "log_ffffffffffffffff"
            manifest.write_text(json.dumps(data))
        elif failure == "revoked-audit":
            grant = environment[2]
            data = json.loads(grant.read_text())
            data["enabled"] = False
            grant.write_text(json.dumps(data))
        elif failure == "revoked-incident":
            grant = environment[1]
            data = json.loads(grant.read_text())
            data["enabled"] = False
            grant.write_text(json.dumps(data))
        elif failure == "symlink-csv":
            if os.name == "nt":
                pytest.skip("Windows symlink creation requires elevated privileges")
            csv = environment[0] / "review.csv"
            data = environment[0] / "other.csv"
            csv.rename(data)
            csv.symlink_to(data)
        args = cli(environment, "apply", port=port, sha=sha,
                   tenant="wrong-tenant" if failure == "wrong-tenant" else "team-a",
                   consent=failure != "missing-consent")
        assert main(args) == 2
        assert json.loads(capsys.readouterr().out)["status"] == "refused"
        assert posts == []


def test_uncertain_post_preserves_reviewed_artifacts(environment, capsys):
    with owg_server(refuse_post=True) as (port, posts, mapping):
        mapping[0] = environment[4]
        report, provider = prepare(environment, port, capsys)
        assert main(cli(environment, "apply", port=port,
                        sha=report["csv_sha256"])) == 3
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["status"] == "append_unverified"
        assert receipt["possible_local_append"] is True
        assert len(posts) == 1
        assert provider.calls == 1
        assert (environment[0] / "review.csv").exists()
        assert (environment[0] / "review.json").exists()


def test_zero_audit_rows_prepare_then_review_without_post(environment, capsys):
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        report, provider = prepare(environment, port, capsys, Provider([]))
        assert report["audit_observations"] == 0
        assert main(cli(environment, "apply", port=port,
                        sha=report["csv_sha256"])) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["audit_observations"] == 0
        assert result["local_owg_append_performed"] is False
        assert provider.calls == 1
        assert posts == []


def test_prepare_refuses_existing_outputs_before_provider_read(environment, capsys):
    (environment[0] / "review.csv").write_text("existing")
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        provider = Provider()
        assert main(cli(environment, "prepare", port=port),
                    provider_opener=provider) == 2
        assert json.loads(capsys.readouterr().out)["status"] == "refused"
        assert provider.calls == 0
        assert posts == []
