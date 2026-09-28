import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const scripts = (html) => [...html.matchAll(/<script nonce="__NONCE__">([\s\S]*?)<\/script>/g)].map((m) => m[1]);

for (const page of ['admin_console.html', 'me_page.html', 'join_verify.html', 'landing.html']) {
  test(`${page}: inline script parses, uses the CSP nonce and loads nothing external`, () => {
    const html = read(path.join('gateway', page));
    const code = scripts(html);
    assert.ok(code.length >= 1, 'has a nonce script');
    for (const body of code) assert.doesNotThrow(() => new Function(body));
    assert.doesNotMatch(html, /<script(?![^>]*nonce="__NONCE__")/);
    assert.doesNotMatch(html, /src="https?:/);
  });
}

test('admin console: named sign-in, setup, roles, roster and identity', () => {
  const html = read('gateway/admin_console.html');
  for (const needle of [
    '/v1/admin/auth/login', '/v1/admin/auth/bootstrap', '/v1/admin/auth/setup/complete', '/v1/sso/start',
    '/v1/admin/people/', '/v1/admin/employees/', '/import', '/offboard', '/invites', '/link',
    '/v1/admin/accounts', '/v1/admin/identity-settings/', 'body.readonly .write', 'gateway_url:location.origin',
    'Require a personal invitation for every new computer', 'self-reported',
  ]) assert.ok(html.includes(needle), needle);
  // Secrets in links travel in the URL fragment and are removed from history.
  assert.match(html, /history\.replaceState\(null,'',location\.pathname\)/);
  assert.match(html, /frag\.get\('setup'\)/);
  assert.ok(html.includes("addEventListener('hashchange',()=>location.reload())"));
  // A double click cannot send the same write twice.
  assert.ok(html.includes("t.dataset.busy='1';t.disabled=true"));
});

test('me page and invite confirmation keep secrets out of URLs sent to servers', () => {
  for (const page of ['gateway/me_page.html', 'gateway/join_verify.html']) {
    const html = read(page);
    assert.match(html, /location\.hash/);
    assert.match(html, /history\.replaceState/);
    assert.ok(html.includes("addEventListener('hashchange',()=>location.reload())"), page);
  }
  assert.ok(read('gateway/me_page.html').includes('/v1/me/evidence'));
  assert.ok(read('gateway/join_verify.html').includes("purpose:'join'"));
});

test('local join UI locks personal identities and links to /me', () => {
  const js = read('dashboard/org_join.js');
  assert.doesNotThrow(() => new Function(js));
  for (const needle of ['You will join as', 'Confirm with company account', '/v1/org-me-link', 'orgJoinActorBox', "noopener"]) {
    assert.ok(js.includes(needle), needle);
  }
});
