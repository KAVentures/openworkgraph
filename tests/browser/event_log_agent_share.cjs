const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the owner shares a log's process intelligence with AI agents, confirms, and revokes.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pishare-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)rows.push(`c${i},Receive,2024-03-0${1+i}T08:00:00Z`,`c${i},Pay,2024-03-0${1+i}T09:00:00Z`);
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgAgentShare');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Not shared: AI agents cannot read this log.'}).waitFor();
   const piLogs=()=>page.evaluate(async()=>(await (await fetch('/v1/process-intelligence/logs')).json()).logs.map(l=>[l.name,l.level]));
   assert.deepEqual(await piLogs(),[]);
   // Sharing asks for confirmation; cancelling changes nothing.
   await page.locator('#owgAgentShareLevel').selectOption('aggregates');
   page.once('dialog',d=>d.dismiss());await page.locator('#owgAgentShareSave').click();
   assert.deepEqual(await piLogs(),[]);
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareSave').click();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Shared with AI agents since'}).waitFor();
   assert.deepEqual(await piLogs(),[['orders','aggregates']]);
   const overview=await page.evaluate(async()=>{const id=(await (await fetch('/v1/process-intelligence/logs')).json()).logs[0].log_id;return (await fetch('/v1/process-intelligence/'+id+'/overview')).json();});
   assert.equal(overview.cases,6);assert.deepEqual(overview.top_variants[0].path,['Receive','Pay']);
   // The tag permission appears only at the cases level and asks for confirmation.
   const tags=page.locator('#owgAgentShareTags');
   assert.equal(await tags.isVisible(),false);
   await page.locator('#owgAgentShareLevel').selectOption('cases');
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareSave').click();
   await tags.waitFor();assert.equal(await tags.isChecked(),false);
   page.once('dialog',d=>d.dismiss());await tags.click();
   assert.equal(await tags.isChecked(),false);
   page.once('dialog',d=>d.accept());await tags.click();
   await page.locator('#owgAgentShareTagsStatus').filter({hasText:'AI agents may tag cases.'}).waitFor();
   const tagged=await page.evaluate(async()=>{const id=(await (await fetch('/v1/process-intelligence/logs')).json()).logs[0].log_id;
     const r=await (await fetch('/v1/process-intelligence/'+id+'/case-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tag:'check',case_ids:['c1']})})).json();
     return [r.tag,r.tagged];});
   assert.deepEqual(tagged,['agent: check',1]);
   // Lowering the level hides the permission and drops it.
   await page.locator('#owgAgentShareLevel').selectOption('aggregates');
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentShareSave').click();
   await page.waitForFunction(()=>document.querySelector('#owgAgentShareTags').closest('label').hidden);
   // Stopping needs no confirmation and takes effect at once.
   await page.locator('#owgAgentShareLevel').selectOption('');await page.locator('#owgAgentShareSave').click();
   await page.locator('#owgAgentShareStatus').filter({hasText:'Not shared'}).waitFor();
   assert.deepEqual(await piLogs(),[]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAgentShare'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Agent sharing browser acceptance passed: off by default, confirmed share, agent-visible overview, confirmed agent tag permission dropped with the cases level, immediate revoke, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
