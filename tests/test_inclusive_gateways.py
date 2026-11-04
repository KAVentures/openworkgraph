"""Inclusive (OR) gateways: one or more conditional branches, join waits for started ones."""
from __future__ import annotations

import itertools

import pytest
from pydantic import ValidationError

from shared.process_bpmn_import import BPMN, BPMNImportError, import_bpmn
from shared.process_catalog import process_bpmn
from shared.process_conformance import compare_path
from shared.process_model import ProcessModel
from shared.process_parallel import replay_node_ids
from shared.process_token_replay import replay_path


def _model(**overrides):
    value = {
        "goal": "Notify stakeholders", "baseline_source": "user_declared",
        "baseline_coverage": "user_declared_design", "baseline_path": ["Declared"], "supporting_case_count": 0,
        "activities": [
            {"id": "s", "label": "Start", "kind": "start"},
            {"id": "r", "label": "Review change", "kind": "activity"},
            {"id": "o", "label": "Who to notify", "kind": "inclusive_split"},
            {"id": "a", "label": "Email customer", "kind": "activity"},
            {"id": "b", "label": "Update CRM", "kind": "activity"},
            {"id": "c", "label": "Alert finance", "kind": "activity"},
            {"id": "j", "label": "Notified", "kind": "inclusive_join"},
            {"id": "x", "label": "Close change", "kind": "activity"},
            {"id": "e", "label": "End", "kind": "end"}],
        "transitions": [
            {"source": "s", "target": "r"}, {"source": "r", "target": "o"},
            {"source": "o", "target": "a", "rule_id": "customer"},
            {"source": "o", "target": "b", "rule_id": "crm"},
            {"source": "o", "target": "c", "rule_id": "finance"},
            {"source": "a", "target": "j"}, {"source": "b", "target": "j"}, {"source": "c", "target": "j"},
            {"source": "j", "target": "x"}, {"source": "x", "target": "e"}],
        "business_rules": [{"id": "customer", "text": "Customer-facing change"},
                           {"id": "crm", "text": "Account data changed"},
                           {"id": "finance", "text": "Price changed"}],
        "entry_activity_id": "s", "terminal_activity_ids": ["e"]}
    value.update(overrides)
    return ProcessModel.model_validate(value)


MODEL = _model()


def test_inclusive_block_validates_and_is_not_linear():
    assert MODEL.linear_path() is None


def test_every_inclusive_branch_needs_a_rule():
    transitions = [dict(t) for t in MODEL.model_dump()["transitions"]]
    transitions[2].pop("rule_id")
    with pytest.raises(ValidationError):
        _model(transitions=transitions)


def test_mismatched_join_kind_is_refused():
    activities = MODEL.model_dump()["activities"]
    activities[6]["kind"] = "parallel_join"
    with pytest.raises(ValidationError):
        _model(activities=activities)


@pytest.mark.parametrize("subset", [s for n in (1, 2, 3) for s in itertools.combinations("abc", n)])
def test_any_nonempty_subset_of_branches_replays_completely(subset):
    for order in itertools.permutations(subset):
        result = replay_node_ids(["s", "r", "o", *order, "j", "x", "e"], MODEL)
        assert result["all_produced_branch_tokens_consumed"] is True
        assert set(result["rule_ids"]) == {{"a": "customer", "b": "crm", "c": "finance"}[n] for n in subset}


def test_join_waits_for_every_started_branch_and_no_branch_runs_twice():
    with pytest.raises(ValueError):
        replay_node_ids(["s", "r", "o", "a", "b", "j", "x", "e", "c"], MODEL)
    with pytest.raises(ValueError):
        replay_node_ids(["s", "r", "o", "a", "a", "j", "x", "e"], MODEL)
    with pytest.raises(ValueError):
        replay_node_ids(["s", "r", "o", "j", "x", "e"], MODEL)


def test_conformance_accepts_subsets_and_flags_missing_steps():
    exact = compare_path(("Review change", "Update CRM", "Email customer", "Close change"), MODEL)
    assert exact["exact_structural_match"] is True
    single = compare_path(("Review change", "Alert finance", "Close change"), MODEL)
    assert single["exact_structural_match"] is True
    missing = compare_path(("Review change", "Close change"), MODEL)
    assert missing["exact_structural_match"] is False and missing["minimum_activity_edit_count"] == 1


def test_token_replay_fits_subset_traces():
    fitting = replay_path(("Review change", "Email customer", "Alert finance", "Close change"), MODEL)
    assert fitting["missing"] == 0 and fitting["remaining"] == 0
    skipped = replay_path(("Review change", "Close change"), MODEL)
    assert skipped["missing"] + skipped["remaining"] > 0


