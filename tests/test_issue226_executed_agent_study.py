"""Real subprocess/HTTP broker contract tests; NO model, provider, or private data."""
from __future__ import annotations

from types import SimpleNamespace
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from scripts.run_issue226_agent_study import (
    TrialBroker, _parse_agent_output, _local_owg_endpoint, _scenarios, run_study,
)
from scripts.evaluate_record_graph_issue226 import evaluate

REF = "owg:r:" + "a" * 32
REF2 = "owg:r:" + "b" * 32


def _http(base, path, token, ref=REF):
    url = base + path + "?" + urlencode({"resource_ref": ref})
    req = Request(url, headers={"X-OWG-Eval-Token": token})
    try:
        with urlopen(req, timeout=3) as result:
            return result.status, json.loads(result.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_broker_enforces_a_b_c_deny_by_default_and_log_is_host_observed():
    case = {"resource_ref": REF}
    scenario = {
        "source_snapshot": {"status": "done", "no_private_fields": True},
        "owg_record_graph": {"resource_ref": REF, "observed_event_count": 4},
    }
    broker = TrialBroker(case, scenario, "C")
    with broker.serving() as base:
        assert _http(base, "/v1/source-audit", "incorrect")[0] == 401
        assert _http(base, "/v1/source-audit", broker.secret, REF2)[0] == 404
        assert _http(base, "/v1/source-audit", broker.secret)[0] == 200
        assert _http(base, "/v1/owg-record", broker.secret)[0] == 200
    assert len(broker.logs) == 2
    assert broker.logs[0]["tool"] == "source_audit_log_read"
    assert len(broker.logs[0]["source_snapshot_sha256"]) == 64
    assert broker.logs[1]["resource_ref"] == REF
    assert broker.logs[1]["tool"] == "get_record_graph"
    assert all(row["origin"] == "orchestrator_tool_execution" for row in broker.logs)
    assert "no_private_fields" not in str(broker.logs)

    for condition in ("A", "B"):
        broker = TrialBroker(case, scenario, condition)
        with broker.serving() as base:
            assert _http(base, "/v1/owg-record", broker.secret)[0] == 403
            assert _http(base, "/v1/source-audit", broker.secret)[0] == (
                403 if condition == "A" else 200
            )
        assert not any(item["tool"] == "get_record_graph" for item in broker.logs)


def test_outage_is_logged_as_actual_attempt_not_fake_source_fact():
    broker = TrialBroker(
        {"resource_ref": REF}, {"source_snapshot": None}, "B",
    )
    with broker.serving() as base:
        assert _http(base, "/v1/source-audit", broker.secret)[0] == 503
    assert broker.logs == [{
        "origin": "orchestrator_tool_execution",
        "tool": "source_audit_log_read",
        "status": "unavailable",
        "source_record_match": True,
    }]


def test_model_output_validation_not_self_reported_tool_calls():
    assert _parse_agent_output('agent notes\n{"predicted_outcome":"unknown"}\n') == "unknown"
    with pytest.raises(ValueError):
        _parse_agent_output(json.dumps({
            "predicted_outcome": "secret business event",
            "host_tool_logs": [{"origin": "orchestrator_tool_execution",
                                "tool": "get_record_graph"}],
        }))
    with pytest.raises(ValueError):
        _parse_agent_output('{"predicted_outcome":"unknown"}\n' + "x" * 40000)


def test_exact_agent_process_invoked_for_matched_runs_without_truth_in_prompt(monkeypatch):
    cases = [{
        "case_id": "case_alpha", "gold_outcome": "resolved", "resource_ref": REF,
        "fold": "development", "stressors": ["interleaved"],
    }, {
        "case_id": "case_beta", "gold_outcome": "unknown", "resource_ref": None,
        "fold": "holdout", "stressors": ["no_reference"],
    }]
    scenarios = {
        "case_alpha": {
            "case_id": "case_alpha", "resource_ref": REF,
            "prompt": "Continue issue analysis; use verified source receipts.",
            "source_snapshot": {"state": "done"},
            "owg_record_graph": {"resource_ref": REF, "record_observed": True},
        },
        "case_beta": {
            "case_id": "case_beta", "resource_ref": None,
            "prompt": "An unidentified work item without references.",
            "source_snapshot": None, "owg_record_graph": None,
        },
    }
    calls = []
    def fake_external_agent(argv, **kwargs):
        assert kwargs["shell"] is False
        assert Path(kwargs["cwd"]).is_dir()
        assert "gold_outcome" not in kwargs["input"]
        assert "OWG_EVAL_OWG_BEARER" not in kwargs["env"]
        assert "OWG_JIRA_OAUTH_ACCESS_TOKEN" not in kwargs["env"]
        assert "SUPABASE_SERVICE_ROLE_KEY" not in kwargs["env"]
        assert kwargs["env"]["OPENAI_API_KEY"] == "synthetic-model-auth-only"
        arm = kwargs["env"]["OWG_EVAL_CONDITION"]
        cid = kwargs["env"]["OWG_EVAL_CASE_ID"]
        base = kwargs["env"]["OWG_EVAL_BASE_URL"]
        token = kwargs["env"]["OWG_EVAL_TOKEN"]
        ref = scenarios[cid]["resource_ref"]
        calls.append((cid, arm))
        if ref and arm in {"B", "C"}:
            assert _http(base, "/v1/source-audit", token, ref)[0] == 200
        if ref and arm == "C":
            assert _http(base, "/v1/owg-record", token, ref)[0] == 200
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"predicted_outcome": "resolved" if ref else "unknown"}),
        )
    monkeypatch.setenv("OWG_EVAL_OWG_BEARER", "never-expose-parent-secret")
    monkeypatch.setenv("OWG_JIRA_OAUTH_ACCESS_TOKEN", "private-jira-secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "private-db-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-model-auth-only")
    runs, report = run_study(
        cases, scenarios, ["fake-agent-binary"], runner=fake_external_agent,
        replicates=2, seed=1234,
    )
    assert len(runs) == 12
    assert len(calls) == 12
    assert len(set((r["case_id"], r["condition"], r["replicate"]) for r in runs)) == 12
    assert report["matched_triplets"] == 4
    assert report["b_c_source_log_ablation_confounds"] == 0
    assert report["c_wrong_or_missing_record_retrievals"] == 0
    # Only one of seven stressors is present: no fake evaluable study status.
    assert report["status"] == "insufficient_evidence"
    assert report["agent_commands_executed_by_collector"] == 12
    assert report["provider_live_calls_performed_by_broker"] is False


def test_missing_record_source_abstention_is_valid_not_confounded():
    stressors = [
        "interleaved", "no_reference", "source_outage", "stale_state",
        "divergent_clocks", "inconsistent_ids", "cross_device",
    ]
    cases = [{
        "case_id": "case_" + str(i+100), "resource_ref": None if tag == "no_reference" else REF,
        "gold_outcome": "unknown", "fold": "holdout", "stressors": [tag],
    } for i, tag in enumerate(stressors)]
    runs = []
    for case in cases:
        for rep in (1, 2):
            for arm in "ABC":
                logs = []
                if arm in "BC" and case["resource_ref"]:
                    entry = {
                        "origin": "orchestrator_tool_execution",
                        "tool": "source_audit_log_read",
                        "source_record_match": True,
                        "status": "unavailable",
                    }
                    logs.append(entry)
                if arm == "C" and case["resource_ref"]:
                    logs.append({
                        "origin": "orchestrator_tool_execution",
                        "tool": "get_record_graph", "status": "success",
                        "resource_ref": REF,
                    })
                runs.append({
                    "case_id": case["case_id"], "replicate": rep, "condition": arm,
                    "predicted_outcome": "unknown", "host_tool_logs": logs,
                })
    result = evaluate(cases, runs)
    assert result["status"] == "evaluable"
    assert result["b_c_source_log_ablation_confounds"] == 0
    assert result["c_wrong_or_missing_record_retrievals"] == 0
    assert result["superiority_claimed"] is False


def test_heldout_truth_and_scenario_must_be_separate(tmp_path):
    cases = [{
        "case_id": "case_one", "gold_outcome": "resolved",
        "resource_ref": REF, "fold": "holdout",
    }]
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps([{
        "case_id": "case_one", "resource_ref": REF,
        "prompt": "Find this item", "gold_outcome": "resolved",
    }]))
    from scripts.evaluate_record_graph_issue226 import _validate_cases
    with pytest.raises(ValueError, match="truth"):
        _scenarios(path, _validate_cases(cases))
    path.write_text(json.dumps([{
        "case_id": "case_one", "resource_ref": REF2, "prompt": "Find this item",
    }]))
    with pytest.raises(ValueError, match="exactly"):
        _scenarios(path, _validate_cases(cases))


