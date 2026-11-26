const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-shadow-review-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_shadow_review_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>spawnError=error);for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic shadow-review server exited: '+logs);
   try{await fs.access(path.join(scratch,'shadow-report.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw new Error('Synthetic shadow-review server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];let reportRequests=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(new URL(request.url()).pathname==='/v1/process-catalog/shadow-review')reportRequests++;});
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Support shadow review · r1',exact:true}).click();
  const report=JSON.parse(await fs.readFile(path.join(scratch,'shadow-report.json'),'utf8'));
  await page.getByLabel('Choose shadow evaluation report',{exact:true}).setInputFiles({name:'shadow.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(report))});
  assert.equal(reportRequests,0);
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogShadowResult h4').filter({hasText:'0 of 1 cases match customer expectations'}).waitFor();
  assert.equal(reportRequests,1);
  assert.match(await page.locator('#owgCatalogShadowResult').textContent(),/Unexpected clarification/);
  assert(!(await page.locator('#owgCatalogShadowResult').textContent()).includes('PRIVATE_'));
  let procurementRequests=0;
  page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/v1/process-catalog/procurement/'))procurementRequests++;});
  const alternate={...report,results_sha256:'d'.repeat(64)};
  const candidate=(id,r)=>({candidate_id:id,provider:'provider',model:id,runtime:'cli',report:r,
    measurements:[{case_id:'case_1',cost_microunits:100,elapsed_ms:500}]});
  const procurement={format:'openworkgraph.agent-procurement.v1',baseline_candidate_id:'current',currency:'USD',
    gates:{minimum_match_basis_points:0,maximum_critical_cases:0,require_all_categories:false},
    candidates:[candidate('current',report),candidate('other',alternate)]};
  await page.getByLabel('Procurement JSON document',{exact:true}).setInputFiles({name:'procurement.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(procurement))});
  assert.equal(procurementRequests,0);
  await page.getByRole('button',{name:'Inspect procurement evidence against saved revision',exact:true}).click();
  await page.locator('#owgCatalogProcurementResult').getByText('Candidate procurement comparison',{exact:true}).waitFor();
  assert.equal(procurementRequests,1);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/current · pass/);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/other · pass/);
  assert(!(await page.locator('#owgCatalogProcurementResult').textContent()).includes('PRIVATE_'));
  await page.getByRole('button',{name:'Clear procurement review',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogProcurementResult').textContent(),'');
  const configs=['current','other'].map((id,i)=>({candidate_id:id,provider:'provider',model:id,runtime:'cli',configuration_sha256:String(i+1).repeat(64)}));
  const design={format:'openworkgraph.procurement-trial-design.v1',reference_report:report,baseline_candidate_id:'current',candidates:configs,
    repeats:2,schedule_seed:1,currency:'USD',gates:{minimum_match_basis_points:0,require_all_categories:false},confirmed:true};
  const submit=async(mode,value)=>{
    await page.getByLabel('Procurement operation',{exact:true}).selectOption(mode);
    await page.getByLabel('Procurement JSON document',{exact:true}).setInputFiles({name:'bundle.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(value))});
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-catalog/procurement/'+mode);
    await page.getByRole('button',{name:'Inspect procurement evidence against saved revision',exact:true}).click();
    const res=await response;assert.equal(res.status(),200,await res.text());
    await page.locator('#owgCatalogProcurementResult h5').first().waitFor();
    return (await res.json()).result;
  };
  const discovery=await submit('freeze',design);
  const held=JSON.parse(JSON.stringify(design));held.reference_report.suite_sha256='e'.repeat(64);
  held.reference_report.cases[0].case_sha256='f'.repeat(64);
  const holdout=await submit('freeze',held);
  function complete(p){
    return {format:'openworkgraph.procurement-trial-captures.v1',plan_sha256:p.plan_sha256,captures:configs.flatMap((c,i)=>[1,2].map(rep=>({
      candidate_id:c.candidate_id,repeat:rep,configuration_sha256:c.configuration_sha256,
      report:{...p.basis.reference_report,results_sha256:String(i*2+rep+2).repeat(64)},
      attempts:[{case_id:'case_1',status:'completed',cost_microunits:100,elapsed_ms:500}]
    })))};
  }
  const costCaptures=complete(discovery);
  await page.getByLabel('Procurement operation',{exact:true}).selectOption('review');
  await page.getByLabel('Procurement JSON document',{exact:true}).setInputFiles({name:'captures.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(costCaptures))});
  await page.getByLabel('Frozen trial plan JSON',{exact:true}).setInputFiles({name:'plan.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(discovery))});
  const pendingTrial=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-catalog/procurement/review');
  await page.getByRole('button',{name:'Inspect procurement evidence against saved revision',exact:true}).click();
  const trialRes=await pendingTrial;assert.equal(trialRes.status(),200,await trialRes.text());
  const trialReview=(await trialRes.json()).result;
  await page.locator('#owgCatalogProcurementResult h5').first().waitFor();
  const times={routine_ms:3600000,review_ms:0,rework_ms:0,fallback_ms:0,escalation_ms:0,other_direct_cost_microunits:0,observed_business_outcome:'success'};
  const categories=['normal','exception','missing_facts'];
  const actualCategory=discovery.basis.reference_report.cases[0].category;
  const portfolio={category_volumes:categories.map(category=>({category,annual_cases:category===actualCategory?100:0})),
    portfolios:[{portfolio_id:'selective',assignments:categories.map(category=>({category,candidate_id:category===actualCategory?'current':null})),
      routing_annual_microunits:0,routing_one_time_microunits:0,routing_cost_per_ai_case_microunits:0,routing_human_ms_per_ai_case:0},
      {portfolio_id:'manual-only',assignments:categories.map(category=>({category,candidate_id:null})),
      routing_annual_microunits:0,routing_one_time_microunits:0,routing_cost_per_ai_case_microunits:0,routing_human_ms_per_ai_case:0}],
    confirm_conditional_category_assignment:true,confirm_non_overlapping_routing_costs:true};
  const economicBundle={plan:discovery,captures:costCaptures,request:{
    format:'openworkgraph.procurement-operating-cost.v1',plan_sha256:discovery.plan_sha256,ledger_sha256:trialReview.ledger_sha256,currency:'USD',
    confirm_non_overlapping_cost_boundary:true,rates:Object.fromEntries(['routine','review','rework','fallback','escalation'].map(k=>[k+'_hourly_microunits',1000000])),
    manual:[1,2].map(repeat=>({case_id:'case_1',repeat,...times})),
    interventions:discovery.slots.filter(s=>s.candidate_id==='current').map(s=>({slot_id:s.slot_id,...times,routine_ms:0,review_ms:1800000})),
    scenario:{annual_cases:100,manual_annual_microunits:0,portfolio,candidate_fixed_costs:configs.map(c=>({candidate_id:c.candidate_id,annual_microunits:0,one_time_microunits:0}))}}};
  const economics=await submit('operating-cost',economicBundle);
  assert.equal(economics.manual_baseline.total_cost.lower_microunits.numerator,'2000000');
  await page.getByText('Human and AI operating cost comparison',{exact:true}).waitFor();
  assert.equal(await page.getByText('Mixed-adoption economics',{exact:true}).count(),2);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/25 adopted \/ 75 manual cases/);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/Declared outcome risk: unknown/);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/Conditional released capacity/);
  await page.getByText('Conditional multi-agent portfolios',{exact:true}).waitFor();
  assert.equal(economics.conditional_portfolios.portfolios[0].ai_cases,100);
  assert.equal(economics.conditional_portfolios.portfolios[1].first_year_savings.lower_microunits.numerator,'0');
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/100 AI-assigned \/ 0 manual cases/);
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/Retrospective case labels are not proven available before execution/);
  await page.getByLabel('Operating cost candidate',{exact:true}).selectOption('other');
  await page.getByLabel('Show incomplete operating cost evidence',{exact:true}).check();
  await page.locator('#owgCatalogProcurementResult').getByText('Showing 1–2 of 2 cost rows',{exact:true}).waitFor();
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/unbounded/);
  assert(!(await page.locator('#owgCatalogProcurementResult').textContent()).includes('PRIVATE_'));
  await page.getByRole('button',{name:'Clear procurement review',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogProcurementResult').textContent(),'');
  const routerRequest={portfolio_id:'selective',router_configuration_sha256:'a'.repeat(64),
    policy:{minimum_distinct_cases_per_positive_category:1},
    confirm_manual_fallback_on_known_router_failure:true,confirm_non_overlapping_router_measurements:true,confirmed:true};
  const routerPlan=await submit('router-freeze',{discovery:economicBundle,holdout_plan:holdout,request:routerRequest});
  await page.getByText('Frozen router rehearsal plan',{exact:true}).waitFor();
  const holdCaptures=complete(holdout);
  await page.getByLabel('Procurement operation',{exact:true}).selectOption('review');
  await page.getByLabel('Procurement JSON document',{exact:true}).setInputFiles({name:'held-captures.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(holdCaptures))});
  await page.getByLabel('Frozen trial plan JSON',{exact:true}).setInputFiles({name:'held-plan.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(holdout))});
  const pendingHeld=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-catalog/procurement/review');
  await page.getByRole('button',{name:'Inspect procurement evidence against saved revision',exact:true}).click();
  const heldRes=await pendingHeld;assert.equal(heldRes.status(),200,await heldRes.text());
  const heldReview=(await heldRes.json()).result;
  await page.locator('#owgCatalogProcurementResult h5').first().waitFor();
  const holdBundle={plan:holdout,captures:holdCaptures,request:{...economicBundle.request,
    plan_sha256:holdout.plan_sha256,ledger_sha256:heldReview.ledger_sha256}};
  const predictions={format:'openworkgraph.procurement-router-captures.v1',router_plan_sha256:routerPlan.router_plan_sha256,
    router_configuration_sha256:routerRequest.router_configuration_sha256,
    predictions:[{...routerPlan.slots[0],status:'classified',predicted_category:actualCategory,
      direct_cost_microunits:0,human_person_ms:0,elapsed_ms:100}]};
  const routed=await submit('router-review',{discovery:economicBundle,holdout:holdBundle,router_plan:routerPlan,captures:predictions});
  await page.getByText('Held-out router rehearsal review',{exact:true}).waitFor();
  assert.equal(routed.scheduled_attempts,2);assert.equal(routed.recorded_attempts,1);
  assert.equal(routed.checks.router_capture_coverage,'unknown');
  await page.getByLabel('Show router problems or unresolved predictions',{exact:true}).check();
  await page.locator('#owgCatalogProcurementResult').getByText('Showing 1–1 of 1 routing rows',{exact:true}).waitFor();
  assert.match(await page.locator('#owgCatalogProcurementResult').textContent(),/not_recorded/);
  await page.getByRole('button',{name:'Clear procurement review',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogProcurementResult').textContent(),'');
  const common={discovery_plan:discovery,discovery_captures:complete(discovery),holdout_plan:holdout};
  const nomination=await submit('nominate',{...common,request:{candidate_id:'other',nominator_alias:'a',confirmed:true,
    policy:{minimum_discovery_cases:1,minimum_holdout_cases:1,minimum_candidate_match_basis_points:0,require_all_categories:false}}});
  const bundle={...common,holdout_captures:complete(holdout),nomination};
  const assessed=await submit('assess',bundle);
  assert(assessed.eligible_for_rehearsal_review);
  await page.locator('#owgCatalogProcurementResult').getByText('Eligible for explicit rehearsal decision.',{exact:true}).waitFor();
  const receipt=await submit('decide',{...bundle,request:{decision:'select_for_substitution_rehearsal',reviewer_alias:'b',confirmed:true,
    expected_assessment_sha256:assessed.assessment_sha256,expected_nomination_sha256:assessed.nomination_sha256}});
  assert.equal(receipt.rehearsal_candidate_id,'other');
  await page.locator('#owgCatalogProcurementResult').getByText('Rehearsal decision receipt',{exact:true}).waitFor();


  await page.getByLabel('Business goal',{exact:true}).fill('Revised draft only');
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  assert.equal(await page.locator('#owgCatalogProcurementResult').textContent(),'');
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogShadowResult h4').waitFor();
  await page.getByLabel('Choose shadow evaluation report',{exact:true}).setInputFiles({name:'wrong-spec.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...report,spec_sha256:'c'.repeat(64)}))});
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  await page.getByRole('button',{name:'Inspect report against saved revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'report does not match this exact saved process specification'}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
  await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Support shadow review · r1',exact:true}).click();
  assert.equal(await page.locator('#owgCatalogShadowResult').textContent(),'');
  const checked=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
assert c.execute('SELECT COUNT(*) FROM workflow_knowledge').fetchone()[0]==1
assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
assert 'PRIVATE_' not in '\\n'.join(c.iterdump())
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
  assert.equal(checked.status,0,checked.stderr);assert.deepEqual(errors,[]);
  console.log('Shadow review browser acceptance passed: actual generated report, explicit inspection, exact saved revision, mismatch review, edit/file/reload clearing, mobile and no persistence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
