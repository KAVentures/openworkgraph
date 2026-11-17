const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-o2o-groups-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let browser,logs='';for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-10000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  const payload={objectTypes:[{name:'order',attributes:[]},{name:'item',attributes:[]}],eventTypes:[{name:'Open',attributes:[]}],objects:[{id:'order1',type:'order',attributes:[],relationships:Array.from({length:201},(_,i)=>({objectId:'item1',qualifier:'label'+String(i).padStart(3,'0')}))},{id:'item1',type:'item',attributes:[],relationships:[]}],events:[{id:'e1',type:'Open',time:'2026-01-01T00:00:00Z',attributes:[],relationships:[{objectId:'order1',qualifier:''}]}]};
  await page.locator('#owgEventLogFile').setInputFiles({name:'groups.jsonocel',mimeType:'application/octet-stream',buffer:Buffer.from(JSON.stringify(payload))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Retained 201 supplied object relationships'}).waitFor();await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  const groups=page.locator('#owgObjectRelationGroups');await groups.locator('summary').click();await groups.filter({hasText:'Showing 1–200 of 201'}).waitFor();
  assert.equal(await page.locator('#owgObjectRelationGroupsPrevious').isDisabled(),true);await page.locator('#owgObjectRelationGroupsNext').click();
  await groups.filter({hasText:'Showing 201–201 of 201'}).waitFor();assert.match(await groups.textContent(),/label200/);
  await groups.getByRole('button',{name:'Inspect group'}).click();await page.locator('#owgObjectRelationStatus').filter({hasText:'1 of 1 matching'}).waitFor();
  assert.equal(await page.locator('#owgObjectRelationQualifier').inputValue(),'label200');await page.locator('#owgObjectRelationGroupsPrevious').click();await groups.filter({hasText:'Showing 1–200 of 201'}).waitFor();
  assert.deepEqual(errors,[]);console.log('Relationship group pagination browser passed: 201 groups, navigation and inspection.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
