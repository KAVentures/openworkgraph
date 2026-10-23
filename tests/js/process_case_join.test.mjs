import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element{
 constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};this.textContent='';this.value='';}
 append(...children){this.children.push(...children);}appendChild(child){this.append(child);return child;}
 replaceChildren(...children){this.children=children;}addEventListener(name,handler){this.handlers[name]=handler;}
 setAttribute(key,value){this.attrs[key]=value;}walk(){return [this,...this.children.flatMap(child=>child.walk())];}
 get text(){return this.textContent+this.children.map(child=>child.text).join(' ');}
}
function setup(){
 const window={},document={createElement:tag=>new Element(tag)},uses=[],actions=[];
 window.owgProcessMining={useSharedCaseImport:file=>{uses.push(file);return true;},withdrawSharedCaseImport:()=>actions.push('withdraw'),analyzeSharedCaseImport:()=>actions.push('analyze')};
 const context=vm.createContext({window,document,TextEncoder,TextDecoder,Blob,fetch:()=>assert.fail('Preparation must not upload'),localStorage:{setItem:()=>assert.fail('No source persistence')}});
 for(const name of ['process_csv_mapping','process_case_join'])vm.runInContext(fs.readFileSync(new URL('../../dashboard/'+name+'.js',import.meta.url),'utf8'),context);
 const parent=new Element('main');window.owgProcessCaseJoin.install(parent);
 const find=(tag,text)=>parent.walk().find(el=>el.tag===tag&&el.textContent===text),byId=id=>parent.walk().find(el=>el.id===id),byLabel=label=>parent.walk().find(el=>el.attrs['aria-label']===label);
 return {window,parent,uses,actions,find,byId,byLabel};
}
const file=(name,body)=>{const bytes=new TextEncoder().encode(body);return {name,size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer};};
const raw=key=>'case_id,activity,timestamp,unused\n'+key+',Open,2026-10-20T00:00:00Z,PRIVATE_UNUSED\n';

test('shared-case UI keeps inspection/preparation local and requires fresh review after input changes',async()=>{
 const {window,parent,uses,actions,find,byId,byLabel}=setup();
 const input=byLabel('Choose two to four event-log exports');input.files=[file('a.csv',raw('NATIVE_KEY')),file('b.csv',raw('NATIVE_KEY'))];input.handlers.change();
 await find('button','Inspect selected export columns').handlers.click();
 assert.equal(uses.length,0);assert(!actions.includes('analyze'));
 find('button','Prepare shared-key import for review').handlers.click();
 assert(byId('owgSharedCaseStatus').text.includes('1 case shares keys'));
 const use=find('button','Use reviewed shared-key import for next analysis'),analyze=find('button','Analyze reviewed combined log'),review=byId('owgSharedCaseReview');
 assert.equal(use.disabled,true);assert.equal(analyze.disabled,true);
 find('button','Show local shared-case lookup').handlers.click();assert(byId('owgSharedCaseLookup').text.includes('NATIVE_KEY'));
 review.checked=true;review.handlers.change();use.handlers.click();assert.equal(uses.length,1);
 assert(!(await uses[0].text()).includes('NATIVE_KEY'));assert(!(await uses[0].text()).includes('PRIVATE_UNUSED'));
 assert.equal(analyze.disabled,false);assert(!actions.includes('analyze'));
 analyze.handlers.click();assert(actions.includes('analyze'));
 review.checked=false;review.handlers.change();assert.equal(use.disabled,true);assert.equal(analyze.disabled,true);
 assert.equal(byId('owgSharedCaseLookup').children.length,0);
 // Mapping changes invalidate the prepared object and cannot be reapproved without preparation.
 const mapping=byLabel('Shared authoritative case key · Source 1');mapping.value='1';mapping.handlers.change();
 review.checked=true;review.handlers.change();assert.equal(use.disabled,true);
 window.owgProcessCaseJoin.reset();assert.equal(byId('owgSharedCaseSources').children.length,0);
 assert.equal(byId('owgSharedCaseLookup').children.length,0);assert.equal(input.value,'');
 assert(!parent.text.includes('NATIVE_KEY'));
});

test('a late multi-export inspection cannot restore private headers or prepare a previous selection',async()=>{
 const {window,parent,find,byId,byLabel}=setup();let finish;
 const original=window.owgProcessCSVMapping,pending=new Promise(resolve=>finish=resolve);
 window.owgProcessCSVMapping={...original,inspectFile:()=>pending};
 const input=byLabel('Choose two to four event-log exports');input.files=[file('old1.csv',raw('one')),file('old2.csv',raw('one'))];input.handlers.change();
 const inspection=find('button','Inspect selected export columns').handlers.click();
 input.files=[file('new1.csv',raw('new')),file('new2.csv',raw('new'))];input.handlers.change();
 finish(original.inspectText('PRIVATE_OLD_HEADER,Activity,Time\nx,Open,2026-10-20T00:00:00Z\n'));await inspection;
 assert(!parent.text.includes('PRIVATE_OLD_HEADER'));assert(parent.text.includes('new1.csv'));
 assert.equal(find('button','Prepare shared-key import for review').disabled,true);
 assert.equal(byId('owgSharedCaseSources').walk().filter(el=>el.attrs['aria-label']?.startsWith('Shared authoritative')).length,0);
 input.files=Array(5).fill(file('extra.csv',raw('x')));input.handlers.change();
 assert.equal(byId('owgSharedCaseSources').children.length,0);assert(byId('owgSharedCaseStatus').text.includes('two to four'));
});