@pytest.mark.parametrize("value", [
    "http://example.org:8787", "https://127.0.0.1:8787",
    "http://127.0.0.1:8787/private", "http://localhost:8787/?token=secret",
])
def test_live_owg_adapter_rejects_remote_or_untrusted_endpoints(value):
    with pytest.raises(ValueError):
        _local_owg_endpoint(value)

def test_local_owg_adapter_normalization():
    assert _local_owg_endpoint("http://127.0.0.1:8787") == "http://127.0.0.1:8787/v1/record-graph"
    assert _local_owg_endpoint("http://127.0.0.1:8787/v1/record-graph") == "http://127.0.0.1:8787/v1/record-graph"



def test_cli_adapter_keeps_model_output_separate_from_claimed_tool_logs(monkeypatch):
    from scripts.issue226_model_cli_adapter import invoke
    from types import SimpleNamespace
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/"+name)
    captured = []
    def fake(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "predicted_outcome": "unknown",
            "host_tool_logs": [{"tool": "get_record_graph", "origin": "orchestrator_tool_execution"}],
            "customer_private_name": "NEVER RETURN THIS",
        }))
    for provider in ("codex", "claude"):
        out = invoke(provider, "Private user prompt", runner=fake)
        assert out == '{"predicted_outcome": "unknown"}'
        command, kwargs = captured[-1]
        assert command[0] == ("/usr/bin/codex" if provider == "codex" else "/usr/bin/claude")
        assert "dangerously" not in " ".join(command)
        assert kwargs["input"] == "Private user prompt"
        assert kwargs["capture_output"] is True
    with pytest.raises(RuntimeError):
        invoke("codex", "prompt", runner=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout='{"predicted_outcome":"impossible"}'
        ))



