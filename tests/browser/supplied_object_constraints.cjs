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
  async function load(doc,name='constraints.jsonocel'){
   await page.locator('#owgEventLogFile').setInputFiles({name,mimeType:'application/json',buffer:Buffer.from(JSON.stringify(doc))});
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
  const violations=page.locator('#owgObjectRuleViolations');
  await page.locator('#owgObjectRuleResults').getByRole('button',{name:/^Inspect violations for /}).click();
  await violations.getByRole('status').filter({hasText:'1 violation. Showing 1 on this page.'}).waitFor();
  await violations.getByRole('cell',{name:'order2',exact:true}).waitFor();
  await violations.getByRole('cell',{name:'No retained events',exact:true}).waitFor();
  async function downloaded(id,extension){
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator(id).click()]);
   const file=path.join(scratch,'rules.'+extension);await download.saveAs(file);return fs.readFile(file,'utf8');
  }
  const json=JSON.parse(await downloaded('#owgObjectRuleExportJson','json'));
  assert.equal(json.supplied_relationship_basis.relationships_sha256,before);assert.equal(json.results[0].business_compliance,'unknown');
  assert.match(await downloaded('#owgObjectRuleExportCsv','csv'),new RegExp(before));
  await page.locator('#owgObjectRuleExactQualifier').check();await page.locator('#owgObjectRuleQualifier').fill(' ');await page.locator('#owgObjectRuleAdd').click();
  data=await check();assert.equal(data.results[1].violating,2);assert.equal(data.results[1].rule.qualifier,' ');
  await page.locator('#owgObjectRuleResults').getByRole('button',{name:/^Inspect violations for /}).nth(1).click();
  await violations.getByRole('button',{name:'Open event history for order1',exact:true}).click();
  await page.locator('#owgObjectRuleObjectHistory').getByRole('status').filter({hasText:'Showing 1 of 1 events.'}).waitFor();
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
  const savedStatus=page.locator('#owgObjectRuleSavedStatus');
  await page.locator('#owgObjectRuleSavedName').fill('Relationship checks');
  await page.locator('#owgObjectRuleSaveSet').click();
  await savedStatus.getByText('Saved Relationship checks.',{exact:true}).waitFor();
  await page.locator('#owgObjectRuleSaved option').filter({hasText:'Relationship checks (3 rules)'}).waitFor({state:'attached'});
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
  await page.locator('#owgObjectRuleSaved').selectOption({label:'Relationship checks (3 rules)'});
  await page.locator('#owgObjectRuleLoadSet').click();
  await savedStatus.getByText('Loaded Relationship checks.',{exact:true}).waitFor();
  data=await check();
  assert.equal(data.results.length,3);
  assert.equal(data.results[0].rule.qualifier,' ');
  assert.equal(data.results[1].rule.qualifier,'');
  await page.locator('#owgObjectRuleSavedName').fill('Updated relationship checks');
  await page.locator('#owgObjectRuleUpdateSet').click();
  await savedStatus.getByText('Updated Updated relationship checks.',{exact:true}).waitFor();
  await page.locator('#owgObjectRuleSaved option').filter({hasText:'Updated relationship checks (3 rules)'}).waitFor({state:'attached'});
  page.once('dialog',d=>d.accept());
  await page.locator('#owgObjectRuleDeleteSet').click();
  await savedStatus.getByText('Deleted Updated relationship checks.',{exact:true}).waitFor();
  assert.equal(await page.locator('#owgObjectRuleList tbody tr').count(),3);
  page.once('dialog',d=>d.accept());
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/delete')),page.locator('#owgEventLogDelete').click()]);
  await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
  // A declared object graph with zero assertions must still expose shape rules.
  const empty=JSON.parse(JSON.stringify(payload));for(const o of empty.objects)o.relationships=[];
  empty.objects.push({id:'unseen',type:'unobserved',attributes:[],relationships:[]});
  await load(empty);await page.locator('#owgEventLogImport').click();await status('Imported 1 events');
  await page.locator('#owgEventLogCaseType option[value="unobserved"]').waitFor({state:'attached'});
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  await page.waitForFunction(()=>document.querySelector('#owgEventLogObjectMap')?.childElementCount>0);
  assert.equal(await page.locator('#owgObjectRules').count(),1,'empty supplied graphs must remain inspectable in the editor');
  assert.equal(await page.locator('#owgObjectRuleTemplate').inputValue(),'supplied_object_cardinality');
  await page.locator('#owgObjectRuleRelated').selectOption('item');await page.locator('#owgObjectRuleMin').fill('1');await page.locator('#owgObjectRuleMax').fill('1');
  await page.locator('#owgObjectRuleAdd').click();data=await check();
  assert.equal(data.results[0].violating,2);assert.equal(data.supplied_relationship_basis.retained_relationships,0);
  assert.equal(data.supplied_relationship_basis.retained_objects,5);
  const emptyLog=data.log.log_id;
  const second=JSON.parse(JSON.stringify(empty));
  second.objects[0].relationships=[{objectId:'item1',qualifier:'contains'}];
  second.events[0].id='second-open';
  await load(second,'second-graph.jsonocel');await page.locator('#owgEventLogImport').click();await status('Retained 1 supplied object relationship');
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgObjectRuleRelated').selectOption('item');await page.locator('#owgObjectRuleMin').fill('1');await page.locator('#owgObjectRuleMax').fill('1');
  await page.locator('#owgObjectRuleAdd').click();
  await page.locator('#owgObjectRuleCompareLog').selectOption(emptyLog);
  await page.locator('#owgObjectRuleCompare').click();
  const results=page.locator('#owgObjectRuleResults');
  await results.getByRole('cell',{name:'+50.0 pp',exact:true}).waitFor();
  await results.getByText('1 of 2 activated objects violate (50.0%)',{exact:true}).waitFor();
  await results.getByText('2 of 2 activated objects violate (100.0%)',{exact:true}).waitFor();
  await results.getByRole('button',{name:/^Inspect comparison violations/}).click();
  await violations.getByRole('button',{name:'Open event history for order1',exact:true}).click();
  await page.locator('#owgObjectRuleObjectHistory').getByRole('cell',{name:'e1',exact:true}).waitFor();
  assert.equal(await page.locator('#owgObjectRuleObjectHistory').getByRole('cell',{name:'second-open',exact:true}).count(),0);
  const comparison=JSON.parse(await downloaded('#owgObjectRuleComparisonJson','json'));
  assert.equal(comparison.comparison.log.log_id,emptyLog);
  assert.notEqual(comparison.baseline.supplied_relationship_basis.relationships_sha256,comparison.comparison.supplied_relationship_basis.relationships_sha256);
  assert.match(await downloaded('#owgObjectRuleComparisonCsv','csv'),new RegExp(emptyLog));
  await page.locator('#owgObjectRuleCompareLog').selectOption('');
  assert.equal(await results.textContent(),'');assert.equal(await violations.textContent(),'');
  assert.equal(await page.locator('#owgObjectRuleComparisonJson').isHidden(),true);
  assert.deepEqual(errors,[]);
  console.log('Supplied graph constraints browser passed, including comparison, per-log evidence and downloads, and target change invalidation.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
