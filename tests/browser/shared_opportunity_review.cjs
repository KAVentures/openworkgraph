const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-shared-opportunity-')),base='http://127.0.0.1:8791';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_collaboration_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));

 const api=async(url,body,who='alice')=>{const r=await fetch(base+url,{headers:{Authorization:'Bearer '+who,...(body?{'Content-Type':'application/json'}:{})},...(body?{method:'POST',body:JSON.stringify(body)}:{})});assert.ok(r.ok,await r.clone().text());return r.json();};
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  const example=JSON.parse(await fs.readFile(path.join(scratch,'process.json'),'utf8'));example.process_model.opportunity_scenario={currency:'USD',monthly_cases:120,minutes_saved_per_case:10,eligible_fraction_percent:100,loaded_hourly_cost:60,recurring_monthly_cost:100,one_time_implementation_cost:1800,sensitivity_percent:25};
  const root=(await api('/v1/human/processes',{title:'Service improvement',process_model:example.process_model,user_confirmed:true,organization_sharing_confirmed:true})).process,baseURL='/v1/human/processes/'+root.workflow_id;
  await api(baseURL+'/access',{expected_access_revision:1,members:[{actor_id:'bob',role:'reviewer'}],user_confirmed:true});
  await api(baseURL+'/reviews',{revision:1,content_sha256:root.content_sha256,decision:'reviewed_design',expected_review_number:0,user_confirmed:true},'bob');
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const login=async who=>{await page.locator('#token').fill(who);await page.getByRole('button',{name:'Connect',exact:true}).click();await page.locator('#status').filter({hasText:'Loaded organization process inventory.'}).waitFor();};
  const selectRoot=async revision=>{await page.locator('#inventory').getByRole('button',{name:'Service improvement · r'+revision,exact:true}).click();await page.locator('#selectedTitle').filter({hasText:'revision '+revision}).waitFor();await page.locator('#opportunityLoad').click();await page.locator('#opportunityEditor').waitFor();};
  const save=async()=>{await page.locator('#opportunityConfirm').check();await page.locator('#opportunitySave').click();await page.locator('#opportunityStatus').filter({hasText:'Opportunity snapshot saved.'}).waitFor();};
  const decide=async target=>{await page.locator('#opportunityTarget').selectOption(target);await page.locator('#opportunityDecisionConfirm').check();await page.locator('#opportunityTransition').click();await page.locator('#opportunityStatus').filter({hasText:'Named stage decision recorded.'}).waitFor();};
  await page.goto(base+'/processes');await login('alice');await selectRoot(1);await page.locator('#opportunityAddAssessment').click();
  for(const key of ['rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate'])await page.locator('#opportunityEase_'+key).selectOption('4');
  await page.locator('#opportunityTitle').fill('Service automation');await page.locator('#opportunityCoordinator').fill('Implementation lead');await page.locator('#opportunityTeam').fill('Service team');await page.locator('#opportunityBudget').fill('2000.00');await page.locator('#opportunityCompliance').check();await save();
  assert.match(await page.locator('#opportunityDecision').textContent(),/Ease 75 \/100 · 20.00 modeled hours\/month · quick win/);
  assert.equal(await page.locator('#opportunityTarget option[value="approved"]').count(),0,'author cannot approve own assessment');
  await page.locator('#opportunityBudget').fill('1999.99');assert.equal(await page.locator('#opportunityDecision').evaluate(n=>n.disabled),true);assert.ok(await page.locator('#opportunityTransition').isDisabled());await page.locator('#opportunityLoad').click();await page.locator('#opportunityStatus').filter({hasText:'Opened opportunity revision 1.'}).waitFor();assert.equal(await page.locator('#opportunityBudget').inputValue(),'2000.00');
  const [firstDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#opportunityExport').click()]);const first=JSON.parse(await fs.readFile(await firstDownload.path(),'utf8'));assert.equal(first.artifact.revision,1);assert.equal(first.artifact.assessment_author_actor_id,'alice');assert.equal(first.execution_authorized,false);
  await page.locator('#disconnect').click();await login('bob');await selectRoot(1);assert.ok(await page.locator('#opportunitySave').isDisabled());
  await page.locator('#opportunityTarget').selectOption('approved');await page.locator('#opportunityDecisionConfirm').check();await page.locator('#opportunityTransition').click();await page.locator('#opportunityStatus').filter({hasText:'compliance-review requirement'}).waitFor();
  await page.locator('#opportunityComplianceAck').check();await decide('approved');
  assert.match(await page.locator('#opportunityDecision').textContent(),/current approved assessment: true/);
  await page.locator('#opportunityInventory').click();await page.locator('#opportunityInventoryStatus').filter({hasText:'1 opportunities in this page'}).waitFor();await page.locator('#opportunityFilter').fill('not a team');assert.equal(await page.locator('#opportunityPipeline').getByRole('button',{name:'Open opportunity '+root.workflow_id,exact:true}).count(),0);await page.locator('#opportunityFilter').fill('Service team');assert.equal(await page.locator('#opportunityPipeline').getByRole('button',{name:'Open opportunity '+root.workflow_id,exact:true}).count(),1);
  await page.locator('#disconnect').click();await login('alice');await selectRoot(1);
  await page.locator('#opportunityImplementation').check();await decide('implemented');assert.match(await page.locator('#opportunityDecision').textContent(),/Recorded stage: implemented/);assert.equal((await api(baseURL+'/opportunity')).business_outcomes_verified,false);
  // Metadata edits also reset approval; saved assessment remains immutable.
  await page.locator('#opportunityInventory').click();await page.locator('#opportunityInventoryStatus').filter({hasText:'1 opportunities in this page'}).waitFor();
  await page.locator('#opportunityConfirm').check();await page.locator('#opportunityTeam').fill('Changed service team');assert.equal(await page.locator('#opportunityConfirm').isChecked(),false);assert.ok(await page.locator('#opportunityTransition').isDisabled());await save();
  assert.match(await page.locator('#opportunityDecision').textContent(),/Recorded stage: assessing/);assert.equal(await page.locator('#opportunityPipeline').getByRole('button',{name:'Open opportunity '+root.workflow_id,exact:true}).count(),0);
  const approvedSnapshot=(await api(baseURL+'/opportunity?revision=2')).artifact;
  await page.locator('#opportunityRevision').selectOption('2');await page.locator('#opportunityHistory').click();await page.locator('#opportunityStatus').filter({hasText:'Opened opportunity revision 2.'}).waitFor();
  assert.match(await page.locator('#opportunityDecision').textContent(),/current status: historical snapshot/);assert.match(await page.locator('#opportunityDecision').textContent(),/current approved assessment: false/);assert.equal(await page.locator('#opportunityTransition').isDisabled(),true);
  const [approvedDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#opportunityExport').click()]);const historicalApproval=JSON.parse(await fs.readFile(await approvedDownload.path(),'utf8'));assert.deepEqual(historicalApproval.artifact,approvedSnapshot);assert.equal(historicalApproval.viewing_historical_revision,true);assert.equal(historicalApproval.current_approved_assessment,false);
  await page.locator('#opportunityRevision').selectOption('1');await page.locator('#opportunityHistory').click();await page.locator('#opportunityStatus').filter({hasText:'Opened opportunity revision 1.'}).waitFor();assert.ok(await page.locator('#opportunitySave').isDisabled());
  const [historicDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#opportunityExport').click()]);assert.deepEqual(JSON.parse(await fs.readFile(await historicDownload.path(),'utf8')).artifact,first.artifact);
  await page.locator('#disconnect').click();await login('bob');await selectRoot(1);await page.locator('#opportunityComplianceAck').check();await decide('approved');
  // New source costs do not inherit an old opportunity approval or permit implementation.
  const newer=structuredClone(root.process_model);newer.opportunity_scenario.one_time_implementation_cost=3000;
  const revised=(await api(baseURL,{title:'Service improvement',process_model:newer,expected_revision:1,user_confirmed:true,organization_sharing_confirmed:true})).process;
  await page.locator('#opportunityLoad').click();await page.locator('#opportunityStatus').filter({hasText:'Opened opportunity revision 5.'}).waitFor();assert.match(await page.locator('#opportunityDecision').textContent(),/current status: needs reassessment/);assert.match(await page.locator('#opportunityDecision').textContent(),/current approved assessment: false/);
  await api(baseURL+'/reviews',{revision:2,content_sha256:revised.content_sha256,decision:'reviewed_design',expected_review_number:0,user_confirmed:true},'bob');
  await page.locator('#disconnect').click();await login('alice');await selectRoot(2);assert.equal(await page.locator('#opportunityTarget option[value="implemented"]').count(),0);
  await page.locator('#opportunityRefresh').click();await page.locator('#opportunityBudget').fill('4000.00');await save();assert.equal((await api(baseURL+'/opportunity')).artifact.approval,null);
  // Old-identity inventory responses cannot repopulate another tenant's page.
  let seen,release;const held=new Promise(r=>seen=r);await page.route('**/process-opportunity-pipeline?*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});await page.locator('#opportunityInventory').click();await held;
  await page.locator('#disconnect').click();await login('foreign');const stale=page.waitForResponse(r=>r.url().includes('/process-opportunity-pipeline?'));release();await stale;assert.equal(await page.locator('#opportunityEditor').count(),0);assert.equal(await page.locator('#opportunityPipeline').getByRole('button',{name:'Open opportunity '+root.workflow_id,exact:true}).count(),0);
  await page.locator('#disconnect').click();await login('reader');await selectRoot(2);assert.ok(await page.locator('#opportunitySave').isDisabled());assert.ok(await page.locator('#opportunityTransition').isDisabled());assert.ok(await page.locator('#opportunityDelete').isDisabled());
  await page.locator('#disconnect').click();await login('alice');await selectRoot(2);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#opportunityDetail'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  page.once('dialog',dialog=>dialog.accept());await page.locator('#opportunityDelete').click();await page.locator('#opportunityStatus').filter({hasText:'Opportunity history erased.'}).waitFor();assert.equal((await api(baseURL+'/opportunity')).latest_revision,0);assert.deepEqual((await api(baseURL)).process,revised);
  assert.deepEqual(errors,[]);console.log('Shared opportunity browser passed: explicit assessment/budget/sharing, independent named review/compliance, self-report boundary, reassessment invalidation, immutable history, changed source, inventory, reader/tenant/held identity, erasure, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
