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
   // A real exported paired graph accepts either interleaving without flattening.
   const graphExport=spawnSync(python,['-c',`
import json
from shared.process_model import ProcessModel
from shared.process_agent_project import build_runtime_spec
m=ProcessModel(goal='Synthetic parallel checks',baseline_source='user_declared',baseline_path=['Checks'],
 activities=[{'id':'fork','label':'Both','kind':'parallel_split'},
 {'id':'checkA','label':'Same check'}, {'id':'checkB','label':'Same check'},
 {'id':'join','label':'Joined','kind':'parallel_join'}, {'id':'close','label':'Close'}],
 transitions=[{'source':a,'target':b} for a,b in [('fork','checkA'),('fork','checkB'),('checkA','join'),('checkB','join'),('join','close')]],
 entry_activity_id='fork',terminal_activity_ids=['close'])
s=build_runtime_spec(dict(format='openworkgraph.versioned-process-export.v1',workflow_id='synthetic_graph',revision=1,
 content_sha256='a'*64,execution_authorization=False,process_model=m.model_dump()))
import hashlib
print(json.dumps(dict(spec=s,sha=hashlib.sha256(json.dumps(s,sort_keys=True,allow_nan=False).encode()).hexdigest())))
`],{encoding:'utf8'});
   assert.equal(graphExport.status,0,graphExport.stderr);
   const graph=JSON.parse(graphExport.stdout);
   const graphReport={format:report.format,deployment:{name:'Parallel checks',runtime:'openai-agents',workflow_spec_sha256:graph.sha},
     runs:[['checkA','checkB','close'],['checkB','checkA','close'],['checkA','close']].map((ids,i)=>({run_id:'graph'+i,
       started_at:new Date(start).toISOString(),finished_at:new Date(start+60000).toISOString(),status:'succeeded',
       steps:ids.map(step_id=>({step_id,activity:'Check',status:'completed',at:new Date(start).toISOString()}))}))};
   await page.locator('#owgMonitorReport').setInputFiles({name:'graph-runs.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(graphReport))});
   await page.locator('#owgMonitorConsent').check();await page.locator('#owgMonitorImport').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Imported 3 runs (8 steps) into Parallel checks'}).waitFor();
   await page.locator('#owgMonitorManage').waitFor({state:'visible'});
   await page.locator('#owgMonitorResults h3',{hasText:'Reported results'}).waitFor();
   await page.locator('#owgMonitorDesign').setInputFiles({name:'workflow.spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(graph.spec))});
   await page.locator('#owgMonitorResults h3',{hasText:'Reported activity structure against the saved graph'}).waitFor();
   const structure=await page.locator('#owgMonitorResults').textContent();
   assert.match(structure,/Exact structural matches/);assert.match(structure,/3 \/ 3/);
   assert.match(structure,/Missing activities mean missing reported evidence/);
   const compared=await page.evaluate(async()=>{const ds=(await(await fetch('/v1/automation-monitoring/deployments')).json()).deployments;
     return (await(await fetch('/v1/automation-monitoring/deployments/'+ds[0].deployment_id+'/monitor')).json()).design_conformance;});
   assert.equal(compared.runs_exact_structural_match,2);assert.equal(compared.runs_compared,3);
   assert.equal(compared.business_outcome_verified,false);
   // A late result must not replace the currently selected deployment/window.
   const graphId=await page.locator('#owgMonitorPick').inputValue();
   const otherReport={...graphReport,deployment:{...graphReport.deployment,name:'Other implementation'},runs:[graphReport.runs[0]]};
   await page.locator('#owgMonitorTarget').selectOption('');
   await page.locator('#owgMonitorReport').setInputFiles({name:'other-runs.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(otherReport))});
   await page.locator('#owgMonitorConsent').check();await page.locator('#owgMonitorImport').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Imported 1 runs (3 steps) into Other implementation'}).waitFor();
   await page.waitForFunction(()=>document.querySelector('#owgMonitorPick').selectedOptions[0]?.textContent.includes('Other implementation'));
   await page.locator('#owgMonitorDesign').setInputFiles({name:'workflow.spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(graph.spec))});
   await page.locator('#owgMonitorResults h3',{hasText:'Reported activity structure against the saved graph'}).waitFor();
   const otherId=await page.locator('#owgMonitorPick').inputValue();
   let release,responseReady;const hold=new Promise(resolve=>{release=resolve;}),held=new Promise(resolve=>{responseReady=resolve;});
   const pattern='**/v1/automation-monitoring/deployments/'+graphId+'/monitor?*';
   await page.route(pattern,async route=>{const response=await route.fetch();responseReady();await hold;await route.fulfill({response});});
   await page.locator('#owgMonitorPick').selectOption(graphId);await page.locator('#owgMonitorShow').click();
   await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Delayed monitoring response was not intercepted')),15000);
     held.then(()=>{clearTimeout(timer);resolve();});});
   await page.locator('#owgMonitorPick').selectOption(otherId);await page.locator('#owgMonitorShow').click();
   await page.locator('#owgMonitorResults h3',{hasText:'Reported activity structure against the saved graph'}).waitFor();
   const completed=page.waitForEvent('requestfinished',{predicate:r=>r.url().includes('/'+graphId+'/monitor?')});
   release();await completed;
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.match(await page.locator('#owgMonitorResults').textContent(),/1 \/ 1/,'late response cannot replace the selected deployment');
   assert.doesNotMatch(await page.locator('#owgMonitorResults').textContent(),/3 \/ 3/);
   await page.unroute(pattern);
   await page.locator('#owgMonitorRecent').fill('14');
   assert.equal(await page.locator('#owgMonitorResults').textContent(),'','window edits invalidate old results');
   await page.locator('#owgMonitorShow').click();
   await page.locator('#owgMonitorResults h3',{hasText:'Reported activity structure against the saved graph'}).waitFor();
   const graphAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgMonitorCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(graphAudit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgMonitorDelete').click();
   await page.locator('#owgMonitorStatus').filter({hasText:'Deployment deleted.'}).waitFor();
   assert.equal(await page.locator('#owgMonitorPick').inputValue(),graphId);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgMonitorDelete').click();
   await page.locator('#owgMonitorManage').waitFor({state:'hidden'});
   assert.deepEqual(errors,[]);
   console.log('Automation monitoring acceptance passed: consented run-report import, reported rates with intervals, drift alerts, reviewed-design comparison, WCAG audit, mobile width, event-log export, verified deletion.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
