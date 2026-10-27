const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-o2o-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();

  const status=text=>page.locator('#owgEventLogStatus').filter({hasText:text}).waitFor();
  const payload={objectTypes:[{name:'order',attributes:[]},{name:'item',attributes:[]}],eventTypes:[{name:'Open',attributes:[]}],
   objects:[{id:'order1',type:'order',attributes:[],relationships:[]},
    ...Array.from({length:31},(_,i)=>({id:'item'+String(i).padStart(2,'0'),type:'item',attributes:[],relationships:[]}))],
   events:[{id:'e1',type:'Open',time:'2026-01-01T00:00:00Z',attributes:[],relationships:[{objectId:'order1',qualifier:''}]}]};
  const malicious='<img src=x onerror="window.o2oInjected=true">';
  payload.objects[0].relationships=payload.objects.slice(1).map((o,i)=>({objectId:o.id,qualifier:i===0?malicious:'contains'}));
  payload.objects[1].relationships=[{objectId:'order1',qualifier:'part-of'},{objectId:'item00',qualifier:''},{objectId:'order1',qualifier:' '}];
  async function load(text,name){
   await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'application/octet-stream',buffer:Buffer.from(text)});
   await status('File mapping ready.');await page.locator('#owgEventLogConsent').check();
  }
  await load(JSON.stringify(payload),'relationships.jsonocel');
  await page.locator('#owgEventLogImport').click();await status('Retained 34 supplied object relationships');
  await page.locator('#owgEventLogCaseType').selectOption('order');
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'34 of 34 matching'}).waitFor();
  assert.equal(await page.locator('#owgObjectRelationRows tbody tr').count(),34);
  assert.equal(await page.locator('#owgObjectRelationRows img').count(),0);
  assert.equal(await page.evaluate(()=>window.o2oInjected),undefined);
  await page.locator('#owgObjectRelationLimit').selectOption('25');await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'25 of 34'}).waitFor();
  await page.locator('#owgObjectRelationNext').click();await page.locator('#owgObjectRelationStatus').filter({hasText:'Page 2: 9 of 34'}).waitFor();
  await page.locator('#owgObjectRelationPrevious').click();await page.locator('#owgObjectRelationStatus').filter({hasText:'Page 1: 25 of 34'}).waitFor();
  await page.locator('#owgObjectRelationRows').getByRole('button',{name:'Inspect object item00',exact:true}).first().focus();await page.keyboard.press('Enter');
  await page.locator('#owgObjectRelationEvidence').filter({hasText:'No retained events are linked'}).waitFor();
  assert.equal(await page.locator('#owgObjectRelationDrawing svg [data-owg-object]').count(),2);
  assert.equal(await page.locator('#owgEventLogCaseType').inputValue(),'order','relationship inspection preserves main case notion');
  await page.locator('#owgObjectRelationDirection').selectOption('outgoing');await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'3 of 3'}).waitFor();
  await page.locator('#owgObjectRelationExactQualifier').check();await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'1 of 1'}).waitFor();
  assert.match(await page.locator('#owgObjectRelationRows').textContent(),/\(unqualified\)/);
  await page.locator('#owgObjectRelationQualifier').fill(' ');await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'1 of 1'}).waitFor();
  assert.match(await page.locator('#owgObjectRelationRows').textContent(),/" "/);
  await page.locator('#owgObjectRelationExactQualifier').uncheck();await page.locator('#owgObjectRelationObject').fill('order1');
  await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationEvidence table').waitFor();assert.match(await page.locator('#owgObjectRelationEvidence').textContent(),/Open/);
  assert.match(await page.locator('#owgObjectRelationDrawing').textContent(),/12 of 25 neighbor/);
  const node=page.locator('#owgObjectRelationDrawing [data-owg-object="item01"]');await node.focus();await page.keyboard.press('Enter');
  await page.locator('#owgObjectRelationEvidence').filter({hasText:'No retained events are linked'}).waitFor();
  await page.locator('#owgQuality summary').click();await page.locator('#owgQualityResults').filter({hasText:'Retained supplied object assertions: 34'}).waitFor();
  await page.locator('#owgEventLogExportFormat').selectOption('xes');await status('omits supplied object relationships');
  for(const [format,extension] of [['ocel','jsonocel'],['ocelxml','xmlocel']]){
   await page.locator('#owgEventLogExportFormat').selectOption(format);
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
   const file=path.join(scratch,'roundtrip.'+extension);await download.saveAs(file);
   const text=await fs.readFile(file,'utf8');assert.ok(text.includes(format==='ocel'?'objectId':'object-id'));
   await load(text,'roundtrip.'+extension);await page.locator('#owgEventLogImport').click();await status('Retained 34 supplied object relationships');
  }
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgObjectRelationStatus').filter({hasText:'34 of 34'}).waitFor();
  await page.locator('#owgObjectRelationObject').fill('item00');await page.locator('#owgObjectRelationRun').click();
  await page.locator('#owgObjectRelationEvidence').filter({hasText:'No retained events are linked'}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const axe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(axe.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile width');
  // A held response must not repopulate the old view after changing case notion.
  let release,held;const gate=new Promise(resolve=>{release=resolve;});
  const started=new Promise(resolve=>{held=resolve;});
  await page.route('**/object-relationships/query',async route=>{const response=await route.fetch();held();await gate;await route.fulfill({response});});
  await page.locator('#owgObjectRelationRun').click();await started;
  await page.locator('#owgEventLogCaseType').selectOption('item');release();
  await page.waitForLoadState('networkidle');assert.equal(await page.locator('#owgObjectRelationships').count(),0);
  await page.unroute('**/object-relationships/query');
  for(let remaining=2;remaining>=0;remaining--){
   page.once('dialog',d=>d.accept());
   await Promise.all([page.waitForResponse(r=>r.url().endsWith('/delete')&&r.request().method()==='POST'),page.locator('#owgEventLogDelete').click()]);
   await page.waitForFunction(n=>document.querySelector('#owgEventLogPick').options.length===n,remaining);
  }
  await page.locator('#owgEventLogManage').waitFor({state:'hidden'});assert.deepEqual(errors,[]);
  console.log('Supplied O2O browser acceptance passed: import, literal qualifiers, directed graph/table pagination, unobserved endpoints, event inspection, JSON/XML roundtrips, export disclosure, escaping, keyboard, WCAG/mobile and deletion.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
