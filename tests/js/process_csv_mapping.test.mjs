import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function mapper(){
 const window={};
 vm.runInNewContext(fs.readFileSync(new URL('../../dashboard/process_csv_mapping.js',import.meta.url),'utf8'),{
   window,TextEncoder,TextDecoder,Blob,fetch:()=>assert.fail('Column inspection must stay on this device'),
   localStorage:{setItem:()=>assert.fail('File content must not be persisted')}});
 return window.owgProcessCSVMapping;
}

test('quoted semicolon export minimizes selected fields and excludes all unused headers and values',async()=>{
 const api=mapper(),inspected=api.inspectText('\ufeffTicket;Stage;When;Private\r\n"case;1";Open;2026-10-20T10:00:00Z;"PRIVATE_VALUE\r\n雪;more"\r\n"case;1";Close;2026-10-20T10:01:00Z;"PRIVATE_""QUOTE"""\r\n',';');
 assert.equal(inspected.row_count,2);assert.equal(inspected.headers[0],'Ticket');
 const file=inspected.createMappedFile({case_id:0,activity:1,timestamp:2});
 assert.equal(file.type,'text/csv');
 const body=await file.text();
 assert.equal(body,'case_id,activity,timestamp\n"case;1","Open","2026-10-20T10:00:00Z"\n"case;1","Close","2026-10-20T10:01:00Z"\n');
 assert(!body.includes('Private')&&!body.includes('PRIVATE_')&&!body.includes('雪'));
 assert.equal(Object.isFrozen(inspected),true);assert.equal(Object.isFrozen(inspected.headers),true);
 assert.deepEqual(Object.keys(inspected).sort(),['createMappedFile','headers','row_count']);
});

test('duplicate or blank header labels are distinguished by explicit column indices; values stay data',async()=>{
 const api=mapper(),inspected=api.inspectText(',Same,Same,unused\na,Open,2026-10-20T00:00:00Z,"<script>bad()</script>"\n');
 assert.equal(inspected.headers[0],'');assert.equal(inspected.headers[1],inspected.headers[2]);
 const body=await inspected.createMappedFile({case_id:0,activity:1,timestamp:2}).text();
 assert(!body.includes('<script>'));assert(body.includes('"a","Open"'));
 for(const value of [{case_id:0,activity:0,timestamp:2},{case_id:-1,activity:1,timestamp:2},
   {case_id:'0',activity:1,timestamp:2},{case_id:0,activity:1,timestamp:9},
   {case_id:0,activity:1,timestamp:2,extra:3},null])
   assert.throws(()=>inspected.createMappedFile(value),/columns/);
});

test('commas tabs escaped quotes line breaks and whitespace use actual CSV grammar',async()=>{
 const api=mapper();
 const quoted=api.inspectText('Ticket,Stage,When\n"a,""b""",Open,2026-10-20T00:00:00Z\n');
 assert((await quoted.createMappedFile({case_id:0,activity:1,timestamp:2}).text()).includes('"a,""b"""'));
 const tabs=api.inspectText('Ticket\tStage\tWhen\n a \t Open \t2026-10-20T00:00:00Z\n','\t');
 assert((await tabs.createMappedFile({case_id:0,activity:1,timestamp:2}).text()).includes('"a","Open"'));
 for(const text of ['a,b,c\nx,y,"unclosed','a,b,c\nx,y,"closed"junk','a,b,c\nx,y,un"quoted',
   'a,b,c\nx,y','a,b,c\nx,y,z,extra','a,b,c\n','a,b,c\nx,y,\0'])
   assert.throws(()=>api.inspectText(text));
 assert.throws(()=>api.inspectText('a,b,c\nx,y,z','|'),/delimiter/);
});

test('byte event and column limits hold before producing a minimized log',()=>{
 const api=mapper();
 assert.throws(()=>api.inspectText('a,b,c\nx,y,'+'雪'.repeat(400000)),/1 MiB/);
 assert.throws(()=>api.inspectText('a,b,c\n'+'x,y,z\n'.repeat(5001)),/5,000-event/);
 const max=api.inspectText('a,b,c\n'+'x,y,z\n'.repeat(5000));assert.equal(max.row_count,5000);
 assert.throws(()=>api.inspectText(Array(101).fill('col').join(',')+'\n'+Array(101).fill('v').join(',')),/100-column/);
 assert.throws(()=>api.inspectText('a'.repeat(121)+',b,c\nx,y,z'),/120 characters/);
});

