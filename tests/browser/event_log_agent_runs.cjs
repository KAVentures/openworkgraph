const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: observed AI agent runs become an event log that the usual analyses read.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-agentruns-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','agent_runs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
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
   // Real native shadow review uses the existing seeded canonical agent runs.
   await page.locator('[role=tab][data-tab=agents]').click();
   const shadow=page.locator('#owgNativeShadowReview');
   await shadow.locator('summary').click();
   await shadow.getByLabel('Test window start (ISO 8601 with offset)',{exact:true}).fill(new Date(Date.now()-3*86400000).toISOString());
   await shadow.getByLabel('Test window end (ISO 8601 with offset)',{exact:true}).fill(new Date().toISOString());
   await shadow.getByLabel('Include disconnected integrations in retained history',{exact:true}).check();
   await shadow.getByRole('button',{name:'Capture exact trace snapshot',exact:true}).click();
   await shadow.getByText('Snapshot captured.',{exact:false}).waitFor();
   const firstRoot=shadow.getByLabel('Root execution',{exact:true});
   assert.equal(await firstRoot.locator('option').count(),10);
   await shadow.getByLabel('Staged package SHA-256',{exact:true}).fill('a'.repeat(64));
   await shadow.getByLabel('Case ID',{exact:true}).fill('case_1');
   await shadow.getByLabel('Expected framework (declare independently of observed label)',{exact:true}).fill('custom-agent');
   await shadow.getByRole('button',{name:'Add declared case',exact:true}).click();
   await shadow.getByRole('button',{name:'Review declared shadow cases',exact:true}).click();
   await shadow.getByText('Review complete for returned evidence only.',{exact:false}).waitFor();
   await shadow.getByRole('heading',{name:'case_1 — observed deviation or failure',exact:true}).waitFor();
   assert.match(await shadow.textContent(),/observed_tool_label_outside_reviewed_list/);
   assert.equal(await shadow.getByRole('button',{name:'Download exact trace JSON',exact:true}).isEnabled(),true);
   assert.equal(await shadow.getByRole('button',{name:'Download declaration JSON',exact:true}).isEnabled(),true);
   assert.equal(await shadow.getByRole('button',{name:'Download review JSON',exact:true}).isEnabled(),true);
   await shadow.getByLabel('Staged package SHA-256',{exact:true}).fill('b'.repeat(64));
   assert.equal(await shadow.getByRole('button',{name:'Download review JSON',exact:true}).isDisabled(),true);
   await shadow.getByLabel('Test window start (ISO 8601 with offset)',{exact:true}).fill(new Date(Date.now()-4*86400000).toISOString());
   assert.equal(await shadow.getByRole('button',{name:'Download exact trace JSON',exact:true}).isDisabled(),true);
   await shadow.locator('summary').click();
   await page.locator('[role=tab][data-tab=evidence]').click();
   const box=page.locator('#owgAgentRuns');await box.scrollIntoViewIfNeeded();
   const go=page.locator('#owgAgentRunsImport');
   assert.equal(await go.isDisabled(),true,'storing needs explicit consent');
   await page.locator('#owgAgentRunsConsent').check();
   await page.locator('#owgAgentRunsName').fill('My agents');
   await go.click();
   await page.locator('#owgAgentRunsStatus').filter({hasText:'Imported 10 agent runs with 40 steps as “My agents”.'}).waitFor();
   assert.equal(await page.locator('#owgAgentRunsConsent').isChecked(),false,'consent is asked again for the next import');
   const result=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='My agents').log_id;
     const variants=(await (await fetch('/v1/event-logs/'+id+'/variants')).json()).variants;
     const rc=await (await fetch('/v1/event-logs/'+id+'/root-cause?outcome=contains_activity&activity='+encodeURIComponent('Run failed'))).json();
     const attrs=(await (await fetch('/v1/event-logs/'+id+'/case-attribute-values')).json()).attributes.map(a=>a.name).sort();
     return {paths:variants.map(v=>(v.activities||v.path).join(' > ')).sort(),rc:rc.associations.map(a=>[a.feature.value,a.risk_difference]),attrs,
       text:JSON.stringify([variants,rc])};});
   assert.deepEqual(result.paths,['Run started > Tool: Grep > Tool: Bash (error) > Run failed','Run started > Tool: Grep > Tool: Bash > Run finished']);
   assert(result.rc.some(([value,diff])=>value==='Agent=Reviewer'&&diff>0),'failures associated with the Reviewer agent: '+JSON.stringify(result.rc));
   assert(result.attrs.includes('Outcome')&&result.attrs.includes('Failed tool calls'));
   assert(!result.text.includes('synthetic-run-')&&!result.text.includes('synthetic-session-'),'native run IDs never reach the log');
   // The log knows where it came from: add newly finished runs now, or every day.
   await page.locator('#owgEventLogAnalyze').click();
   const refresh=page.locator('#owgAgentRunsRefresh');await refresh.waitFor();await refresh.scrollIntoViewIfNeeded();
   await page.locator('#owgAgentRunsRefreshInfo').filter({hasText:'made from observed AI agent runs'}).waitFor();
   await page.locator('#owgAgentRunsRefreshNow').click();
   await page.locator('#owgAgentRunsRefreshStatus').filter({hasText:'No new finished runs.'}).waitFor();
   page.once('dialog',d=>d.accept());await page.locator('#owgAgentRunsRefreshDaily').click();
   await page.locator('#owgAgentRunsRefreshStatus').filter({hasText:'Scheduled daily.'}).waitFor();
   const kinds=await page.evaluate(async()=>(await (await fetch('/v1/schedules')).json()).schedules.map(s=>s.kind));
   assert.deepEqual(kinds,['event_log_agent_runs']);
   const audit2=await (async()=>{await page.addScriptTag({content:axeSource});return page.evaluate(async()=>window.axe.run(document.querySelector('#owgAgentRunsRefresh'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));})();
   assert.deepEqual(audit2.violations.map(v=>v.id),[]);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAgentRuns'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Agent mining browser acceptance passed: consented import of observed agent runs, variants, root cause by agent, case attributes, no native IDs, refresh now and daily, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
