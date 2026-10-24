const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: consented run-report import → reported rates and drift alerts → design comparison → event log → deletion.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-monitor-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const start=Date.UTC(2024,2,1);
 const run=(i,status,day)=>{const t=start+day*864e5+i*6e4,at=m=>new Date(t+m*6e4).toISOString();
   const steps=[{step_id:'step_001',activity:'Receive',status:'completed',at:at(0)},{step_id:'step_002',activity:'Validate',status:status==='succeeded'?'completed':'handed_to_human',at:at(2)}];
   if(status==='succeeded')steps.push({step_id:'step_003',activity:'Close',status:'completed',at:at(4)});
   return {run_id:'r'+i,started_at:at(0),finished_at:at(5),status,steps};};
 const runs=[...Array.from({length:40},(_,i)=>run(i,'succeeded',Math.floor(i/2))),...Array.from({length:20},(_,i)=>run(100+i,i<10?'succeeded':'handed_to_human',25+Math.floor(i/7)))];
 const report={format:'openworkgraph.automation-run-report.v1',deployment:{name:'Refund agent',runtime:'ai-sdk'},runs};
 const spec={format:'openworkgraph.agent-starter.v1',to_be:{steps:[{id:'step_001',activity:'Receive'},{id:'step_002',activity:'Validate'},{id:'step_003',activity:'Close'}]}};
 try{
   const hash=spawnSync(python,['-c','import hashlib,json,sys;print(hashlib.sha256(json.dumps(json.loads(sys.argv[1]),sort_keys=True,allow_nan=False).encode()).hexdigest())',JSON.stringify(spec)],{encoding:'utf8'});
   assert.equal(hash.status,0,hash.stderr);report.deployment.workflow_spec_sha256=hash.stdout.trim();
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgMonitorCard').waitFor();
   assert.equal(await page.locator('#owgMonitorManage').isHidden(),true);
   await page.locator('#owgMonitorReport').setInputFiles({name:'runs.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(report))});
   assert.equal(await page.locator('#owgMonitorImport').isDisabled(),true,'storing runs needs explicit consent');
   await page.locator('#owgMonitorConsent').check();await page.locator('#owgMonitorImport').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Imported 60 runs (170 steps) into Refund agent'}).waitFor();
   await page.locator('#owgMonitorResults [role=alert]').waitFor();
   const alert=await page.locator('#owgMonitorResults [role=alert]').textContent();
   assert.match(alert,/Success rate dropped/);assert.match(alert,/More runs were handed to a person/);
   const text=await page.locator('#owgMonitorResults').textContent();
   assert.match(text,/Recent20/);assert.match(text,/100\.0%/);assert.match(text,/Attach the starter/);

   await page.locator('#owgMonitorDesign').setInputFiles({name:'wrong.spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...spec,title:'Wrong version'}))});
   await page.locator('#owgMonitorStatus').filter({hasText:'does not match the workflow spec named by retained reports'}).waitFor();
   const pinned=await page.evaluate(async()=>(await(await fetch('/v1/automation-monitoring/deployments')).json()).deployments[0]);
   assert.equal(pinned.reported_spec_sha256,report.deployment.workflow_spec_sha256);
   assert.equal(pinned.design_attached,false);assert.equal(pinned.runs,60);
   await page.locator('#owgMonitorDesign').setInputFiles({name:'workflow.spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(spec))});
   await page.locator('#owgMonitorResults h3',{hasText:'Compared with the reviewed design'}).waitFor();
   assert.match(await page.locator('#owgMonitorResults').textContent(),/Completed every step in order/);

   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgMonitorCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'monitoring card WCAG violations');
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   await page.setViewportSize({width:1280,height:900});

   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgMonitorToLog').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Saved the event log "Refund agent runs" with 170 steps'}).waitFor();
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgMonitorDelete').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Deployment deleted.'}).waitFor();
   assert.equal(await page.locator('#owgMonitorManage').isHidden(),true);
   const left=await page.evaluate(async()=>(await (await fetch('/v1/automation-monitoring/deployments')).json()).deployments.length);
   assert.equal(left,0);
   assert.deepEqual(errors,[]);
   console.log('Automation monitoring acceptance passed: consented run-report import, reported rates with intervals, drift alerts, reviewed-design comparison, WCAG audit, mobile width, event-log export, verified deletion.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
