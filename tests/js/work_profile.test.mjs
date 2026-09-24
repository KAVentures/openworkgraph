import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('dashboard/work_profile.js','utf8');

assert.match(source, /\/v1\/dashboard-work-profile\?scope=current/);
assert.doesNotMatch(source, /fetch\('\/v1\/work-profile\?scope=current/);
assert.match(source, /\/v1\/self-tags/);
assert.match(source, /not employee productivity scores/i);
assert.match(source, /clipboard contents are never read/i);
assert.match(source, /user-provided context, not sensed behavior/i);
assert.match(source, /Gaps are not automatically treated as breaks/i);
assert.match(source, /Manual transfers/);
assert.match(source, /AI-tool usage/);
assert.match(source, /dashboard hides resource names and paths/i);
assert.doesNotMatch(source, /x\.resource_locator/);
assert.doesNotMatch(source, /x\.target_label/);
assert.doesNotMatch(source, /reactive vs\. productive/i);
assert.doesNotMatch(source, /notification-driven/i);
assert.doesNotMatch(source, /rage click/i);

console.log('work profile dashboard source contract ok');
