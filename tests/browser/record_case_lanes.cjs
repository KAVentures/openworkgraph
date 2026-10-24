const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// #265 acceptance: interleaved work on several opted-in records stays in separate exact-record lanes in a real browser.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-record-lanes-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','record_case_lanes_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Record-lane server exited: '+logs);
     try{await fs.access(path.join(scratch,'original-events.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Record-lane server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];let writes=0;
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{if(!['GET','HEAD'].includes(request.method())&&new URL(request.url()).pathname.startsWith('/v1/'))writes++;});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=overview]').click();
   const lanes=page.locator('#workflowRecordCaseLanes');
   await page.locator('#refreshWorkflowEvidence').click();
   await lanes.locator('details[data-record-resource-ref]').nth(2).waitFor();
   assert.equal(await lanes.locator('details[data-record-resource-ref]').count(),3,'A, B and C only: conflicting D and single E are not lanes');
   const lane=ref=>lanes.locator('details[data-record-resource-ref="owg:r:'+ref.repeat(32)+'"]');
   assert.equal(await lane('d').count(),0,'a reference seen on two hosts is suppressed, not split or merged');
   assert.equal(await lane('e').count(),0,'a single observation is not a lane');
   assert.match(await lane('a').locator('summary').textContent(),/jira · issue\s*· 3 observed events, 2 actors, 2 sessions/);
   assert.match(await lane('b').locator('summary').textContent(),/2 observed events, 1 actors, 1 sessions/);
   assert.match(await lane('c').locator('summary').textContent(),/github · pull_request\s*· 2 observed events/);
   // Keyboard: open lane A from its summary and read its own chronology only.
   await lane('a').locator('summary').focus();await page.keyboard.press('Enter');
   assert.equal(await lane('a').evaluate(node=>node.open),true);
   const steps=await lane('a').locator('ol li').allTextContents();
   assert.equal(steps.length,3);
   assert.deepEqual(steps.map(text=>text.split(' · ').at(-1)),['browser_open','browser_click','browser_submit']);
   assert.ok(steps.every(text=>/T09:0[136]:00/.test(text)),'lane A holds only its own interleaved events: '+steps.join(' | '));
   assert.deepEqual((await lane('b').locator('ol li').allTextContents()).map(text=>text.split(' · ').at(-1)),['browser_open','browser_submit']);
   assert.match(await lane('a').textContent(),/No cross-record join, verified handoff or successful business outcome is implied/);
   // A refresh with unchanged evidence keeps the reviewer's open lane.
   await page.locator('#refreshWorkflowEvidence').click();
   await page.locator('#refreshWorkflowEvidence').filter({hasText:'Refresh examples'}).waitFor();
   assert.equal(await lane('a').evaluate(node=>node.open),true,'refresh keeps the open lane');
   const text=await page.locator('body').textContent();
   for(const secret of ['PRIVATE CUSTOMER TITLE','ACME-4711','SECRET-LOCATOR'])assert.equal(text.includes(secret),false,'no raw title or locator: '+secret);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#workflowEvidenceDrafting'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'record-lane WCAG violations');
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal scroll at 390 px');
   assert.equal(writes,0,'viewing lanes never writes');
   const verified=spawnSync(python,['-c',`import json,sqlite3,sys
c=sqlite3.connect(sys.argv[1]);original=json.load(open(sys.argv[2]))
assert [list(row) for row in c.execute('SELECT * FROM events ORDER BY id')]==original
`,path.join(scratch,'data','workflow_observer.db'),path.join(scratch,'original-events.json')],{encoding:'utf8'});
   assert.equal(verified.status,0,verified.stderr);assert.deepEqual(errors,[]);
   console.log('Record-lane browser acceptance passed: three interleaved opted-in records by three people stay in separate exact-record lanes with their own chronology; a host-conflicting reference is suppressed and a single observation is not a lane; keyboard open, refresh keeps the open lane, no titles or locators shown, WCAG, mobile width, no writes and canonical rows unchanged.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
