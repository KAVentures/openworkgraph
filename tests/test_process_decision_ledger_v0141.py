"""Safe manual exception/decision ledger: no conditional execution semantics."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

import pytest

from server.process_agent_starter import AgentStarterError, build_agent_starter

ROOT=Path(__file__).resolve().parents[1]


def _reviewed_packet():
    condition = {
        "step_index": 1, "at_activity": "Review",
        "kind": "exception_condition",
        "condition_label": "Missing approval",
        "response": "pause_for_review",
        "source": "user_declared_unverified",
        "evaluated_by_owg": False,
        "automatic_branch_authorized": False,
        "executable": False,
    }
    return {
        "format": "openworkgraph.proposed-process-design.v1",
        "status": "human_reviewed_draft_not_approved_policy",
        "user_reviewed_export": True,
        "approved_business_policy": False,
        "authorized_agent_actions": False,
        "agent_execution_triggered": False,
        "source_case_ids_included": False,
        "uploaded_data_retained_by_owg": False,
        "business_goal_declared_by_user": "Resolve the business case",
        "as_is": {"path": ["Receive", "Review"],
                  "source_observation_not_policy": True},
        "to_be": {
            "proposed_path": ["Receive", "Review", "Close"],
            "proposed_not_deployed": True,
            "decision_review_ledger": {
                "entries": [condition],
                "status": "unverified_user_declared_review_conditions",
                "executable": False,
                "verified_business_policy": False,
            },
        },
    }


def test_nonexecuting_review_ledger_is_exported_only_as_untrusted_data():
    with ZipFile(BytesIO(build_agent_starter(_reviewed_packet()))) as archive:
        spec=json.loads(archive.read("workflow.spec.json"))
        conditions=spec["to_be"]["manual_review_conditions"]
        assert len(conditions)==1
        assert conditions[0]["at_step_id"]=="step_002"
        assert conditions[0]["condition_label"]=="Missing approval"
        assert conditions[0]["response"]=="pause_for_review"
        assert conditions[0]["executable"] is False
        assert spec["to_be"]["automatic_decision_routing_enabled"] is False
        assert b"Missing approval" not in archive.read("AGENTS.md")
        assert b"Missing approval" not in archive.read("dry_run.py")
        from xml.etree import ElementTree as ET
        ns={"bpmn":"http://www.omg.org/spec/BPMN/20100524/MODEL"}
        bpmn=archive.read("process.bpmn")
        tree=ET.fromstring(bpmn)
        process=tree.find("bpmn:process",ns)
        assert process is not None and process.attrib["isExecutable"]=="false"
        assert len(process.findall("bpmn:exclusiveGateway",ns))==1
        assert len(process.findall("bpmn:manualTask",ns))==1
        assert len(process.findall("bpmn:task",ns))==3
        assert len(process.findall("bpmn:sequenceFlow",ns))==7
        assert b"Missing approval" in bpmn  # descriptive XML documentation only
        assert b"conditionExpression" not in bpmn
        assert b"isExecutable=\"true\"" not in bpmn


@pytest.mark.parametrize("bad_entry", [
    {"step_index": 100},
    {"step_index": True},
    {"step_index": -1},
    {"kind": "auto_accept"},
    {"condition_label": "<script>alert(1)</script>"},
    {"condition_label": "   "},
    {"response": "execute_write"},
    {"evaluated_by_owg": True},
    {"automatic_branch_authorized": True},
    {"executable": True},
    {"at_activity": "Wrong"},
    {"source": "verified_policy"},
])
def test_agent_starter_rejects_invalid_or_implicitly_authorized_rules(bad_entry):
    packet=_reviewed_packet()
    original=packet["to_be"]["decision_review_ledger"]["entries"][0]
    packet["to_be"]["decision_review_ledger"]["entries"]=[{**original, **bad_entry}]
    with pytest.raises(AgentStarterError):
        build_agent_starter(packet)


def test_duplicate_and_unbounded_conditions_are_rejected():
    packet=_reviewed_packet()
    ledger=packet["to_be"]["decision_review_ledger"]
    ledger["entries"]*=2
    with pytest.raises(AgentStarterError, match="duplicate"):
        build_agent_starter(packet)
    ledger["entries"]=[{**ledger["entries"][0], "condition_label": f"Condition {i}"}
                      for i in range(13)]
    with pytest.raises(AgentStarterError, match="too many"):
        build_agent_starter(packet)


def test_legacy_reviewed_process_without_rule_ledger_remains_compatible():
    packet=_reviewed_packet()
    del packet["to_be"]["decision_review_ledger"]
    with ZipFile(BytesIO(build_agent_starter(packet))) as archive:
        spec=json.loads(archive.read("workflow.spec.json"))
        assert spec["to_be"]["manual_review_conditions"]==[]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_browser_packet_validates_rules_and_keeps_repeated_step_positions():
    src=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scope={window:{},document:{}};
vm.runInNewContext(fs.readFileSync('dashboard/process_designer.js','utf8'),scope);
const model=scope.window.__owgProcessDesignModel;
const report={format:'openworkgraph.process-mining-csv-preview.v1',input_rows:4,case_count:2};
const variant={observed_path:['Receive','Review','Review'],case_count:2,path_truncated:false};
const path=['Receive','Review','Review'];
const rules=[{
 step_index:2,kind:'decision_point',condition_label:'Unexpected evidence',
 response:'request_source_recheck'
}];
assert.equal(model.validateRules(path,rules)[0].at_activity,'Review');
const output=model.packet(report,variant,path,'Investigate case',true,rules);
assert.equal(output.to_be.decision_review_ledger.entries[0].step_index,2);
assert.equal(output.to_be.decision_review_ledger.executable,false);
assert.equal(output.to_be.decision_review_ledger.entries[0].automatic_branch_authorized,false);
assert.throws(()=>model.validateRules(path,[{...rules[0],step_index:3}]),/Decision/);
assert.throws(()=>model.validateRules(path,[rules[0],rules[0]]),/Duplicate/);
assert.throws(()=>model.validateRules(path,[{...rules[0],condition_label:'<img>'}]),/Decision/);
assert.throws(()=>model.packet(report,variant,path,'Investigate case',false,rules),/review/);
"""
    r=subprocess.run(["node","-e",src],cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr


def test_decision_ledger_is_manual_ui_and_bounded_static_script():
    designer=(ROOT/"dashboard/process_designer.js").read_text(encoding="utf-8")
    script=(ROOT/"dashboard/process_decisions.js").read_text(encoding="utf-8")
    html=(ROOT/"dashboard/index.html").read_text(encoding="utf-8")
    routes=(ROOT/"server/workflow_evidence_routes.py").read_text(encoding="utf-8")
    ci=(ROOT/".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert '<script src="/process-decisions.js"></script>' in html
    assert '@app.get("/process-decisions.js")' in routes
    assert 'node --check dashboard/process_decisions.js' in ci
    assert 'window.owgDecisionLedger?.render?.(state,host,render)' in designer
    assert 'state.rules=[];' in designer  # invalidate positional refs on step edits
    assert 'window.__owgProcessDesignModel.validateRules' in script
    assert 'state.reviewed=false' in script
    for forbidden in ("fetch(", "localStorage", "innerHTML", "sendBeacon",
                      "new Function(", "eval(", "document.cookie"):
        assert forbidden not in script,forbidden