test('actual file decoding rejects invalid UTF-8 and oversized files without reading them',async()=>{
 const api=mapper();
 await assert.rejects(()=>api.inspectFile({size:1,arrayBuffer:async()=>new Uint8Array([255]).buffer}),/UTF-8/);
 let read=false;
 await assert.rejects(()=>api.inspectFile({size:1024*1024+1,arrayBuffer:async()=>{read=true;}}),/1 MiB/);
 assert.equal(read,false);
 const raw=new TextEncoder().encode('a,b,c\nx,y,z\n');
 const result=await api.inspectFile({size:raw.byteLength,arrayBuffer:async()=>raw.buffer});
 assert.equal(result.row_count,1);
});

test('optional participant and team identities become scoped aliases before upload with explicit authorization',async()=>{
 const api=mapper(),value=api.inspectText('Case,Step,Time,Person,Group,Notes\na,Open,2026-10-20T00:00:00Z,ALICE_SECRET,SALES_SECRET,UNSELECTED_SECRET\na,Review,2026-10-20T00:01:00Z,BOB_SECRET,SALES_SECRET,x\na,Close,2026-10-20T00:02:00Z,ALICE_SECRET,OPS_SECRET,y\nb,Open,2026-10-20T00:03:00Z,,,z\n');
 const mapping={case_id:0,activity:1,timestamp:2,participant:3,team:4};
 assert.throws(()=>value.createMappedFile(mapping),/authorize/);
 const file=value.createMappedFile(mapping,true),body=await file.text();
 assert.equal(Object.prototype.propertyIsEnumerable.call(file,'localParticipantLookup'),false);
 assert.equal(file.localParticipantLookup.participant[0].source,'ALICE_SECRET');
 assert.equal(file.localParticipantLookup.participant[0].label,'participant_1');
 assert(!JSON.stringify(file).includes('_SECRET'));

 assert.equal(body.split('\n')[0],'case_id,activity,timestamp,participant,team');
 assert(body.includes('"participant_1","team_1"'));assert(body.includes('"participant_2","team_1"'));
 assert(body.includes('"participant_1","team_2"'));assert(body.includes('"b","Open","2026-10-20T00:03:00Z","",""'));
 assert(!body.includes('_SECRET'));assert.equal(await value.createMappedFile(mapping,true).text(),body);
 assert.equal((await value.createMappedFile({case_id:0,activity:1,timestamp:2}).text()).split('\n')[0],'case_id,activity,timestamp');
});

test('identity labels stay opaque data with bounded counts, separate columns and no implicit reuse',async()=>{
 const api=mapper(),value=api.inspectText('c,a,t,p\nx,Open,2026-10-20T00:00:00Z,__proto__\nx,Close,2026-10-20T00:01:00Z,constructor\n');
 const body=await value.createMappedFile({case_id:0,activity:1,timestamp:2,participant:3},true).text();
 assert(body.includes('participant_1')&&body.includes('participant_2'));assert(!body.includes('__proto__')&&!body.includes('constructor'));
 assert.throws(()=>value.createMappedFile({case_id:0,activity:1,timestamp:2,participant:3,team:3},true),/different/);
 const tooLong=api.inspectText('c,a,t,p\nx,Open,2026-10-20T00:00:00Z,'+'a'.repeat(121));
 assert.throws(()=>tooLong.createMappedFile({case_id:0,activity:1,timestamp:2,participant:3},true),/120/);
 const tooMany=api.inspectText('c,a,t,p\n'+Array.from({length:1001},(_,i)=>'x,Open,2026-10-20T00:00:00Z,p'+i).join('\n'));
 assert.throws(()=>tooMany.createMappedFile({case_id:0,activity:1,timestamp:2,participant:3},true),/1,000/);
});
