import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../browser_extension/popup_exclusions.js', import.meta.url), 'utf8');
const html = fs.readFileSync(new URL('../../browser_extension/popup.html', import.meta.url), 'utf8');
const KEY='openworkgraph_browser_site_exclusions_v1';
const SERVER_KEY='openworkgraph_work_text_exclusions_v1';

function node(tag = 'div') {
  return {
    tagName: tag, children: [], handlers: {}, attributes: {}, textContent: '',
    disabled: false, className:'',
    append(...children) {this.children.push(...children);},
    appendChild(child) {this.children.push(child);},
    replaceChildren(...children) {this.children=[...children];},
    setAttribute(name,value) {this.attributes[name]=value;},
    addEventListener(type, listener) {this.handlers[type]=listener;}
  };
}
function harness(url = 'https://project.example.com/customer/secret?token=ABC', initial = {}, opts = {}) {
  const nodes = Object.fromEntries([
    '#never-record-current', '#never-record-current-site',
    '#never-record-sites', '#never-record-status'
  ].map(key=>[key,node()]));
  const events=[];
  const storage={...initial};
  const listeners=[];
  const browser={
    storage:{
      local:{
        async get(key) {
          if(opts.failStorage) throw new Error('storage read failed');
          return Object.hasOwn(storage,key)?{[key]:storage[key]}:{};
        },
        async set(fields) {
          if(opts.failStorage || opts.failWrite) throw new Error('storage write failed');
          const changes=Object.fromEntries(Object.entries(fields).map(([key,newValue])=>
            [key,{oldValue:storage[key],newValue}]));
          Object.assign(storage,fields);
          events.push(...Object.keys(fields));
          for(const listener of listeners)listener(changes,'local');
        }
      },
      onChanged:{addListener:listener=>listeners.push(listener)}
    },
    tabs:{query:async()=>url?[{url}]:[]}
  };
  const document={
    readyState:'complete',
    querySelector(selector){return nodes[selector] || null;},
    createElement:node
  };
  const context=vm.createContext({browser,document,URL});
  vm.runInContext(source,context);
  const helpers=context.__OWG_NEVER_RECORD_POPUP_TESTS__;
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  return {nodes,storage,events,helpers,flush,
    setActiveUrl:next=>{url=next;},
    click:selector=>nodes[selector].handlers.click(),
    removeAt:index=>nodes['#never-record-sites'].children[index].children[1].handlers.click()};
}

test('popup offers local site management with accessible labels and no inline scripts',()=>{
  assert.match(html, /id="never-record-current"/);
  assert.match(html, /id="never-record-sites"/);
  assert.match(html, /id="never-record-status" role="status" aria-live="polite"/);
  assert.match(html, /src="popup_exclusions\.js"/);
  assert.match(html, /does not delete past recordings or pause desktop capture/i);
  assert.doesNotMatch(source,/fetch\s*\(/);
});

test('Never record active website adds local host only without altering server title exclusions',async()=>{
  const server={hosts:['controlled.example.com'],titles:['password','private','incognito','bank']};
  const app=harness(undefined,{[SERVER_KEY]:server});
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,false);
  assert.equal(app.nodes['#never-record-current-site'].textContent,'Active website: project.example.com');
  await app.click('#never-record-current');
  await app.flush();
  assert.deepEqual(Array.from(app.storage[KEY].hosts), ['project.example.com']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles), ['password','private','incognito','bank']);
  assert(!JSON.stringify(app.storage).includes('/customer/secret'));
  assert(!JSON.stringify(app.storage).includes('token=ABC'));
  assert.equal(app.nodes['#never-record-current'].disabled,true);
  assert.match(app.nodes['#never-record-status'].textContent,/already excluded/);
  assert.equal(app.nodes['#never-record-sites'].children.length,1);
  assert.equal(app.nodes['#never-record-sites'].children[0].children[0].textContent,'project.example.com');
  await app.removeAt(0);
  await app.flush();
  assert.deepEqual(Array.from(app.storage[KEY].hosts),[]);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles), ['password','private','incognito','bank']);
  assert.equal(app.nodes['#never-record-current'].disabled,false);
});

