import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

const read = path => fs.readFileSync(new URL('../../' + path, import.meta.url), 'utf8');
const exclusions = read('browser_extension/browser_capture_exclusions.js');

function extensionHarness(initial = {}) {
  const stored = {...initial};
  const listeners = {storage: []};
  const register = name => ({addListener: fn => {listeners[name] = fn;}});
  const ext = {
    storage: {
      local: {
        async get(key) {return Object.hasOwn(stored, key) ? {[key]: stored[key]} : {};},
        async set(fields) {
          const changes = Object.fromEntries(Object.entries(fields)
            .map(([key, value]) => [key, {oldValue: stored[key], newValue: value}]));
          Object.assign(stored, fields);
          for (const listener of listeners.storage) listener(changes, 'local');
        }
      },
      onChanged: {addListener: fn => listeners.storage.push(fn)}
    },
    runtime: {
      getManifest: () => ({version:'1.0'}), getURL: path => 'chrome-extension://owg/' + path,
      onMessage: register('message'), onInstalled: register('installed'),
      onStartup: register('startup')
    },
    tabs: {
      query: async () => [{url:'https://private.example.com/work',title:'Sensitive work',id:7}],
      get: async id => ({url:'https://private.example.com/work',id,title:'Sensitive work'}),
      onActivated: register('activated'), onUpdated: register('updated')
    },
    webNavigation: {
      onBeforeNavigate: register('before'), onCommitted: register('committed'),
      onCompleted: register('completed'), onHistoryStateUpdated: register('history')
    },
    alarms: {create() {}, onAlarm: register('alarm')},
    action: {async setBadgeText() {}, async setBadgeBackgroundColor() {}}
  };
  return {stored, listeners, ext};
}

test('local Never record patterns match full hosts and subdomains but never lookalikes', async () => {
  const fixture = extensionHarness({
    openworkgraph_work_text_exclusions_v1: {
      hosts:['*.private.example.com', 'bank.example'], titles:['salary', 'patient']
    }
  });
  const context = vm.createContext({browser:fixture.ext});
  vm.runInContext(exclusions, context);
  const policy = context.OWGBrowserExclusions;
  await policy.ready;
  assert.equal(policy.isExcluded('private.example.com', 'Dashboard'), true);
  assert.equal(policy.isExcluded('docs.private.example.com', 'Dashboard'), true);
  assert.equal(policy.isExcluded('bank.example', 'Home'), true);
  assert.equal(policy.isExcluded('bank.example.attacker.com', 'Home'), false);
  assert.equal(policy.isExcluded('mybank.example', 'Home'), false);
  assert.equal(policy.isExcluded('work.example.com', 'Patient follow up'), true);
  assert.equal(policy.isExcluded('work.example.com', 'Quarterly results'), false);
  await fixture.ext.storage.local.set({
    openworkgraph_work_text_exclusions_v1:{hosts:['work.example.com'],titles:[]}
  });
  assert.equal(policy.isExcluded('work.example.com', 'Anything'), true);
  assert.equal(policy.isExcluded('private.example.com', 'Dashboard'), false);
});

