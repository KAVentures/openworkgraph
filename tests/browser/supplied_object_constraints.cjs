const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-supplied-constraints-'));
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
  const payload={objectTypes:[],eventTypes:[],objects:[
   {id:'order1',type:'order',attributes:[],relationships:[{objectId:'item1',qualifier:'contains'},{objectId:'item2',qualifier:''},{objectId:'item2',qualifier:' '}]},
   {id:'order2',type:'order',attributes:[],relationships:[]},
   {id:'item1',type:'item',attributes:[],relationships:[{objectId:'order1',qualifier:'reverse'}]},
   {id:'item2',type:'item',attributes:[],relationships:[]}],
   events:[{id:'e1',type:'Open',time:'2026-01-01T00:00:00Z',attributes:[],relationships:[{objectId:'order1',qualifier:''}]}]};
  async function load(doc){
   await page.locator('#owgEventLogFile').setInputFiles({name:'constraints.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
   await status('File mapping ready.');await page.locator('#owgEventLogConsent').check();
  }
  await load(payload);await page.locator('#owgEventLogImport').click();await status('Retained 4 supplied object relationships');
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgObjectRules').waitFor();
  assert.equal(await page.locator('#owgObjectRuleTemplate').inputValue(),'supplied_object_cardinality','zero-event neighbor types must not hide the supplied rule editor');
  assert.ok((await page.locator('#owgObjectRuleRelated option').allTextContents()).includes('item'));
  await page.locator('#owgObjectRuleType').selectOption('order');await page.locator('#owgObjectRuleRelated').selectOption('item');
  await page.locator('#owgObjectRuleMin').fill('2');await page.locator('#owgObjectRuleMax').fill('2');
  await page.locator('#owgObjectRuleAdd').click();
  const check=async()=>{
   const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/object-constraints')&&r.request().method()==='POST'),page.locator('#owgObjectRuleRun').click()]);
   assert.equal(response.status(),200);const data=await response.json();
   await page.locator('#owgObjectRuleResults').filter({hasText:'Supplied retained graph:'}).waitFor();return data;
  };
  let data=await check();const before=data.supplied_relationship_basis.relationships_sha256;
  assert.equal(data.results[0].units,2);assert.equal(data.results[0].violating,1);
  assert.deepEqual(data.results[0].violating_examples,[{unit:'order2',detail:'0 related'}]);
  assert.equal(data.supplied_relationship_basis.retained_relationships,4);
  async function downloaded(id,extension){
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator(id).click()]);
   const file=path.join(scratch,'rules.'+extension);await download.saveAs(file);return fs.readFile(file,'utf8');
  }
  const json=JSON.parse(await downloaded('#owgObjectRuleExportJson','json'));
  assert.equal(json.supplied_relationship_basis.relationships_sha256,before);assert.equal(json.results[0].business_compliance,'unknown');
  assert.match(await downloaded('#owgObjectRuleExportCsv','csv'),new RegExp(before));
  await page.locator('#owgObjectRuleExactQualifier').check();await page.locator('#owgObjectRuleQualifier').fill(' ');await page.locator('#owgObjectRuleAdd').click();
  data=await check();assert.equal(data.results[1].violating,2);assert.equal(data.results[1].rule.qualifier,' ');
  await page.locator('#owgObjectRuleQualifier').fill('');await page.locator('#owgObjectRuleAdd').click();
  data=await check();assert.equal(data.results[2].violating,2);assert.equal(data.results[2].rule.qualifier,'');
  // Edits invalidate a held response and its downloads, even within the same view.
  let release,entered;const gate=new Promise(r=>release=r),started=new Promise(r=>entered=r);
  await page.route('**/object-constraints',async route=>{const response=await route.fetch();entered();await gate;await route.fulfill({response});});
  await page.locator('#owgObjectRuleRun').click();await started;
  await page.locator('#owgObjectRuleList').getByRole('button',{name:/^Remove object rule /}).first().click();
  release();await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('#owgObjectRuleResults').textContent(),'');assert.equal(await page.locator('#owgObjectRuleExportJson').isHidden(),true);
  await page.unroute('**/object-constraints');
  const malicious='<img src=x onerror="window.constraintInjected=true">';
  await page.locator('#owgObjectRuleQualifier').fill(malicious);await page.locator('#owgObjectRuleAdd').click();
  data=await check();assert.equal(data.results.at(-1).violating,2);
  assert.equal(await page.locator('#owgObjectRules img').count(),0);assert.equal(await page.evaluate(()=>window.constraintInjected),undefined);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const axe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(axe.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile width');
  // An assertion-only append changes the pinned graph, never event/case membership.
  const changed=JSON.parse(JSON.stringify(payload));changed.objects[1].relationships=[{objectId:'item1',qualifier:'contains'}];
  await load(changed);await page.locator('#owgEventLogAppend').click();await status('Added 0 new events');
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();await page.locator('#owgObjectRules').waitFor();
  await page.locator('#owgObjectRuleRelated').selectOption('item');await page.locator('#owgObjectRuleMin').fill('1');await page.locator('#owgObjectRuleMax').fill('2');
  await page.locator('#owgObjectRuleAdd').click();data=await check();
  assert.equal(data.results[0].violating,0);assert.equal(data.supplied_relationship_basis.revision,1);assert.equal(data.supplied_relationship_basis.retained_relationships,5);
  assert.notEqual(data.supplied_relationship_basis.relationships_sha256,before);
  assert.match(await page.locator('#owgEventLogPick').textContent(),/1 events/);
  await page.locator('#owgObjectRuleAdd').focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('#owgObjectRuleList tbody tr').count(),2,'keyboard adds a second reviewed rule');
  page.once('dialog',d=>d.accept());
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/delete')),page.locator('#owgEventLogDelete').click()]);
  await page.locator('#owgEventLogManage').waitFor({state:'hidden'});assert.deepEqual(errors,[]);
  console.log('Supplied graph constraints browser passed: unobserved types, explicit direction/qualifiers, distinct degrees, digest/revision-pinned JSON/CSV, no event joins, stale edit guard, escaping, WCAG/mobile, keyboard, assertion-only append and deletion.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
