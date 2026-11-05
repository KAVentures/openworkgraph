const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: signed report channel, replay/gap handling, version inventory, incident triage.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-channels-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const spec='c'.repeat(64);
 const report=(ids,status)=>({format:'openworkgraph.automation-run-report.v1',deployment:{name:'Billing agent',runtime:'n8n',workflow_spec_sha256:spec},
   runs:ids.map(id=>({run_id:id,started_at:'2024-03-01T10:00:00Z',finished_at:'2024-03-01T10:01:00Z',status,
     steps:status==='failed'?[{step_id:'s1',activity:'Charge card',status:'failed',at:'2024-03-01T10:00:30Z'}]:[],outcome:{verified:false,source:'n8n'}}))});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgMonitorCard').waitFor();
   const dep=await page.evaluate(async doc=>{await window.__owgAuthReady;const r=await fetch('/v1/automation-monitoring/reports',{method:'POST',headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'},body:JSON.stringify(doc)});if(!r.ok)throw new Error(await r.text());return (await r.json()).deployment.deployment_id;},report(['seed'],'succeeded'));
   await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgMonitorPick').selectOption(dep);
   await page.locator('#owgMonitorChannel').click();await page.locator('#owgMonitorChannelCreate').click();
   const secret=await page.locator('#owgMonitorChannelSecret').inputValue();assert.match(secret,/^[0-9a-f]{64}$/);
   const channelId=(await page.locator('#owgMonitorChannelPanel').textContent()).match(/channels\/(chn_[0-9a-f]{16})\/reports/)[1];
   const send=(seq,doc,key,label)=>page.evaluate(async({seq,doc,key,label,channelId})=>{
     const body=new TextEncoder().encode(JSON.stringify({format:'openworkgraph.signed-run-report.v1',version_label:label||'v1.0.0',report:doc}));
     const ts=Math.floor(Date.now()/1000);const hex=s=>new Uint8Array(s.match(/../g).map(h=>parseInt(h,16)));
     const k=await crypto.subtle.importKey('raw',hex(key),{name:'HMAC',hash:'SHA-256'},false,['sign']);
     const prefix=new TextEncoder().encode(ts+'.'+seq+'.');const msg=new Uint8Array(prefix.length+body.length);msg.set(prefix);msg.set(body,prefix.length);
     const sig=[...new Uint8Array(await crypto.subtle.sign('HMAC',k,msg))].map(b=>b.toString(16).padStart(2,'0')).join('');
     const r=await fetch('/v1/automation-monitoring/channels/'+channelId+'/reports',{method:'POST',body,headers:{'Content-Type':'application/json','X-OWG-Report-Timestamp':String(ts),'X-OWG-Report-Sequence':String(seq),'X-OWG-Report-Signature':'v1='+sig}});
     return {status:r.status,body:await r.json()};
   },{seq,doc,key,label,channelId});
   const first=await send(1,report(['f1','f2'],'failed'),secret);assert.equal(first.status,200);assert.equal(first.body.outcome,'imported');
   assert.equal((await send(1,report(['f1','f2'],'failed'),secret)).body.duplicate,true);
   assert.equal((await send(1,report(['other'],'failed'),secret)).status,409);
   assert.equal((await send(2,report(['x'],'failed'),'00'.repeat(32))).status,401);
   assert.equal((await send(4,report(['r4'],'succeeded'),secret)).status,200);
   await page.locator('#owgMonitorChannel').click();
   const panel=page.locator('#owgMonitorChannelPanel');
   await panel.getByText('v1.0.0',{exact:true}).waitFor();
   const row=await panel.locator('table').first().textContent();assert.match(row,/2, 3/,'skipped sequence numbers are reported as missing');
   assert.equal(await page.locator('#owgMonitorChannelSecret').count(),0,'the secret is not shown again');
   await page.locator('#owgMonitorIncidents').click();
   const incidentsPanel=page.locator('#owgMonitorIncidentPanel');
   for(const kind of ['Failed runs','Lost reports','Reused sequence numbers','Rejected signatures'])await incidentsPanel.getByText(new RegExp('^'+kind)).first().waitFor();
   const failed=incidentsPanel.locator('fieldset').filter({hasText:'Step: Charge card'});
   await failed.getByRole('textbox').fill('Processor outage');
   await failed.getByRole('button',{name:/^Resolve incident/}).click();
   await incidentsPanel.locator('fieldset').filter({hasText:'Processor outage'}).getByText(/Failed runs · resolved/).waitFor();
   await send(5,report(['f5'],'failed'),secret);
   await page.locator('#owgMonitorIncidents').click();
   await incidentsPanel.locator('fieldset').filter({hasText:'Step: Charge card'}).getByText(/Failed runs · open/).waitFor();
   assert.match(await incidentsPanel.locator('fieldset').filter({hasText:'Step: Charge card'}).textContent(),/reopened 1×/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgMonitorCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   await page.locator('#owgMonitorChannel').click();await page.locator('#owgMonitorChannelRevoke').click();
   await page.locator('#owgMonitorChannelCreate').waitFor();
   assert.equal((await send(6,report(['f6'],'failed'),secret)).status,403);
   assert.deepEqual(errors,[]);
   console.log('Report channel acceptance passed: shown-once secret, signed import, idempotent resend, replay conflict, bad signature, lost-report gaps, version inventory, incident resolve and reopen, revocation, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
