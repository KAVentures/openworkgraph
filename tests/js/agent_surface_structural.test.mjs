import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// Minimal element stand-ins: only what the detector reads (attributes, tag,
// closest, form). No text content is exercised except the English fallback.
function el({tag = 'button', attrs = {}, text = '', form = null, editable = false} = {}) {
  const node = {
    tagName: tag.toUpperCase(), textContent: text, form, isContentEditable: editable,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    closest(sel) {
      if (sel === 'form') return node.form || null;
      if (sel === '[data-testid]') return attrs['data-testid'] ? node : null;
      return /button|role="button"|input\[type="submit"\]/.test(sel) && (tag === 'button' || attrs.role === 'button') ? node : null;
    },
  };
  return node;
}

new Function(fs.readFileSync('browser_extension/agent_surface_adapters.js', 'utf8'))();
const api = globalThis.__OWG_AGENT_SURFACE_ADAPTERS_FOR_TESTS__;

test('send is recognised without reading any label (any UI language)', () => {
  assert.equal(api.isSendControl(el({attrs: {'data-testid': 'send-button', 'aria-label': 'Skicka prompt'}})), true);
  const composer = el({tag: 'textarea'});
  const form = {querySelectorAll: () => [composer]};
  composer.form = form;
  assert.equal(api.isSendControl(el({attrs: {type: 'submit', 'aria-label': 'Skicka'}, form})), true);
  // A Swedish label with no structural signal is not guessed from text...
  assert.equal(api.isSendControl(el({attrs: {'aria-label': 'Skicka'}})), false);
  // ...while the English fallback still works.
  assert.equal(api.isSendControl(el({attrs: {'aria-label': 'Send'}})), true);
});

test('Enter in the message box sends; Shift+Enter, IME composition and other fields do not', () => {
  const box = el({tag: 'div', attrs: {contenteditable: 'true'}, editable: true});
  const form = {querySelectorAll: () => [box]};
  box.form = form;
  assert.equal(api.isComposerSend({key: 'Enter', target: box}), true);
  assert.equal(api.isComposerSend({key: 'Enter', shiftKey: true, target: box}), false);
  assert.equal(api.isComposerSend({key: 'Enter', isComposing: true, target: box}), false);
  assert.equal(api.isComposerSend({key: 'a', target: box}), false);
  assert.equal(api.isComposerSend({key: 'Enter', target: el({tag: 'input', attrs: {type: 'text'}})}), false);
});

test('stop and busy state come from structure first', () => {
  assert.equal(api.isStopControl(el({attrs: {'data-testid': 'stop-button', 'aria-label': 'Stoppa'}})), true);
  assert.equal(api.isStopControl(el({attrs: {'aria-label': 'Stoppa'}})), false);
  const streaming = {querySelectorAll: (sel) => (sel.includes('data-is-streaming') ? [{}] : [])};
  assert.equal(api.hasBusyState(streaming), true);
  const idle = {querySelectorAll: () => [el({attrs: {'aria-label': 'Skicka'}})]};
  assert.equal(api.hasBusyState(idle), false);
});

test('no class-name selectors and no content in lifecycle messages', () => {
  const source = fs.readFileSync('browser_extension/agent_surface_adapters.js', 'utf8');
  assert.doesNotMatch(source, /querySelector(All)?\([^\n]*['"`]\.[A-Za-z]/);
  assert.doesNotMatch(source, /\.value\b|innerText/);
});
