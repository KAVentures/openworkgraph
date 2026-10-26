const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-benefits-')),base='http://127.0.0.1:8791';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_collaboration_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));

 const api=async(url,body,who='alice')=>{const r=await fetch(base+url,{headers:{Authorization:'Bearer '+who,...(body?{'Content-Type':'application/json'}:{})},...(body?{method:'POST',body:JSON.stringify(body)}:{})});assert.ok(r.ok,await r.clone().text());return r.json();};
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  const example=JSON.parse(await fs.readFile(path.join(scratch,'process.json'),'utf8'));
  example.process_model.opportunity_scenario={currency:'USD',monthly_cases:100,minutes_saved_per_case:10,eligible_fraction_percent:100,loaded_hourly_cost:60,recurring_monthly_cost:100,one_time_implementation_cost:1800,sensitivity_percent:25};
  const records=[];for(const title of ['Root One','Root Two','Scenario A','Scenario B']){
    const r=(await api('/v1/human/processes',{title,process_model:example.process_model,user_confirmed:true,organization_sharing_confirmed:true})).process;
    await api('/v1/human/processes/'+r.workflow_id+'/access',{expected_access_revision:1,members:[{actor_id:'bob',role:'reviewer'}],user_confirmed:true});
    await api('/v1/human/processes/'+r.workflow_id+'/reviews',{revision:1,content_sha256:r.content_sha256,decision:'reviewed_design',expected_review_number:0,user_confirmed:true},'bob');records.push(r);
  }
  const [r1,r2,a,b]=records,rootURL='/v1/human/processes/'+r1.workflow_id;
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const login=async who=>{await page.locator('#token').fill(who);await page.getByRole('button',{name:'Connect',exact:true}).click();await page.locator('#status').filter({hasText:'Loaded organization process inventory.'}).waitFor();};
  const select=async root=>{await page.locator('#inventory').getByRole('button',{name:root.title+' · r1',exact:true}).click();await page.locator('#selectedTitle').filter({hasText:root.title}).waitFor();await page.locator('#benefitLoad').click();await page.locator('#benefitEditor').waitFor();};
  const save=async()=>{await page.locator('#benefitConfirm').check();await page.locator('#benefitSave').click();await page.locator('#benefitStatus').filter({hasText:'Benefit plan saved.'}).waitFor();};
  const add=async process=>{await page.locator('#benefitChoices').click();await page.locator('#benefitChoice option').nth(3).waitFor({state:'attached'});await page.locator('#benefitChoice').selectOption(process.workflow_id);await page.locator('#benefitAdd').click();};
  const status=page.locator('#benefitPortfolioResult');
  const portfolio=async()=>{await page.locator('#benefitPortfolioLoad').click();await page.locator('#benefitPortfolioStatus').filter({hasText:'Portfolio loaded.'}).waitFor();};
  const calculate=async()=>{await page.locator('#benefitPortfolioConfirm').check();await page.locator('#benefitPortfolioEstimate').click();await status.getByText('Status: modeled estimate',{exact:true}).waitFor();};
  await page.goto(base+'/processes');await login('alice');await select(r1);
  await add(a);await page.locator('#benefitScope_link_1').fill('invoice_pool');await page.locator('#benefitInclude_link_1').check();
  await add(b);await page.locator('#benefitScope_link_2').fill('payment_pool');assert.equal(await page.locator('#benefitInclude_link_2').isChecked(),false);
  await page.locator('#benefitTitle').fill('Service value assumptions');await save();
  assert.match(await page.locator('#benefitEstimate').textContent(),/1000.00/);
  const [firstDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#benefitExport').click()]);const first=JSON.parse(await fs.readFile(await firstDownload.path(),'utf8'));assert.equal(first.artifact.revision,1);assert.equal(first.totals.net_monthly_value_before_other_costs,'900.00');
  assert.deepEqual((await api(rootURL)).process,r1);
  await select(r2);await add(a);await page.locator('#benefitScope_link_1').fill('invoice_pool');await page.locator('#benefitInclude_link_1').check();await save();
  await portfolio();assert.match(await status.textContent(),/1 unique scenario processes/);assert.ok(await page.locator('#benefitPortfolioEstimate').isDisabled());await calculate();
  const [companyDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#benefitPortfolioExport').click()]);const company=JSON.parse(await fs.readFile(await companyDownload.path(),'utf8'));assert.equal(company.totals.gross_labor_value_per_month,'1000.00');assert.equal(company.totals.recurring_monthly_cost,'100.00');assert.equal(company.contributors[0].linked_root_ids.length,2);assert.equal(company.realized_savings_verified,false);
  await select(r1);await page.locator('#benefitInclude_link_2').check();await page.locator('#benefitConfirm').check();await page.locator('#benefitTitle').fill('Two reviewed scopes');assert.equal(await page.locator('#benefitConfirm').isChecked(),false);assert.equal(await page.locator('#benefitScopes').isChecked(),false);await page.locator('#benefitScopes').check();await save();
  await portfolio();await calculate();assert.match(await status.textContent(),/2000.00/);
  // Historical plan artifacts stay exact; diagnostics follow current source designs.
  await page.locator('#benefitRevision').selectOption('1');await page.locator('#benefitHistory').click();await page.locator('#benefitStatus').filter({hasText:'Opened benefit plan revision 1.'}).waitFor();assert.ok(await page.locator('#benefitSave').isDisabled());
  const [historicalDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#benefitExport').click()]);assert.deepEqual(JSON.parse(await fs.readFile(await historicalDownload.path(),'utf8')).artifact,first.artifact);
  await select(r1);await portfolio();await api('/v1/human/processes/'+a.workflow_id+'/archive',{expected_access_revision:2,archived:true,user_confirmed:true});
  await page.locator('#benefitPortfolioConfirm').check();await page.locator('#benefitPortfolioEstimate').click();await page.locator('#benefitPortfolioStatus').filter({hasText:'Portfolio or referenced designs changed'}).waitFor();assert.equal(await status.textContent(),'');
  await portfolio();assert.match(await status.textContent(),/withheld/);assert.match(await status.textContent(),/archived/);assert.ok(await page.locator('#benefitPortfolioEstimate').isDisabled());assert.doesNotMatch(await status.textContent(),/gross labor value per month/);
  await api('/v1/human/processes/'+a.workflow_id+'/archive',{expected_access_revision:3,archived:false,user_confirmed:true});
  // Held old-identity company results cannot restore data in a different tenant.
  let seen,release;const held=new Promise(r=>seen=r);await page.route('**/process-benefit-portfolio?*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#benefitPortfolioLoad').click();await held;await page.locator('#disconnect').click();await login('foreign');const stale=page.waitForResponse(r=>r.url().includes('/process-benefit-portfolio?'));release();await stale;assert.equal(await status.textContent(),'');assert.equal(await page.locator('#benefitEditor').count(),0);
  await page.locator('#disconnect').click();await login('reader');await select(r1);assert.ok(await page.locator('#benefitSave').isDisabled());assert.ok(await page.locator('#benefitDelete').isDisabled());await portfolio();await calculate();assert.match(await status.textContent(),/2000.00/);
  await page.locator('#disconnect').click();await login('alice');await select(r1);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#benefitDetail'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  page.once('dialog',dialog=>dialog.accept());await page.locator('#benefitDelete').click();await page.locator('#benefitStatus').filter({hasText:'Benefit history erased.'}).waitFor();assert.equal((await api(rootURL+'/benefits')).latest_revision,0);assert.deepEqual((await api(rootURL)).process,r1);
  assert.deepEqual(errors,[]);console.log('Benefit contributions browser passed: explicit links/inclusion/scopes, immutable history, no duplicate company value/costs, reviewed fingerprint/export, stale/archived withholding, tenant/held identity/reader, erasure, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
