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
   assert.equal(evidenceReads,readsBeforeImport,'file import must make no evidence/network request');
   await audit(page,'#owgWorkflowExplorer','Reopened unconfirmed local shadow plan');
   await page.locator('#owgExplorerHandoffConfirmed').check();
   assert.equal(await downloadPlan.isDisabled(),false,'new human confirmation can enable local download');
   await page.locator('#owgExplorerPlan_expected').fill('Changed after import');
   assert.equal(await downloadPlan.isDisabled(),true,'editing imported criteria revokes approval');
   await audit(page,'#owgWorkflowExplorer','Edited reopened shadow plan');
   await audit(page,'#owgWorkflowExplorer','Confirmed shadow-plan handoff');
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
