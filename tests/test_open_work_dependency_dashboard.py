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
    this.style = {};
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
let rejectFetch;
let seenSignal;
let fetchCount = 0;
const window = {};
const fetch = (_url, options) => {
  fetchCount++;
  seenSignal = options.signal;
  return new Promise((resolve, reject) => { resolveFetch = resolve; rejectFetch = reject; });
};
vm.runInNewContext(
  fs.readFileSync('dashboard/open_work_dependencies.js', 'utf8'),
  {document, window, fetch, AbortController, encodeURIComponent}
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
  // A superseded fetch may reject with a network error rather than AbortError.
  // That rejection must not overwrite the newer validation result.
  field.value = 'owg:w:' + 'b'.repeat(32);
  const oldRejected = form.listeners.submit({preventDefault() {}});
  field.value = 'invalid-again';
  await form.listeners.submit({preventDefault() {}});
  rejectFetch(new Error('late network failure'));
  await oldRejected;
  assert.match(status.textContent, /valid opaque/, 'stale rejection must not overwrite validation');
  assert.equal(list.children.length, 0, 'stale rejection must not restore results');
  assert.equal(button.disabled, false);
  // Authentication bootstrap must complete before any API request is sent.
  let releaseAuth;
  window.__owgAuthReady = new Promise(resolve => { releaseAuth = resolve; });
  const countBeforeAuth = fetchCount;
  field.value = 'owg:w:' + 'c'.repeat(32);
  const waiting = form.listeners.submit({preventDefault() {}});
  assert.equal(fetchCount, countBeforeAuth, 'must not fetch before auth bootstrap');
  assert.equal(button.disabled, true);
  // Superseding an in-flight auth wait must not issue a late fetch.
  field.value = 'bad-ref';
  await form.listeners.submit({preventDefault() {}});
  releaseAuth();
  await waiting;
  assert.equal(fetchCount, countBeforeAuth, 'cancelled auth wait must not fetch');
  assert.match(status.textContent, /valid opaque/);
  assert.equal(button.disabled, false);
  // A failed bootstrap must fail closed without calling the API.
  window.__owgAuthReady = Promise.reject(new Error('auth bootstrap failed'));
  field.value = 'owg:w:' + 'd'.repeat(32);
  await form.listeners.submit({preventDefault() {}});
  assert.equal(fetchCount, countBeforeAuth, 'failed auth bootstrap must not fetch');
  assert.match(status.textContent, /Could not retrieve/);
  assert.equal(button.disabled, false);
  // A syntactically successful HTTP response can still contain malformed or
  // hostile data. Reject the entire response without showing partial counts.
  window.__owgAuthReady = Promise.resolve();
  field.value = 'owg:w:' + 'e'.repeat(32);
  const malformed = form.listeners.submit({preventDefault() {}});
  await Promise.resolve();
  resolveFetch({ok:true, json:async () => ({
    format:'openworkgraph.reviewed-durable-dependencies.v1',
    agent_execution_authorized:false, customer_outcome:'unknown',
    reviewed_case_count:-1, reviewed_dependency_count:1,
    added_reviewed_edges:0, removed_stale_edges:0
  })});
  await malformed;
  assert.match(status.textContent, /Unexpected review response/);
  assert.equal(list.children.length, 0);
  assert.equal(button.disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
