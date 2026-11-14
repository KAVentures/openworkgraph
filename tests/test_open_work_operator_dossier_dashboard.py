"""Operator dossier uses reviewed declaration CAS and explicit scan approval."""
import pathlib
import shutil
import subprocess
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_dossier_operator_consent_cas_and_unknown_outcome():
    js = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class E {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={};
    this.textContent=''; this.value=''; this.checked=false; this.disabled=false; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children=[...children]; }
  setAttribute() {}
  addEventListener(type, fn) { this.listeners[type]=fn; }
}
const host = new E('div');
const document = {readyState:'complete',createElement:t=>new E(t),
  createTextNode:t=>({textContent:t}),
  getElementById(id) {
    if (id==='panel-evidence') return host;
    const find=n=>n.id===id?n:(n.children||[]).map(find).find(Boolean);
    return find(host);
  }};
const ref='owg:w:'+'a'.repeat(32);
let requests=[];
const fetch=async (url,opts)=>{
  requests.push({url,opts});
  if (url==='/v1/open-work/declarations') return {ok:true,json:async()=>({
    format:'openworkgraph.local-case-declarations.v1',available:true,
    items:[{work_item_ref:ref,revision:2,edit_token:'b'.repeat(32)}]})};
  if (url==='/v1/open-work/declarations/dossier') return {ok:true,json:async()=>({
    format:'openworkgraph.local-reviewed-case-dossier.v1',work_item_ref:ref,
    source_reported_status:{availability:'unknown',state:'unknown'},
    owner_declaration:{goal_code:'collect_payment',expected_outcome_code:'invoice_paid'},
    declared_prerequisite_count:1,agent_execution_authorized:false,
    customer_outcome_verified:false,expected_outcome_verified:false,
    source_write_performed:false})};
  throw Error('unexpected URL');
};
vm.runInNewContext(fs.readFileSync('dashboard/open_work_dependencies.js','utf8'),
  {document,window:{__owgAuthReady:Promise.resolve()},fetch,AbortController,encodeURIComponent});
const card=document.getElementById('owgDurableDependencyView');
const forms=card.children.filter(x=>x.tagName==='form');
assert.equal(forms.length,2);
const form=forms[1], refField=form.children[0].children[0];
const consent=form.children[1].children[0];
const status=card.children[card.children.indexOf(form)+1];
const results=card.children[card.children.indexOf(form)+2];
(async()=>{
  refField.value=ref;
  await form.listeners.submit({preventDefault(){}});
  assert.equal(requests.length,0,'no read without explicit consent');
  consent.checked=true;
  await form.listeners.submit({preventDefault(){}});
  assert.equal(requests.length,2);
  const payload=JSON.parse(requests[1].opts.body);
  assert.equal(payload.expected_revision,2);
  assert.equal(payload.expected_edit_token,'b'.repeat(32));
  assert.equal(payload.confirm_source_scan,true);
  assert.equal(payload.max_pages,1);
  assert.equal(consent.checked,false,'consent is one-shot');
  assert.match(status.textContent,/not business success/);
  assert.ok(results.children.some(x=>/Customer outcome: unknown/.test(x.textContent)));
  await form.listeners.submit({preventDefault(){}});
  assert.equal(requests.length,2,'no implicit second scan');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(["node","-e",js],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
