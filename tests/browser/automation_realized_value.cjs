const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: record baseline and after measurements for an implemented idea (R16).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-realized-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();
   const auth={'Authorization':'OWG-Session '+session,'Content-Type':'application/json'};
   const call=async(p,body)=>{const r=await fetch(base+p,{method:'POST',headers:auth,body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw new Error(p+': '+JSON.stringify(j));return j;};
   let idea=await call('/v1/automation-pipeline',{title:'Match remittances'});
   idea=await call('/v1/automation-pipeline/'+idea.idea_id+'/transition',{to:'assessing',expected_revision:idea.revision});
   const ease={rules_based:5,structured_inputs:5,process_stability:5,digital_inputs:5,low_exception_rate:5};
   idea=await call('/v1/automation-pipeline/'+idea.idea_id+'/assess',{ease,cases_per_month:300,minutes_per_case:10,eligible_share:0.5,expected_revision:idea.revision});
   idea=await call('/v1/automation-pipeline/'+idea.idea_id+'/transition',{to:'approved',expected_revision:idea.revision});
   const day=n=>{const d=new Date();d.setUTCDate(d.getUTCDate()+n);return d.toISOString().slice(0,10);};
   await call('/v1/automation-pipeline/'+idea.idea_id+'/measurements',{period:'baseline',start:day(-40),end:day(-11),cases:300,minutes_per_case:10});
   idea=await call('/v1/automation-pipeline/'+idea.idea_id+'/transition',{to:'implemented',expected_revision:idea.revision});
   await page.locator('#owgPipelineRefresh').click();
   await page.getByRole('button',{name:'Open idea Match remittances'}).click();
   const section=page.locator('#owgPipelineRealized');await section.waitFor();
   await page.locator('#owgRealizedTable tbody tr').first().waitFor();
   assert.equal(await page.locator('#owgRealizedTable tbody tr').count(),1);
   // An after period before implementation is refused with the server's reason.
   await page.locator('#owgMeasPeriod').selectOption('after');
   await page.locator('#owgMeasStart').fill(day(-3));await page.locator('#owgMeasEnd').fill(day(0));
   await page.locator('#owgMeasCases').fill('40');await page.locator('#owgMeasMinutes').fill('4');
   await page.locator('#owgMeasAdd').click();
   await page.locator('#owgMeasStatus').filter({hasText:'on or after the implementation date'}).waitFor();
   await page.locator('#owgMeasStart').fill(day(0));await page.locator('#owgMeasCases').fill('10');
   await page.locator('#owgMeasSource').selectOption('time_tracking');await page.locator('#owgMeasRef').fill('Timesheet export');
   await page.locator('#owgMeasAdd').click();
   await page.locator('#owgMeasStatus').filter({hasText:'Measurement recorded.'}).waitFor();
   const comparison=await page.locator('#owgRealizedComparison').textContent();
   assert.match(comparison,/Minutes per case: 6 fewer after \(baseline 10, after 4\)/);
   assert.match(comparison,/the assessment modeled 25/);
   const weighted=await page.locator('#owgRealizedWeightedComparison').textContent();
   assert.match(weighted,/Pooled baseline: 10 min\/case across 30 days; after: 4 min\/case across 1 days/);
   assert.match(weighted,/Duration-weighted total hours\/month/);
   assert.match(await section.textContent(),/Source labels are not independently verified/);
   assert.match(await section.textContent(),/not proof that the automation caused it/);
   assert.match(await section.textContent(),/Only one baseline and one after period/);
   // Portfolio-wide readiness is a separate human review; a missing baseline
   // source reference blocks any headline savings claim.
   await page.locator('#owgPipelineBenefitReadiness').click();
   const review=page.locator('#owgPipelineBenefitReadinessResult');
   await review.getByText('Benefit evidence readiness',{exact:true}).waitFor();
   assert.match(await review.textContent(),/1 missing source reference/);
   assert.match(await review.textContent(),/Match remittances/);
   assert.match(await review.textContent(),/Review the provenance/);
   await page.locator('#owgPipelineBenefitReadinessFilter').selectOption('missing_source_reference');
   assert.match(await page.locator('#owgPipelineBenefitReadinessRows').textContent(),/1 matching ideas/);
   await page.locator('#owgPipelineBenefitReadinessSearch').fill('not-an-idea');
   assert.match(await page.locator('#owgPipelineBenefitReadinessRows').textContent(),/0 matching ideas/);
   await page.locator('#owgPipelineBenefitReadinessSearch').fill('Match remittances');
   assert.match(await page.locator('#owgPipelineBenefitReadinessRows').textContent(),/1 matching ideas/);
   await page.getByRole('button',{name:'Remove after measurement from '+day(0)}).click();
   await page.locator('#owgRealizedTable tbody tr').nth(1).waitFor({state:'detached'});
   await page.locator('#owgPipelineBenefitReadiness').click();
   await page.locator('#owgPipelineBenefitReadinessFilter').selectOption('missing_after');
   assert.match(await page.locator('#owgPipelineBenefitReadinessRows').textContent(),/Match remittances/);
   assert.match(await page.locator('#owgPipelineBenefitReadinessRows').textContent(),/Record a post-implementation measurement/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Automation realized-value browser acceptance passed: baseline before implementation, after period refused before the implementation date, after recorded with source, per-case and modeled comparison with causal caveat, removal, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
