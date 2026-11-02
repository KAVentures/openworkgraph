const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser WCAG and keyboard-only acceptance for the observed-workflow explorer.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-explorer-a11y-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','workflow_groupings_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const audit=async(page,selector,label)=>{
   await page.addScriptTag({content:axeSource});
   const result=await page.evaluate(async selector=>window.axe.run(document.querySelector(selector),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}),selector);
   const found=result.violations.map(v=>`${v.id} (${v.impact}): ${v.nodes.length} node(s) e.g. ${v.nodes[0].target.join(' ')}`);
   assert.deepEqual(found,[],`${label} WCAG violations:\n${found.join('\n')}`);
 };
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic explorer server exited: '+logs);
     try{await fs.access(path.join(scratch,'original-events.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Synthetic explorer server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];let writes=0,evidenceReads=0;
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&pathname.startsWith('/v1/workflow'))writes++;if(pathname==='/v1/workflow-evidence')evidenceReads++;});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=overview]').click();
   const open=page.getByRole('button',{name:'Explore workflows',exact:true});
   // Keyboard only from here: focus, Enter/Space, Tab.
   await open.focus();await page.keyboard.press('Enter');
   await page.locator('#owgExplorerStatus').filter({hasText:'candidate groups shown'}).waitFor();
   await audit(page,'#owgWorkflowExplorer','Explorer with candidate list');
   const first=page.locator('#owgExplorerCandidates button').first();
   await first.focus();await page.keyboard.press('Space');
   assert.equal(await first.getAttribute('aria-pressed'),'true');
   const node=page.locator('#owgExplorerDetail svg g[role=button]').first();
   await node.focus();assert.equal(await node.evaluate(el=>el===document.activeElement),true);
   await page.keyboard.press('Enter');
   await page.locator('#owgExplorerStepInfo').filter({hasText:/step/i}).waitFor();
   await audit(page,'#owgWorkflowExplorer','Explorer with selected variant step');
   // Functional variant comparison: exact structural alignment is a user-triggered
   // view, never a substitute for source inspection or reviewed approval.
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Compared synthetic workflow',workflow_id:'compared_fixture',revision:2,
       bundle:{
         selector:{execution_ids:['execution:compare_left','execution:compare_right'],selected_execution_count:2},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[
           {readable_steps:['Open case','Review case','Close case'],
            structural_steps:['action:open','action:review','action:close'],
            execution_count:3,execution_ids:['execution:compare_left']},
           {readable_steps:['Open case','<img src=x onerror=alert(1)>','Close case'],
            structural_steps:['action:open','action:escalate','action:close'],
            execution_count:2,execution_ids:['execution:compare_right']}
         ]}]}
       }
     });
   });
   const compareA=page.locator('#owgExplorerCompareA');
   const compareB=page.locator('#owgExplorerCompareB');
   const compare=page.getByRole('button',{name:'Compare observed variants',exact:true});
   assert.equal(await compareA.inputValue(),'0');
   assert.equal(await compareB.inputValue(),'1');
   const readsBeforeCompare=evidenceReads;
   await compare.focus();await page.keyboard.press('Enter');
   const compareRows=page.locator('#owgExplorerCompareResult tbody tr');
   assert.equal(await compareRows.count(),4,'structural LCS must preserve ordered shared and differing steps');
   assert.deepEqual(await compareRows.locator('td:first-child').allTextContents(),
     ['shared','only A','only B','shared']);
   assert.match(await page.locator('#owgExplorerCompareResult').textContent(),/2 shared structural steps/);
   assert.equal(await page.locator('#owgExplorerCompareResult img').count(),0,'untrusted step labels are text, not HTML');
   assert.match(await page.locator('#owgExplorerCompareResult').textContent(),/<img src=x onerror=alert\(1\)>/);
   assert.equal(evidenceReads,readsBeforeCompare,'comparison must not fetch source evidence');
   await audit(page,'#owgWorkflowExplorer','Compared exact observed variants');
   await compareB.selectOption('0');
   await compare.click();
   assert.match(await page.locator('#owgExplorerCompareResult').textContent(),/Choose two different/);
   await compareB.selectOption('1');
   await compare.click();
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   assert.equal(await page.locator('#owgExplorerHandoffPanel').count(),1);
   let comparedEvidenceIds='';
   await page.route('**/v1/workflow-evidence?*',route=>{
     comparedEvidenceIds=new URL(route.request().url()).searchParams.get('execution_ids');
     return route.fulfill({status:200,contentType:'application/json',
       body:JSON.stringify({selector:{execution_ids:['execution:compare_right'],selected_execution_count:1},
         structural_alignment:{high_support_steps:[]},provenance:{source_limit_reached:false}})});
   });
   const inspectB=page.getByRole('button',{name:'Inspect variant B evidence',exact:true});
   await inspectB.focus();await page.keyboard.press('Enter');
   await page.locator('#owgExplorerEvidence h4').waitFor();
   assert.equal(comparedEvidenceIds,'execution:compare_right','comparison drilldown must select only lane B refs');
   assert.equal(await page.locator('#owgExplorerHandoffPanel').count(),0,'switching lanes revokes old handoff review');
   assert.match(await page.locator('#owgExplorerStepInfo').textContent(),/Variant 2 selected from comparison/);
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   assert.match(await page.locator('#owgExplorerHandoffPanel').textContent(),/Variant 2:/);
   assert.equal(await page.getByRole('button',{name:'Copy non-executing shadow-test plan request',exact:true}).isDisabled(),true,
     'comparison and inspection cannot bypass fresh acknowledgement and human criteria');
   await page.unroute('**/v1/workflow-evidence?*');
   // A clipped exact lane or incomplete structural mapping is not compared
   // as a complete route, even if the visible prefix looks similar.
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Clipped synthetic workflow',workflow_id:'clipped_fixture',revision:1,
       bundle:{selector:{execution_ids:[],selected_execution_count:0},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[
           {readable_steps:Array.from({length:25},(_,i)=>'Step '+i),
            structural_steps:Array.from({length:25},(_,i)=>'action:step_'+i),
            execution_count:1,execution_ids:[]},
           {readable_steps:['Step 0','Step 1'],structural_steps:['action:step_0','action:step_1'],
            execution_count:1,execution_ids:[]}
         ]}]}}
     });
   });
   await compare.click();
   assert.match(await page.locator('#owgExplorerCompareResult').textContent(),/Exact structural comparison unavailable/);
   assert.equal(await page.locator('#owgExplorerCompareResult tbody tr').count(),0);
   assert.equal(evidenceReads,readsBeforeCompare+1,'refused comparison must not request evidence');
   assert.equal(writes,0,'comparison and evidence inspection must never write to workflow APIs');
   await audit(page,'#owgWorkflowExplorer','Clipped variant comparison refusal');
   // Every focusable control must be reachable by Tab and have a visible accessible name.
   const unnamed=await page.evaluate(()=>[...document.querySelectorAll('#owgWorkflowExplorer button, #owgWorkflowExplorer [role=button], #owgWorkflowExplorer input, #owgWorkflowExplorer select')]
     .filter(el=>!el.disabled&&el.getClientRects().length&&!(el.getAttribute('aria-label')||el.textContent.trim()||(el.labels&&el.labels.length)||el.getAttribute('title')||el.getAttribute('placeholder')))
     .map(el=>el.outerHTML.slice(0,120)));
   assert.deepEqual(unnamed,[]);
   const negative=await page.evaluate(()=>[...document.querySelectorAll('#owgWorkflowExplorer button, #owgWorkflowExplorer [role=button]')]
     .filter(el=>el.getClientRects().length&&el.tabIndex<0).map(el=>el.outerHTML.slice(0,120)));
   assert.deepEqual(negative,[]);
   // A period change must invalidate both a pending candidate read and the old
   // displayed map; the user must explicitly load the newly selected scope.
   const period=page.locator('#owgExplorerPeriod');
   let releaseFamilies;
   const familiesGate=new Promise(resolve=>{releaseFamilies=resolve;});
   await page.route('**/v1/workflow-evidence/families?*',async route=>{
     await familiesGate;
     await route.continue();
   });
   const pendingFamilies=page.waitForRequest(request=>new URL(request.url()).pathname==='/v1/workflow-evidence/families');
   await open.click();await pendingFamilies;
   await period.selectOption('30');
   assert.match(await page.locator('#owgExplorerStatus').textContent(),/Period changed/);
   assert.equal(await page.locator('#owgExplorerCandidates button').count(),0);
   assert.match(await page.locator('#owgExplorerDetail').textContent(),/new period/);
   const lateFamilies=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/workflow-evidence/families');
   releaseFamilies();await lateFamilies;
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await page.unroute('**/v1/workflow-evidence/families?*');
   assert.equal(await page.locator('#owgExplorerCandidates button').count(),0);
   assert.match(await page.locator('#owgExplorerStatus').textContent(),/Period changed/);
   await audit(page,'#owgWorkflowExplorer','Explorer after changing period');

   await open.click();
   await page.locator('#owgExplorerStatus').filter({hasText:'candidate groups shown'}).waitFor();
   assert((await page.locator('#owgExplorerCandidates button').count())>0);
   // An old evidence response must not be attached to a new period.
   let releaseEvidence;
   const evidenceGate=new Promise(resolve=>{releaseEvidence=resolve;});
   await page.route('**/v1/workflow-evidence?*',async route=>{
     await evidenceGate;
     await route.continue();
   });
   const pendingEvidence=page.waitForRequest(request=>new URL(request.url()).pathname==='/v1/workflow-evidence');
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await pendingEvidence;
   await period.selectOption('all');
   assert.equal(await page.locator('#owgExplorerEvidence').count(),0);
   const lateEvidence=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/workflow-evidence');
   releaseEvidence();await lateEvidence;
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await page.unroute('**/v1/workflow-evidence?*');
   assert.equal(await page.locator('#owgExplorerEvidence').count(),0);
   assert.match(await page.locator('#owgExplorerStatus').textContent(),/Period changed/);

   // Switching to a different step in the same candidate also invalidates
   // a pending inspection: selectedId alone cannot protect that race.
   await open.click();
   await page.locator('#owgExplorerStatus').filter({hasText:'candidate groups shown'}).waitFor();
   const steps=page.locator('#owgExplorerDetail svg g[role=button]');
   assert((await steps.count())>=2);
   await steps.first().click();
   const firstStepInfo=await page.locator('#owgExplorerStepInfo').textContent();
   let releaseVariant;
   const variantGate=new Promise(resolve=>{releaseVariant=resolve;});
   await page.route('**/v1/workflow-evidence?*',async route=>{
     await variantGate;
     await route.continue();
   });
   const pendingVariant=page.waitForRequest(request=>new URL(request.url()).pathname==='/v1/workflow-evidence');
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await pendingVariant;
   await steps.nth(1).click();
   assert.notEqual(await page.locator('#owgExplorerStepInfo').textContent(),firstStepInfo);
   assert.equal(await page.locator('#owgExplorerEvidence').textContent(),'');
   const lateVariant=page.waitForResponse(response=>new URL(response.url()).pathname==='/v1/workflow-evidence');
   releaseVariant();await lateVariant;
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await page.unroute('**/v1/workflow-evidence?*');
   assert.equal(await page.locator('#owgExplorerEvidence').textContent(),'');
   assert.match(await page.locator('#owgExplorerStepInfo').textContent(),/Variant [0-9]+, step [0-9]+/);
   // A reviewed group can retain candidate-wide refs even when one exact
   // variant has no valid refs. Never attribute a different variant's evidence
   // to the explicitly selected unlinked variant or copy those refs as support.
   await page.evaluate(()=>{
     window.__owgCopiedHandoff='';
     window.copyText=text=>{window.__owgCopiedHandoff=text;};
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Synthetic reviewed group',workflow_id:'synthetic',revision:1,
       bundle:{
         selector:{execution_ids:['execution:other_variant'],selected_execution_count:1},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[{
           readable_steps:['Unlinked observed step'],structural_steps:['action:unlinked'],
           execution_count:1,execution_ids:[]
         }]}]}
       }
     });
   });
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   const acknowledge=page.locator('#owgExplorerHandoffConfirmed');
   const copyInvestigation=page.getByRole('button',{name:'Copy reviewed investigation request',exact:true});
   const copyShadow=page.getByRole('button',{name:'Copy non-executing shadow-test plan request',exact:true});
   const downloadPlan=page.getByRole('button',{name:'Download non-executing shadow-plan JSON',exact:true});
   assert.equal(await copyInvestigation.isDisabled(),true,'copy must require explicit review');
   assert.equal(await copyShadow.isDisabled(),true,'shadow needs exact selected inspected variant');
   assert.equal(await downloadPlan.isDisabled(),true,'unreviewed plan must not download');
   await audit(page,'#owgWorkflowExplorer','Unconfirmed agent handoff review');
   await acknowledge.check();
   await copyInvestigation.click();
   assert.match(await page.evaluate(()=>window.__owgCopiedHandoff),/execution:other_variant/);
   assert.match(await page.evaluate(()=>window.__owgCopiedHandoff),/Use authorized read-only get_workflow_evidence/);
   assert.match(await page.evaluate(()=>window.__owgCopiedHandoff),/No complete exact variant is asserted/);
   await page.locator('#owgExplorerDetail svg g[role=button]').first().click();
   assert.equal(await page.locator('#owgExplorerHandoffPanel').count(),0,'selecting a step invalidates the old review');
   const evidenceReadsBefore=evidenceReads;
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   assert.match(await page.locator('#owgExplorerEvidence').textContent(),/No selectable execution references available/);
   assert.equal(evidenceReads,evidenceReadsBefore,'unlinked selected variant must not request unrelated candidate evidence');
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   assert.equal(await copyShadow.isDisabled(),true,'unlinked variant cannot become a shadow-plan request');
   assert.equal(await page.locator('#owgExplorerShadowPlan').count(),0,'no shadow-plan editor for an unlinked variant');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   await copyInvestigation.click();
   const unlinkedHandoff=await page.evaluate(()=>window.__owgCopiedHandoff);
   assert.match(unlinkedHandoff,/Find exact supporting executions independently/);
   assert(!unlinkedHandoff.includes('execution:other_variant'),'handoff must not attribute unrelated candidate refs to selected variant');

   // A reviewed group with exact selected refs still cannot copy a shadow plan
   // until the user inspects current bounded support and confirms the review.
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Reviewed synthetic example',workflow_id:'reviewed_test',revision:4,
       bundle:{
         selector:{execution_ids:['execution:synthetic_ref'],selected_execution_count:1},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[{
           readable_steps:['Read record','Check outcome'],structural_steps:['action:read','action:check'],
           execution_count:1,execution_ids:['execution:synthetic_ref']
         }]}]}
       }
     });
   });
   await page.locator('#owgExplorerDetail svg g[role=button]').first().click();
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   assert.equal(await copyShadow.isDisabled(),true,'reviewed group alone does not prove current source support');
   assert.equal(await downloadPlan.isDisabled(),true,'reviewed group alone cannot export a plan');
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify({selector:{execution_ids:['execution:other_ref'],selected_execution_count:1},
       structural_alignment:{high_support_steps:[]},provenance:{source_limit_reached:false}})
   }));
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   await page.locator('#owgExplorerHandoffConfirmed').check();
   assert.equal(await copyShadow.isDisabled(),true,'a successful HTTP response with unrelated refs is not inspected support');
   await page.unroute('**/v1/workflow-evidence?*');
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify({selector:{execution_ids:['execution:synthetic_ref'],selected_execution_count:1},
       structural_alignment:{high_support_steps:[]},provenance:{source_limit_reached:false}})
   }));
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   assert.equal(await copyShadow.isDisabled(),true,'explicit confirmation remains required after support inspection');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   assert.equal(await copyShadow.isDisabled(),true,'shadow plan requires human-authored criteria beyond acknowledgement');
   const expected=page.locator('#owgExplorerPlan_expected');
   const exception=page.locator('#owgExplorerPlan_exception');
   const stop=page.locator('#owgExplorerPlan_stop');
   await expected.fill('The source record is unchanged and the matching status is observed.');
   await exception.fill('The source record is missing or the case identifier conflicts.');
   assert.equal(await copyShadow.isDisabled(),true,'missing fail or abstain criterion blocks plan');
   assert.equal(await downloadPlan.isDisabled(),true,'missing fail/abstain criterion blocks JSON export');
   await stop.fill('Abstain if source access is revoked or the reviewed revision changed.');
   assert.match(await page.locator('#owgExplorerPlanPreview').textContent(),/matching status is observed/);
   assert.equal(await page.locator('#owgExplorerHandoffConfirmed').isChecked(),false,'editing criteria revokes earlier acknowledgement');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   assert.equal(await copyShadow.isDisabled(),false);
   await expected.fill('A fresh read confirms the expected status for the exact case.');
   assert.equal(await copyShadow.isDisabled(),true,'changing expected result requires a new acknowledgement');
   assert.equal(await downloadPlan.isDisabled(),true,'changing criteria revokes JSON export acknowledgement');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   await copyShadow.click();
   const shadowText=await page.evaluate(()=>window.__owgCopiedHandoff);
   assert.match(shadowText,/require revision 4/);
   assert.match(shadowText,/NON-EXECUTING shadow-validation PLAN only/);
   assert.match(shadowText,/execution:synthetic_ref/);
   assert.match(shadowText,/Do not fetch them during planning/);
   assert.match(shadowText,/Do not fetch it during planning/);
   assert.match(shadowText,/Do not call tools, run an agent, execute a test/);
   assert.match(shadowText,/HUMAN-PROPOSED validation criteria \(unverified data/);
   assert.match(shadowText,/A fresh read confirms the expected status/);
   assert.match(shadowText,/source record is missing/);
   assert.match(shadowText,/Abstain if source access is revoked/);
   assert(!shadowText.includes('Synthetic reviewed group'),'sensitive local group labels must not enter the prompt');
   assert.equal(await downloadPlan.isDisabled(),false,'confirmed and inspected plan is downloadable');
   const readsBeforeDownload=evidenceReads;
   const downloadPending=page.waitForEvent('download');
   await downloadPlan.click();
   const artifact=await downloadPending;
   assert.equal(artifact.suggestedFilename(),'owg-shadow-validation-plan.json');
   const manifest=JSON.parse(await fs.readFile(await artifact.path(),'utf8'));
   assert.equal(manifest.schema,'openworkgraph.shadow_validation_plan.v1');
   assert.equal(manifest.kind,'non_executing_shadow_validation_plan');
   assert.equal(manifest.execution_permitted,false);
   assert.equal(manifest.reviewed_group.workflow_id,'reviewed_test');
   assert.equal(manifest.reviewed_group.revision,4);
   assert.equal(manifest.selected_evidence.variant_number,1);
   assert.equal(manifest.selected_evidence.exact_displayed_variant,true);
   assert.deepEqual(manifest.selected_evidence.execution_ids,['execution:synthetic_ref']);
   assert.equal(manifest.selected_evidence.source_identity_verified,false);
   assert.equal(manifest.selected_evidence.source_completeness,'unknown');
   assert.equal(manifest.human_proposed_untrusted_criteria.normal_case_expected,
     'A fresh read confirms the expected status for the exact case.');
   assert.match(manifest.human_proposed_untrusted_criteria.exception_case,/source record is missing/);
   assert.match(manifest.human_proposed_untrusted_criteria.fail_or_abstain_rule,/Abstain if source access is revoked/);
   assert.equal(manifest.validation.status,'not_run');
   assert.equal(manifest.validation.observed_result,null);
   assert.equal(manifest.safety.allowed,'planning_only_no_tool_calls');
   assert.equal(evidenceReads,readsBeforeDownload,'local JSON export must not fetch evidence');
   assert(!JSON.stringify(manifest).includes('Read record'),'step labels must not leak into portable plan');
   assert(!JSON.stringify(manifest).includes('Reviewed synthetic example'),'private group labels must not leak');
   // Local synthetic rehearsal is an interactive, non-executing test of the
   // analyst's proposed normal/exception criteria, never a source test.
   const rehearsalRun=page.locator('#owgExplorerSyntheticRun');
   const rehearsalDownload=page.locator('#owgExplorerSyntheticDownload');
   const inventedNormal=page.locator('#owgExplorerSyntheticObserved');
   const inventedException=page.locator('#owgExplorerSyntheticException');
   const rehearsalStatus=page.locator('#owgExplorerSyntheticStatus');
   assert.equal(await rehearsalRun.isDisabled(),false);
   assert.equal(await rehearsalDownload.isDisabled(),true);
   const readsBeforeRehearsal=evidenceReads;
   await rehearsalRun.click();
   assert.match(await rehearsalStatus.textContent(),/SYNTHETIC ABSTAIN/);
   assert.equal(await rehearsalDownload.isDisabled(),false);
   await inventedNormal.fill(manifest.human_proposed_untrusted_criteria.normal_case_expected);
   assert.equal(await rehearsalDownload.isDisabled(),true,'editing synthetic input invalidates the prior receipt');
   assert.equal(await acknowledge.isChecked(),false,'synthetic input change requires new acknowledgement');
   await inventedException.selectOption('abstain');
   await acknowledge.check();
   await rehearsalRun.click();
   assert.match(await rehearsalStatus.textContent(),/SYNTHETIC PASS/);
   assert.match(await rehearsalStatus.textContent(),/No real shadow test/);
   await audit(page,'#owgWorkflowExplorer','Synthetic-only rehearsal with grade');
   const receiptPending=page.waitForEvent('download');
   await rehearsalDownload.click();
   const receiptArtifact=await receiptPending;
   assert.equal(receiptArtifact.suggestedFilename(),'owg-synthetic-shadow-rehearsal.json');
   const receipt=JSON.parse(await fs.readFile(await receiptArtifact.path(),'utf8'));
   assert.equal(receipt.schema,'openworkgraph.synthetic_shadow_rehearsal.v1');
   assert.equal(receipt.kind,'local_synthetic_rehearsal_not_source_validation');
   assert.equal(receipt.synthetic_grade,'PASS');
   assert.equal(receipt.real_shadow_test_status,'not_run');
   assert.equal(receipt.execution_permitted,false);
   assert.equal(receipt.source_identity_verified,false);
   assert.equal(receipt.plan.validation.status,'not_run');
   assert.equal(receipt.plan.reviewed_group.revision,4);
   assert.deepEqual(receipt.plan.selected_evidence.execution_ids,['execution:synthetic_ref']);
   assert(!JSON.stringify(receipt).includes('Reviewed synthetic example'));
   // A reviewer can rehearse multiple invented cases before a separate real
   // shadow validation. Aggregate FAIL > ABSTAIN > PASS, without any source call.
   const suite=page.locator('#owgExplorerSyntheticSuite');
   const suiteAdd=page.locator('#owgExplorerSuiteAdd');
   const suiteRun=page.locator('#owgExplorerSuiteRun');
   const suiteDownload=page.locator('#owgExplorerSuiteDownload');
   const suiteStatus=page.locator('#owgExplorerSuiteStatus');
   const suiteCases=page.locator('#owgExplorerSuiteCases .owg-explorer-suite-case');
   assert.equal(await suiteCases.count(),2,'two editable invented cases are available');
   assert.equal(await suiteRun.isDisabled(),false);
   assert.equal(await suiteDownload.isDisabled(),true);
   const suiteReadsBefore=evidenceReads;
   await suiteCases.nth(0).locator('textarea').fill(manifest.human_proposed_untrusted_criteria.normal_case_expected);
   await suiteCases.nth(0).locator('select').selectOption('abstain');
   await suiteCases.nth(1).locator('textarea').fill('Invented incorrect status');
   await suiteCases.nth(1).locator('select').selectOption('continue');
   assert.equal(await acknowledge.isChecked(),false,'invented suite edit revokes acknowledgement');
   await acknowledge.check();
   await suiteRun.focus();await page.keyboard.press('Enter');
   assert.match(await suiteStatus.textContent(),/SYNTHETIC SUITE FAIL/);
   assert.match(await suiteStatus.textContent(),/PASS 1, FAIL 1, ABSTAIN 0/);
   assert.equal(await suiteDownload.isDisabled(),false);
   await audit(page,'#owgWorkflowExplorer','Invented multi-case suite with grading');
   const suitePending=page.waitForEvent('download');
   await suiteDownload.click();
   const suiteArtifact=await suitePending;
   assert.equal(suiteArtifact.suggestedFilename(),'owg-synthetic-shadow-suite.json');
   const suiteReceipt=JSON.parse(await fs.readFile(await suiteArtifact.path(),'utf8'));
   assert.equal(suiteReceipt.schema,'openworkgraph.synthetic_shadow_suite.v1');
   assert.equal(suiteReceipt.synthetic_aggregate_grade,'FAIL');
   assert.deepEqual(suiteReceipt.synthetic_grade_counts,{PASS:1,FAIL:1,ABSTAIN:0});
   assert.deepEqual(suiteReceipt.invented_scenario_results.map(row=>row.synthetic_grade),['PASS','FAIL']);
   assert.equal(suiteReceipt.execution_permitted,false);
   assert.equal(suiteReceipt.source_identity_verified,false);
   assert.equal(suiteReceipt.real_shadow_test_status,'not_run');
   assert.equal(suiteReceipt.plan.validation.status,'not_run');
   assert.equal(suiteReceipt.plan.reviewed_group.revision,4);
   assert.deepEqual(suiteReceipt.plan.selected_evidence.execution_ids,['execution:synthetic_ref']);
   assert(!JSON.stringify(suiteReceipt).includes('Reviewed synthetic example'));
   // Portable scenario *template* (no grades) can be explicitly saved and
   // reopened after a fresh review, without treating a receipt as authority.
   const suiteTemplateDownload=page.locator('#owgExplorerSuiteTemplateDownload');
   assert.equal(await suiteTemplateDownload.isDisabled(),false);
   const templatePending=page.waitForEvent('download');
   await suiteTemplateDownload.focus();await page.keyboard.press('Enter');
   const templateArtifact=await templatePending;
   assert.equal(templateArtifact.suggestedFilename(),'owg-invented-shadow-scenarios.json');
   const template=JSON.parse(await fs.readFile(await templateArtifact.path(),'utf8'));
   const savedSuiteTemplateBytes=await fs.readFile(await templateArtifact.path());
   assert.equal(template.schema,'openworkgraph.synthetic_shadow_suite_template.v1');
   assert.equal(template.kind,'local_invented_scenarios_unrun');
   assert.equal(template.execution_permitted,false);
   assert.equal(template.source_identity_verified,false);
   assert.equal(template.real_shadow_test_status,'not_run');
   assert.equal(template.plan.validation.status,'not_run');
   assert.equal(template.plan.reviewed_group.revision,4);
   assert.deepEqual(template.plan.selected_evidence.execution_ids,['execution:synthetic_ref']);
   assert.equal(template.invented_scenarios.length,2);
   assert.deepEqual(template.invented_scenarios.map(row=>row.exception_decision),['abstain','continue']);
   assert(!JSON.stringify(template).includes('synthetic_grade'),'a template never imports or exports grades');
   assert(!JSON.stringify(template).includes('Reviewed synthetic example'),'private group labels must not leak');
   await suiteCases.nth(1).locator('textarea').fill(manifest.human_proposed_untrusted_criteria.normal_case_expected);
   await suiteCases.nth(1).locator('select').selectOption('unknown');
   assert.equal(await suiteDownload.isDisabled(),true,'edited case invalidates old suite receipt');
   assert.equal(await acknowledge.isChecked(),false);
   await acknowledge.check();await suiteRun.click();
   assert.match(await suiteStatus.textContent(),/SYNTHETIC SUITE ABSTAIN/);
   await suiteCases.nth(1).locator('select').selectOption('abstain');
   await acknowledge.check();await suiteRun.click();
   assert.match(await suiteStatus.textContent(),/SYNTHETIC SUITE PASS/);
   // Eight is a hard UI bound; removing cases invalidates results and a
   // Compare two explicitly run, invented-only suites without trusting the
   // imported grades or carrying forward a provider result/approval.
   const compareImport=page.locator('#owgExplorerSuiteCompareImport');
   const compareStatus=page.locator('#owgExplorerSuiteCompareStatus');
   const compareDownload=page.locator('#owgExplorerSuiteCompareDownload');
   const uploadBaseline=(value)=>compareImport.setInputFiles({
     name:'prior-synthetic-suite.json',mimeType:'application/json',
     buffer:Buffer.isBuffer(value)?value:Buffer.from(typeof value==='string'?value:JSON.stringify(value))
   });
   assert.equal(await compareDownload.isDisabled(),true,'comparison needs an explicit prior receipt');
   await uploadBaseline(template);
   await compareStatus.filter({hasText:/Not a non-executing OWG synthetic suite receipt/}).waitFor();
   await uploadBaseline('{bad');
   await compareStatus.filter({hasText:/invalid JSON/}).waitFor();
   await uploadBaseline({...suiteReceipt,execution_permitted:true});
   await compareStatus.filter({hasText:/Not a non-executing OWG synthetic suite receipt/}).waitFor();
   await uploadBaseline({...suiteReceipt,plan:{...suiteReceipt.plan,
     reviewed_group:{...suiteReceipt.plan.reviewed_group,revision:5}}});
   await compareStatus.filter({hasText:/review identity/}).waitFor();
   await uploadBaseline({...suiteReceipt,invented_scenario_results:
     suiteReceipt.invented_scenario_results.map((row,index)=>index===1?{...row,synthetic_grade:'PASS'}:row)});
   await compareStatus.filter({hasText:/grade or reasons are inconsistent/}).waitFor();
   await uploadBaseline({...suiteReceipt,synthetic_grade_counts:{PASS:2,FAIL:0,ABSTAIN:0}});
   await compareStatus.filter({hasText:/aggregate or grade counts/}).waitFor();
   await uploadBaseline({...suiteReceipt,invented_scenario_results:suiteReceipt.invented_scenario_results.slice(0,1)});
   await compareStatus.filter({hasText:/2–8 invented cases/}).waitFor();
   await uploadBaseline(Buffer.alloc(32769,65));
   await compareStatus.filter({hasText:/32 KiB/}).waitFor();
   await uploadBaseline(suiteReceipt);
   await compareStatus.filter({hasText:/SYNTHETIC COMPARISON/}).waitFor();
   assert.match(await compareStatus.textContent(),/1 changed inputs, 1 changed grades/);
   assert.match(await compareStatus.textContent(),/Baseline FAIL → current PASS/);
   assert.equal(await acknowledge.isChecked(),false,'baseline import must not restore approval');
   assert.equal(await compareDownload.isDisabled(),true,'comparison export requires fresh acknowledgement');
   await audit(page,'#owgWorkflowExplorer','Local invented suite baseline comparison');
   await acknowledge.check();
   assert.equal(await compareDownload.isDisabled(),false);
   const comparisonPending=page.waitForEvent('download');
   await compareDownload.focus();await page.keyboard.press('Enter');
   const comparisonArtifact=await comparisonPending;
   assert.equal(comparisonArtifact.suggestedFilename(),'owg-synthetic-shadow-comparison.json');
   const comparison=JSON.parse(await fs.readFile(await comparisonArtifact.path(),'utf8'));
   assert.equal(comparison.schema,'openworkgraph.synthetic_shadow_suite_comparison.v1');
   assert.equal(comparison.execution_permitted,false);
   assert.equal(comparison.source_identity_verified,false);
   assert.equal(comparison.real_shadow_test_status,'not_run');
   assert.equal(comparison.plan.reviewed_group.revision,4);
   assert.deepEqual(comparison.plan.selected_evidence.execution_ids,['execution:synthetic_ref']);
   assert.equal(comparison.invented_cases,2);
   assert.equal(comparison.invented_inputs_changed,1);
   assert.equal(comparison.synthetic_grades_changed,1);
   assert.deepEqual(comparison.per_scenario.map(row=>row.baseline_grade),['PASS','FAIL']);
   assert.deepEqual(comparison.per_scenario.map(row=>row.current_grade),['PASS','PASS']);
   assert(!JSON.stringify(comparison).includes('Invented incorrect status'),
     'comparison report excludes raw invented observations');
   assert(!JSON.stringify(comparison).includes('Reviewed synthetic example'),
     'comparison report excludes private workflow labels');
   assert.equal(evidenceReads,suiteReadsBefore,'local comparison cannot read evidence');
   assert.equal(writes,0,'local comparison cannot write business or workflow data');
   await suiteCases.nth(0).locator('textarea').fill('Changed after comparison');
   assert.equal(await compareDownload.isDisabled(),true,'scenario edit invalidates comparison');
   assert.match(await compareStatus.textContent(),/No current synthetic comparison/);
   assert.equal(await acknowledge.isChecked(),false,'edit revokes comparison acknowledgement');
   // one-case suite cannot be run or downloaded.
   for(let i=0;i<6;i++)await suiteAdd.click();
   assert.equal(await suiteCases.count(),8);
   assert.equal(await suiteAdd.isDisabled(),true);
   assert.equal(await suiteDownload.isDisabled(),true);
   for(let i=0;i<7;i++)await suiteCases.last().getByRole('button',{name:'Remove this invented scenario'}).click();
   assert.equal(await suiteCases.count(),1);
   await acknowledge.check();
   assert.equal(await suiteRun.isDisabled(),true);
   await suiteAdd.click();
   assert.equal(await suiteCases.count(),2);
   assert.equal(await acknowledge.isChecked(),false);
   assert.equal(evidenceReads,suiteReadsBefore,'local suite and receipt must not read evidence');
   assert.equal(writes,0,'local suite cannot write to workflow APIs');
   await inventedNormal.fill('Invented mismatched result');
   assert.equal(await rehearsalDownload.isDisabled(),true);
   await acknowledge.check();
   await rehearsalRun.click();
   assert.match(await rehearsalStatus.textContent(),/SYNTHETIC FAIL/);
   await inventedException.selectOption('unknown');
   assert.equal(await rehearsalDownload.isDisabled(),true);
   await acknowledge.check();
   await rehearsalRun.click();
   assert.match(await rehearsalStatus.textContent(),/SYNTHETIC ABSTAIN/);
   await stop.fill('Changed synthetic stop rule after rehearsal');
   assert.equal(await rehearsalDownload.isDisabled(),true,'editing proposed stop rule invalidates synthetic receipt');
   assert.equal(await acknowledge.isChecked(),false);
   assert.equal(evidenceReads,readsBeforeRehearsal,'rehearsal and receipt cannot fetch evidence');
   assert.equal(writes,0,'synthetic rehearsal cannot write to workflow APIs');

   // Reopen the downloaded local plan after a fresh browser load. Nothing
   // carries forward automatically: a reviewed group and fresh selected-ref
   // inspection are required before importing any proposed criteria.
   const savedPlanBytes=await fs.readFile(await artifact.path());
   await page.reload();
   await page.locator('[role=tab][data-tab=overview]').click();
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Reviewed synthetic example',workflow_id:'reviewed_test',revision:4,
       bundle:{
         selector:{execution_ids:['execution:synthetic_ref'],selected_execution_count:1},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[{
           readable_steps:['Read record','Check outcome'],structural_steps:['action:read','action:check'],
           execution_count:1,execution_ids:['execution:synthetic_ref']
         }]}]}
       }
     });
   });
   await page.locator('#owgExplorerDetail svg g[role=button]').first().click();
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await page.getByRole('button',{name:'Review safe agent handoff',exact:true}).click();
   const imported=page.locator('#owgExplorerPlanImport');
   const importMessage=page.locator('#owgExplorerPlanImportStatus');
   const upload=(value)=>imported.setInputFiles({
     name:'shadow-plan.json',mimeType:'application/json',
     buffer:Buffer.isBuffer(value)?value:Buffer.from(typeof value==='string'?value:JSON.stringify(value))
   });
   assert.equal(await downloadPlan.isDisabled(),true,'browser reload cannot restore prior acknowledgement');
   const readsBeforeImport=evidenceReads;
   await upload(receipt);
   await importMessage.filter({hasText:/Not an unexecuted OWG shadow-plan v1 proposal/}).waitFor();
   assert.equal(await page.locator('#owgExplorerPlan_expected').inputValue(),'',
     'a synthetic receipt cannot be imported as an executable or approved plan');
   await upload('{invalid');
   await importMessage.filter({hasText:/invalid JSON/}).waitFor();
   await upload({...manifest,execution_permitted:true});
   await importMessage.filter({hasText:/Plan refused/}).waitFor();
   await upload({...manifest,validation:{...manifest.validation,status:'passed'}});
   await importMessage.filter({hasText:/Plan refused/}).waitFor();
   await upload({...manifest,reviewed_group:{...manifest.reviewed_group,revision:5}});
   await importMessage.filter({hasText:/review identity/}).waitFor();
   await upload({...manifest,selected_evidence:{...manifest.selected_evidence,execution_ids:['execution:wrong']}});
   await importMessage.filter({hasText:/execution references differ/}).waitFor();
   await upload({...manifest,selected_evidence:{...manifest.selected_evidence,window_utc:{since:'2000-01-01T00:00:00Z'}}});
   await importMessage.filter({hasText:/period differs/}).waitFor();
   await upload(Buffer.alloc(32769,65));
   await importMessage.filter({hasText:/32 KiB/}).waitFor();
   assert.equal(await page.locator('#owgExplorerPlan_expected').inputValue(),'',
     'refused plans must not populate criteria');
   await upload(savedPlanBytes);
   await importMessage.filter({hasText:/restored locally/}).waitFor();
   assert.equal(await page.locator('#owgExplorerPlan_expected').inputValue(),
     manifest.human_proposed_untrusted_criteria.normal_case_expected);
   assert.equal(await page.locator('#owgExplorerPlan_exception').inputValue(),
     manifest.human_proposed_untrusted_criteria.exception_case);
   assert.equal(await page.locator('#owgExplorerPlan_stop').inputValue(),
     manifest.human_proposed_untrusted_criteria.fail_or_abstain_rule);
   assert.equal(await page.locator('#owgExplorerHandoffConfirmed').isChecked(),false,
     'import must never carry forward an approval');
   assert.equal(await downloadPlan.isDisabled(),true);
   const templateImport=page.locator('#owgExplorerSuiteTemplateImport');
   const templateMessage=page.locator('#owgExplorerSuiteTemplateStatus');
   const uploadTemplate=(value)=>templateImport.setInputFiles({
     name:'invented-scenarios.json',mimeType:'application/json',
     buffer:Buffer.isBuffer(value)?value:Buffer.from(typeof value==='string'?value:JSON.stringify(value))
   });
   const restoredCases=page.locator('#owgExplorerSuiteCases .owg-explorer-suite-case');
   await uploadTemplate(suiteReceipt);
   await templateMessage.filter({hasText:/Not an unexecuted OWG invented-scenario template/}).waitFor();
   await uploadTemplate('{bad');
   await templateMessage.filter({hasText:/invalid JSON/}).waitFor();
   await uploadTemplate({...template,execution_permitted:true});
   await templateMessage.filter({hasText:/Template refused/}).waitFor();
   await uploadTemplate({...template,plan:{...template.plan,reviewed_group:{...template.plan.reviewed_group,revision:5}}});
   await templateMessage.filter({hasText:/review identity/}).waitFor();
   await uploadTemplate({...template,invented_scenarios:[...template.invented_scenarios,
     {...template.invented_scenarios[0],exception_decision:'execute'}]});
   await templateMessage.filter({hasText:/Invented scenario 3/}).waitFor();
   await uploadTemplate({...template,synthetic_aggregate_grade:'PASS'});
   await templateMessage.filter({hasText:/Not an unexecuted OWG invented-scenario template/}).waitFor();
   await uploadTemplate(Buffer.alloc(32769,65));
   await templateMessage.filter({hasText:/32 KiB/}).waitFor();
   await page.locator('#owgExplorerPlan_expected').fill('Edited proposed result');
   await uploadTemplate(template);
   await templateMessage.filter({hasText:/criteria differ/}).waitFor();
   await page.locator('#owgExplorerPlan_expected').fill(
     manifest.human_proposed_untrusted_criteria.normal_case_expected);
   assert.equal(await restoredCases.count(),2);
   await uploadTemplate(savedSuiteTemplateBytes);
   await templateMessage.filter({hasText:/2 invented scenarios restored locally/}).waitFor();
   assert.equal(await restoredCases.count(),2);
   assert.equal(await restoredCases.nth(0).locator('textarea').inputValue(),
     template.invented_scenarios[0].normal_observation);
   assert.equal(await restoredCases.nth(1).locator('textarea').inputValue(),
     template.invented_scenarios[1].normal_observation);
   assert.equal(await restoredCases.nth(1).locator('select').inputValue(),'continue');
   assert.equal(await page.locator('#owgExplorerHandoffConfirmed').isChecked(),false);
   assert.equal(await suiteDownload.isDisabled(),true,'import cannot restore a prior grade or receipt');
   assert.equal(await suiteRun.isDisabled(),true,'import cannot restore approval');
   await audit(page,'#owgWorkflowExplorer','Reopened invented scenario template before approval');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   await suiteRun.focus();await page.keyboard.press('Enter');
   assert.match(await suiteStatus.textContent(),/SYNTHETIC SUITE FAIL/);
   assert.equal(await suiteDownload.isDisabled(),false,'new explicit review and rerun create a new local result');
   assert.equal(evidenceReads,readsBeforeImport,'file imports and local suite rerun must make no evidence/network request');
   await audit(page,'#owgWorkflowExplorer','Reopened unconfirmed local shadow plan');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   assert.equal(await downloadPlan.isDisabled(),false,'new human confirmation can enable local download');
   await page.locator('#owgExplorerPlan_expected').fill('Changed after import');
   assert.equal(await downloadPlan.isDisabled(),true,'editing imported criteria revokes approval');
   await audit(page,'#owgWorkflowExplorer','Edited reopened shadow plan');
   await audit(page,'#owgWorkflowExplorer','Confirmed shadow-plan handoff');

   // Vertical product journey: exact observed variant -> fresh bounded evidence
   // -> explicit human design review -> existing UNSAVED process catalog editor.
   // It never creates a process or executes an agent merely by navigating.
   await page.unroute('**/v1/workflow-evidence?*');
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Design review fixture',workflow_id:'design_fixture',revision:3,
       bundle:{selector:{execution_ids:['execution:design_ref'],selected_execution_count:1},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[{
           readable_steps:['Read case','Check exception'],
           structural_steps:['action:read','action:check'],
           execution_count:4,execution_ids:['execution:design_ref']
         }]}]}}
     });
   });
   await page.locator('#owgExplorerDetail svg g[role=button]').first().click();
   const openDesign=page.getByRole('button',{name:'Review variant for process design',exact:true});
   const continueDesign=page.getByRole('button',{name:'Continue to unsaved process catalog draft',exact:true});
   await openDesign.focus();await page.keyboard.press('Enter');
   assert.equal(await continueDesign.isDisabled(),true,'a reviewed group alone cannot bypass fresh evidence inspection');
   assert.match(await page.locator('#owgExplorerDesignStatus').textContent(),/Inspect this variant/);
   const readsBeforeDesign=evidenceReads;
   const designResponse=(ids,limited=false)=>({selector:{execution_ids:ids,selected_execution_count:ids.length},
     structural_alignment:{high_support_steps:[]},provenance:{source_limit_reached:limited}});
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify(designResponse(['execution:unrelated']))
   }));
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   assert.equal(await page.locator('#owgExplorerDesignPanel').count(),0,'new inspection revokes design review');
   await openDesign.click();
   assert.match(await page.locator('#owgExplorerDesignStatus').textContent(),/does not exactly match/);
   assert.equal(await continueDesign.isDisabled(),true);
   await page.unroute('**/v1/workflow-evidence?*');
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify(designResponse(['execution:design_ref'],true))
   }));
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await openDesign.click();
   assert.match(await page.locator('#owgExplorerDesignStatus').textContent(),/Source limit or truncation/);
   assert.equal(await continueDesign.isDisabled(),true);
   await page.unroute('**/v1/workflow-evidence?*');
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify(designResponse(['execution:design_ref']))
   }));
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await openDesign.click();
   assert.equal(await continueDesign.isDisabled(),true,'current evidence still requires human confirmation');
   assert.match(await page.locator('#owgExplorerDesignPanel').textContent(),/Source identity, total coverage/);
   await audit(page,'#owgWorkflowExplorer','Explicit source-backed design review');
   await page.locator('#owgExplorerDesignConfirmed').check();
   assert.equal(await continueDesign.isDisabled(),false);
   // Portable, fingerprint-only design review: reopening never restores consent,
   // source inspection, or human approval, even when the same revision matches.
   const checkpointDownload=page.getByRole('button',{name:'Download reviewed design checkpoint',exact:true});
   assert.equal(await checkpointDownload.isDisabled(),false);
   const checkpointPending=page.waitForEvent('download');
   await checkpointDownload.focus();await page.keyboard.press('Enter');
   const checkpointArtifact=await checkpointPending;
   const checkpointBytes=await fs.readFile(await checkpointArtifact.path());
   const checkpoint=JSON.parse(checkpointBytes.toString('utf8'));
   assert.equal(checkpoint.schema,'openworkgraph.observed_design_checkpoint.v1');
   assert.equal(checkpoint.execution_permitted,false);
   assert.equal(checkpoint.source_identity_verified,false);
   assert.equal(checkpoint.process_saved,false);
   assert.equal(checkpoint.approval_restored_on_import,false);
   assert.equal(checkpoint.reviewed_group.revision,3);
   assert.match(checkpoint.selected_evidence.fingerprint_sha256,/^[0-9a-f]{64}$/);
   assert.equal(checkpointBytes.includes(Buffer.from('execution:design_ref')),false);
   assert.equal(checkpointBytes.includes(Buffer.from('Read case')),false);
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Design review fixture',workflow_id:'design_fixture',revision:3,
       bundle:{selector:{execution_ids:['execution:design_ref'],selected_execution_count:1},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[{
           readable_steps:['Read case','Check exception'],
           structural_steps:['action:read','action:check'],
           execution_count:4,execution_ids:['execution:design_ref']
         }]}]}}
     });
   });
   await page.locator('#owgExplorerDetail svg g[role=button]').first().click();
   await openDesign.click();
   assert.equal(await continueDesign.isDisabled(),true);
   assert.equal(await page.locator('#owgExplorerDesignConfirmed').isDisabled(),true);
   const checkpointInput=page.locator('#owgExplorerDesignCheckpointImport');
   const uploadCheckpoint=value=>checkpointInput.setInputFiles({
     name:'observed-design-checkpoint.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(value))
   });
   const readsBeforeCheckpointImport=evidenceReads;
   await uploadCheckpoint({...checkpoint,execution_permitted:true});
   await page.locator('#owgExplorerDesignCheckpointStatus').filter({hasText:/Not a safe, unexecuted/}).waitFor();
   await uploadCheckpoint({...checkpoint,reviewed_group:{...checkpoint.reviewed_group,revision:99}});
   await page.locator('#owgExplorerDesignCheckpointStatus').filter({hasText:/revision, variant or evidence scope differs/}).waitFor();
   await uploadCheckpoint({...checkpoint,selected_evidence:{
     ...checkpoint.selected_evidence,fingerprint_sha256:'0'.repeat(64)}});
   await page.locator('#owgExplorerDesignCheckpointStatus').filter({hasText:/fingerprint differs/}).waitFor();
   await checkpointInput.setInputFiles({
     name:'oversized-checkpoint.json',mimeType:'application/json',buffer:Buffer.alloc(16385,65)
   });
   await page.locator('#owgExplorerDesignCheckpointStatus').filter({hasText:/16 KiB limit/}).waitFor();
   await checkpointInput.setInputFiles({
     name:'observed-design-checkpoint.json',mimeType:'application/json',buffer:checkpointBytes
   });
   await page.locator('#owgExplorerDesignCheckpointStatus').filter({hasText:/Checkpoint matches/}).waitFor();
   assert.equal(evidenceReads,readsBeforeCheckpointImport,'import makes no background evidence read');
   assert.equal(await continueDesign.isDisabled(),true,'matching checkpoint never restores approval');
   await audit(page,'#owgWorkflowExplorer','Reopened unapproved design checkpoint');
   await page.getByRole('button',{name:'Review selected examples',exact:true}).click();
   await page.locator('#owgExplorerEvidence h4').waitFor();
   await openDesign.click();
   assert.equal(await continueDesign.isDisabled(),true,'fresh inspection still needs new review');
   await page.locator('#owgExplorerDesignConfirmed').check();
   assert.equal(await continueDesign.isDisabled(),false);
   const writesBeforeDesign=writes;
   await continueDesign.focus();await page.keyboard.press('Enter');
   assert.equal(writes,writesBeforeDesign,'catalog draft navigation is not a process save or business write');
   assert.match(await page.locator('#owgCatalogEditor').textContent(),/Review process · unsaved observed proposal/);
   assert.match(await page.locator('#owgCatalogStatus').textContent(),/Unsaved proposal from a bounded observation/);
   assert.equal(evidenceReads,readsBeforeDesign+4,'four explicit evidence reads; checkpoint import makes none');
   await page.locator('[role=tab][data-tab=overview]').click();
   await page.locator('#owgExplorerDetail svg g[role=button]').last().click();
   assert.equal(await page.locator('#owgExplorerDesignPanel').count(),0,'switching steps invalidates design approval');
   await openDesign.click();
   assert.equal(await continueDesign.isDisabled(),true,'another selection needs another inspection');
   await audit(page,'#owgWorkflowExplorer','Design review after selection change');
   // Two-sided comparison → separate exact source reads → explicit target → unsaved catalog.
   await page.evaluate(()=>{
     window.owgWorkflowExplorer.showReviewedGroup({
       label:'Two-sided design fixture',workflow_id:'compared_design',revision:6,
       bundle:{selector:{execution_ids:['execution:design_A','execution:design_B'],selected_execution_count:2},
         structural_alignment:{structural_sequence_variants:[{exact_variants:[
           {readable_steps:['Read A record','Check A exception','Close A'],
            structural_steps:['action:read','action:check','action:close'],
            execution_count:7,execution_ids:['execution:design_A']},
           {readable_steps:['Read B record','Escalate B','Close B'],
            structural_steps:['action:read','action:escalate','action:close'],
            execution_count:5,execution_ids:['execution:design_B']}
         ]}]}}
     });
     window.__owgComparedDrafts=[];
     const original=window.owgProcessCatalog.draftObserved;
     window.owgProcessCatalog.draftObserved=value=>{
       window.__owgComparedDrafts.push(value);return original(value);
     };
   });
   await compare.click();
   const target=page.locator('#owgExplorerCompareTarget');
   const twoSidedCheck=page.locator('#owgExplorerCompareConfirmed');
   const twoSidedDraft=page.getByRole('button',{name:'Open chosen variant as unsaved catalog draft',exact:true});
   const alternativeCheck=page.locator('#owgExplorerCompareAlternativeConfirmed');
   const alternativeDraft=page.getByRole('button',{name:'Open AS-IS and observed alternative as unsaved TO-BE draft',exact:true});
   const inspectA=page.getByRole('button',{name:'Inspect variant A evidence',exact:true});
   const inspectB2=page.getByRole('button',{name:'Inspect variant B evidence',exact:true});
   assert.equal(await target.isDisabled(),true,'comparison alone cannot choose a design');
   assert.equal(await twoSidedDraft.isDisabled(),true);
   assert.equal(await alternativeCheck.isDisabled(),true,'TO-BE hypothesis requires two source inspections');
   assert.equal(await alternativeDraft.isDisabled(),true);
   const readsBeforeTwoSided=evidenceReads;
   await page.route('**/v1/workflow-evidence?*',route=>route.fulfill({
     status:200,contentType:'application/json',
     body:JSON.stringify({selector:{execution_ids:['execution:wrong'],selected_execution_count:1},
       provenance:{source_limit_reached:false},structural_alignment:{high_support_steps:[]}})
   }));
   await inspectA.focus();await page.keyboard.press('Enter');
   await page.locator('#owgExplorerCompareEvidenceA').filter({hasText:/Evidence incomplete/}).waitFor();
   assert.equal(await target.isDisabled(),true,'unrelated source refs cannot unlock draft');
   await page.unroute('**/v1/workflow-evidence?*');
   const seenComparedIds=[];
   await page.route('**/v1/workflow-evidence?*',route=>{
     const ids=new URL(route.request().url()).searchParams.get('execution_ids');
     seenComparedIds.push(ids);
     return route.fulfill({status:200,contentType:'application/json',
       body:JSON.stringify({selector:{execution_ids:[ids],selected_execution_count:1},
         provenance:{source_limit_reached:false,truncated:false},
         structural_alignment:{high_support_steps:[]}})});
   });
   await compare.click();
   await inspectA.click();
   await page.locator('#owgExplorerCompareEvidenceA').filter({hasText:/Inspected 1 exact/}).waitFor();
   assert.equal(await target.isDisabled(),true,'one-sided inspection is insufficient');
   await inspectB2.click();
   await page.locator('#owgExplorerCompareEvidenceB').filter({hasText:/Inspected 1 exact/}).waitFor();
   assert.deepEqual(seenComparedIds,['execution:design_A','execution:design_B']);
   await audit(page,'#owgWorkflowExplorer','Two independently inspected compared variants');
   await target.selectOption('B');
   assert.equal(await twoSidedDraft.isDisabled(),true,'target choice is not approval');
   await twoSidedCheck.check();
   assert.equal(await twoSidedDraft.isDisabled(),false);
   assert.equal(await alternativeDraft.isDisabled(),true,'separate TO-BE acknowledgement is required');
   await alternativeCheck.check();
   assert.equal(await alternativeDraft.isDisabled(),false);
   await inspectA.click();
   assert.equal(await alternativeDraft.isDisabled(),true,'reinspection revokes the alternative proposal');
   assert.equal(await alternativeCheck.isDisabled(),true);
   await page.locator('#owgExplorerCompareEvidenceA').filter({hasText:/Inspected 1 exact/}).waitFor();
   assert.equal(await target.isDisabled(),true,'a third inspection restarts the two-sided review');
   await inspectB2.click();
   await page.locator('#owgExplorerCompareEvidenceB').filter({hasText:/Inspected 1 exact/}).waitFor();
   await target.selectOption('A');await twoSidedCheck.check();
   await compareB.selectOption('0');
   assert.equal(await twoSidedDraft.count(),0,'reconfiguring lanes destroys stale approval controls');
   await compareB.selectOption('1');await compare.click();
   await inspectA.click();
   await page.locator('#owgExplorerCompareEvidenceA').filter({hasText:/Inspected 1 exact/}).waitFor();
   await inspectB2.click();
   await page.locator('#owgExplorerCompareEvidenceB').filter({hasText:/Inspected 1 exact/}).waitFor();
   await target.selectOption('A');await twoSidedCheck.check();
   const writesBeforeDraft=writes;
   page.once('dialog',dialog=>dialog.accept());
   await twoSidedDraft.focus();await page.keyboard.press('Enter');
   assert.equal(writes,writesBeforeDraft,'catalog draft navigation must not POST or execute');
   const drafts=await page.evaluate(()=>window.__owgComparedDrafts);
   assert.equal(drafts.length,1);
   assert.deepEqual(drafts[0].evidenceRefs,['execution:design_A'],'B refs must not leak into A draft');
   assert.deepEqual(drafts[0].path,['Read A record','Check A exception','Close A']);
   assert.equal(drafts[0].count,7);
   assert.match(await page.locator('#owgCatalogEditor').textContent(),/Read A record → Check A exception → Close A/);
   assert.equal(await twoSidedDraft.isDisabled(),true,'confirmation is consumed');
   assert.equal(evidenceReads,readsBeforeTwoSided+7,'no implicit reads in comparison or draft');
   await page.locator('[role=tab][data-tab=overview]').click();
   // A distinct, explicitly reviewed observed alternative becomes editable TO-BE,
   // while the chosen AS-IS lane and only its refs remain the baseline.
   await target.selectOption('B');
   assert.equal(await alternativeDraft.isDisabled(),true);
   await twoSidedCheck.check();
   assert.equal(await alternativeDraft.isDisabled(),true,'AS-IS approval alone cannot propose TO-BE');
   await alternativeCheck.check();
   assert.equal(await alternativeDraft.isDisabled(),false);
   const readsBeforeAlternative=evidenceReads;
   page.once('dialog',dialog=>dialog.accept());
   await alternativeDraft.focus();await page.keyboard.press('Enter');
   assert.equal(writes,writesBeforeDraft,'opening editable TO-BE draft must not save or execute');
   assert.equal(evidenceReads,readsBeforeAlternative,'catalog navigation must not re-read evidence');
   const draftsWithAlternative=await page.evaluate(()=>window.__owgComparedDrafts);
   assert.equal(draftsWithAlternative.length,2);
   assert.deepEqual(draftsWithAlternative[1].path,['Read B record','Escalate B','Close B']);
   assert.deepEqual(draftsWithAlternative[1].proposedPath,['Read A record','Check A exception','Close A']);
   assert.deepEqual(draftsWithAlternative[1].evidenceRefs,['execution:design_B'],
     'only the chosen AS-IS lane may provide catalog evidence refs');
   const editor=await page.locator('#owgCatalogEditor').textContent();
   assert.match(editor,/Read B record → Escalate B → Close B/,'baseline remains chosen AS-IS');
   assert.match(editor,/Read A record/,'editable proposed activities come from other lane');
   assert.match(await page.locator('#owgCatalogStatus').textContent(),/TO-BE activities.*not verified policy/i);
   assert.equal(await alternativeDraft.isDisabled(),true,'alternative acknowledgement is consumed');

   // Functional human clarification: actual process-model edit, reviewed save,
   // then reopen the same revision in a fresh browser session.
   const differencePanel=page.locator('#owgCatalogDifferenceReview');
   await differencePanel.waitFor();
   assert.match(await differencePanel.textContent(),/6 unmatched ordered steps/);
   assert.equal(await differencePanel.locator('fieldset').count(),6);
   const repeated=await page.evaluate(()=>window.__owgProcessCatalogModel.designPathDifferences(
     ['A','B','A'],[{label:'A'},{label:'A'},{label:'B'}]));
   assert.deepEqual(repeated,[{kind:'as_is_only',label:'B',position:2},
     {kind:'to_be_only',label:'B',position:3}],
     'repeated labels are aligned in order, not deduplicated into a set');
   await audit(page,'#owgCatalogDifferenceReview','Human AS-IS to TO-BE clarification');
   let catalogWrites=0,savedCatalog;
   page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/v1/process-catalog')catalogWrites++;});
   const addGates=page.getByRole('button',{name:'Add selected human review gates',exact:true});
   await addGates.click();
   assert.match(await page.locator('#owgCatalogDifferenceStatus').textContent(),/Choose at least one/);
   await page.locator('#owgCatalogDifferenceConfirm0').check();
   await addGates.click();
   assert.match(await page.locator('#owgCatalogDifferenceStatus').textContent(),/Every selected gate needs/);
   await page.locator('#owgCatalogDifferenceActivity0').selectOption('step_1');
   await page.locator('#owgCatalogDifferenceKind0').selectOption('decision_point');
   await page.locator('#owgCatalogDifferenceResponse0').selectOption('pause_for_review');
   await page.locator('#owgCatalogDifferenceLabel0').fill('Invalid:label');
   await addGates.click();
   assert.match(await page.locator('#owgCatalogDifferenceStatus').textContent(),/valid 1–64 character label/);
   await page.locator('#owgCatalogDifferenceLabel0').fill('Review missing source step');
   await page.locator('#owgCatalogEditor').getByLabel('Business goal').fill('Clarify the observed process difference');
   await addGates.click();
   assert.match(await page.locator('#owgCatalogDifferenceStatus').textContent(),/process or review changed/);
   await page.getByRole('button',{name:'Refresh graph preview',exact:true}).click();
   await page.locator('#owgCatalogDifferenceConfirm0').check();
   await page.locator('#owgCatalogDifferenceActivity0').selectOption('step_1');
   await page.locator('#owgCatalogDifferenceKind0').selectOption('decision_point');
   await page.locator('#owgCatalogDifferenceResponse0').selectOption('pause_for_review');
   await page.locator('#owgCatalogDifferenceLabel0').fill('Review missing source step');
   await addGates.focus();await page.keyboard.press('Enter');
   await page.locator('#owgCatalogStatus').filter({hasText:/Added 1 unverified human-only/}).waitFor();
   assert.equal(catalogWrites,0,'clarification never saves automatically');
   assert.equal(await page.locator('#owgCatalogEditor').getByLabel('Condition label 1').inputValue(),'Review missing source step');
   await page.locator('#owgCatalogDifferenceConfirm0').check();
   await page.locator('#owgCatalogDifferenceActivity0').selectOption('step_1');
   await page.locator('#owgCatalogDifferenceKind0').selectOption('decision_point');
   await page.locator('#owgCatalogDifferenceResponse0').selectOption('pause_for_review');
   await page.locator('#owgCatalogDifferenceLabel0').fill('Review missing source step');
   await addGates.click();
   assert.match(await page.locator('#owgCatalogDifferenceStatus').textContent(),/Duplicate clarification gate/);
   assert.equal(await page.locator('#owgCatalogEditor').getByLabel('Condition label 1').count(),1);
   await page.route('**/v1/process-catalog',async route=>{
     if(route.request().method()!=='POST')return route.continue();
     const body=route.request().postDataJSON();
     savedCatalog={...body,workflow_id:'reviewed_difference_fixture',revision:1,content_sha256:'f'.repeat(64)};
     await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(savedCatalog)});
   });
   await page.locator('#owgCatalogReviewed').check();
   await page.getByRole('button',{name:'Save new revision',exact:true}).click();
   await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1'}).waitFor();
   assert.equal(catalogWrites,1,'one separately reviewed save request');
   assert.deepEqual(savedCatalog.process_model.manual_review_conditions,[{
     activity_id:'step_1',kind:'decision_point',condition_label:'Review missing source step',
     response:'pause_for_review',source:'user_declared_unverified',evaluated_by_owg:false,
     automatic_branch_authorized:false,executable:false}]);
   assert.deepEqual(savedCatalog.evidence_refs,['execution:design_B'],
     'alternative lane evidence is never borrowed');
   assert.equal(savedCatalog.process_model.execution_authorized,false);
   const reopened=await browser.newPage();
   reopened.on('pageerror',error=>errors.push(error.message));
   await reopened.route('**/v1/process-catalog/revision?*',route=>{
     const q=new URL(route.request().url()).searchParams;
     assert.equal(q.get('workflow_id'),'reviewed_difference_fixture');
     assert.equal(q.get('revision'),'1');
     return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(savedCatalog)});
   });
   try{
     await reopened.goto(base+'/#session='+session);
     await reopened.locator('#owgProcessCatalog').waitFor();
     await reopened.evaluate(()=>window.owgProcessCatalog.openSaved({
       workflow_id:'reviewed_difference_fixture',revision:1,content_sha256:'f'.repeat(64)}));
     assert.equal(await reopened.locator('#owgCatalogEditor').getByLabel('Condition label 1').inputValue(),'Review missing source step');
     assert.match(await reopened.locator('#owgCatalogStatus').textContent(),/Opened immutable revision 1/);
     await reopened.locator('[role=tab][data-tab=evidence]').click();
     await audit(reopened,'#owgCatalogDifferenceReview','Reopened human clarification');
   }finally{await reopened.close();}
   const overflow=await browser.newPage();
   overflow.on('pageerror',error=>errors.push(error.message));
   try{
     await overflow.goto(base+'/#session='+session);
     await overflow.locator('#owgProcessCatalog').waitFor();
     await overflow.evaluate(()=>window.owgProcessCatalog.draftObserved({
       basis:'selected_observed_variant',count:1,
       path:Array.from({length:14},(_,i)=>'AS-IS '+i),
       proposedPath:Array.from({length:14},(_,i)=>'TO-BE '+i),
       evidenceRefs:[]}));
     assert.match(await overflow.locator('#owgCatalogDifferenceReview').textContent(),
       /More than 12 unmatched steps/);
     assert.equal(await overflow.getByRole('button',{name:'Add selected human review gates'}).count(),0,
       'over-limit differences must not create a misleading partial ledger');
     await audit(overflow,'#owgCatalogDifferenceReview','Over-limit clarification refusal');
   }finally{await overflow.close();}

   await page.locator('[role=tab][data-tab=overview]').click();
   await audit(page,'#owgWorkflowExplorer','Two-sided AS-IS and proposed TO-BE catalog handoff');
   await page.unroute('**/v1/workflow-evidence?*');
   await period.selectOption('7');
   assert.equal(await page.locator('#owgExplorerHandoffPanel').count(),0,'period change invalidates reviewed copy and JSON download');
   assert.equal(writes,0,'handoff flow never writes to workflow APIs');
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
   assert.equal(writes,0);assert.deepEqual(errors,[]);
   console.log('Explorer accessibility acceptance passed: WCAG 2.1 A/AA axe audit of candidates and selected step, keyboard-only open/select/inspect, named and tabbable controls, mobile width, no writes.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
