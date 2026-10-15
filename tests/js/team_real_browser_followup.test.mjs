import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const dashboard = fs.readFileSync('dashboard/index.html', 'utf8');
const scripts = [...dashboard.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x => x[1]);
const source = scripts.find(s => s.includes('function savedDaySurface(event)'));
assert.ok(source);

function helper(name, next) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n' + next, start);
  assert.ok(end > start, next);
  return new Function(source.slice(start, end) + ';return ' + name)();
}

test('all dashboard inline scripts and changed modules parse', () => {
  for (const code of scripts) assert.doesNotThrow(() => new Function(code));
  for (const file of ['dashboard/connections.js', 'dashboard/gateway_panel.js'])
    assert.doesNotThrow(() => new Function(fs.readFileSync(file, 'utf8')));
});

test('saved-day browser surfaces use known site identity not Chrome', () => {
  const surface = helper('savedDaySurface', 'function savedDayAction');
  assert.equal(surface({ app: 'Google Chrome', metadata: {
    page: { hostname: 'mail.google.com' } } }), 'Gmail');
  assert.equal(surface({ app: 'Browser', page_host: 'app.fortnox.se.' }), 'Fortnox');
  assert.equal(surface({ app: 'Google Chrome', browser_hostname: 'jira.acme.atlassian.net' }), 'Jira');
  assert.equal(surface({ app: 'Google Chrome', page_host: 'mail.google.com.evil.com' }),
    'mail.google.com.evil.com');
  assert.equal(surface({ app: 'Microsoft Excel' }), 'Microsoft Excel');
});

test('saved-day browser presents actions without raw snake_case', () => {
  const action = helper('savedDayAction', 'async function showSavedDay');
  assert.equal(action({ event_type: 'focus_span' }), 'Focused on application');
  assert.equal(action({ event_type: 'resource_reference_observed' }), 'Viewed work reference');
  assert.equal(action({ event_type: 'browser_navigation', action: 'navigate' }), 'Visited page');
  assert.equal(action({ event_type: 'browser_paste', action: 'paste' }), 'Pasted (contents not recorded)');
  assert.equal(action({ event_type: 'some_other_event' }), 'some other event');
});

test('rejoin, copy and fresh sync wording are accurate', () => {
  const connect = fs.readFileSync('dashboard/connections.js', 'utf8');
  const gateway = fs.readFileSync('dashboard/gateway_panel.js', 'utf8');
  assert.doesNotMatch(connect, /OWG sign-in above/);
  assert.match(connect, /ChatGPT cloud-sharing section below/);
  assert.match(gateway, /Have a new invitation/);
  const service = fs.readFileSync('connector/service.py', 'utf8');
  assert.match(service, /state.delete\("last_success_at"\)/);
});
