import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'..','..');
const source=fs.readFileSync(path.join(root,'dashboard','evidence_delete.js'),'utf8');

test('evidence deletion dashboard script parses',()=>{
  assert.doesNotThrow(()=>new Function(source));
});

test('evidence deletion uses inline confirmation and authenticated local endpoint',()=>{
  assert.match(source,/Delete last 15 minutes/);
  assert.match(source,/Delete last 1 hour/);
  assert.match(source,/Custom range/);
  assert.match(source,/\/v1\/evidence\/delete/);
  assert.match(source,/await window\.__owgAuthReady/);
  assert.match(source,/already synchronized to an organization Gateway is not automatically recalled/);
  assert.doesNotMatch(source,/window\.confirm\(/);
});

test('the delete layer never overwrites the real version label',()=>{
  // It used to write a fixed "v0.57.0" until the summary arrived.
  assert.doesNotMatch(source,/versionLabel/);
  assert.doesNotMatch(source,/v0\.\d+\.\d+/);
});

test('delete controls sit below the evidence and stay quiet until confirmed',()=>{
  assert.match(source,/panel\.appendChild\(controls\)/);
  assert.match(source,/class="danger-quiet small" id="delete15m"/);
  assert.match(source,/id="confirmEvidenceDelete" style="background:var\(--red\)/);
});

test('a completed deletion shows the server verification and kept agent records',()=>{
  const nodes=[];
  const document={readyState:'complete',querySelector:()=>null,createElement:()=>({set textContent(v){this.innerHTML=String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}})};
  const window={};
  const exported=source.replace(/\}\)\(\);\s*$/,'window.__summary=deletionSummary;})();');
  new Function('window','document',exported)(window,document);
  const clean=window.__summary({verification:{verified_clean:true,remaining_in_separately_deleted_stores:{agent_session_messages:2,agent_working_detail:0}},receipt:{sha256:'abcdef0123456789ffff'}});
  assert.match(clean,/Verified:/);
  assert.match(clean,/2 agent conversation messages and 0 agent working detail records/);
  assert.match(clean,/Receipt abcdef0123456789</);
  const dirty=window.__summary({verification:{verified_clean:false,remaining_in_separately_deleted_stores:{}}});
  assert.match(dirty,/Not verified:/);
  assert.doesNotMatch(dirty,/agent conversation/);
  assert.equal(window.__summary({}),'');
  assert.ok(nodes.length===0);
});

test('the confirmation offers deleting AI conversations too and sends the explicit choice',()=>{
  assert.match(source,/id="deleteAgentConversations" checked/);
  assert.match(source,/Also delete AI conversation messages and agent working detail from this time/);
  assert.match(source,/include_agent_conversations:includeAgentConversations===true/);
  assert.match(source,/apiDelete\(since,until,host\.querySelector\('#deleteAgentConversations'\)\.checked\)/);
  const document={readyState:'complete',querySelector:()=>null,createElement:()=>({set textContent(v){this.innerHTML=String(v);}})};
  const window={};
  new Function('window','document',source.replace(/\}\)\(\);\s*$/,'window.__summary=deletionSummary;})();'))(window,document);
  const html=window.__summary({verification:{verified_clean:true,remaining_in_separately_deleted_stores:{}},
    agent_conversations_included:true,agent_conversation_messages_deleted:3,agent_working_detail_deleted:1});
  assert.match(html,/3 AI conversation messages and 1 agent working detail record deleted/);
  assert.doesNotMatch(html,/are kept/);
});