test('Never record preserves custom title patterns and unrelated config fields',async()=>{
  const old={hosts:['already.example'],future_setting:{mode:'local'}};
  const server={hosts:['company.example.com'],titles:['patient','health']};
  const app=harness('https://portal.example.com/',{[KEY]:old,[SERVER_KEY]:server});
  await app.flush();
  await app.click('#never-record-current');
  assert.deepEqual(Array.from(app.storage[KEY].hosts),['already.example','portal.example.com']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles),['patient','health']);
  assert.equal(app.storage[KEY].future_setting.mode,'local');
  await app.removeAt(1);
  assert.deepEqual(Array.from(app.storage[KEY].hosts),['already.example']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles),['patient','health']);
});

test('wildcard and parent-domain exclusions cover active subdomains without duplicating entries',async()=>{
  const app=harness('https://docs.team.example.com/private',{
    [KEY]:{hosts:['*.team.example.com']}
  });
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,true);
  assert.match(app.nodes['#never-record-status'].textContent,/already excluded/);
  assert.equal(app.events.length,0);
  assert.equal(app.helpers.covers('team.example.com','*.team.example.com'),true);
  assert.equal(app.helpers.covers('attackerteam.example.com','*.team.example.com'),false);
});

test('chrome:// and oversized domains cannot be saved as Never record exclusions',async()=>{
  const internal=harness('chrome://extensions');
  await internal.flush();
  assert.equal(internal.nodes['#never-record-current'].disabled,true);
  assert.equal(internal.storage[KEY],undefined);
  assert.equal(internal.helpers.hostFromUrl('https://'+'a'.repeat(121)+'.example.com/'),'');
  const long=harness('https://'+'b'.repeat(121)+'.example.com/');
  await long.flush();
  assert.equal(long.nodes['#never-record-current'].disabled,true);
  assert.equal(long.storage[KEY],undefined);
});

test('reading exclusions failure disables writes instead of silently deleting restrictions',async()=>{
  const app=harness('https://sensitive.example.com/',{}, {failStorage:true});
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,true);
  assert.match(app.nodes['#never-record-status'].textContent,/Recording is blocked until the policy is available/);
  assert.equal(app.events.length,0);
});

test('full 100-host local policy disables further additions and remains untouched',async()=>{
  const hosts=Array.from({length:100},(_,i)=>'host'+i+'.example.com');
  const app=harness('https://another.example.com/',{[KEY]:{hosts},[SERVER_KEY]:{hosts:[],titles:['patient']}});
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,true);
  assert.match(app.nodes['#never-record-status'].textContent,/limit reached/);
  assert.equal(app.events.length,0);
  assert.deepEqual(Array.from(app.storage[KEY].hosts),hosts);
});


test('failed write leaves prior exclusion policy unchanged and shows a persistent error',async()=>{
  const original={hosts:['sensitive.example.com']};
  const app=harness('https://new.example.com/',{[KEY]:original,[SERVER_KEY]:{hosts:[],titles:['patient']}},{failWrite:true});
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,false);
  await app.click('#never-record-current');
  await app.flush();
  assert.deepEqual(Array.from(app.storage[KEY].hosts),['sensitive.example.com']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles),['patient']);
  assert.equal(app.nodes['#never-record-current'].disabled,false);
  assert.match(app.nodes['#never-record-status'].textContent,/storage write failed/);
  assert.equal(app.events.length,0);
});


test('server exclusion storage is never overwritten by browser-only Never record updates',async()=>{
  const server={hosts:['server.example.com'],titles:['patient','finance']};
  const app=harness('https://other.example.com/',{[SERVER_KEY]:server});
  await app.flush();
  await app.click('#never-record-current');
  assert.deepEqual(Array.from(app.storage[KEY].hosts),['other.example.com']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].hosts),['server.example.com']);
  assert.deepEqual(Array.from(app.storage[SERVER_KEY].titles),['patient','finance']);
  assert.equal(app.events.filter(k=>k===SERVER_KEY).length,0);
  assert.equal(app.events.filter(k=>k===KEY).length,1);
});