def test_nested_parallel_inside_an_inclusive_branch():
    value = MODEL.model_dump()
    value["activities"] += [{"id": "ps", "label": "Fork", "kind": "parallel_split"},
                            {"id": "p1", "label": "Draft note", "kind": "activity"},
                            {"id": "p2", "label": "Notify sales", "kind": "activity"},
                            {"id": "pj", "label": "Forked", "kind": "parallel_join"}]
    value["transitions"] = [t for t in value["transitions"] if not (t["source"] == "c" and t["target"] == "j")] + [
        {"source": "c", "target": "ps"}, {"source": "ps", "target": "p1"}, {"source": "ps", "target": "p2"},
        {"source": "p1", "target": "pj"}, {"source": "p2", "target": "pj"}, {"source": "pj", "target": "j"}]
    nested = ProcessModel.model_validate(value)
    replay_node_ids(["s", "r", "o", "c", "ps", "p2", "p1", "pj", "j", "x", "e"], nested)
    with pytest.raises(ValueError):  # the inclusive join cannot fire while the nested branch is still open
        replay_node_ids(["s", "r", "o", "c", "ps", "p1", "j", "x", "e"], nested)


def _bpmn(default=""):
    return (f'<definitions xmlns="{BPMN}"><process id="p"><startEvent id="s" name="Start"/>'
            f'<task id="r" name="Review change"/><inclusiveGateway id="o" name="Who to notify"{default}/>'
            '<task id="a" name="Email customer"/><task id="b" name="Update CRM"/>'
            '<inclusiveGateway id="j" name="Notified"/><endEvent id="e" name="End"/>'
            '<sequenceFlow id="f1" sourceRef="s" targetRef="r"/><sequenceFlow id="f2" sourceRef="r" targetRef="o"/>'
            '<sequenceFlow id="fa" sourceRef="o" targetRef="a"><conditionExpression>customer_facing</conditionExpression></sequenceFlow>'
            '<sequenceFlow id="fb" sourceRef="o" targetRef="b"/>'
            '<sequenceFlow id="f5" sourceRef="a" targetRef="j"/><sequenceFlow id="f6" sourceRef="b" targetRef="j"/>'
            '<sequenceFlow id="f7" sourceRef="j" targetRef="e"/></process></definitions>').encode()


def test_bpmn_import_and_export_round_trip():
    result = import_bpmn(_bpmn(' default="fb"'))
    model = ProcessModel.model_validate(result["process_model"])
    kinds = sorted(a.kind for a in model.activities if a.kind.startswith("inclusive"))
    assert kinds == ["inclusive_join", "inclusive_split"]
    texts = sorted(rule.text for rule in model.business_rules)
    assert texts == ["Otherwise (default branch when no other condition applies)", "customer_facing"]
    assert "inclusive_branches_run_when_their_unconfirmed_rules_apply" in result["warnings"]
    packet = {"workflow_id": "process_x", "revision": 1, "content_sha256": "0" * 64, "process_model": model.model_dump()}
    exported = process_bpmn(packet)
    assert exported.count("inclusiveGateway") >= 2
    again = ProcessModel.model_validate(import_bpmn(exported.encode())["process_model"])
    assert sorted(a.kind for a in again.activities) == sorted(a.kind for a in model.activities)


def test_bpmn_rejects_ambiguous_inclusive_gateways():
    with pytest.raises(BPMNImportError):
        import_bpmn(_bpmn().replace(b'<sequenceFlow id="f7" sourceRef="j" targetRef="e"/>',
                                    b'<sequenceFlow id="f7" sourceRef="j" targetRef="e"/><sequenceFlow id="f8" sourceRef="j" targetRef="r"/>'))
    with pytest.raises(BPMNImportError):
        import_bpmn(_bpmn().replace(b'<inclusiveGateway id="j" name="Notified"/>',
                                    b'<inclusiveGateway id="j" name="Notified" default="f7"/>'))


def test_agent_project_carries_the_token_flow_contract():
    from shared.process_agent_project import build_runtime_project
    import io
    import json
    import zipfile
    packet = {"format": "openworkgraph.versioned-process-export.v1", "workflow_id": "process_x", "revision": 1,
              "content_sha256": "0" * 64, "process_model": MODEL.model_dump(), "execution_authorization": False}
    archive = zipfile.ZipFile(io.BytesIO(build_runtime_project(packet)))
    spec_name = "workflow.spec.json"
    spec = json.loads(archive.read(spec_name))
    assert spec["control_flow_contract"] == "openworkgraph.parallel-node-firing-plan.v1"
