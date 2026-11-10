"""Browser extension pause/resume behavior, including the privacy boundary (#634)."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser_extension"


def test_pause_switch_is_wired_to_all_capture_channels():
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    content = (EXTENSION / "content.js").read_text(encoding="utf-8")
    depth = (EXTENSION / "capture_depth.js").read_text(encoding="utf-8")
    work_text = (EXTENSION / "work_text_capture.js").read_text(encoding="utf-8")
    popup = (EXTENSION / "popup.html").read_text(encoding="utf-8")
    popup_js = (EXTENSION / "popup.js").read_text(encoding="utf-8")
    for script in (background, content, depth, work_text):
        assert "openworkgraph_browser_paused_v1" in script
    assert 'if (browserPaused) return false;' in background
    assert 'if (owgBrowserPaused) return;' in content
    assert 'if (owgDepthPaused) return false;' in depth
    assert 'setEnabled(consentEnabled && !browserPaused)' in work_text
    assert 'id="toggle-capture"' in popup
    assert 'type: "owg_browser_pause_set"' in popup_js


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for JS integration checks")
def test_browser_pause_blocks_post_and_drops_unsent_events():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert').strict;
const {setTimeout: delay} = require('timers/promises');
const pauseKey = 'openworkgraph_browser_paused_v1';
const queueKey = 'openworkgraph_pending_browser_events';
const stored = {
  [pauseKey]: true,
  [queueKey]: [{event_id:'old-offline-event', action:'click', page:{hostname:'example.com'}}]
};
const sent = [];
let delayNextEvidence = false;
let releaseEvidence;
const listeners = {};
const event = name => ({addListener: fn => { listeners[name] = fn; }});
const extension = {
  runtime: {
    getManifest: () => ({version: '1.0'}),
    getURL: path => 'chrome-extension://owg/' + path,
    onMessage: event('message'),
    onInstalled: event('installed'),
    onStartup: event('startup')
  },
  storage: {
    local: {
      async get(key) {
        if (typeof key === 'string') return {[key]: stored[key]};
        return Object.fromEntries((key || []).map(item => [item, stored[item]]));
      },
      async set(values) {Object.assign(stored, values);}
    }
  },
  tabs: {
    query: async () => [{url:'https://example.com/private',title:'Private tab',id:9}],
    get: async id => ({url:'https://example.com/foo',title:'Example',id}),
    onActivated: event('activated'),
    onUpdated: event('updated')
  },
  webNavigation: {
    onBeforeNavigate: event('before'),
    onCommitted: event('committed'),
    onCompleted: event('completed'),
    onHistoryStateUpdated: event('history')
  },
  alarms: {create: () => {}, onAlarm: event('alarm')},
  action: {async setBadgeText() {}, async setBadgeBackgroundColor() {}}
};
async function fetchFake(url, options={}) {
  const path = new URL(url).pathname;
  if (options.method !== 'POST') {
    return {ok: true, json: async () => ({})};
  }
  const body = JSON.parse(options.body);
  sent.push({path,body});
  if (path === '/v1/browser-events' && delayNextEvidence) {
    delayNextEvidence = false;
    await new Promise(resolve => {releaseEvidence = resolve;});
  }
  return {ok:true};
}
const context = {
  browser: extension, fetch: fetchFake, URL, Math, Date, Promise, console,
  setTimeout, clearTimeout, crypto: require('crypto').webcrypto
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync('browser_extension/background.js', 'utf8'), context,
  {filename: 'browser_extension/background.js'});

function popup(message, origin='chrome-extension://owg/popup.html') {
  return new Promise(resolve => {
    const keptOpen = listeners.message(message, {url:origin}, resolve);
    if (keptOpen !== true) setImmediate(() => resolve(undefined));
  });
}
async function sendClick() {
  listeners.message({
    type:'workflow_observer_event', observed_at:'2026-11-10T08:00:00Z',
    action:'click', page:{url:'https://example.com/foo',title:'Example'},
    target:{tag:'button'}, metadata:{}
  }, {tab:{url:'https://example.com/foo',title:'Example',id:3,windowId:1},frameId:0}, () => {});
  await delay(20);
}
const evidence = () => sent.filter(x => x.path === '/v1/browser-events');
(async () => {
  await delay(30);
  assert.equal(stored[queueKey].length, 0, 'startup pause must purge stale offline evidence');
  assert.equal((await popup({type:'owg_browser_pause_state'})).paused, true);
  await sendClick();
  assert.equal(evidence().length, 0, 'paused capture must not send structural events');
  assert.equal((await popup({type:'owg_browser_pause_set',paused:false})).paused, false);
  await sendClick();
  assert.equal(evidence().length, 1, 'resuming must allow future events');

  // Already-started requests can finish; pause must not acknowledge until they do.
  delayNextEvidence = true;
  await sendClick();
  assert.equal(typeof releaseEvidence, 'function');
  stored[queueKey] = [{event_id:'pending', action:'click', page:{hostname:'example.com'}}];
  let resolved = false;
  const pausing = popup({type:'owg_browser_pause_set',paused:true}).then(value => {
    resolved = true;
    return value;
  });
  await delay(5);
  assert.equal(resolved, false, 'pause cannot acknowledge before an in-flight send completes');
  releaseEvidence();
  assert.equal((await pausing).paused, true);
  assert.equal(stored[queueKey].length, 0, 'pause discards all unsent queue entries');
  await sendClick();
  assert.equal(evidence().length, 2, 'subsequent events must remain blocked');
  listeners.message({type:'owg_work_text_snapshot',kind:'page',text:'secret',
    hostname:'example.com',pathname:'/foo'}, {frameId:0,tab:{active:true,url:'https://example.com/foo'}}, () => {});
  await delay(5);
  assert.equal(sent.filter(x => x.path === '/v1/work-text/ingest').length, 0);
  const denied = await popup({type:'owg_browser_pause_set',paused:false}, 'https://example.com/page');
  assert.equal(denied.ok, false, 'content scripts cannot change the pause state');
  assert.equal((await popup({type:'owg_browser_pause_state'})).paused, true);
  const pausedHeartbeats = sent.filter(x => x.path === '/v1/browser-heartbeat' && x.body.status === 'paused');
  assert(pausedHeartbeats.length > 0, 'paused status remains visible');
  assert(pausedHeartbeats.every(x => Object.keys(x.body.page || {}).length === 0),
    'paused heartbeats must not reveal the active browser tab');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=40
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_capture_coverage_distinguishes_paused_browser_from_disabled_signals(monkeypatch):
    """A paused browser is missing evidence, not proof of zero activity."""
    from server import capture_depth, interaction_evidence, main, work_text_capture
    from server import db
    from shared import capture_control

    monkeypatch.setattr(capture_control, "read_state", lambda: {"state": "recording"})
    from datetime import datetime, timezone
    received_at = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "BROWSER_STATUS", {"status": "paused", "received_at": received_at})
    monkeypatch.setattr(main, "COLLECTOR_STATUS", {"status": "recording", "received_at": received_at})
    monkeypatch.setattr(db, "rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(interaction_evidence, "_storage", lambda: {})
    monkeypatch.setattr(work_text_capture, "get_policy", lambda: {"capture_enabled": True})
    monkeypatch.setattr(capture_depth, "load", lambda: {
        "profile": "custom",
        "signals": {"hover_dwell": True, "drag_drop": True,
                    "pointer_trajectory": False, "friction_candidates": True},
        "parameters": {},
    })

    report = interaction_evidence.coverage_manifest(hours=24)
    modes = report["modalities"]
    assert report["browser_capture_paused"] is True
    assert modes["browser_structural_events"] == "browser_paused"
    assert modes["browser_hover_dwell"] == "browser_paused"
    assert modes["browser_drag_drop"] == "browser_paused"
    assert modes["browser_pointer_trajectory"] == "off"
    assert modes["work_text"] == "browser_paused"
    assert modes["desktop_clicks_and_scrolls"] == "on"
    assert modes["desktop_hover_dwell"] == "on"

    # Resuming restores the normal capture coverage, not sharing permissions.
    monkeypatch.setattr(main, "BROWSER_STATUS", {"status": "connected", "received_at": received_at})
    resumed = interaction_evidence.coverage_manifest(hours=24)
    assert resumed["browser_capture_paused"] is False
    assert resumed["modalities"]["browser_hover_dwell"] == "on"