test('capture matcher unions popup-only and server exclusions, surviving every server refresh',async()=>{
  const matcherSource=fs.readFileSync(new URL('../../browser_extension/browser_capture_exclusions.js',import.meta.url),'utf8');
  const stored={
    [SERVER_KEY]:{hosts:['enterprise.example.com'],titles:['patient']},
    [KEY]:{hosts:['local.example.com']}
  };
  const listeners=[];
  const ext={
    storage:{
      local:{
        async get(key){return Object.hasOwn(stored,key)?{[key]:stored[key]}:{};},
        async set(values){
          const changes=Object.fromEntries(Object.entries(values).map(([key,value])=>
            [key,{oldValue:stored[key],newValue:value}]));
          Object.assign(stored,values);
          for(const listener of listeners)listener(changes,'local');
        }
      },
      onChanged:{addListener:fn=>listeners.push(fn)}
    }
  };
  const ctx=vm.createContext({browser:ext});
  vm.runInContext(matcherSource,ctx);
  const matcher=ctx.OWGBrowserExclusions;
  await matcher.ready;
  assert.equal(matcher.isExcluded('enterprise.example.com','Work'),true);
  assert.equal(matcher.isExcluded('docs.local.example.com','Work'),true);
  assert.equal(matcher.isExcluded('unrelated.example.com','Patient follow-up'),true);
  assert.equal(matcher.isExcluded('unrelated.example.com','Work'),false);
  // The live OWG server periodically refreshes the first key; browser-only
  // excluded sites must remain excluded even if server sends an empty list.
  matcher.setPolicy({hosts:[],titles:[]});
  await ext.storage.local.set({[SERVER_KEY]:{hosts:[],titles:[]}});
  assert.equal(matcher.isExcluded('docs.local.example.com','Work'),true);
  assert.equal(matcher.isExcluded('enterprise.example.com','Work'),false);
  await ext.storage.local.set({[KEY]:{hosts:[]}});
  assert.equal(matcher.isExcluded('docs.local.example.com','Work'),false);
  await ext.storage.local.set({[SERVER_KEY]:{hosts:['new.enterprise.example'],titles:['finance']}});
  assert.equal(matcher.isExcluded('new.enterprise.example','Work'),true);
  assert.equal(matcher.isExcluded('home.example','Finance dashboard'),true);
});

test('local exclusion storage unavailability blocks capture even when server policy loads',async()=>{
  const matcherSource=fs.readFileSync(new URL('../../browser_extension/browser_capture_exclusions.js',import.meta.url),'utf8');
  const ext={storage:{
    local:{async get(key){
      if(key===KEY)throw new Error('local sites not readable');
      return {[SERVER_KEY]:{hosts:[],titles:[]}};
    }},
    onChanged:{addListener(){}}
  }};
  const ctx=vm.createContext({browser:ext});
  vm.runInContext(matcherSource,ctx);
  const matcher=ctx.OWGBrowserExclusions;
  await matcher.ready;
  assert.equal(matcher.isExcluded('ordinary.example.com','Normal work'),true);
});


test('popup Never record cannot accidentally exclude a different tab after tab focus changes',async()=>{
  const app=harness('https://expected.example.com/private');
  await app.flush();
  assert.equal(app.nodes['#never-record-current'].disabled,false);
  app.setActiveUrl('https://unexpected.example.com/payments');
  await app.click('#never-record-current');
  await app.flush();
  assert.equal(app.storage[KEY],undefined);
  assert.match(app.nodes['#never-record-status'].textContent,/active website changed/);
  assert.equal(app.nodes['#never-record-current-site'].textContent,'Active website: unexpected.example.com');
  assert.equal(app.nodes['#never-record-current'].disabled,false);
  await app.click('#never-record-current');
  await app.flush();
  assert.deepEqual(Array.from(app.storage[KEY].hosts),['unexpected.example.com']);
});
