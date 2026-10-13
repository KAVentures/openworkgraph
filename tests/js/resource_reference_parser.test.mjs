import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function parser() {
  const context = vm.createContext({URL, console});
  const source = fs.readFileSync(new URL('../../browser_extension/resource_reference_parser.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);
  return raw => JSON.parse(JSON.stringify(context.OpenWorkGraphResourceReferences.parse(raw)));
}

test('extracts only bounded Google workspace object locators', () => {
  const parse = parser();
  const id = '1A2b3C4d5E6f7G8h9I0jKLMNop';
  assert.deepEqual(parse(`https://docs.google.com/document/d/${id}/edit?token=SECRET#heading=h.x`), {
    provider: 'google_drive',
    resource_kind: 'document',
    resolver_locator: `document:${id}`,
    host: 'docs.google.com',
    resolution: 'observed',
  });
});

test('extracts GitHub, Jira, Salesforce, Linear and Gmail references without full URLs', () => {
  const parse = parser();
  const values = [
    parse('https://github.com/KAVentures/openworkgraph/pull/123?diff=split'),
    parse('https://acme.atlassian.net/browse/OPS-431?focusedCommentId=SECRET'),
    parse('https://acme.lightning.force.com/lightning/r/Opportunity/006ABCDEF123456789/view'),
    parse('https://linear.app/acme/issue/ENG-42/fix-the-thing'),
    parse('https://mail.google.com/mail/u/0/#inbox/FMfcgzQZSxExampleThread'),
  ];
  assert.equal(values[0].resolver_locator, 'KAVentures/openworkgraph/pull/123');
  assert.equal(values[1].resolver_locator, 'OPS-431');
  assert.equal(values[2].resolver_locator, 'Opportunity:006ABCDEF123456789');
  assert.equal(values[3].resolver_locator, 'acme:ENG-42');
  assert.equal(values[4].resolver_locator, 'web-thread:FMfcgzQZSxExampleThread');
  for (const value of values) {
    const blob = JSON.stringify(value);
    assert.equal(blob.includes('?'), false);
    assert.equal(blob.includes('#'), false);
    assert.equal(blob.includes('SECRET'), false);
    assert.equal(blob.includes('https://'), false);
  }
});

test('does not turn arbitrary URLs or sensitive-looking app routes into references', () => {
  const parse = parser();
  assert.equal(parse('https://ehr.example/patient/123456789'), null);
  assert.equal(parse('https://example.com/reset/SECRETSECRETSECRET'), null);
  assert.equal(parse('https://github.com/KAVentures/openworkgraph/settings'), null);
  assert.equal(parse('https://mail.google.com/mail/u/0/#inbox'), null);
  assert.equal(parse('https://mail.google.com/mail/u/0/#search/averylongsinglewordquery'), null);
});

test('durable browser resource fingerprints use pairing-secret HMAC rather than a plain hash', () => {
  const source = fs.readFileSync(new URL('../../browser_extension/resource_reference_enrichment.js', import.meta.url), 'utf8');
  assert.match(source, /OWGBrowserAuth\?\.pairingSecret/);
  assert.match(source, /name:\s*"HMAC"/);
  assert.match(source, /openworkgraph-resource-reference-sensor-v1:/);
  assert.match(source, /owg:e:/);
  assert.equal(source.includes('crypto.subtle.digest("SHA-256"'), false);
});

test('recognizes only known ChatGPT and Claude conversation UUID paths', () => {
  const parse = parser();
  const id = '123e4567-e89b-42d3-a456-426614174000';
  const chat = parse('https://chatgpt.com/c/' + id);
  const old = parse('https://chat.openai.com/c/' + id);
  const claude = parse('https://claude.ai/chat/' + id);
  assert.deepEqual(chat, {
    provider: 'chatgpt', resource_kind: 'conversation',
    resolver_locator: 'conversation:' + id, host: 'chatgpt.com',
    resolution: 'observed',
  });
  assert.equal(old.provider, 'chatgpt');
  assert.equal(claude.provider, 'claude');
  assert.equal(claude.resolver_locator, 'conversation:' + id);
  for (const ref of [chat, old, claude]) {
    assert.equal(JSON.stringify(ref).includes('https://'), false);
  }
  assert.equal(parse('https://chatgpt.com/'), null);
  assert.equal(parse('https://chatgpt.com/c/not-a-uuid'), null);
  assert.equal(parse('https://chatgpt.com/c/' + id + '?query=patient'), null);
  assert.equal(parse('https://chatgpt.com/share/' + id), null);
  assert.equal(parse('https://claude.ai/chat/' + id + '#prompt'), null);
  assert.equal(parse('https://claude.ai/settings'), null);
  assert.equal(parse('https://evil.chatgpt.com/c/' + id), null);
});
