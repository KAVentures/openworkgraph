import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html=fs.readFileSync(new URL('../../dashboard/index.html',import.meta.url),'utf8');
const tabInit=html.split('\n').find(line=>line.startsWith('function initTabs()'));
assert.ok(tabInit,'dashboard tab initializer exists');

function initialize(search,storedTab){
  const activated=[],events=[];
  const tabs=['overview','evidence','connect','export','settings','organization'].map(tab=>({
    dataset:{tab},onclick:null,onkeydown:null,
  }));
  const document={
    querySelectorAll:()=>tabs,
    querySelector:selector=>{
      const match=/^\[data-tab="([a-z]+)"\]$/.exec(selector);
      return match ? tabs.find(t=>t.dataset.tab===match[1])||null : null;
    },
  };
  const sessionStorage={getItem:()=>storedTab};
  const window={location:{search},openChatGPTPersonalLink:()=>events.push('oauth')};
  const context=vm.createContext({document,window,URLSearchParams,sessionStorage,
    TAB_KEY:'owg_dashboard_active_tab_v57',
    activateTab:tab=>activated.push(tab)});
  vm.runInContext(tabInit+'\ninitTabs();',context);
  return {activated,events,tabs};
}

test('explicit setup deep link opens the Connect AI tab, not any OAuth or sync',()=>{
  const x=initialize('?setup=chatgpt','evidence');
  assert.deepEqual(x.activated,['connect']);
  assert.deepEqual(x.events,[]);
  assert.equal(x.tabs.find(t=>t.dataset.tab==='connect').onclick instanceof Function,false);
  x.tabs.find(t=>t.dataset.tab==='overview').onclick();
  assert.deepEqual(x.activated,['connect','overview']);
});

test('normal standalone startup preserves the previously chosen tab',()=>{
  assert.deepEqual(initialize('', 'export').activated,['export']);
  assert.deepEqual(initialize('?setup=unknown','settings').activated,['settings']);
  assert.deepEqual(initialize('',null).activated,['overview']);
});

test('unknown setup values are never interpreted as actions or tab names',()=>{
  for(const search of ['?setup=enroll','?setup=chatgpt%2Fcomplete','?setup=observe','?setup=chatgpt&record=true']){
    const x=initialize(search,'overview');
    assert.deepEqual(x.activated,[search.startsWith('?setup=chatgpt&')?'connect':'overview']);
    assert.deepEqual(x.events,[]);
  }
  assert.match(html,/onclick="openChatGPTPersonalLink\(\)"/);
  assert.match(html,/id="chatgptPersonalConnect"/);
});
