import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const html=fs.readFileSync(new URL('../../gateway/work_text_account.html',import.meta.url),'utf8');
const script=html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1].replace('__CONFIG__',JSON.stringify({issuer:'https://identity.example/auth/v1',scope:'openid'}));
async function page({search='',pending=null,enabled=true}={}){
 const elements={},storage=new Map(),calls=[],redirects=[];
 if(pending)storage.set('owg-cloud-text-oauth',JSON.stringify(pending));
 function element(id){return elements[id]??=( {value:'',checked:false,hidden:false,disabled:false,textContent:'',children:[],replaceChildren(){this.children=[];this.value='';},append(child){this.children.push(child);this.value||=child.value;}} );}
 const context=vm.createContext({document:{getElementById:element,createElement:()=>({})},
  sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  location:{origin:'https://mcp.example',pathname:'/work-text',search,assign:url=>redirects.push(url)},
  history:{replaceState(...args){calls.push({history:args});}},crypto:webcrypto,Uint8Array,TextEncoder,URLSearchParams,btoa,
  confirm:()=>true,fetch:async(url,options={})=>{calls.push({url,options});return {ok:true,json:async()=>{
   if(url.endsWith('/register'))return {client_id:'synthetic-client'};
   if(url.endsWith('/token'))return {access_token:'synthetic-access'};
   if(url.endsWith('/work-text-status'))return {operator_available:enabled,status:'not_enabled',devices:[{device_id:'my-computer',approved:false}]};
   if(url.endsWith('/work-text-delete'))return {deleted:2};
   return {status:'device_approved'};
  }};}});
 await vm.runInContext(script,context);
 return {elements,storage,calls,redirects};
}
function pending(){return {state:'valid-state',verifier:'synthetic-verifier',client_id:'synthetic-client',expires:Date.now()+60000};}
test('sign-in uses PKCE and stores no bearer token',async()=>{
 const p=await page();await p.elements.signin.onclick();
 const url=new URL(p.redirects[0]);assert.equal(url.searchParams.get('code_challenge_method'),'S256');
 assert.ok(url.searchParams.get('state'));assert.equal(url.searchParams.get('redirect_uri'),'https://mcp.example/work-text');
 assert.ok(![...p.storage.values()].some(v=>v.includes('synthetic-access')));
});
test('invalid callback state never exchanges a code',async()=>{
 const p=await page({pending:pending(),search:'?code=example&state=wrong'});
 assert.ok(p.elements.status.textContent.includes('could not be verified'));
 assert.equal(p.calls.filter(c=>c.url).length,0);assert.equal(p.storage.size,0);
});
test('approval needs explicit acknowledgement and uses selected device; deletion clears consent',async()=>{
 const p=await page({pending:pending(),search:'?code=example&state=valid-state'});
 assert.equal(p.elements.approve.disabled,true);
 await p.elements.approve.onclick();assert.ok(!p.calls.some(c=>c.url?.endsWith('/work-text-reauthorize')));
 p.elements.ack.checked=true;p.elements.ack.onchange();assert.equal(p.elements.approve.disabled,false);
 await p.elements.approve.onclick();
 const approval=p.calls.find(c=>c.url?.endsWith('/work-text-reauthorize'));
 assert.equal(approval.options.headers['X-OWG-Device-ID'],'my-computer');
 assert.equal(approval.options.headers['X-OWG-Consent-Version'],'cloud-work-text-v1');
 assert.equal(p.elements.ack.checked,false);
 await p.elements.erase.onclick();assert.ok(p.elements.status.textContent.includes('Deleted 2'));
 p.elements.signout.onclick();assert.equal(p.elements.controls.hidden,true);assert.equal(p.storage.size,0);
});
test('operator-disabled screen cannot approve a device',async()=>{
 const p=await page({pending:pending(),search:'?code=example&state=valid-state',enabled:false});
 p.elements.ack.checked=true;p.elements.ack.onchange();assert.equal(p.elements.approve.disabled,true);
 await p.elements.approve.onclick();assert.ok(!p.calls.some(c=>c.url?.endsWith('/work-text-reauthorize')));
});
