import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class FakeElement {
  constructor({tagName='DIV', textContent='', attrs={}} = {}) {
    this.tagName = tagName;
    this.textContent = textContent;
    this.attrs = {...attrs};
    this.id = attrs.id || '';
    this.parentElement = null;
    this.isContentEditable = false;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  matches(selector) {
    const role = this.getAttribute('role');
    if (selector.includes("[role='button']") && role === 'button') return true;
    if (selector.includes("[role='link']") && role === 'link') return true;
    if (selector.includes("[role='tab']") && role === 'tab') return true;
    if (selector.includes("[role='menuitem']") && role === 'menuitem') return true;
    if (selector.includes("[role='option']") && role === 'option') return true;
    return ['BUTTON','A','SUMMARY','OPTION'].includes(this.tagName);
  }
  closest() { return null; }
}

function makeContext() {
  const listeners = [];
  const context = vm.createContext({
    browser: undefined,
    chrome: {runtime: {sendMessage() { return Promise.resolve(); }}},
    Element: FakeElement,
    location: {href: 'https://example.com/work'},
    window: null,
    document: {
      title: 'Work',
      getElementById() { return null; },
      querySelector() { return null; },
    },
    CSS: {escape(value) { return String(value); }},
    URL,
    Date,
    String,
    Array,
    Object,
    Set,
    RegExp,
    Promise,
    console,
    addEventListener(name, fn) { listeners.push([name, fn]); },
  });
  context.window = context;
  context.window.top = context.window;
  return context;
}

function loadContent() {
  const context = makeContext();
  const source = fs.readFileSync(new URL('../../browser_extension/content.js', import.meta.url), 'utf8');
  vm.runInContext(source, context);
  return context;
}

test('short genuine fallback labels survive', () => {
  const context = loadContent();
  const button = new FakeElement({tagName: 'BUTTON', textContent: 'Merge pull request'});
  const label = vm.runInContext('labelForControl(__el)', Object.assign(context, {__el: button}));
  assert.equal(label, 'Merge pull request');
});

test('long clickable row bodies are not captured as fallback labels', () => {
  const context = loadContent();
  const row = new FakeElement({
    tagName: 'A',
    textContent: 'Lars Eklund Project Falcon signed NDA and pricing for 2.4 MSEK deal Hi attached is the signed agreement and confidential terms',
  });
  const label = vm.runInContext('labelForControl(__el)', Object.assign(context, {__el: row}));
  assert.equal(label, '');
});

test('role button on a record row is also dropped when body-sized', () => {
  const context = loadContent();
  const row = new FakeElement({
    tagName: 'DIV',
    attrs: {role: 'button'},
    textContent: 'Anna Svensson Diabetes typ 2 Avd 4 Dr Koyar journal note and current medication information',
  });
  const label = vm.runInContext('labelForControl(__el)', Object.assign(context, {__el: row}));
  assert.equal(label, '');
});

test('explicit semantic labels remain unchanged for compatibility', () => {
  const context = loadContent();
  const button = new FakeElement({
    tagName: 'BUTTON',
    attrs: {'aria-label': 'Confirm transfer after reviewing recipient and amount details'},
    textContent: 'ignored fallback body',
  });
  const label = vm.runInContext('labelForControl(__el)', Object.assign(context, {__el: button}));
  assert.equal(label, 'Confirm transfer after reviewing recipient and amount details');
});
