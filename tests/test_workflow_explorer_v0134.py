from __future__ import annotations

"""Regression checks for the additive read-only workflow-variant explorer."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_explorer_is_additive_to_existing_dashboard_and_evidence_contracts():
    injection = (ROOT / "server/workflow_evidence_dashboard.py").read_text(encoding="utf-8")
    explorer = (ROOT / "dashboard/workflow_explorer.js").read_text(encoding="utf-8")
    legacy = (ROOT / "dashboard/workflow_evidence.js").read_text(encoding="utf-8")
    assert '"/workflow-evidence.js"' in injection
    assert '"/workflow-explorer.js"' in injection
    assert '<script src="/workflow-evidence.js"></script>' in injection
    assert '<script src="/workflow-explorer.js"></script>' in injection
    assert "/v1/workflow-evidence/families?" in explorer
    assert "/v1/workflow-evidence?" in explorer
    assert "candidate_clusters" in explorer
    assert "exact_variants" in explorer
    assert "source_limit_reached" in explorer
    assert "window.__owgAuthReady" in explorer
    assert "get_workflow_evidence" in explorer
    assert "get_workflow_evidence" in legacy
    # It remains a read-only view; never install a second collector or action path.
    assert 'method: "POST"' not in explorer
    assert "method: 'POST'" not in explorer
    assert "PUT" not in explorer
    assert "DELETE" not in explorer
    assert "navigator.sendBeacon" not in explorer
    assert "localStorage" not in explorer


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js unavailable")
def test_explorer_model_keeps_variant_boundaries_and_repeated_steps():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const scope = {
  window: {},
  document: {readyState: 'loading', addEventListener() {}}
};
vm.runInNewContext(fs.readFileSync('dashboard/workflow_explorer.js', 'utf8'), scope);
const model = scope.window.__owgWorkflowExplorerModel;
assert(model && model.buildLanes);
const candidate = {
  surface_labels: {'surface:gmail': 'Gmail'},
  core_steps: ['surface:gmail'],
  execution_count: 4,
  exact_variant_count: 2,
  exact_variants: [
    {
      readable_steps: ['surface:gmail', 'action:edit', 'surface:gmail', 'action:submit'],
      execution_count: 3,
      execution_ids: ['execution:aaaaaaaaaaaaaaaa', 'wrong id']
    },
    {
      readable_steps: ['surface:gmail', 'action:click'],
      execution_count: 1,
      execution_ids: ['execution:bbbbbbbbbbbbbbbb']
    }
  ]
};
const lanes = model.buildLanes(candidate);
assert.equal(lanes.length, 2);
assert.equal(lanes[0].steps.length, 4);
assert.equal(lanes[0].steps[0], 'Gmail');
assert.equal(lanes[0].steps[2], 'Gmail');
assert.equal(lanes[0].steps[1], 'edit');
assert.equal(lanes[0].count, 3);
assert.equal(lanes[0].ids.length, 1);
assert.equal(lanes[1].count, 1);
assert.equal(lanes[1].ids[0], 'execution:bbbbbbbbbbbbbbbb');
assert.equal(model.buildLanes({exact_variants: []}).length, 0);
const huge = {exact_variants: Array.from({length: 8}, () => ({
  readable_steps: Array.from({length: 30}, (_, index) => 'step-' + index),
  execution_count: 1,
  execution_ids: []
}))};
const clipped = model.buildLanes(huge);
assert.equal(clipped.length, 6);
assert.equal(clipped[0].steps.length, 24);
assert.equal(clipped[0].omittedSteps, 6);
const injected = model.buildLanes({
  exact_variants: [{readable_steps: ['<img src=x onerror=alert(1)>']}]
});
assert.equal(injected[0].steps[0], '<img src=x onerror=alert(1)>');
assert(!fs.readFileSync('dashboard/workflow_explorer.js', 'utf8').includes('eval('));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