def test_zero_cost_preflight_separates_gold_from_agent_inputs(tmp_path, capsys):
    from scripts.run_issue226_agent_study import main
    cases = [{
        "case_id": "case_one", "gold_outcome": "unknown", "resource_ref": None,
        "fold": "holdout", "stressors": ["no_reference"],
    }]
    scenario = [{"case_id": "case_one", "resource_ref": None,
                 "prompt": "A task with no resolvable record",
                 "source_snapshot": None, "owg_record_graph": None}]
    truth = tmp_path / "cases.json"
    tasks = tmp_path / "scenarios.json"
    truth.write_text(json.dumps(cases))
    tasks.write_text(json.dumps(scenario))
    assert main(["--cases", str(truth), "--scenarios", str(tasks),
                 "--validate-only"]) == 0
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["status"] == "input_validated_no_model_executed"
    assert preflight["model_calls"] == 0
    assert preflight["holdout_cases"] == 1
    assert "gold_outcome" not in preflight
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "cases.json", "scenarios.json",
    ]
    scenario[0]["source_snapshot"] = {"something": "must never be exposed"}
    tasks.write_text(json.dumps(scenario))
    with pytest.raises(ValueError, match="unlinked"):
        main(["--cases", str(truth), "--scenarios", str(tasks),
              "--validate-only"])



def test_live_local_owg_bearer_ignores_proxy_environment(monkeypatch):
    import io
    import scripts.run_issue226_agent_study as study
    intercepted = []
    class FakeOpener:
        def open(self, request, timeout):
            intercepted.append((request, timeout))
            return io.BytesIO(json.dumps({"resource_ref": REF}).encode("utf-8"))
    def opener(handler):
        # Even if HTTP_PROXY is configured on the runner, no proxy may
        # intercept local OWG credentials or the returned evidence.
        assert handler.proxies == {}
        return FakeOpener()
    monkeypatch.setattr(study, "build_opener", opener)
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.example.invalid:8080")
    result = study._live_owg(
        REF, "http://127.0.0.1:8787/v1/record-graph", "test-local-token",
    )
    assert result["resource_ref"] == REF
    assert len(intercepted) == 1
    request, timeout = intercepted[0]
    assert timeout == 7
    assert "127.0.0.1:8787" in request.full_url
    assert request.get_header("Authorization") == "Bearer test-local-token"
