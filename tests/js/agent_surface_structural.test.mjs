import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// Minimal element stand-ins: only what the detector reads (attributes, tag,
// closest, form/main/container relationships). Text is used only for the old
// English fallback.
function el({tag = 'button', attrs = {}, text = '', form = null, main = null, container = null, editable = false} = {}) {
  const node = {
    tagName: tag.toUpperCase(), textContent: text, form, isContentEditable: editable,
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    closest(sel) {
      if (sel === 'form') return node.form || null;
      if (sel === 'main,[role="main"]') return main || null;
      if (sel.includes('data-testid*="composer"')) return container || null;
      if (sel === 'dialog,[role="dialog"]') return null;
      return /button|role="button"|input\[type="submit"\]/.test(sel) && (tag === 'button' || attrs.role === 'button') ? node : null;
    },
  };
  return node;
}

function composerForm({busy = false} = {}) {
  const busyNode = {hidden: false, disabled: false, getAttribute: () => null};
  const form = {
    querySelectorAll(sel) {
      if (sel.includes('textarea')) return [composer];
      if (sel.includes('aria-busy') || sel.includes('data-is-streaming')) return busy ? [busyNode] : [];
      return [];
    },
  };
  const composer = el({tag: 'textarea', form});
  return {form, composer};
}

new Function(fs.readFileSync('browser_extension/agent_surface_adapters.js', 'utf8'))();
const api = globalThis.__OWG_AGENT_SURFACE_ADAPTERS_FOR_TESTS__;

test('send is recognised structurally without reading a localized label', () => {
  const {form} = composerForm();
  assert.equal(api.isSendControl(el({attrs: {'data-testid': 'send-button', 'aria-label': 'Skicka prompt'}, form})), true);
  assert.equal(api.isSendControl(el({attrs: {type: 'submit', 'aria-label': 'Skicka'}, form})), true);
  // A Swedish label with no structural signal is not guessed from text...
  assert.equal(api.isSendControl(el({attrs: {'aria-label': 'Skicka'}})), false);
  // ...while the English compatibility fallback still works.
  assert.equal(api.isSendControl(el({attrs: {'aria-label': 'Send'}})), true);
});

test('Enter in the message box sends; Shift+Enter, IME composition and other fields do not', () => {
  const {form} = composerForm();
  const box = el({tag: 'div', attrs: {contenteditable: 'true'}, editable: true, form});
  form.querySelectorAll = (sel) => sel.includes('textarea') ? [box] : [];
  assert.equal(api.isComposerSend({key: 'Enter', target: box}), true);
  assert.equal(api.isComposerSend({key: 'Enter', shiftKey: true, target: box}), false);
  assert.equal(api.isComposerSend({key: 'Enter', isComposing: true, target: box}), false);
  assert.equal(api.isComposerSend({key: 'a', target: box}), false);
  assert.equal(api.isComposerSend({key: 'Enter', target: el({tag: 'input', attrs: {type: 'text'}})}), false);
});

test('busy state is scoped to a real conversation/composer surface', () => {
  const {form, composer} = composerForm({busy: true});
  const streaming = {
    querySelectorAll(sel) {
      if (sel.includes('textarea')) return [composer];
      if (sel.includes('button')) return [];
      return [];
    },
  };
  assert.equal(api.hasBusyState(streaming), true);

  // A page-global loading spinner with no composer is not an agent run.
  const unrelatedBusy = {
    querySelectorAll(sel) {
      if (sel.includes('aria-busy')) return [{hidden: false, disabled: false, getAttribute: () => null}];
      return [];
    },
  };
  assert.equal(api.hasBusyState(unrelatedBusy), false);

  const {form: stopForm} = composerForm();
  assert.equal(api.isStopControl(el({attrs: {'data-testid': 'stop-button', 'aria-label': 'Stoppa'}, form: stopForm})), true);
  assert.equal(api.isStopControl(el({attrs: {'aria-label': 'Stoppa'}})), false);
  void form;
});

test('a structural send/stop token outside a composer surface is ignored', () => {
  assert.equal(api.isSendControl(el({attrs: {'data-testid': 'send-button', 'aria-label': 'Skicka'}})), false);
  assert.equal(api.isStopControl(el({attrs: {'data-testid': 'stop-button', 'aria-label': 'Stoppa'}})), false);
});

test('no class-name selectors and no content in lifecycle messages', () => {
  const source = fs.readFileSync('browser_extension/agent_surface_adapters.js', 'utf8');
  assert.doesNotMatch(source, /querySelector(All)?\([^\n]*['"`]\.[A-Za-z]/);
  assert.doesNotMatch(source, /\.value\b|innerText/);
});
