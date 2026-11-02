const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {createHash}=require('node:crypto');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: add an idea → assess → approve with a note → implemented, CSV export, stale-edit refusal.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pipeline-'));
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
   const context=await browser.newContext({acceptDownloads:true});
   const page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgPipelineCard').waitFor();
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();

   await page.locator('#owgPipelineTitle').fill('Copy approved prices into renewals');
   await page.locator('#owgPipelineSource').selectOption('process_catalog');await page.locator('#owgPipelineReference').fill('Renewals r3');
   await page.locator('#owgPipelineAdd').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Idea added.'}).waitFor();
   await page.getByRole('button',{name:'Move to Assessing'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   assert.equal(await page.getByRole('button',{name:'Move to Approved'}).count(),1);
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'assess the idea before approving it'}).waitFor();

   for(const key of ['rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate'])
     await page.locator('#owgPipelineQ_'+key).selectOption('4');
   await page.locator('#owgPipelineCases').fill('300');await page.locator('#owgPipelineMinutes').fill('10');await page.locator('#owgPipelineShare').fill('50');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Assessment saved.'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Ease 75\/100 · 25 modeled hours per month · Quick win/);
   assert.match(await page.locator('#owgPipelineList').textContent(),/Quick win/);

   await page.locator('#owgPipelineSearch').fill('RENEWALS r3');
   await page.locator('#owgPipelineViewCount').filter({hasText:'1 shown of 1'}).waitFor();
   await page.locator('#owgPipelineQuadrant').selectOption('unassessed');
   await page.getByText('No ideas in this view.',{exact:true}).waitFor();
   await page.locator('#owgPipelineViewCount').filter({hasText:'0 shown of 1'}).waitFor();
   await page.locator('#owgPipelineReset').click();
   await page.locator('#owgPipelineViewCount').filter({hasText:'1 shown of 1'}).waitFor();
   assert.equal(await page.locator('#owgPipelineSearch').inputValue(),'');
   await page.locator('#owgPipelineSort').selectOption('impact');
   assert.equal(await page.getByRole('button',{name:'Open idea Copy approved prices into renewals'}).count(),1);

   // A second tab edits the same idea; the stale page must be refused, not overwrite it.
   const ideaId=(await (await page.evaluate(async()=>{const r=await fetch('/v1/automation-pipeline');return r.json();})).ideas[0]).idea_id;
   await page.evaluate(async id=>{const r=await fetch('/v1/automation-pipeline/'+id);const idea=await r.json();
     await fetch('/v1/automation-pipeline/'+id+'/transition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to:'on_hold',expected_revision:idea.revision})});},ideaId);
   await page.locator('#owgPipelineNote').fill('Owner agreed');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'reload it before editing'}).waitFor();
   await page.getByRole('button',{name:'Open idea Copy approved prices into renewals'}).click();
   await page.getByRole('button',{name:'Move to Assessing'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   await page.locator('#owgPipelineNote').fill('Owner agreed');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   const history=await page.locator('#owgPipelineDetail ol').textContent();
   assert.match(history,/Idea.*Assessing.*On hold.*Assessing.*Approved.*Owner agreed/s);
   assert.match(await page.locator('#owgPipelineCard').textContent(),/nothing is run, built or sent/);

   const [csv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPipelineCsv').click()]);
   const text=await fs.readFile(await csv.path(),'utf8');
   assert.match(text,/^idea_id,title,state,/);assert.match(text,/Copy approved prices into renewals,approved,process_catalog,Renewals r3,75,25\.0,quick_win/);

   // Reassessment must invalidate the old approval and prevent implementation
   // until the new assumptions have been reviewed. Inspect both saved bases.
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Saving an assessment clears this approval/);
   await page.locator('#owgPipelineMinutes').fill('1');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Approval cleared; review the new assessment.'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/State: Assessing/);
   assert.equal(await page.getByRole('button',{name:'Move to Implemented'}).count(),0);
   const bases=page.locator('#owgPipelineDetail ol details');
   await bases.last().locator('summary').click();
   assert.match(await bases.last().textContent(),/Saved assessment: 300 cases\/month × 1 minutes × 50% eligible/);
   assert.match(await bases.last().textContent(),/Previous assessment: 300 cases\/month × 10 minutes × 50% eligible/);
   await page.locator('#owgPipelineNote').fill('Re-reviewed lower estimate');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   await page.getByRole('button',{name:'Move to Implemented'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Implemented.'}).waitFor();
   assert.equal(await page.locator('#owgPipelineAssess').count(),0);
   const implemented=await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id)).json(),ideaId);
   assert.equal(implemented.execution_authorized,false);
   assert.equal(implemented.history.filter(h=>h.state==='approved').at(-1).assessment.minutes_per_case,1);

   await page.locator('#owgPipelineFilter').selectOption('rejected');
   await page.locator('#owgPipelineList').getByText('No ideas in this view.').waitFor();
   await page.locator('#owgPipelineFilter').selectOption('');

   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'pipeline card WCAG violations');

   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgPipelineDelete').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Idea deleted.'}).waitFor();
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();


   // Catalog → idea → pinned source navigation. Unsaved editor changes must
   // never become the source of a saved-process opportunity.
   const saved=await page.evaluate(async()=>{
     const model={goal:'Review the selected renewal',baseline_source:'user_declared',baseline_coverage:'user_declared_design',baseline_path:['Review'],
       activities:[{id:'review',label:'Review'}],transitions:[],entry_activity_id:'review',terminal_activity_ids:['review'],
       outcome_expectations:[{terminal_activity_id:'review',operation:'github_pr_merged',user_confirmed:true}]};
     return (await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:'renewal_review',title:'Saved renewal review',process_model:model,user_confirmed:true,expected_revision:0})})).json();
   });
   await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
   await page.locator('#owgCatalogList').getByRole('button',{name:'Saved renewal review · r1',exact:true}).click();
   await page.getByRole('textbox',{name:'Business goal',exact:true}).fill('UNSAVED goal must stay outside the idea');
   await page.getByRole('button',{name:'Add saved revision to automation pipeline',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Current saved revision'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Review the selected renewal/);
   assert.doesNotMatch(await page.locator('#owgPipelineDetail').textContent(),/UNSAVED/);
   const linked=(await page.evaluate(async()=> (await fetch('/v1/automation-pipeline')).json())).ideas[0];
   assert.deepEqual(linked.process_reference,{workflow_id:saved.workflow_id,revision:1,content_sha256:saved.content_sha256});
   // Reopen exactly r1 (explicitly discard the unsaved editor draft).
   page.once('dialog',dialog=>dialog.accept());
   await page.getByRole('button',{name:'Open linked saved process',exact:true}).click();
   await page.locator('#owgCatalogStatus').filter({hasText:'Opened immutable revision 1.'}).waitFor();
   assert.equal(await page.getByRole('textbox',{name:'Business goal',exact:true}).inputValue(),'Review the selected renewal');
   // Resume an exact saved design and focus the human assessment without
   // creating a project or reading a provider.
   let guidedWrites=0;
   page.on('request',request=>{if(request.method()!=='GET'&&
     /(agent-project|source-check-export|project-reports)/.test(request.url()))guidedWrites++;});
   await page.locator('#owgPipelineJourney').click();
   const guide=page.locator('#owgPipelineJourneyGuide');
   await guide.getByRole('heading',{name:'Resume this evidence-to-implementation journey'}).waitFor();
   assert(await guide.locator('#owgPipelineJourneyExport').isDisabled());
   assert(await guide.locator('#owgPipelineJourneyOpenProcess').isEnabled());
   page.once('dialog',dialog=>dialog.accept());
   await guide.locator('#owgPipelineJourneyOpenProcess').click();
   await guide.locator('#owgPipelineJourneyGuideStatus').filter({hasText:'Requested exact linked revision 1'}).waitFor();
   await guide.locator('#owgPipelineJourneyReviewIdea').click();
   assert.equal(await page.evaluate(()=>document.activeElement?.id),'owgPipelineAssess');
   assert.equal(guidedWrites,0);
   assert(await page.locator('#owgPipelineProjectDownload').isDisabled());
   await page.getByRole('button',{name:'Move to Assessing',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   for(const key of ['rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate'])
     await page.locator('#owgPipelineQ_'+key).selectOption('4');
   await page.locator('#owgPipelineCases').fill('300');await page.locator('#owgPipelineMinutes').fill('10');await page.locator('#owgPipelineShare').fill('50');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Assessment saved.'}).waitFor();
   await page.getByRole('button',{name:'Move to Approved',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   // A delayed journey from an earlier idea view must not populate a newly
   // opened detail view or re-enable its stale navigation controls.
   let heldJourneyRoute,releaseJourney;
   const journeyHeld=new Promise(resolve=>{releaseJourney=resolve;});
   await page.route('**/v1/automation-pipeline/*/journey',route=>{heldJourneyRoute=route;releaseJourney();});
   await page.evaluate(()=>{document.getElementById('owgPipelineJourney').dataset.oldView='yes';});
   await page.locator('#owgPipelineJourney').click();await journeyHeld;
   await page.evaluate(id=>window.dispatchEvent(new CustomEvent('owg-pipeline-open',{detail:{idea_id:id}})),linked.idea_id);
   await page.waitForFunction(()=>document.getElementById('owgPipelineJourney')?.dataset.oldView!=='yes');
   const staleResponse=page.waitForResponse(r=>r.url().endsWith('/'+linked.idea_id+'/journey'));
   await heldJourneyRoute.continue();await staleResponse;await page.unroute('**/v1/automation-pipeline/*/journey');
   assert.equal(await page.locator('#owgPipelineJourneyGuide').count(),0,'superseded journey cannot restore controls');
   const linkedBeforeExport=await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id)).json(),linked.idea_id);
   await page.locator('#owgPipelineJourney').click();
   await guide.locator('#owgPipelineJourneyExport').waitFor();
   assert(await guide.locator('#owgPipelineJourneyExport').isEnabled());
   await guide.locator('#owgPipelineJourneyExport').click();
   assert.equal(await page.evaluate(()=>document.activeElement?.id),'owgPipelineProjectDownload');
   assert.equal(guidedWrites,0,'navigation must not export an agent project');
   const [projectDownload,projectResponse]=await Promise.all([
     page.waitForEvent('download'),
     page.waitForResponse(r=>r.url().endsWith('/'+linked.idea_id+'/agent-project')&&r.request().method()==='POST'),
     page.locator('#owgPipelineProjectDownload').click()
   ]);
   assert.equal(projectResponse.status(),200);
   const zipPath=await projectDownload.path();const zipBytes=await fs.readFile(zipPath);
   assert.equal(createHash('sha256').update(zipBytes).digest('hex'),projectResponse.headers()['x-owg-archive-sha256']);
   const inspected=spawnSync(python,['-c',`
import json,sys,zipfile,hashlib
with zipfile.ZipFile(sys.argv[1]) as z:
 spec=json.loads(z.read('workflow.spec.json'));manifest=json.loads(z.read('opportunity.lineage.json'))
 assert spec['execution_authorization'] is False and spec['live_business_tools']==[]
 assert manifest['spec_sha256']==hashlib.sha256(json.dumps(spec,sort_keys=True,allow_nan=False).encode()).hexdigest()
 print(json.dumps({"manifest":manifest,"spec":spec}))
`,zipPath],{encoding:'utf8'});
   assert.equal(inspected.status,0,inspected.stderr);
   const {manifest,spec:exportedSpec}=JSON.parse(inspected.stdout);
   assert.equal(manifest.idea_id,linked.idea_id);assert.equal(manifest.idea_revision,linkedBeforeExport.revision);
   assert.deepEqual(manifest.process_reference,linked.process_reference);
   assert.equal(manifest.project_id,projectResponse.headers()['x-owg-project-id']);
   await page.locator('#owgPipelineProjects').getByRole('status').filter({hasText:'Project downloaded.'}).waitFor();
   await page.locator('#owgPipelineProjects li').filter({hasText:'current idea revision'}).waitFor();
   const [sameProject]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPipelineProjectDownload').click()]);
   assert.deepEqual(await fs.readFile(await sameProject.path()),zipBytes);
   await page.locator('#owgPipelineProjects').getByRole('status').filter({hasText:'Project downloaded.'}).waitFor();
   const recorded=await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id+'/projects')).json(),linked.idea_id);
   assert.equal(recorded.projects.length,1);assert.equal(recorded.deployment_verified,false);
   // Navigate into the exact selected project's existing read-only report and
   // source-check forms. The latter still requires fresh explicit consent.
   await page.locator('#owgPipelineJourney').click();
   await page.locator('#owgPipelineJourneyProject').selectOption(manifest.project_id);
   // Explicit, bounded review packet is reconstructed from persisted references,
   // not an approval or a new source/provider read. It never includes raw case data.
   const writesBeforePacket=guidedWrites;
   const [handoffDownload]=await Promise.all([
     page.waitForEvent('download'),
     page.locator('#owgPipelineJourneyReviewPacket').click()
   ]);
   assert.equal(handoffDownload.suggestedFilename(),'owg-evidence-handoff-review.json');
   const handoffPacket=JSON.parse(await fs.readFile(await handoffDownload.path(),'utf8'));
   assert.equal(handoffPacket.format,'openworkgraph.evidence-handoff-review.v1');
   assert.equal(handoffPacket.scope,'human_review_only');
   assert.equal(handoffPacket.execution_authorized,false);
   assert.equal(handoffPacket.source_read_authorized,false);
   assert.equal(handoffPacket.idea.idea_id,linked.idea_id);
   assert.equal(handoffPacket.idea.revision,linkedBeforeExport.revision);
   assert.equal(handoffPacket.process.content_sha256,saved.content_sha256);
   assert.equal(handoffPacket.project.project_id,manifest.project_id);
   assert.equal(handoffPacket.project.spec_sha256,manifest.spec_sha256);
   // The lineage manifest lives inside the ZIP, so it cannot carry the ZIP's own hash;
   // compare with the hash of the downloaded archive instead.
   assert.equal(handoffPacket.project.archive_sha256,projectResponse.headers()['x-owg-archive-sha256']);
   assert(handoffPacket.readiness.blockers.includes('dataset_origin_not_linked'));
   assert(handoffPacket.readiness.blockers.includes('no_imported_report'));
   assert(handoffPacket.readiness.blockers.includes('execution_and_business_outcomes_unverified'));
   assert.equal(handoffPacket.readiness.verified_business_outcomes,false);
   assert.equal(guidedWrites,writesBeforePacket,'review packet must not write or read a provider');
   assert(!JSON.stringify(handoffPacket).includes('PRIVATE_RAW_CASE_VALUE'));
   assert(!JSON.stringify(handoffPacket).includes('owg:r:'));
   // Importing a local review packet performs a fresh session-authenticated
   // journey read and compares identities; it never restores approval.
   const priorPacket=page.locator('#owgPipelineJourneyPriorPacket');
   const setPacket=async packet=>priorPacket.setInputFiles({
     name:'prior-handoff-review.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(packet))
   });
   await setPacket(handoffPacket);
   const [comparisonResponse]=await Promise.all([
     page.waitForResponse(r=>r.url().endsWith('/'+linked.idea_id+'/journey')&&r.request().method()==='GET'),
     page.locator('#owgPipelineJourneyComparePacket').click()
   ]);
   assert.equal(comparisonResponse.status(),200);
   await page.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields match the current local references/).waitFor();
   assert.equal(guidedWrites,writesBeforePacket,'local packet comparison must not write');
   const matchingInspection=page.locator('#owgPipelineJourneyInspectionWorklist');
   assert(await matchingInspection.locator('input[type=checkbox]').count()>0,
     'even a matching snapshot still has outstanding human checks');
   assert((await matchingInspection.textContent()).includes('All approval and verification gates remain open.'));

   const alteredPacket=JSON.parse(JSON.stringify(handoffPacket));
   alteredPacket.process.status='changed';
   alteredPacket.project.spec_sha256='a'.repeat(64);
   alteredPacket.source_check_exports.count+=1;
   alteredPacket.readiness.blockers.push('idea_not_approved');
   await setPacket(alteredPacket);
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields changed since the saved packet/).waitFor();
   const comparisonText=await page.locator('#owgPipelineJourneyCompareResult').textContent();
   assert(comparisonText.includes('Saved process availability'));
   assert(comparisonText.includes('Project revision and artifact hashes'));
   // Human-inspection checklist is local and never approves any of the
   // underlying decision/source/execution gates. Export is pinned to both
   // snapshots and contains no raw case, receipt or free-form user text.
   const inspectionWorklist=page.locator('#owgPipelineJourneyInspectionWorklist');
   const inspectionChecks=inspectionWorklist.locator('input[type=checkbox]');
   assert(await inspectionChecks.count()>=5);
   await inspectionChecks.first().focus();
   await page.keyboard.press('Space');
   assert(await inspectionChecks.first().isChecked(),'keyboard can mark inspected locally');
   const [inspectionDownload]=await Promise.all([
     page.waitForEvent('download'),
     inspectionWorklist.locator('#owgPipelineJourneyInspectionDownload').click()
   ]);
   assert.equal(inspectionDownload.suggestedFilename(),'owg-human-inspection-worklist.json');
   const inspectionRecord=JSON.parse(await fs.readFile(await inspectionDownload.path(),'utf8'));
   assert.equal(inspectionRecord.format,'openworkgraph.handoff-human-inspection.v1');
   assert.equal(inspectionRecord.scope,'local_human_inspection_only');
   assert.equal(inspectionRecord.idea_id,linked.idea_id);
   assert.equal(inspectionRecord.project_id,manifest.project_id);
   assert.equal(inspectionRecord.saved_basis.project_spec_sha256,'a'.repeat(64));
   assert.equal(inspectionRecord.current_basis.project_spec_sha256,manifest.spec_sha256);
   assert.equal(inspectionRecord.current_basis.process_sha256,saved.content_sha256);
   assert.equal(inspectionRecord.inspected_count,1);
   assert(inspectionRecord.pending_inspection_count>0);
   assert(inspectionRecord.items.some(item=>item.kind==='changed_area'&&item.inspected_locally));
   assert(inspectionRecord.items.some(item=>item.kind==='outstanding_blocker'&&!item.inspected_locally));
   for(const flag of ['execution_authorized','source_read_authorized','human_approval_granted',
     'verified_execution','verified_business_outcomes'])assert.equal(inspectionRecord[flag],false);
   assert.equal(inspectionRecord.source_and_outcome_verification,'not_established');
   assert(!JSON.stringify(inspectionRecord).includes('PRIVATE_RAW_CASE_VALUE'));
   assert(!JSON.stringify(inspectionRecord).includes('owg:r:'));
   assert(!JSON.stringify(inspectionRecord).includes('raw_receipt'));
   assert.match(inspectionRecord.comparison_sha256,/^[a-f0-9]{64}$/);
   // A local file can resume only the same explicit human inspection after
   // reopening the dashboard. This never changes server-side approval state.
   await inspectionChecks.first().uncheck();
   const inspectionFilePayload={
     name:'previous-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(inspectionRecord))
   };
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles(inspectionFilePayload);
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/Restored 1 local inspection marks/}).waitFor();
   assert(await inspectionChecks.first().isChecked(),'same-comparison import restores a local mark');
   await inspectionChecks.first().uncheck();
   const wrongInspection={...inspectionRecord,project_id:'unrelated-project'};
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'wrong-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(wrongInspection))
   });
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/does not match this exact fresh comparison/}).waitFor();
   assert(!await inspectionChecks.first().isChecked(),'wrong-project worklist must not restore marks');
   const legacyInspection={...inspectionRecord};
   delete legacyInspection.comparison_sha256;
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'legacy-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(legacyInspection))
   });
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/does not match this exact fresh comparison/}).waitFor();
   assert(!await inspectionChecks.first().isChecked(),'old unbound worklists must not restore marks');
   const forgedInspection={...inspectionRecord,execution_authorized:true};
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'forged-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(forgedInspection))
   });
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/does not match this exact fresh comparison/}).waitFor();
   assert(!await inspectionChecks.first().isChecked(),'imported execution claims are rejected');
   const staleInspection={...inspectionRecord,comparison_sha256:'f'.repeat(64)};
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'stale-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(staleInspection))
   });
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/does not match this exact fresh comparison/}).waitFor();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'oversized-inspection.json',mimeType:'application/json',
     buffer:Buffer.alloc(65537,0x20)
   });
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestore').click();
   await inspectionWorklist.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/no larger than 64 KiB/}).waitFor();
   assert.equal(guidedWrites,writesBeforePacket,'worklist reimport must remain read-only');

   const inspectionRestart=await context.newPage();
   let inspectionRestartWrites=0;
   inspectionRestart.on('request',r=>{
     if(r.url().startsWith(base+'/v1/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))
       inspectionRestartWrites++;
   });
   await inspectionRestart.goto(base+'/#session='+session);
   await inspectionRestart.locator('[role=tab][data-tab=evidence]').click();
   await inspectionRestart.locator('#owgPipelineCard').waitFor();
   await inspectionRestart.evaluate(id=>window.dispatchEvent(new CustomEvent(
     'owg-pipeline-open',{detail:{idea_id:id}})),linked.idea_id);
   await inspectionRestart.locator('#owgPipelineProcessStatus').filter({hasText:'Current saved revision'}).waitFor();
   await inspectionRestart.locator('#owgPipelineJourney').click();
   await inspectionRestart.locator('#owgPipelineJourneyGuide').waitFor();
   await inspectionRestart.locator('#owgPipelineJourneyProject').selectOption(manifest.project_id);
   await inspectionRestart.locator('#owgPipelineJourneyPriorPacket').setInputFiles({
     name:'original-review.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(alteredPacket))
   });
   await inspectionRestart.locator('#owgPipelineJourneyComparePacket').click();
   await inspectionRestart.locator('#owgPipelineJourneyInspectionWorklist').waitFor();
   const restartedChecks=inspectionRestart.locator('#owgPipelineJourneyInspectionWorklist input[type=checkbox]');
   assert(!await restartedChecks.first().isChecked(),'restart never silently restores inspection marks');
   await inspectionRestart.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles(inspectionFilePayload);
   await inspectionRestart.locator('#owgPipelineJourneyInspectionRestore').focus();
   await inspectionRestart.keyboard.press('Enter');
   await inspectionRestart.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/Restored 1 local inspection marks/}).waitFor();
   assert(await restartedChecks.first().isChecked(),'explicit keyboard import resumes the exact inspection after restart');
   assert.equal(inspectionRestartWrites,0,'review restoration never writes server state');
   await inspectionRestart.close();
   const invalidInspection=await page.evaluate(([oldPacket,newPacket])=>{
     try{
       window.__owgPipelineModel.handoffInspectionWorklist(oldPacket,newPacket,
         ['change:invented-unreviewed-category'],'2026-11-02T00:00:00Z');
       return false;
     }catch{return true;}
   },[alteredPacket,handoffPacket]);
   assert.equal(invalidInspection,true,'cannot import arbitrary inspection or approval claims');
   assert.equal(guidedWrites,writesBeforePacket,'inspection and export must not write or read source');
   const driftActions=page.locator('#owgPipelineJourneyDriftActions');
   assert(await driftActions.getByRole('button',{name:'Inspect exact saved process revision'}).isEnabled());
   assert((await driftActions.textContent()).includes('Review changed areas'));
   // Keyboard navigation is an explicit review action, not an automatic export.
   await driftActions.getByRole('button',{name:'Inspect selected immutable project'}).focus();
   await page.keyboard.press('Enter');
   assert.equal(await page.evaluate(()=>document.activeElement.id),'owgPipelineJourneyProject');
   await driftActions.getByRole('button',{name:'Prepare separate source review for this project'}).click();
   await page.locator('#owgPipelineSourceProject').waitFor();
   assert.equal(await page.locator('#owgPipelineSourceProject').inputValue(),manifest.project_id);
   assert.equal(await page.locator('#owgPipelineSourceConsent').isChecked(),false);
   await driftActions.getByRole('button',{name:'Inspect outstanding human review checks'}).click();
   assert.equal(await page.evaluate(()=>document.activeElement.id),'owgPipelineJourneyReadiness');
   assert.equal(guidedWrites,writesBeforePacket,'drift review navigation must not write or read source');
   const wrongPacket=JSON.parse(JSON.stringify(handoffPacket));
   wrongPacket.project.project_id='unrelated-project';
   await setPacket(wrongPacket);
   assert.equal(await page.locator('#owgPipelineJourneyInspectionWorklist').count(),0,
     'new packet selection must invalidate old local inspection marks and download');
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').filter({hasText:/belongs to another idea or project/}).waitFor();
   assert.equal(await page.locator('#owgPipelineJourneyDriftActions').count(),0,'invalid packet must clear actionable review links');
   await priorPacket.setInputFiles({name:'huge.json',mimeType:'application/json',buffer:Buffer.alloc(65537,32)});
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').filter({hasText:/no larger than 64 KiB/}).waitFor();
   await setPacket(handoffPacket);
   assert.equal(guidedWrites,writesBeforePacket,'invalid packets must not write or upload');
   // A different selected project must not inherit the first project's hashes.
   const secondPacket=await page.evaluate((packet)=>{
     const model=window.__owgPipelineModel;
     const journey={idea:{revision:packet.idea.revision,state:'approved'},process_status:{status:'current',process_reference:packet.process},
       projects:[{...packet.project,project_id:'second-project',spec_sha256:'b'.repeat(64),archive_sha256:'c'.repeat(64),
         reported_specifications:[],source_check_exports:{count:0}}]};
     return model.handoffReviewPacket(journey,'second-project',packet.idea.idea_id,'2026-11-02T00:00:00Z');
   },handoffPacket);
   assert.equal(secondPacket.project.project_id,'second-project');
   assert.equal(secondPacket.project.spec_sha256,'b'.repeat(64));
   assert.notEqual(secondPacket.project.spec_sha256,handoffPacket.project.spec_sha256);
   const boundedPacket=await page.evaluate((packet)=>{
     const journey={idea:{revision:packet.idea.revision,state:'approved'},
       process_status:{status:'current',process_reference:packet.process},
       dataset_origins:Array.from({length:65},()=>({status:'current',raw_case_text:'SECRET'})),
       projects:[{...packet.project,project_id:'bounded',reported_specifications:Array.from({length:65},()=>({status:'matching_reported_specification',raw_receipt:'SECRET'})),
         source_check_exports:{count:0}}]};
     return window.__owgPipelineModel.handoffReviewPacket(journey,'bounded',packet.idea.idea_id,'2026-11-02T00:00:00Z');
   },handoffPacket);
   assert.equal(boundedPacket.dataset_origins.length,64);
   assert.equal(boundedPacket.reported_specifications.length,64);
   assert.equal(boundedPacket.dataset_origins_truncated,true);
   assert.equal(boundedPacket.reported_specifications_truncated,true);
   assert(boundedPacket.readiness.blockers.includes('dataset_origin_list_truncated'));
   assert(boundedPacket.readiness.blockers.includes('reported_specification_list_truncated'));
   assert(!JSON.stringify(boundedPacket).includes('SECRET'));
   const writesBeforeNavigation=guidedWrites;
   await page.locator('#owgPipelineJourneyReports').click();
   await page.locator('#owgPipelineReportProject').waitFor();
   assert.equal(await page.locator('#owgPipelineReportProject').inputValue(),manifest.project_id);
   await page.locator('#owgPipelineJourneySource').click();
   await page.locator('#owgPipelineSourceProject').waitFor();
   assert.equal(await page.locator('#owgPipelineSourceProject').inputValue(),manifest.project_id);
   assert.equal(await page.locator('#owgPipelineSourceConsent').isChecked(),false);
   assert.equal(guidedWrites,writesBeforeNavigation,'stage navigation is read-only');
   // Source-state checks are explicitly confirmed and downloaded only. The
   // unobserved synthetic record must remain unknown and never reach a provider.
   let sourceDownloads=0;page.on('download',d=>{if(d.suggestedFilename()==='owg-project-source-check.json')sourceDownloads++;});
   await page.locator('#owgPipelineSourceChecks').click();
   await page.locator('#owgPipelineSourceExpectation option').waitFor({state:'attached'});
   assert(await page.locator('#owgPipelineSourceExport').isDisabled());
   const sourceA='owg:r:'+'a'.repeat(32),sourceB='owg:r:'+'b'.repeat(32),sourceC='owg:r:'+'c'.repeat(32);
   await page.locator('#owgPipelineSourceRecord').fill(sourceA);await page.locator('#owgPipelineSourceConsent').check();
   assert(await page.locator('#owgPipelineSourceExport').isEnabled());
   await page.locator('#owgPipelineSourceRecord').fill(sourceB);
   assert.equal(await page.locator('#owgPipelineSourceConsent').isChecked(),false);
   assert(await page.locator('#owgPipelineSourceExport').isDisabled());
   await page.locator('#owgPipelineSourceConsent').check();
   const [checkDownload,checkResponse]=await Promise.all([page.waitForEvent('download'),
     page.waitForResponse(r=>r.url().endsWith('/source-check-export')&&r.request().method()==='POST'),
     page.locator('#owgPipelineSourceExport').click()]);
   assert.equal(checkResponse.status(),200);
   const checkBytes=await fs.readFile(await checkDownload.path());const checkDoc=JSON.parse(checkBytes.toString('utf8'));
   assert.equal(createHash('sha256').update(checkBytes).digest('hex'),checkResponse.headers()['x-owg-document-sha256']);
   assert.equal(checkDoc.project_id,manifest.project_id);assert.equal(checkDoc.spec_sha256,manifest.spec_sha256);
   assert.equal(checkDoc.receipt.resource_ref,sourceB);assert.equal(checkDoc.receipt.availability,'unknown');
   assert.equal(checkDoc.receipt.operation_verified,false);assert.equal(checkDoc.receipt_body_persisted,false);
   assert.equal(checkDoc.agent_caused_operation_verified,false);assert.equal(checkDoc.reported_run_case_identity_verified,false);
   await page.locator('#owgPipelineSourceCheckForm').getByRole('status').filter({hasText:'Source-check document downloaded.'}).waitFor();
   assert.equal(await page.locator('#owgPipelineSourceRecord').inputValue(),'');
   assert.equal(await page.locator('#owgPipelineSourceConsent').isChecked(),false);
   const checkHistory=await page.evaluate(async({id,project})=>(await fetch('/v1/automation-pipeline/'+id+'/projects/'+project+'/source-check-exports')).json(),{id:linked.idea_id,project:manifest.project_id});
   assert.equal(checkHistory.exports.length,1);assert.equal(checkHistory.exports[0].document_sha256,checkResponse.headers()['x-owg-document-sha256']);
   assert(!JSON.stringify(checkHistory).includes(sourceB));assert(!JSON.stringify(checkHistory).includes('resource_ref'));
   const sourceAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(sourceAudit.violations.map(v=>v.id),[]);
   // Changing the selected record while a source response is held must prevent
   // a late download from appearing to describe the new record.
   let heldRoute,signalHeld;const held=new Promise(resolve=>{signalHeld=resolve;});
   await page.route('**/source-check-export',route=>{heldRoute=route;signalHeld();});
   await page.locator('#owgPipelineSourceRecord').fill(sourceB);await page.locator('#owgPipelineSourceConsent').check();
   await page.locator('#owgPipelineSourceExport').click();await held;
   await page.locator('#owgPipelineSourceRecord').fill(sourceC);await heldRoute.continue();
   await page.waitForFunction(()=>!document.getElementById('owgPipelineSourceConsent').disabled);
   assert.equal(sourceDownloads,1);assert.equal(await page.locator('#owgPipelineSourceRecord').inputValue(),sourceC);
   assert.equal(await page.locator('#owgPipelineSourceConsent').isChecked(),false);
   await page.unroute('**/source-check-export');
   const reported=await page.evaluate(async specHash=>{
     const doc={format:'openworkgraph.automation-run-report.v1',deployment:{name:'Synthetic renewal report',runtime:'openai-agents',workflow_spec_sha256:specHash},
       runs:[{run_id:'reported_run',started_at:'2024-03-01T10:00:00Z',finished_at:'2024-03-01T10:01:00Z',status:'succeeded',steps:[],
         outcome:{verified:true,source:'CLIENT_ASSERTION_NOT_INDEPENDENT'}}]};
     const r=await fetch('/v1/automation-monitoring/reports',{method:'POST',headers:{'Content-Type':'application/json','X-OWG-Local-Storage-Confirmed':'true'},body:JSON.stringify(doc)});
     if(!r.ok)throw new Error(await r.text());return r.json();
   },manifest.spec_sha256);
   await page.locator('#owgPipelineReviewReports').click();
   await page.locator('#owgPipelineReportDeployment').selectOption(reported.deployment.deployment_id);
   await page.locator('#owgPipelineLinkReport').click();
   await page.locator('#owgPipelineReportLinks').getByRole('status').filter({hasText:'monitoring specification changed'}).waitFor();
   // A report's declared hash alone is insufficient. Attach the actual exported spec.
   await page.evaluate(async({id,spec})=>{
     const r=await fetch('/v1/automation-monitoring/deployments/'+id+'/design',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});
     if(!r.ok)throw new Error(await r.text());
   },{id:reported.deployment.deployment_id,spec:exportedSpec});
   await page.locator('#owgPipelineReviewReports').click();
   await page.locator('#owgPipelineLinkReport').click();
   await page.locator('#owgPipelineReportLinks li').filter({hasText:'Matching reported specification'}).waitFor();
   const linkedReports=await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id+'/project-reports')).json(),linked.idea_id);
   assert.equal(linkedReports.links.length,1);assert.equal(linkedReports.business_outcomes_verified,false);
   // Reopen the pinned journey after a dashboard restart in another tab.
   const resumed=await context.newPage();
   await resumed.goto(base+'/#session='+session);
   await resumed.locator('[role=tab][data-tab=evidence]').click();
   await resumed.locator('#owgPipelineCard').waitFor();
   await resumed.evaluate(id=>window.dispatchEvent(new CustomEvent('owg-pipeline-open',{detail:{idea_id:id}})),linked.idea_id);
   await resumed.locator('#owgPipelineProcessStatus').filter({hasText:'Current saved revision'}).waitFor();
   await resumed.locator('#owgPipelineJourney').click();
   await resumed.locator('#owgPipelineJourneyGuide').waitFor();
   await resumed.locator('#owgPipelineJourneyProject').selectOption(manifest.project_id);
   await resumed.locator('#owgPipelineJourneyArtifact').getByText(/Matching reported specification/).waitFor();
   const [resumedDownload]=await Promise.all([resumed.waitForEvent('download'),
     resumed.locator('#owgPipelineJourneyReviewPacket').click()]);
   const resumedPacket=JSON.parse(await fs.readFile(await resumedDownload.path(),'utf8'));
   assert.equal(resumedPacket.project.project_id,manifest.project_id);
   assert.equal(resumedPacket.project.spec_sha256,manifest.spec_sha256);
   assert.equal(resumedPacket.reported_specifications[0].status,'matching_reported_specification');
   assert(!resumedPacket.readiness.blockers.includes('no_imported_report'));
   assert(resumedPacket.readiness.blockers.includes('execution_and_business_outcomes_unverified'));
   // A previously saved pre-report packet now reveals a real report-status drift.
   await resumed.locator('#owgPipelineJourneyPriorPacket').setInputFiles({
     name:'before-report.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(handoffPacket))
   });
   await resumed.locator('#owgPipelineJourneyComparePacket').click();
   await resumed.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields changed since the saved packet/).waitFor();
   // The report was imported after the original inspection. A previously
   // exported inspection record must not be replayed on this changed basis.
   await resumed.locator('#owgPipelineJourneyInspectionImportFile').setInputFiles({
     name:'pre-report-inspection.json',mimeType:'application/json',
     buffer:Buffer.from(JSON.stringify(inspectionRecord))
   });
   await resumed.locator('#owgPipelineJourneyInspectionRestore').click();
   await resumed.locator('#owgPipelineJourneyInspectionRestoreStatus')
     .filter({hasText:/does not match this exact fresh comparison/}).waitFor();
   assert(!await resumed.locator('#owgPipelineJourneyInspectionWorklist input[type=checkbox]').first().isChecked(),
     'source/report drift cannot replay old inspection marks');
   const resumedDrift=resumed.locator('#owgPipelineJourneyDriftActions');
   await resumedDrift.getByRole('button',{name:'Review imported reports for this project'}).click();
   await resumed.locator('#owgPipelineReportProject').waitFor();
   assert.equal(await resumed.locator('#owgPipelineReportProject').inputValue(),manifest.project_id);
   await resumedDrift.getByRole('button',{name:'Prepare separate source review for this project'}).click();
   await resumed.locator('#owgPipelineSourceProject').waitFor();
   assert.equal(await resumed.locator('#owgPipelineSourceProject').inputValue(),manifest.project_id);
   assert.equal(await resumed.locator('#owgPipelineSourceConsent').isChecked(),false);

   assert((await resumed.locator('#owgPipelineJourneyResult').textContent()).includes(manifest.spec_sha256));
   await resumed.close();
   await page.locator('#owgPipelineJourney').click();
   const journey=page.locator('#owgPipelineJourneyResult');
   await journey.getByText('Dataset and cohort → process: origin is not linked.',{exact:true}).waitFor();
   await journey.getByText(projectResponse.headers()['x-owg-archive-sha256'],{exact:true}).waitFor();
   await page.locator('#owgPipelineJourneyArtifact').getByText(/Matching reported specification/).waitFor();
   assert((await journey.textContent()).includes(manifest.spec_sha256));
   assert((await journey.textContent()).includes('Receipt and result are not retained'));
   assert((await journey.textContent()).includes('Artifact execution and individual run/case attribution are unverified'));
   assert.equal(linkedReports.links[0].per_run_attribution_verified,false);
   assert.equal(linkedReports.links[0].independent_execution_verified,false);
   const reportsAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(reportsAudit.violations.map(v=>v.id),[]);
   await page.evaluate(async id=>fetch('/v1/automation-monitoring/deployments/'+id+'/delete',{method:'POST'}),reported.deployment.deployment_id);
   await page.locator('#owgPipelineReviewReports').click();
   await page.locator('#owgPipelineReportLinks li').filter({hasText:'Reported deployment unavailable'}).waitFor();
   await page.locator('#owgPipelineJourney').click();
   await page.locator('#owgPipelineJourneyArtifact').getByText(/Reported deployment unavailable/).waitFor();
   await page.getByRole('button',{name:'Remove report link '+reported.deployment.deployment_id,exact:true}).click();
   await page.locator('#owgPipelineReportLinks').getByText('No report links recorded.',{exact:true}).waitFor();
   await page.evaluate(async record=>{
     await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:record.workflow_id,title:record.title,process_model:{...record.process_model,goal:'Changed renewal policy'},expected_revision:1,user_confirmed:true})});
   },saved);
   await page.getByRole('button',{name:'Refresh process status',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Process changed since this idea was created'}).waitFor();
   await page.locator('#owgPipelineJourney').click();
   await journey.getByText(/Saved process renewal_review · revision 1 · changed since this idea/).waitFor();
   await guide.getByText(/Process basis is changed/).waitFor();
   const [changedDownload]=await Promise.all([page.waitForEvent('download'),
     page.locator('#owgPipelineJourneyReviewPacket').click()]);
   const changedPacket=JSON.parse(await fs.readFile(await changedDownload.path(),'utf8'));
   assert.equal(changedPacket.process.status,'changed');
   assert(changedPacket.readiness.blockers.includes('process_not_current'));
   await page.locator('#owgPipelineJourneyPriorPacket').setInputFiles({
     name:'original-review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(handoffPacket))
   });
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields changed since the saved packet/).waitFor();
   assert((await page.locator('#owgPipelineJourneyCompareResult').textContent()).includes('Saved process availability'));
   assert(await page.locator('#owgPipelineJourneyDriftActions').getByRole('button',{name:'Inspect exact saved process revision'}).isEnabled());

   assert(await guide.locator('#owgPipelineJourneyOpenProcess').isEnabled());
   page.once('dialog',dialog=>dialog.accept());
   await guide.locator('#owgPipelineJourneyOpenProcess').click();
   await page.locator('#owgCatalogStatus').filter({hasText:'Opened immutable revision 1.'}).waitFor();
   assert.equal(await page.getByRole('textbox',{name:'Business goal',exact:true}).inputValue(),'Review the selected renewal');
   await page.getByRole('button',{name:'Move to Implemented',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'linked process changed or is unavailable'}).waitFor();
   assert.equal((await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id)).json(),linked.idea_id)).state,'approved');
   await page.locator('#owgPipelineProjectDownload').click();
   await page.locator('#owgPipelineProjects').getByRole('status').filter({hasText:'linked process changed or is unavailable'}).waitFor();
   const linkedAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(linkedAudit.violations.map(v=>v.id),[]);
   await page.evaluate(async()=>{
     await fetch('/v1/process-catalog/forget',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:'renewal_review',user_confirmed:true})});
   });
   await page.getByRole('button',{name:'Refresh process status',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Linked revision is unavailable'}).waitFor();
   await page.locator('#owgPipelineJourney').click();
   await journey.getByText(/Saved process renewal_review · revision 1 · unavailable/).waitFor();
   assert(await guide.locator('#owgPipelineJourneyOpenProcess').isDisabled());
   const [deletedDownload]=await Promise.all([page.waitForEvent('download'),
     page.locator('#owgPipelineJourneyReviewPacket').click()]);
   const deletedPacket=JSON.parse(await fs.readFile(await deletedDownload.path(),'utf8'));
   assert.equal(deletedPacket.process.status,'unavailable');
   assert(deletedPacket.readiness.blockers.includes('process_not_current'));
   await page.locator('#owgPipelineJourneyPriorPacket').setInputFiles({
     name:'original-review.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(handoffPacket))
   });
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields changed since the saved packet/).waitFor();
   assert(await page.locator('#owgPipelineJourneyDriftActions').getByRole('button',{name:'Inspect exact saved process revision'}).isDisabled(),
     'unavailable process cannot be silently replaced with a newer revision');
   const writesAfterDeletion=guidedWrites;
   await page.locator('#owgPipelineJourneyComparePacket').click();
   await page.locator('#owgPipelineJourneyCompareResult').getByText(/Recorded fields changed since the saved packet/).waitFor();
   assert.equal(guidedWrites,writesAfterDeletion,'read-only comparison after source deletion must not write');


   assert(await page.getByRole('button',{name:'Open linked saved process',exact:true}).isDisabled());
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   assert.deepEqual(errors,[]);
   console.log('Automation pipeline acceptance passed: idea → assessment gate → stale-edit refusal → approval → reassessment clears approval → fresh review → implemented, assessment snapshots, CSV export, filters, catalog pin → exact revision reopen → tracked project ZIP/hash/manifest and repeat export → explicit source-check download with unknown source state, digest-only history and held-response guard → exact reported-spec association with unknown outcomes → combined read-only journey with digests and missing evidence, source/report drift and deletion → changed/deleted source refusal, deletion, WCAG and mobile.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