test('extension loads local exclusions before content and background scripts', () => {
  const manifest = JSON.parse(read('browser_extension/manifest.json'));
  const worker = read('browser_extension/secure_background.js');
  assert.equal(manifest.content_scripts[0].js[0], 'browser_capture_exclusions.js');
  assert.equal(manifest.background.scripts[0], 'browser_capture_exclusions.js');
  assert.match(worker, /importScripts\("browser_capture_exclusions.js"/);
  assert.match(read('browser_extension/content.js'), /owgBrowserNeverRecord\(\)/);
  assert.match(read('browser_extension/capture_depth.js'), /owgDepthExcluded\(\)/);
});

test('excluded browser activity never reaches the local event queue or heartbeat page', async () => {
  const KEY='openworkgraph_work_text_exclusions_v1';
  const QUEUE='openworkgraph_pending_browser_events';
  const fixture=extensionHarness({
    openworkgraph_browser_paused_v1:false,
    [KEY]:{hosts:['private.example.com'],titles:['private']},
    [QUEUE]:[
      {event_id:'blocked',action:'click',page:{hostname:'private.example.com',pathname:'/work',title:'Sensitive'}},
      {event_id:'okay',action:'click',page:{hostname:'work.example.com',pathname:'/work',title:'Work'}}
    ]
  });
  const posted=[];
  const fresh={
    excluded_browser_host_patterns:['private.example.com'],
    excluded_title_patterns:['private']
  };
  const context=vm.createContext({
    browser:fixture.ext, URL, Date, Math, Promise, crypto:webcrypto, console,
    setTimeout, clearTimeout,
    fetch: async (url, options={}) => {
      if (options.method!=='POST') return {ok:true,json:async()=>fresh};
      posted.push({path:new URL(url).pathname,body:JSON.parse(options.body)});
      return {ok:true};
    }
  });
  context.globalThis=context;
  vm.runInContext(exclusions, context);
  vm.runInContext(read('browser_extension/background.js'), context);
  await delay(80);
  assert.equal(fixture.stored[QUEUE].length, 0, 'startup should flush the safe event and purge excluded backlog');
  const events=()=>posted.filter(x=>x.path==='/v1/browser-events');
  assert(events().some(x=>x.body.event_id==='okay'));
  assert(!events().some(x=>x.body.event_id==='blocked'));
  const heartbeats=posted.filter(x=>x.path==='/v1/browser-heartbeat');
  assert(heartbeats.length>0);
  assert(heartbeats.every(x=>Object.keys(x.body.page||{}).length===0));

  function send(url,title) {
    fixture.listeners.message({
      type:'workflow_observer_event',action:'click',observed_at:new Date().toISOString(),
      page:{url,title},target:{tag:'button'},metadata:{}
    },{frameId:0,tab:{url,title,id:3,windowId:1}},()=>{});
  }
  send('https://private.example.com/work','Sensitive work');
  send('https://work.example.com/work','Private balance');
  await delay(30);
  assert.equal(events().length,1, 'host and title exclusions suppress new events');
  send('https://work.example.com/work','Work');
  await delay(30);
  assert.equal(events().length,2, 'allowed browser activity continues recording');

  // A newly excluded domain purges previously unsent evidence even while offline.
  fixture.stored[QUEUE]=[
    {event_id:'pending',action:'click',page:{hostname:'work.example.com',pathname:'/work',title:'Work'}}
  ];
  await fixture.ext.storage.local.set({[KEY]:{hosts:['work.example.com'],titles:['private']}});
  await delay(35);
  assert.equal(fixture.stored[QUEUE].length,0);
});


test('Never record fails closed until the storage policy has loaded or is recovered', async () => {
  let finish;
  const gate = new Promise(resolve => {finish = resolve;});
  const ext = {
    storage: {
      local: {get: () => gate},
      onChanged: {addListener() {}}
    }
  };
  const context = vm.createContext({browser: ext});
  vm.runInContext(exclusions, context);
  const policy = context.OWGBrowserExclusions;
  assert.equal(policy.isExcluded('work.example.com', 'Normal work'), true,
    'capture must be disabled until exclusion policy resolution');
  finish({openworkgraph_work_text_exclusions_v1: {hosts:['sensitive.example.com'],titles:[]}});
  await policy.ready;
  assert.equal(policy.isExcluded('work.example.com', 'Normal work'), false);
  assert.equal(policy.isExcluded('sensitive.example.com', 'Normal work'), true);

  const broken = vm.createContext({browser: {
    storage:{local:{get: async () => {throw new Error('storage unavailable');}},
             onChanged:{addListener() {}}}
  }});
  vm.runInContext(exclusions, broken);
  await broken.OWGBrowserExclusions.ready;
  assert.equal(broken.OWGBrowserExclusions.isExcluded('work.example.com', ''), true,
    'unknown stored exclusions must fail closed');
});


test('agent lifecycle adapter gates state sampling and emission by Never record policy', () => {
  const adapter = read('browser_extension/agent_surface_adapters.js');
  assert.match(adapter, /function excludedAgentPage\(/);
  assert.match(adapter, /function sendLifecycle\([\s\S]*?if\(excludedAgentPage\(\)\) return;/);
  assert.match(adapter, /const sample=\(\)=>\{\s*if \(excludedAgentPage\(\)\) \{reset\(\); return;\}/);
});


test('new extension installations preserve default title exclusions', async () => {
  const fixture=extensionHarness({});
  const ctx=vm.createContext({browser:fixture.ext});
  vm.runInContext(exclusions,ctx);
  const matcher=ctx.OWGBrowserExclusions;
  await matcher.ready;
  assert.equal(matcher.isExcluded('ordinary.example.com','Private bank account'),true);
  assert.equal(matcher.isExcluded('ordinary.example.com','Password entry'),true);
  assert.equal(matcher.isExcluded('ordinary.example.com','Public project discussion'),false);
});
