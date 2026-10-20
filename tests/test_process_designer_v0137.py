"""Reviewable process-design UI, evidence semantics and export guardrails."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_process_designer_model_contract_and_review_gate():
    code=r"""
const fs=require('fs');
const vm=require('vm');
const assert=require('assert');
const scope={window:{},document:{}};
const src=fs.readFileSync('dashboard/process_designer.js','utf8');
vm.runInNewContext(src,scope);
const m=scope.window.__owgProcessDesignModel;
const report={format:'openworkgraph.process-mining-csv-preview.v1',input_rows:8,case_count:3};
const variant={observed_path:['Receive','Review','Review','Close'],case_count:2,path_truncated:false};
assert(m.validStep('Review'));
assert(!m.validStep('<script>'));
assert(!m.validStep(' '));
const d=m.diff(variant.observed_path,['Receive','Review','Close','Archive']);
assert.equal(d.removed.length,1);
assert.equal(d.removed[0],'Review');
assert.equal(d.added[0],'Archive');
assert.equal(d.order_changed,false);
assert.equal(m.diff(['A','B'],['B','A']).order_changed,true);
assert.deepEqual(Array.from(m.diff(['A','A'],['A']).removed),['A']);
assert.throws(()=>m.packet(report,variant,variant.observed_path,'Close ticket',false),/review/);
assert.throws(()=>m.packet(report,{...variant,path_truncated:true},variant.observed_path,'Close ticket',true),/complete/);
assert.throws(()=>m.packet(report,variant,['<script>'],'Close ticket',true),/path|labeled/);
assert.throws(()=>m.packet(report,variant,variant.observed_path,'',true),/goal|Describe/);
const output=m.packet(report,variant,['Receive','Review','Close'],'Resolve ticket',true);
assert.equal(output.format,'openworkgraph.proposed-process-design.v1');
assert.equal(output.status,'human_reviewed_draft_not_approved_policy');
assert.equal(output.user_reviewed_export,true);
assert.equal(output.approved_business_policy,false);
assert.equal(output.authorized_agent_actions,false);
assert.equal(output.agent_execution_triggered,false);
assert.equal(output.to_be.proposed_path.length,3);
assert.equal(output.to_be.change_summary.removed[0],'Review');
assert.equal(output.as_is.source_observation_not_policy,true);
assert.equal(output.source_case_ids_included,false);
assert(!JSON.stringify(output).includes('native_id'));
"""
    run=subprocess.run(["node","-e",code],cwd=ROOT,text=True,
                       capture_output=True,timeout=25)
    assert run.returncode==0,run.stderr


def test_process_designer_is_additive_local_no_automatic_upload_or_writes():
    script=(ROOT/"dashboard/process_designer.js").read_text(encoding="utf-8")
    miner=(ROOT/"dashboard/process_mining.js").read_text(encoding="utf-8")
    html=(ROOT/"dashboard/index.html").read_text(encoding="utf-8")
    routes=(ROOT/"server/workflow_evidence_routes.py").read_text(encoding="utf-8")
    ci=(ROOT/".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert '<script src="/process-designer.js"></script>' in html
    assert '@app.get("/process-designer.js")' in routes
    assert 'ROOT / "dashboard" / "process_designer.js"' in routes
    assert "window.owgProcessDesigner.open(report,selected)" in miner
    assert "window.owgProcessDesigner?.reset?.()" in miner
    assert "window.owgProcessDesigner?.hasDraft?.()" in miner
    assert "hasDraft:()=>!!state" in script
    assert "file.addEventListener('change'" in miner
    assert 'node --check dashboard/process_designer.js' in ci
    assert "user_reviewed_export:true" in script
    assert "agent_execution_triggered:false" in script
    assert "authorized_agent_actions:false" in script
    assert "approved_business_policy:false" in script
    assert "URL.createObjectURL(blob)" in script
    for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon(", "localStorage",
                      "indexedDB", "eval(", "innerHTML", "document.cookie",
                      "method:'POST'"):
        assert forbidden not in script, forbidden
