import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dashboard=fs.readFileSync(new URL('../../dashboard/event_log_workspace.js',import.meta.url),'utf8');

test('review actions have a separate, bounded local audit section',()=>{
  assert.match(dashboard,/owgSignalAuditHistory/);
  assert.match(dashboard,/owgSignalAuditRefresh/);
  assert.match(dashboard,/\/signal-review-history/);
  assert.match(dashboard,/getJSON\(auditBase\(\)\+'\?limit=100'\)/);
  assert.match(dashboard,/Only the most recent 3,000 entries per dataset are retained/);
});
test('audit history is generation guarded, displays the privacy boundary and permits manual reload',()=>{
  assert.match(dashboard,/generation!==state\.generation\|\|requestId!==auditRequestId/);
  assert.match(dashboard,/no verified individual identity or execution approval/i);
  assert.match(dashboard,/auditRefresh\.onclick=\(\)=>refreshAudit\(\)/);
  assert.match(dashboard,/await refreshAudit\(\)/);
  assert.match(dashboard,/x\.action==='snoozed'\?String\(x\.snooze_minutes\)\+' min'/);
});
