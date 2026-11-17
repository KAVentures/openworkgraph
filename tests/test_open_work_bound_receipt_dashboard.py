"""Browser-script regression for explicit operator receipt review and revocation."""
import pathlib
import shutil
import subprocess
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_receipt_review_requires_approval_and_clears_on_withdrawal():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {
    this.tagName=tag; this.children=[]; this.listeners={}; this.value='';
    this.textContent=''; this.disabled=false; this.checked=false; this.style={};
  }
  append(...xs) { this.children.push(...xs); }
  appendChild(x) { this.children.push(x); return x; }
  replaceChildren(...xs) { this.children=[...xs]; }
  setAttribute() {}
  addEventListener(k, fn) { this.listeners[k]=fn; }
}
const host=new Element('div');
const document={
  readyState:'complete',
  getElementById(id) {
    if(id==='panel-evidence') return host;
    const walk=n => {
      if(n.id===id) return n;
      for(const x of n.children||[]) {const r=walk(x); if(r) return r;}
      return null;
    };
    return walk(host);
  },
  createElement:t=>new Element(t),
  createTextNode:t=>({textContent:t})
};
const ref='owg:w:'+'a'.repeat(32);
const calls=[];
let delayReceipt=false;
let releaseReceipt;
let invalidReceipt=false;
const fetch=async (url, options) => {
  calls.push({url,options});
  if(url==='/v1/open-work/declarations') return {ok:true,json:async()=>({
    format:'openworkgraph.local-case-declarations.v1',available:true,
    items:[{work_item_ref:ref,revision:1,edit_token:'b'.repeat(32)}]
  })};
  if(url.endsWith('/attempt-receipts')) {
    if(delayReceipt) await new Promise(resolve=>{releaseReceipt=resolve});
    return {ok:true,json:async()=>({
    format:'openworkgraph.bound-attempt-receipts.v1',case_ref:ref,run_ref:'run:1',
    receipt_provenance:'local_untrusted_journal',
    provider_event_independently_rechecked_on_this_read:false,
    customer_outcome_verified:invalidReceipt,agent_execution_authorized:false,
    source_read_performed:false,
    entries:[{id:1,checked_at:'2026-11-17T00:00:00Z',
      status:'unknown',provider_operation:'unknown',customer_outcome:'unknown'}]
  })};
  }
  throw Error('unexpected route');
};
vm.runInNewContext(fs.readFileSync('dashboard/open_work_dependencies.js','utf8'),
  {document,window:{},fetch,AbortController,encodeURIComponent});
const card=document.getElementById('owgDurableDependencyView');
const form=card.children.find(x=>x.tagName==='form' && x.children.some(y=>y.textContent==='Case reference: '));
assert.ok(form,'operator receipt form mounted');
const inputs=form.children.slice(0,3).map(label=>label.children[0]);
const approval=form.children[3].children[0];
const button=form.children[4];
const status=card.children[card.children.indexOf(form)+1];
const results=card.children[card.children.indexOf(form)+2];
(async()=>{
  [inputs[0].value,inputs[1].value,inputs[2].value]=[ref,'deployment','run'];
  await form.listeners.submit({preventDefault(){}});
  assert.equal(calls.length,0,'no request without approval');
  approval.checked=true;
  await form.listeners.submit({preventDefault(){}});
  assert.equal(calls.length,2,'declaration admission then bound journal read');
  assert.equal(JSON.parse(calls[1].options.body).confirm_run_comparison,true);
  assert.equal(results.children.length,3);
  assert.equal(approval.checked,true,'approval remains visible for withdrawal');
  await form.listeners.submit({preventDefault(){}});
  assert.equal(calls.length,2,'one approval cannot silently repeat a read');
  approval.checked=false;
  approval.listeners.change();
  assert.equal(results.children.length,0,'withdrawal clears displayed historical data');
  assert.match(status.textContent,/withdrawn/);
  assert.equal(button.disabled,false);
  // A revoked review must never render a late response, even if fetch ignores abort.
  delayReceipt=true;
  approval.checked=true;
  const pending=form.listeners.submit({preventDefault(){}});
  for(let i=0;i<20 && !releaseReceipt;i++) await new Promise(resolve=>setImmediate(resolve));
  assert.ok(releaseReceipt,'deferred receipt request reached');
  approval.checked=false;
  approval.listeners.change();
  releaseReceipt();
  await pending;
  assert.equal(results.children.length,0,'late response after withdrawal remains hidden');
  assert.match(status.textContent,/withdrawn/);
  // An inconsistent server projection must fail closed without asserting success.
  delayReceipt=false;
  invalidReceipt=true;
  approval.checked=true;
  await form.listeners.submit({preventDefault(){}});
  assert.equal(results.children.length,0,'invalid outcome verification claim rejected');
  assert.match(status.textContent,/denied|unavailable/);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
