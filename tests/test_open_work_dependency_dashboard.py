"""Regression guard for the read-only durable-dependency dashboard response race.

Runs the actual browser script in a minimal Node DOM harness; no provider access.
"""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_invalid_newer_submission_cancels_older_dependency_response():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.value = '';
    this.textContent = '';
    this.disabled = false;
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
}
const host = new Element('div');
const document = {
  readyState: 'complete',
  getElementById(id) {
    if (id === 'panel-evidence') return host;
    const walk = node => {
      if (node.id === id) return node;
      for (const child of node.children || []) {
        const found = walk(child);
        if (found) return found;
      }
      return null;
    };
    return walk(host);
  },
  createElement(tag) { return new Element(tag); },
  createTextNode(value) { return { textContent: value }; }
};
let resolveFetch;
let seenSignal;
const fetch = (_url, options) => {
  seenSignal = options.signal;
  return new Promise(resolve => { resolveFetch = resolve; });
};
vm.runInNewContext(
  fs.readFileSync('dashboard/open_work_dependencies.js', 'utf8'),
  {document, fetch, AbortController, encodeURIComponent}
);
const card = document.getElementById('owgDurableDependencyView');
assert.ok(card, 'dashboard must mount');
const form = card.children.find(x => x.tagName === 'form');
const field = form.children[0].children[0];
const button = form.children[2];
const status = document.getElementById('owgDependencyStatus');
const list = document.getElementById('owgDependencyResults').children[0];
(async () => {
  field.value = 'owg:w:' + 'a'.repeat(32);
  const old = form.listeners.submit({preventDefault() {}});
  assert.ok(seenSignal && !seenSignal.aborted);
  field.value = 'invalid';
  await form.listeners.submit({preventDefault() {}});
  assert.equal(seenSignal.aborted, true, 'new invalid input must cancel old fetch');
  assert.match(status.textContent, /valid opaque/);
  assert.equal(button.disabled, false);
  resolveFetch({ok:true, json:async () => ({
    format:'openworkgraph.reviewed-durable-dependencies.v1',
    agent_execution_authorized:false, customer_outcome:'unknown',
    reviewed_case_count:100, reviewed_dependency_count:100
  })});
  await old;
  assert.match(status.textContent, /valid opaque/, 'stale response must not overwrite validation');
  assert.equal(list.children.length, 0, 'stale data must not reappear');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
