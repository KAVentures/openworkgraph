"""Owner-chosen, per-log, read-only process intelligence for connected AI agents (MCP)."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_agent_sharing as sharing
from server import event_log_workspace as workspace
from server import process_intelligence_settings as settings

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    monkeypatch.delenv("OWG_PROCESS_INTELLIGENCE_TOOLS", raising=False)


def _log(name="orders"):
    rows = ["case,activity,time,who"]
    for i in range(12):
        slow = i % 2 == 0
        rows += [f"c{i},Receive,2024-03-{1 + i:02d}T08:00:00Z,clerk",
                 f"c{i},Approve,2024-03-{1 + i:02d}T09:00:00Z,{'bob' if slow else 'ana'}",
                 f"c{i},Pay,2024-03-{(4 if slow else 1) + i:02d}T10:00:00Z,bot"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def test_sharing_levels_and_revocation():
    log, other = _log(), _log("other")
    assert sharing.share_status(log)["level"] is None and settings.tools_enabled() is False
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)
    assert sharing.shared_logs()["logs"] == []
    sharing.set_share(log, "aggregates")
    assert settings.tools_enabled() is True
    listed = sharing.shared_logs()["logs"]
    assert [(entry["log_id"], entry["level"], entry["case_notions"], entry["agents_may_tag_cases"]) for entry in listed] == [
        (log, "aggregates", ["case"], False)]
    ov = sharing.overview(log, sla_hours=24)
    assert ov["cases"] == 12 and ov["top_variants"][0]["path"] == ["Receive", "Approve", "Pay"]
    assert "throughput_seconds" in ov["kpis"] and ov["kpis"]["sla"]["breached_cases"] == 6
    auto = sharing.automation_candidates(log)
    assert [c["activity"] for c in auto["candidates"]][0] in {"Receive", "Approve", "Pay"} and auto["weights"]["routine"] == 0.4
    points = sharing.decision_rules(log)
    assert "decision_points" in points and points["log_id"] == log
    net = sharing.heuristics_net(log)
    assert net["cases"] == 12 and {e["from"] for e in net["edges"]} >= {"Receive", "Approve"}
    prints = sharing.footprints(log)
    assert prints["cases"] == 12 and prints["conformance"]["cells"] == len(prints["activities"]) ** 2
    quality = sharing.model_quality(log, levels="0,0.3")
    assert [r["noise"] for r in quality["levels"]] == [0.0, 0.3] and quality["levels"][0]["fitness"] == 1.0
    assert quality["best_f_score_noise"] in {0.0, 0.3} and "case_id" not in str(quality)
    deviations = sharing.model_deviations(log, noise=0)
    assert deviations["cases"] == 12 and deviations["fitness"]["log"] == 1.0 and deviations["log_moves"] == []
    assert "case_id" not in str(deviations) and deviations["process_model_sha256"]
    due = sharing.due_dates(log, "Pay", due_days=2)
    assert due["groups"]["on_time"] == 6 and due["groups"]["late"] == 6 and due["most_late_cases"] is None
    after = sharing.next_steps(log, ["Receive"])
    assert after["cases_at_point"] == 12 and after["next"][0]["activity"] == "Approve"
    why = sharing.model_deviation_causes(log, noise=0)
    assert why["non_fitting_paths"] == 0 and why["associations"] == [] and "case_id" not in str(why)
    start = sharing.brief(log, sla_hours=24)
    assert start["cases"] == 12 and start["model"]["best_noise"] is not None and "adherence" in start
    # 12 cases is below the 20 that unusual cases need: reported, not silently skipped.
    assert "unusual" not in start and "at least 20 cases" in start["parts_unavailable"]["unusual"]
    assert "case_id" not in str(start) and start["next_tools"][0]["tool"] == "get_process_insights"
    found = sharing.insights(log, sla_hours=24)
    assert found["cases"] == 12 and found["findings"] and "case_id" not in str(found)
    assert any(f["kind"] == "sla_breaches" for f in found["findings"])
    funnel = sharing.funnel(log, ["Receive", "Pay"])
    assert funnel["cases"] == 12 and funnel["steps"][0]["activity"] == "Receive" and "case_id" not in str(funnel)
    profile = sharing.temporal_profile(log)
    assert profile["cases"] == 12 and "top_cases" not in profile and "case_id" not in str(profile)
    rc = sharing.root_cause(log, "duration_over", hours=24)
    bob = next(a for a in rc["associations"] if a["feature"] == {"kind": "resource", "value": "bob"})
    assert bob["risk_difference"] == 1.0 and bob["holds_after_bonferroni"]
    # Case-level reading needs the cases level; the other log stays closed.
    with pytest.raises(sharing.NotShared):
        sharing.explain_case(log, "c0")
    with pytest.raises(sharing.NotShared):
        sharing.overview(other)
    sharing.set_share(log, "cases")
    assert sharing.explain_case(log, "c0")["case_id"] == "c0"
    assert sharing.slowest_cases(log, limit=3)["cases"][0]["case_id"] in {"c0", "c2", "c4", "c6", "c8", "c10"}
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.set_share(log, "everything")
    sharing.set_share(log, None)
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)
    assert settings.tools_enabled() is False


def test_agent_case_tags_need_cases_level_and_a_separate_grant():
    from server import event_log_case_tags as case_tags
    log = _log()
    sharing.set_share(log, "aggregates")
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.set_tag_grant(log, True)  # the cases level comes first
    with pytest.raises(sharing.NotShared):
        sharing.agent_tag_cases(log, {"tag": "review", "case_ids": ["c0"]})
    sharing.set_share(log, "cases")
    assert sharing.share_status(log)["agents_may_tag_cases"] is False
    with pytest.raises(sharing.NotAllowed):
        sharing.agent_tag_cases(log, {"tag": "review", "case_ids": ["c0"]})
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.set_tag_grant(log, "yes")
    assert sharing.set_tag_grant(log, True)["agents_may_tag_cases"] is True
    assert sharing.shared_logs()["logs"][0]["agents_may_tag_cases"] is True
    # Outside an AI request the IDs are used as given; the tag is always prefixed and noted.
    done = sharing.agent_tag_cases(log, {"tag": "needs review", "case_ids": ["c0", "c2", "nope"], "note": "slow approver"})
    assert (done["tag"], done["tagged"], done["unknown_case_ids"]) == ("agent: needs review", 2, 1)
    listed = case_tags.listing(log)
    assert [(t["tag"], t["cases"]) for t in listed["tags"]] == [("agent: needs review", 2)]
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.agent_tag_cases(log, {"tag": "x", "case_ids": ["c0"], "cohort": {"tag": "y"}})
    # Lowering the level drops the grant; sharing again at cases needs a new grant.
    sharing.set_share(log, "aggregates")
    sharing.set_share(log, "cases")
    assert sharing.share_status(log)["agents_may_tag_cases"] is False
    with pytest.raises(sharing.NotAllowed):
        sharing.agent_tag_cases(log, {"tag": "review", "case_ids": ["c0"]})


def test_agent_pseudonyms_resolve_locally(monkeypatch):
    from server import ai_context
    log = _log()
    sharing.set_share(log, "cases")
    sharing.set_tag_grant(log, True)
    token = ai_context.redact_contextually({"case_id": "c4"})["case_id"]
    assert token != "c4"
    monkeypatch.setattr(ai_context, "ai_request_active", lambda: True)
    monkeypatch.setattr(ai_context, "effective_detail", lambda: {"detail_level": ai_context.DETAIL_REDACTED})
    done = sharing.agent_tag_cases(log, {"tag": "dup", "case_ids": [token, "CASE_ID_000000"]})
    assert (done["tagged"], done["unknown_case_ids"], done["ambiguous_case_ids"]) == (1, 1, 0)
    # A real ID is not accepted in redacted mode: the agent never saw it.
    with pytest.raises(workspace.EventLogWorkspaceError):
        sharing.agent_tag_cases(log, {"tag": "dup", "case_ids": ["c4"]})


def test_agents_read_signal_states_without_evaluating():
    from server import event_log_signals as signal_rules
    log = _log()
    sharing.set_share(log, "aggregates")
    assert sharing.signals(log)["signals"] == []
    slow = signal_rules.add_rule(log, {"name": "Slow payments", "when": {"kind": "cases", "condition": "duration_over",
                                                                       "seconds": 24 * 3600}})["rule_id"]
    signal_rules.add_rule(log, {"name": "Too many cases", "when": {"kind": "kpi", "metric": "case_count",
                                                                   "operator": "above", "threshold": 100}})
    before = sharing.signals(log)
    assert [s["status"] for s in before["signals"]] == ["not_evaluated", "not_evaluated"]
    # Reading never evaluates.
    assert signal_rules.list_rules(log)["rules"][0].get("last_evaluated_at") is None
    signal_rules.evaluate(log)
    after = {s["name"]: s for s in sharing.signals(log)["signals"]}
    assert after["Slow payments"]["status"] == "unreviewed" and after["Slow payments"]["matching_cases"] == 6
    assert "case_ids" not in after["Slow payments"]  # aggregates level: counts only
    assert after["Too many cases"]["status"] == "cleared" and after["Too many cases"]["value"] == 12
    assert sharing.signals(log)["triggered"] == 1
    sharing.set_share(log, "cases")
    slow_item = next(s for s in sharing.signals(log)["signals"] if s["rule_id"] == slow)
    assert sorted(slow_item["case_ids"]) == sorted(f"c{i}" for i in range(0, 12, 2)) and not slow_item["case_ids_truncated"]
    sharing.set_share(log, None)
    with pytest.raises(sharing.NotShared):
        sharing.signals(log)


def test_deleting_the_log_removes_its_share():
    log = _log()
    sharing.set_share(log, "cases")
    workspace.delete_log(log)
    assert sharing.shared_logs()["logs"] == []
    with pytest.raises(sharing.NotShared):
        sharing.overview(log)


def test_routes_owner_shares_agent_reads(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from server.ai_access import set_ai_access
rows = ["case,activity,time"] + [f"c{i},A,2024-03-0{1+i}T08:00:00Z" for i in range(6)] + [f"c{i},B,2024-03-0{1+i}T09:00:00Z" for i in range(6)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    agent = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "X-OpenWorkGraph-Context": "ai"}
    log_id = client.post("/v1/event-logs/import?format=csv&name=orders&case_column=case&activity_column=activity&timestamp_column=time",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    set_ai_access(True)
    # Not shared: indistinguishable from an unknown log.
    assert client.get("/v1/process-intelligence/logs", headers=agent).json()["logs"] == []
    unknown = client.get("/v1/process-intelligence/log_" + "0" * 16 + "/overview", headers=agent)
    unshared = client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent)
    assert unknown.status_code == unshared.status_code == 404 and unknown.json() == unshared.json()
    # Only the dashboard can share; an agent cannot widen its own access.
    assert client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "cases"}, headers=agent).status_code == 403
    r = client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "aggregates"}, headers=owner)
    assert r.status_code == 200 and r.json()["level"] == "aggregates", r.text
    assert client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "all"}, headers=owner).status_code == 422
    r = client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent)
    assert r.status_code == 200 and r.json()["cases"] == 6, r.text
    assert client.get(f"/v1/process-intelligence/{log_id}/case?case_id=c0", headers=agent).status_code == 404
    r = client.get(f"/v1/process-intelligence/{log_id}/root-cause?outcome=rework", headers=agent)
    assert r.status_code == 200, r.text
    other = client.post("/v1/event-logs/import?format=csv&name=other&case_column=case&activity_column=activity&timestamp_column=time",
                        content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    unshared_cmp = client.post(f"/v1/process-intelligence/{log_id}/compare-logs", json={"comparison_log_id": other}, headers=agent)
    unknown_cmp = client.post(f"/v1/process-intelligence/{log_id}/compare-logs",
                              json={"comparison_log_id": "log_" + "0" * 16}, headers=agent)
    assert unshared_cmp.status_code == unknown_cmp.status_code == 404 and unshared_cmp.json() == unknown_cmp.json()
    client.post(f"/v1/event-logs/{other}/agent-share", json={"level": "aggregates"}, headers=owner)
    r = client.post(f"/v1/process-intelligence/{log_id}/compare-logs", json={"comparison_log_id": other}, headers=agent)
    assert r.status_code == 200 and r.json()["kpis"]["comparison"]["cases"] == 6, r.text
    assert client.post(f"/v1/process-intelligence/{log_id}/compare-logs", json={"comparison_log_id": other, "x": 1},
                       headers=agent).status_code == 422
    r = client.get(f"/v1/process-intelligence/{log_id}/process-model?noise=0", headers=agent)
    assert r.status_code == 200 and r.json()["process_tree"].startswith(("->(", "X(", "*(", "+(", "'")), r.text
    assert client.get(f"/v1/process-intelligence/{log_id}/process-model?activities=0", headers=agent).status_code == 422
    r = client.get(f"/v1/process-intelligence/{log_id}/signals", headers=agent)
    assert r.status_code == 200 and r.json()["signals"] == [], r.text
    r = client.get(f"/v1/process-intelligence/{log_id}/automation-candidates?limit=5", headers=agent)
    assert r.status_code == 200 and [c["activity"] for c in r.json()["candidates"]] == ["B", "A"], r.text
    # The global AI-access switch still applies.
    set_ai_access(False)
    assert client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent).status_code == 403
    set_ai_access(True)
    client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": "cases"}, headers=owner)
    r = client.get(f"/v1/process-intelligence/{log_id}/case?case_id=c0", headers=agent)
    # At the default AI-context detail level identifiers reach the agent pseudonymised.
    assert r.status_code == 200 and r.json()["events"] == 2 and r.json()["case_id"] != "c0", r.text
    assert r.headers["X-OpenWorkGraph-Detail-Level"] != "full"
    token = r.json()["case_id"]
    # Tagging needs a separate owner grant; an agent cannot grant it to itself.
    tag = {"tag": "needs review", "case_ids": [token], "note": "unusual path"}
    assert client.post(f"/v1/process-intelligence/{log_id}/case-tags", json=tag, headers=agent).status_code == 403
    assert client.post(f"/v1/event-logs/{log_id}/agent-share/tags", json={"allowed": True}, headers=agent).status_code == 403
    r = client.post(f"/v1/event-logs/{log_id}/agent-share/tags", json={"allowed": True}, headers=owner)
    assert r.status_code == 200 and r.json()["agents_may_tag_cases"] is True, r.text
    assert client.post(f"/v1/event-logs/{log_id}/agent-share/tags", json={"allowed": "yes"}, headers=owner).status_code == 422
    # The agent tags with the pseudonym it was given; the owner sees the real case, prefixed and noted.
    r = client.post(f"/v1/process-intelligence/{log_id}/case-tags", json=tag, headers=agent)
    assert r.status_code == 200 and r.json()["tagged"] == 1, r.text
    tags = client.get(f"/v1/event-logs/{log_id}/case-tags?tag=agent:%20needs%20review", headers=owner).json()
    assert [c["case_id"] for c in tags["cases"]] == ["c0"] and tags["cases"][0]["note"].startswith("Added by an AI agent."), tags
    assert client.post(f"/v1/process-intelligence/{log_id}/case-tags", json={**tag, "cohort": {}}, headers=agent).status_code == 422
    # Agents still cannot read the dashboard-only event-log API.
    assert client.get(f"/v1/event-logs/{log_id}/summary", headers=agent).status_code == 403
    client.post(f"/v1/event-logs/{log_id}/agent-share", json={"level": None}, headers=owner)
    assert client.get(f"/v1/process-intelligence/{log_id}/overview", headers=agent).status_code == 404
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


async def _listed_tools(tmp_path: Path, enabled: bool) -> set[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "WORKFLOW_OBSERVER_DATA": str(tmp_path / "mcpdata"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "mcpauth"), "WORKFLOW_OBSERVER_API": "http://127.0.0.1:1"})
    for key in ("OWG_EXPERIMENTAL_GOVERNANCE", "OWG_OPEN_WORK_EXPERIMENTAL_TOOLS"):
        env.pop(key, None)
    env["OWG_PROCESS_INTELLIGENCE_TOOLS"] = "1" if enabled else "0"
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.compact_stdio"], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}


PROCESS_TOOLS = {"list_process_logs", "get_process_overview", "get_process_overview_for_cases", "get_process_root_cause",
                 "explain_process_case", "get_slowest_process_cases", "tag_process_cases",
                 "get_process_rework", "get_process_waiting_times", "get_process_drift", "get_stuck_process_cases",
                 "get_process_automation_candidates", "get_process_signals", "get_process_decision_rules",
                 "get_process_temporal_profile", "get_process_heuristics_net",
                 "get_process_footprints", "get_process_model_quality", "compare_process_logs", "get_process_model", "get_process_attributes",
                 "get_process_deviations", "list_process_reference_nets", "get_process_reference_deviations",
                 "get_process_funnel",
                 "get_process_trace_clusters", "check_process_compliance", "get_process_performance_spectrum",
                 "get_unusual_process_cases",
                 "get_process_due_dates",
                 "simulate_process_scenario",
                 "get_process_next_steps",
                 "predict_open_process_cases", "predict_next_process_activity",
                 "get_process_insights",
                 "get_process_activity_glossary",
                 "get_process_deviation_causes",
                 "get_process_object_map",
                 "align_process_case",
                 "get_process_data_quality",
                 "get_process_brief",
                 "get_process_intervention_impact",
                 "get_process_model_change",
                 "get_process_costs",
                 "compare_process_models",
                 "compare_process_cases",
                 "get_process_activity_profile",
                 "get_process_variants", "get_process_map", "get_process_duration_distribution",
                 "get_process_time_patterns",
                 "get_process_signal_scorecard",
                 "get_process_breakdown",
                 "find_similar_process_cases",
                 "get_process_cooccurrence", "get_process_work_in_progress", "get_process_batching"}


def test_mcp_tools_are_listed_only_when_a_log_is_shared(tmp_path):
    from tests.test_compact_mcp_v087 import DEFAULT_TOOLS
    off = asyncio.run(_listed_tools(tmp_path, False))
    assert off == DEFAULT_TOOLS
    on = asyncio.run(_listed_tools(tmp_path, True))
    assert on == DEFAULT_TOOLS | PROCESS_TOOLS


def test_compare_logs_needs_both_shared_and_withholds_small_rows():
    def make(name, rework_every, rare=False):
        rows = ["case,activity,time,who"]
        for i in range(20):
            steps = ["Receive", "Check"] + (["Check"] if i % rework_every == 0 else []) + ["Pay"]
            if rare and i < 2:
                steps.insert(1, "Escalate")
            rows += [f"c{i},{a},2024-03-{1 + i:02d}T{8 + k:02d}:00:00Z,clerk" for k, a in enumerate(steps)]
        return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv",
                                    mapping=MAPPING)["log_id"]
    before, after = make("before", 2), make("after", 5, rare=True)
    sharing.set_share(before, "aggregates")
    with pytest.raises(sharing.NotShared):
        sharing.compare_logs(before, after)
    sharing.set_share(after, "aggregates")
    result = sharing.compare_logs(before, after)
    assert result["kpis"]["baseline"]["cases"] == result["kpis"]["comparison"]["cases"] == 20
    assert result["kpis"]["difference"]["repeat_rate"] == -0.3
    # "Escalate" occurs in 2 cases of one log only: withheld, never named.
    assert "Escalate" not in str(result) and result["withheld"]["activities"] >= 1
    variant_rows = result["variants"]["largest_share_differences"]
    assert all(r["baseline_cases"] == 0 or r["baseline_cases"] >= 5 for r in variant_rows)
    assert all(r["comparison_cases"] == 0 or r["comparison_cases"] >= 5 for r in variant_rows)
    assert "'c1'" not in str(result) and "clerk" not in str(result)
    with pytest.raises(workspace.EventLogWorkspaceError, match="fewer than 5 cases"):
        sharing.compare_logs(before, after, comparison_filter={"activity": "Escalate"})
    with pytest.raises(workspace.EventLogWorkspaceError, match="limit"):
        sharing.compare_logs(before, after, limit=0)
def test_process_model_returns_the_discovered_draft_without_case_ids():
    rows = ["case,activity,time,who"]
    for i in range(12):
        decision = "Approve" if i % 3 else "Reject"
        rows += [f"c{i},Receive,2024-03-{1 + i:02d}T08:00:00Z,clerk", f"c{i},{decision},2024-03-{1 + i:02d}T09:00:00Z,ana",
                 f"c{i},Pay,2024-03-{1 + i:02d}T10:00:00Z,bot"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="m", source_format="csv",
                               mapping=MAPPING)["log_id"]
    with pytest.raises(sharing.NotShared):
        sharing.process_model(log)
    sharing.set_share(log, "aggregates")
    model = sharing.process_model(log, noise=0)
    assert model["process_tree"] == "->( 'Receive', X( 'Approve', 'Reject' ), 'Pay' )"
    labels = [a["label"] for a in model["model"]["activities"] if a["kind"] == "activity"]
    assert labels == ["Receive", "Approve", "Reject", "Pay"]
    assert model["quality"]["fitting_case_share"] == 1.0 and model["process_model_sha256"]
    assert any("8 of 12 cases" in rule["text"] and rule["user_confirmed"] is False
               for rule in model["model"]["business_rules"])
    assert model["execution_authorized"] is False and model["observed_path_is_policy"] is False
    text = str(model)
    assert "'c1'" not in text and "clerk" not in text and "ana" not in text


def test_process_tree_notation_round_trips_and_refuses_unwritable_labels():
    assert sharing._tree_notation({"op": "loop", "children": [
        {"op": "and", "children": [{"op": "activity", "activity": "A"}, {"op": "activity", "activity": "B"}]},
        {"op": "tau"}]}) == "*( +( 'A', 'B' ), tau )"
    assert sharing._tree_notation({"op": "seq", "children": [{"op": "activity", "activity": "O'Brien"}]}) is None


def test_attributes_list_filters_without_rare_values_or_extremes():
    rows = ["case,activity,time,region,amount,ticket"]
    for i in range(20):
        region = "EU" if i < 12 else ("US" if i < 18 else "APAC")
        rows += [f"c{i},A,2024-03-{1 + i:02d}T08:00:00Z,{region},{10 * i},T{i}A",
                 f"c{i},B,2024-03-{1 + i:02d}T09:00:00Z,{region},{10 * i},T{i}B"]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="a", source_format="csv",
                               mapping={**MAPPING, "resource_column": "", "attribute_columns": ["region", "amount", "ticket"]})["log_id"]
    with pytest.raises(sharing.NotShared):
        sharing.attributes(log)
    sharing.set_share(log, "aggregates")
    result = sharing.attributes(log)
    by_name = {a["attribute"]: a for a in result["attributes"]}
    assert [v["value"] for v in by_name["region"]["top_values"]] == ["EU", "US"]      # APAC (2 cases) withheld
    assert by_name["region"]["level"] == "case" and by_name["ticket"]["level"] == "event"
    assert by_name["ticket"]["top_values"] == [] and "T1A" not in str(result)          # identifier-like
    assert set(by_name["amount"]["numeric"]) == {"p25", "median", "p75", "mean"} and "190" not in str(by_name["amount"])
    assert result["withheld"]["values"] > 0 and "'c1'" not in str(result)

