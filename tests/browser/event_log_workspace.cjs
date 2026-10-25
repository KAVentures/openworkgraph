const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: import a log, analyse, KPIs, conformance, root cause, export and delete.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-event-logs-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 // 60 cases: every third is slow and has rework (Fix), every tenth skips Approve.
 const rows=['case_id;step;time;who'];
 for(let c=0;c<60;c++){
   const slow=c%3===0,steps=['Receive','Check',...(slow?['Fix','Check']:[]),'Approve','Close'].filter(s=>!(c%10===0&&s==='Approve'));
   let t=Date.UTC(2024,0,1+c%20,8);
   for(const s of steps){t+=(slow?3:0.25)*3600e3;rows.push(['C'+c,s,new Date(t).toISOString(),s==='Check'?'bot':(slow?'ann':'bob')].join(';'));}
 }
 rows.push('C99;Receive;2024-01-05 09:00:00;bob');
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Event-log server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Event-log server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage({acceptDownloads:true}),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const card=page.locator('#owgEventLogCard');await card.waitFor();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);

   await page.locator('#owgEventLogFile').setInputFiles({name:'claims.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogActivity').selectOption('step');
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'case_id');
   assert.equal(await page.locator('#owgEventLogTime').inputValue(),'time');
   assert.equal(await page.locator('#owgEventLogResource').inputValue(),'who');
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true,'import needs explicit local-storage consent');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogKeepRejected').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'1 rejected'}).waitFor().catch(async error=>{throw new Error('import status: '+await page.locator('#owgEventLogStatus').textContent()+' '+error.message);});
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/Imported 274 events/);
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/1 rejected: 1 time without a UTC offset/);
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/1 rejected rows were kept for review/);

   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogResults h3',{hasText:'Most common variants'}).waitFor();
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Receive → Check → Approve → Close/);
   await page.locator('#owgQuality summary').click();
   await page.locator('#owgQualityResults table').first().waitFor();
   const qualityText=await page.locator('#owgQualityResults').textContent();
   assert.match(qualityText,/Import\s*csv\s*274\s*1 \(1 time without a UTC offset\)/);
   assert.match(qualityText,/Case columncase_id/);assert.match(qualityText,/not known to be complete/);
   assert.doesNotMatch(qualityText,/C99/,'rejected rows are not shown');

   await page.locator('#owgMapStatus').filter({hasText:'Showing 5 activities'}).waitFor();
   assert.equal(await page.locator('#owgMapDrawing').count(),1);
   await page.locator('#owgMapActivities').evaluate(node=>{node.value='50';node.dispatchEvent(new Event('input',{bubbles:true}));});
   await page.locator('#owgMapStatus').filter({hasText:'Showing 2 activities'}).waitFor();
   assert.equal(await page.locator('#owgMapActivitiesValue').textContent(),'50%');
   await page.locator('#owgMapActivities').evaluate(node=>{node.value='100';node.dispatchEvent(new Event('input',{bubbles:true}));});
   await page.locator('#owgMapStatus').filter({hasText:'Showing 5 activities'}).waitFor();
   assert.match(await page.locator('#owgMapSlowest').textContent(),/Slowest paths/);

   // Case playback along the drawn map, then a follower case filter shared by the analysis views.
   await page.locator('#owgPlaybackStatus').filter({hasText:'Playing 60 of 60 cases'}).waitFor();
   assert.equal(await page.locator('#owgMapDrawing circle[data-owg-token]').count(),60);
   await page.locator('#owgPlaybackPosition').evaluate(node=>{node.value='0';node.dispatchEvent(new Event('input',{bubbles:true}));});
   assert.match(await page.locator('#owgPlaybackTime').textContent(),/^2024-01-\d\d \d\d:\d\d UTC · [1-9]\d* cases in progress$/);
   const visibleTokens=()=>page.locator('#owgMapDrawing circle[data-owg-token][visibility=visible]').count();
   assert.ok(await visibleTokens()>0,'tokens are placed on the map');
   await page.locator('#owgPlaybackPlay').click();
   assert.equal(await page.locator('#owgPlaybackPlay').getAttribute('aria-pressed'),'true');
   await page.waitForFunction(()=>Number(document.querySelector('#owgPlaybackPosition').value)>5);
   await page.locator('#owgPlaybackPlay').click();
   assert.equal(await page.locator('#owgPlaybackPlay').getAttribute('aria-pressed'),'false');
   await page.locator('#owgCaseFilterAdd').click();
   await page.locator('#owgCaseFilterFrom1').selectOption('Check');await page.locator('#owgCaseFilterMode1').selectOption('direct');await page.locator('#owgCaseFilterTo1').selectOption('Fix');
   await page.locator('#owgCaseFilterAdd').click();
   await page.locator('#owgCaseFilterFrom2').selectOption('Fix');await page.locator('#owgCaseFilterMode2').selectOption('not-eventual');await page.locator('#owgCaseFilterTo2').selectOption('Approve');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 2 of 60 cases'}).waitFor();
   assert.match(await page.locator('#owgCaseFilterStatus').textContent(),/Check is directly followed by Fix; Fix is never eventually followed by Approve/);
   assert.equal(await page.locator('#owgCaseFilterFrom2').inputValue(),'Fix','rules stay editable after applying');
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Receive → Check → Fix → Check → Close/);
   await page.locator('#owgPlaybackStatus').filter({hasText:'Playing 2 of 2 cases'}).waitFor();
   // The case evidence drill-down selects the same filtered cases.
   await page.locator('#owgEventLogCasesBrowse').click();
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'2 matching cases (case filter applied)'}).waitFor();
   assert.deepEqual((await page.locator('#owgEventLogCasesList button').allTextContents()).sort(),['C0','C30']);
   // Save the active filter, clear it, then re-apply it from the saved list.
   await page.locator('#owgFilterSetName').fill('Rework without approval');
   await page.locator('#owgFilterSetSave').click();
   await page.getByRole('button',{name:'Apply saved filter Rework without approval',exact:true}).waitFor();
   await page.locator('#owgCaseFilterClear').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'every case is included'}).waitFor();
   assert.equal(await page.locator('#owgFilterSetSave').isDisabled(),true,'only an applied filter can be saved');
   await page.getByRole('button',{name:'Apply saved filter Rework without approval',exact:true}).click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 2 of 60 cases'}).waitFor();
   await page.locator('#owgFilterSetList').filter({hasText:'Check is directly followed by Fix; Fix is never eventually followed by Approve'}).waitFor();
   await page.locator('#owgCaseFilterClear').click();
   await page.locator('#owgCaseFilterStatus').filter({hasText:'every case is included'}).waitFor();
   assert.equal(await page.locator('#owgCaseFilterCount').count(),0);
   await page.locator('#owgMapStatus').filter({hasText:'Showing 5 activities'}).waitFor();

   await page.locator('#owgEventLogCasesBrowse').click();
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'60 matching cases. Showing 30'}).waitFor();
   await page.getByRole('button',{name:'Next case page',exact:true}).click();
   await page.getByRole('button',{name:'First case page',exact:true}).click();
   await page.getByRole('button',{name:'Open case C0',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 5 of 5'}).waitFor();
   assert.match(await page.locator('#owgEventLogCaseEvents').textContent(),/Fix/);
   const variantSection=page.locator('#owgEventLogResults h3',{hasText:'Most common variants'}).locator('..');
   await variantSection.getByRole('button',{name:'Inspect cases',exact:true}).first().click();
   await page.locator('#owgEventLogCasesPath').filter({hasText:'Exact path:'}).waitFor();
   await page.getByRole('button',{name:'Inspect cases containing Fix',exact:true}).click();
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'20 matching cases.'}).waitFor();
   // Inductive-miner style discovery into a reviewable catalog draft.
   await page.locator('#owgDiscoveryRun').click();
   await page.locator('#owgDiscoveryStatus').filter({hasText:'checked cases'}).waitFor();
   let outline=await page.locator('#owgDiscoveryOutline').textContent();
   assert.match(outline,/Receive\nRepeat as needed \(20 repeats\):\n {2}Check\n {2}going round again through:\n {4}Fix\nApprove\nClose/,'rework loop');
   assert.doesNotMatch(outline,/\(skip\)/,'the 10% that skip approval is below the default noise filter');
   await page.locator('#owgDiscoveryNoise').fill('0');
   await page.locator('#owgDiscoveryRun').click();
   await page.locator('#owgDiscoveryOutline').filter({hasText:'(skip)'}).waitFor();
   outline=await page.locator('#owgDiscoveryOutline').textContent();
   assert.match(outline,/Choose one of:\n {2}Option 1 · 6 cases:\n {4}\(skip\)\n {2}Option 2 · 54 cases:\n {4}Approve\nClose/);
   assert.match(await page.locator('#owgDiscoveryStatus').textContent(),/fits 60 of 60 checked cases/);
   const [snapshotResponse]=await Promise.all([
     page.waitForResponse(r=>r.url().endsWith('/model-discovery-draft')&&r.request().method()==='POST'),
     page.locator('#owgDiscoveryDraft').click()
   ]);
   assert.equal(snapshotResponse.status(),200);
   const sourceSnapshot=(await snapshotResponse.json()).source_snapshot;
   assert.match(sourceSnapshot.reference,/^urn:openworkgraph:local-log-discovery:[0-9a-f]{64}$/);
   await page.locator('#owgCatalogStatus').filter({hasText:'Unsaved draft discovered from an event log'}).waitFor();
   const draftRules=await page.locator('#owgCatalogEditor').evaluate(node=>[...node.querySelectorAll('input,textarea')].map(i=>i.value).join('\n'));
   assert.match(draftRules,/Observed choice, not a confirmed rule: 54 of 60 cases/);
   await page.locator('#owgCatalogEditor').getByRole('textbox',{name:'Business goal',exact:true}).fill('Decide claims');
   await page.locator('#owgCatalogReviewed').check();
   const [savedResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/process-catalog'&&r.request().method()==='POST'),
     page.locator('#owgCatalogEditor').getByRole('button',{name:'Save new revision',exact:true}).click()
   ]);
   assert.equal(savedResponse.status(),200);
   const savedDiscovery=await savedResponse.json();
   assert.deepEqual(savedDiscovery.evidence_refs,[sourceSnapshot.reference]);
   await page.getByRole('button',{name:'Inspect discovery source snapshots',exact:true}).click();
   await page.locator('#owgCatalogOriginResult').getByText('Source data unchanged',{exact:true}).waitFor();
   assert.match(await page.locator('#owgCatalogOriginResult').textContent(),/User-selected reference to an edited graph/);
   assert.match(await page.locator('#owgCatalogOriginResult').textContent(),/"noise": 0/);
   let heldOrigin,signalOrigin;const originHeld=new Promise(resolve=>{signalOrigin=resolve;});
   await page.route('**/event-log-discovery/process-origin?*',route=>{heldOrigin=route;signalOrigin();});
   await page.getByRole('button',{name:'Inspect discovery source snapshots',exact:true}).click();await originHeld;
   const catalogStatusBefore=await page.locator('#owgCatalogStatus').textContent();
   await page.locator('#owgCatalogEditor').getByRole('textbox',{name:'Business goal',exact:true}).fill('New unsaved goal');
   const failedOrigin=page.waitForResponse(r=>r.url().includes('/event-log-discovery/process-origin'));
   await heldOrigin.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'OLD_STALE_SOURCE_ERROR'})});
   await (await failedOrigin).finished();
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.equal(await page.locator('#owgCatalogStatus').textContent(),catalogStatusBefore,'an old failed check must not replace status after an edit');
   await page.unroute('**/event-log-discovery/process-origin?*');
   await page.locator('#owgCatalogEditor').getByRole('textbox',{name:'Business goal',exact:true}).fill('Decide claims');
   await page.getByRole('button',{name:'Add saved revision to automation pipeline',exact:true}).click();
   await page.locator('#owgPipelineJourney').click();
   await page.locator('#owgPipelineJourneyResult').getByText(/Dataset and cohort → process: source data unchanged/).waitFor();
   assert((await page.locator('#owgPipelineJourneyResult').textContent()).includes(sourceSnapshot.source_content_sha256));
   await page.addScriptTag({content:axeSource});
   const discoveryAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCatalogEditor'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(discoveryAudit.violations.map(v=>v.id),[]);
   const discoveredBPMN=await page.evaluate(async saved=>fetch('/v1/process-catalog/export?'+new URLSearchParams({workflow_id:saved.workflow_id,revision:String(saved.revision),format:'bpmn'})).then(r=>r.text()),savedDiscovery);
   assert.match(discoveredBPMN,/exclusiveGateway/);
   // A completed snapshot request must not replace the saved editor after the
   // user refreshes analysis. Accept any unexpected confirmation so a missing
   // generation guard would visibly replace the graph and fail this assertion.
   let heldSnapshot,signalSnapshot;const snapshotHeld=new Promise(resolve=>{signalSnapshot=resolve;});
   await page.route('**/model-discovery-draft',route=>{heldSnapshot=route;signalSnapshot();});
   await page.locator('#owgDiscoveryDraft').click();await snapshotHeld;
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgWaitingRun').waitFor();
   let lateDraftDialogs=0;const lateDialog=dialog=>{lateDraftDialogs++;return dialog.accept();};
   page.on('dialog',lateDialog);
   const lateResponse=page.waitForResponse(r=>r.url().endsWith('/model-discovery-draft'));
   await heldSnapshot.continue();await (await lateResponse).finished();
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   page.off('dialog',lateDialog);await page.unroute('**/model-discovery-draft');
   assert.equal(lateDraftDialogs,0,'a stale response must not ask to replace the current saved graph');
   await page.locator('#owgCatalogEditor').getByRole('heading',{name:'Review process · revision 1',exact:true}).waitFor();
   await page.locator('#owgWaitingRun').click();
   await page.locator('#owgWaitingStatus').filter({hasText:'No start or complete values'}).waitFor();
   await page.locator('#owgDotsRun').click();
   await page.locator('#owgDotsStatus').filter({hasText:'60 of 60 cases with 274 events.'}).waitFor();
   assert.match(await page.locator('#owgDotsLegend').textContent(),/^Check · 80/);
   await page.locator('#owgDotsCanvas').scrollIntoViewIfNeeded();
   const dot=await page.locator('#owgDotsCanvas').evaluate(canvas=>{const p=canvas.__owgPoints[0],r=canvas.getBoundingClientRect();return {x:r.left+p.x,y:r.top+p.y};});
   await page.mouse.move(dot.x,dot.y);
   await page.locator('#owgDotsTip').filter({hasText:'Case C20 · Receive · 2024-01-01 08:15'}).waitFor().catch(async error=>{
     throw new Error('tooltip: '+await page.locator('#owgDotsTip').textContent()+' '+JSON.stringify(dot)+' '+error.message);});
   await page.locator('#owgDotsTime').selectOption('relative');await page.locator('#owgDotsOrder').selectOption('duration');
   await page.locator('#owgDotsRun').click();
   await page.locator('#owgDotsResults summary').waitFor();
   const firstRow=await page.locator('#owgDotsResults tbody tr').first().textContent();
   assert.match(firstRow,/^C\d+/);assert.match(firstRow,/15\.0 h/,'longest case first');
   await page.locator('#owgConceptDriftRun').click();
   await page.locator('#owgConceptDriftStatus').filter({hasText:'among 0 window boundaries tested'}).waitFor();
   await page.locator('#owgConceptDriftWindow').selectOption('cases');await page.locator('#owgConceptDriftCases').fill('20');
   await page.locator('#owgConceptDriftRun').click();
   await page.locator('#owgConceptDriftStatus').filter({hasText:'0 change points among 2 window boundaries tested.'}).waitFor();
   assert.match(await page.locator('#owgConceptDriftResults').textContent(),/cases 1–20\s*20/);
   // Save a reviewed graph through the actual catalog and pin a historical revision.
   const saved=await page.evaluate(async()=>{
     const labels=['Receive','Check','Approve','Close'];
     const process_model={goal:'Complete the declared claim review',baseline_source:'user_declared',baseline_path:labels,
       activities:labels.map(label=>({id:label.toLowerCase(),label})),
       transitions:labels.slice(1).map((label,i)=>({source:labels[i].toLowerCase(),target:label.toLowerCase()})),
       entry_activity_id:'receive',terminal_activity_ids:['close']};
     const body={workflow_id:'claims_review',title:'Reviewed claims',user_confirmed:true,expected_revision:0,process_model};
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(!response.ok)throw new Error(await response.text());return {record:await response.json(),body};
   });
   await page.locator('#owgEventLogGraphLoad').click();
   await page.locator('#owgEventLogGraphPick').selectOption('claims_review');
   await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogSavedGraphMap svg').waitFor();
   await page.locator('#owgEventLogSavedGraphMap').getByRole('button',{name:'Select activity check',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgEventLogCasesStatus').filter({hasText:'60 matching cases'}).waitFor();
   const resultCell=()=>page.locator('#owgEventLogGraphResults table').first().locator('tbody tr td');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='60');
   assert.equal(await resultCell().nth(1).textContent(),'36');assert.equal(await resultCell().nth(3).textContent(),'0');
   const [graphJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogGraphJson').click()]);
   const comparison=JSON.parse(await fs.readFile(await graphJson.path(),'utf8'));
   assert.equal(comparison.process_reference.content_sha256,saved.record.content_sha256);
   assert.equal(comparison.policy_compliance_verified,false);
   assert.ok(comparison.structural_fitness.score>0&&comparison.structural_fitness.score<1);
   assert.equal(comparison.structural_fitness.lower_bound,comparison.structural_fitness.upper_bound);
   assert.equal(comparison.visible_prefix_precision.score,1);
   assert.equal(comparison.visible_prefix_precision.matching_cases,36);
   assert.equal(comparison.visible_prefix_precision.case_population_coverage,0.6);
   assert.match(await page.locator('#owgEventLogGraphMetrics').textContent(),/100\.0%/);
   assert.equal(comparison.token_replay.replayed_cases,60);assert.equal(comparison.token_replay.fitting_cases,36);
   assert.match(await page.locator('#owgEventLogGraphTokenReplay').textContent(),/36 of 60/);
   const [metricCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogGraphCsv').click()]);
   const metricText=await fs.readFile(await metricCsv.path(),'utf8');
   assert.match(metricText,/fitness_lower_bound,fitness_upper_bound,analyzed_fitness,visible_label_precision,precision_matching_cases,precision_case_coverage/);
   assert.match(metricText,/,1,36,0\.6\r\n$/);
   await page.evaluate(async body=>{
     body.expected_revision=1;body.process_model.activities[2].label='Authorize';
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(!response.ok)throw new Error(await response.text());
   },saved.body);
   let graphRelease,graphSeen;
   const graphGate=new Promise(resolve=>graphRelease=resolve),graphFetched=new Promise(resolve=>graphSeen=resolve);
   await page.route('**/process-conformance*',async route=>{const response=await route.fetch();graphSeen();await graphGate;await route.fulfill({response});});
   await page.locator('#owgEventLogGraphRun').click();await graphFetched;
   await page.locator('#owgEventLogGraphRevision').fill('2');
   assert.equal(await page.locator('#owgEventLogGraphRun').isDisabled(),true);
   await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'revision 2.'}).waitFor();
   const oldGraph=page.waitForResponse(response=>response.url().includes('/process-conformance'));
   graphRelease();await (await oldGraph).finished();await page.unroute('**/process-conformance*');
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.equal(await page.locator('#owgEventLogGraphResults').textContent(),'','old revision results cannot repaint a newly loaded graph');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='60');
   assert.equal(await resultCell().nth(1).textContent(),'0');assert.equal(await resultCell().nth(2).textContent(),'60');
   await page.locator('#owgEventLogSavedGraphMap').getByRole('button',{name:'Select activity approve',exact:true}).click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'No displayed log activity has the exact label'}).waitFor();
   await page.locator('#owgEventLogGraphRevision').fill('1');await page.locator('#owgEventLogGraphOpen').click();
   await page.locator('#owgEventLogGraphStatus').filter({hasText:'revision 1.'}).waitFor();
   await page.locator('#owgEventLogGraphCohortResource').fill('ann');
   await page.locator('#owgEventLogGraphRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgEventLogGraphResults table tbody tr td')?.textContent==='20');
   assert.equal(await resultCell().nth(2).textContent(),'20');
   await page.locator('#owgEventLogGraphCohortResource').fill('absent');await page.locator('#owgEventLogGraphRun').click();
   await page.locator('#owgEventLogGraphResults').getByText('No cases match this selection; no verdict was established.').waitFor();
   assert.match(await page.locator('#owgEventLogGraphMetrics').textContent(),/Unknown/);
   await page.locator('#owgEventLogSla').fill('4');await page.locator('#owgEventLogBots').fill('bot');
   await page.locator('#owgEventLogKpiRun').click();
   await page.getByText('Rework rate').waitFor();
   const kpi=await page.locator('#owgEventLogKpiRun').locator('xpath=../..').textContent();
   assert.match(kpi,/33\.3%/,'rework and SLA breach both cover the 20 slow cases');
   await page.locator('#owgEventLogKpiCohortResource').fill('ann');
   await page.locator('#owgEventLogKpiRun').click();
   await page.waitForFunction(()=>/\b20\b/.test(document.querySelector('#owgEventLogKpiRun').parentElement.parentElement.querySelector('table tbody tr td').textContent));
   const [kpiCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogKpiCsv').click()]);
   const kpiText=await fs.readFile(await kpiCsv.path(),'utf8');
   assert.match(kpiText,/^metric,value\r\ncases,20\r\n/);assert.match(kpiText,/rework_rate,1\r\n/);

   await page.locator('#owgEventLogCohortALabel').fill('Bob');await page.locator('#owgEventLogCohortAResource').fill('bob');
   await page.locator('#owgEventLogCohortBLabel').fill('Ann');await page.locator('#owgEventLogCohortBResource').fill('ann');
   await page.locator('#owgEventLogBenchRun').click();
   await page.getByText('Difference (second − first)').waitFor();
   const bench=await page.locator('#owgEventLogBenchRun').locator('xpath=../..').textContent();
   assert.match(bench,/Bob40/);assert.match(bench,/Ann20/);assert.match(bench,/Rework rate\+100\.0 pts/);assert.match(bench,/do not say why/);
   const [benchJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogBenchmarkJson').click()]);
   assert.equal(JSON.parse(await fs.readFile(await benchJson.path(),'utf8')).cohorts[1].cases,20);
   await page.locator('#owgMapCompareRun').click();
   await page.locator('#owgMapCompareResults').getByText('only in Ann',{exact:false}).first().waitFor();
   const firstPath=await page.locator('#owgMapCompareResults tbody tr').first().textContent();
   assert.match(firstPath,/^Check → Fix0\.0% → 100\.0%\+100\.0 pts/);
   assert.match(await page.locator('#owgMapCompareResults h4').allTextContents().then(t=>t.join('|')),/^Bob · 40 cases\|Ann · 20 cases$/);
   assert.equal(await page.locator('#owgMapCompareResults svg').count()>=2,true,'both maps are drawn');

   await page.locator('#owgEventLogSimLoad').click();
   await page.locator('#owgEventLogSimRun').waitFor();
   assert.match(await page.locator('#owgEventLogSimForm').textContent(),/Calibrated 60 of 60 complete cases/);
   const measuredPaths=await page.evaluate(()=>window.__owgEventLogModel.parseWeightedPaths(document.querySelector('#owgEventLogSimPaths').value));
   assert.deepEqual(measuredPaths[0],{activities:['Receive','Check','Approve','Close'],weight:0.6});
   await page.locator('#owgEventLogSimNewName').fill('Validate');await page.locator('#owgEventLogSimAdd').click();
   const validate=page.getByLabel('Minutes for Validate');await validate.fill('5');
   await page.locator('#owgEventLogSimNewName').fill('__proto__');await page.locator('#owgEventLogSimAdd').click();
   await page.getByLabel('Minutes for __proto__',{exact:true}).fill('7');
   await page.getByLabel('Resources for __proto__',{exact:true}).fill('2');
   await page.getByLabel('Resources for Check').fill('3');
   await page.locator('#owgEventLogSimPaths').fill(JSON.stringify([{activities:['Receive','Validate','__proto__','Check','Approve','Close'],weight:0.8},{activities:['Receive','Check','Fix','Check','Approve','Close'],weight:0.2}]));
   await page.locator('#owgEventLogSimLabel').fill('Validate first');await page.locator('#owgEventLogSimCases').fill('500');
   const [simulationResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const special=(await simulationResponse.json()).result.activities.find(a=>a.activity==='__proto__');
   assert.equal(special.minutes,7);assert.equal(special.resources,2);
   await page.getByText('Simulated baseline').waitFor();
   const simulated=await page.locator('#owgEventLogSimRun').locator('xpath=../..').textContent();
   assert.match(simulated,/Validate first/);assert.match(simulated,/Median cycle time change/);assert.match(simulated,/not forecasts/);assert.match(simulated,/Poisson/);
   // Held-out validation: calibrate on earlier cases only and compare with the later ones.
   const [validationResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulation/validation')),page.locator('#owgEventLogSimValidate').click()]);
   const validation=await validationResponse.json();
   assert.equal(validationResponse.status(),200,JSON.stringify(validation));assert.equal(validation.forecast_or_guarantee,false);
   assert.ok(validation.cases.held_out>=10&&validation.cases.calibration>=10);
   await page.locator('#owgEventLogSimValidation').getByRole('columnheader',{name:'Later cases'}).waitFor();
   assert.match(await page.locator('#owgEventLogSimValidation').textContent(),/Kolmogorov–Smirnov|Too few cases/);
   assert.match(await page.locator('#owgEventLogSimValidation').textContent(),/cases spanning the cutoff are left out/);
   // Mean measured times: the validation request carries the basis, and the form refills measured minutes.
   await page.locator('#owgEventLogSimHoldoutBasis').selectOption('mean');
   const [meanResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulation/validation')),page.locator('#owgEventLogSimValidate').click()]);
   assert.equal((await meanResponse.json()).activity_time_basis,'mean');
   const medianFix=await page.locator('[aria-label="Minutes for Fix"]').inputValue();
   await page.locator('#owgEventLogSimTimeBasis').selectOption('mean');
   const meanFix=await page.locator('[aria-label="Minutes for Fix"]').inputValue();
   assert.ok(Number(meanFix)>0&&Number(medianFix)>0,'both bases give a measured time for Fix');
   await page.locator('#owgEventLogSimTimeBasis').selectOption('median');
   assert.equal(await page.locator('[aria-label="Minutes for Fix"]').inputValue(),medianFix,'switching back restores the median');
   // Working calendar: weekdays 09:00–17:00 in Berlin for both runs, plus Saturdays in the scenario.
   assert.equal(await page.locator('#owgEventLogSimTimezone').isVisible(),false,'calendar fields appear only with working days');
   await page.locator('#owgEventLogSimDays').selectOption('weekdays');
   await page.locator('#owgEventLogSimTimezone').fill('Europe/Berlin');await page.locator('#owgEventLogSimHolidays').fill('2025-12-25');
   await page.locator('#owgEventLogSimScenarioDays').selectOption('weekdays_sat');
   const [calendarResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const calendared=await calendarResponse.json();
   assert.deepEqual(Object.keys(calendared.baseline_calendar.hours),['mon','tue','wed','thu','fri']);
   assert.equal(calendared.scenario.calendar.hours.sat[0][0],'09:00');assert.deepEqual(calendared.baseline_calendar.holidays,['2025-12-25']);
   await page.getByText('Working time: baseline mon, tue, wed, thu, fri 09:00–17:00 (Europe/Berlin), 1 holidays; scenario mon, tue, wed, thu, fri, sat').waitFor();
   assert.match(await page.locator('#owgEventLogSimRun').locator('xpath=../..').textContent(),/pauses outside them/);
   const badCalendar=await page.evaluate(()=>{try{window.__owgEventLogModel.simCalendar({days:'weekdays',from:'17:00',to:'09:00',timezone:'UTC'});return 'accepted';}catch(e){return e.message;}});
   assert.match(badCalendar,/end after the start/);
   // A shared pool in the scenario: Check and Approve done by one team of two through one queue.
   await page.locator('#owgEventLogSimPoolName').fill('Review team');await page.locator('#owgEventLogSimPoolResources').fill('2');
   await page.locator('#owgEventLogSimPoolActivities').selectOption(['Check','Approve']);
   const [poolResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const pooled=await poolResponse.json();
   assert.deepEqual(pooled.scenario.pools,{'Review team':{resources:2,activities:['Check','Approve']}});
   assert.equal(pooled.baseline.pools,undefined,'the measured baseline keeps dedicated resources');
   await page.getByRole('cell',{name:'Check (Review team)',exact:true}).waitFor();
   await page.getByRole('cell',{name:'Check, Approve',exact:true}).waitFor();
   const badPool=await page.evaluate(()=>{try{window.__owgEventLogModel.simPool({name:'Team',resources:'2',activities:['Check']});return 'accepted';}catch(e){return e.message;}});
   assert.match(badPool,/at least two activities/);
   // Seasonal arrivals for both runs: weekday office hours entered as weights, then measured from this log.
   await page.locator('#owgEventLogSimDays').selectOption('');
   assert.equal(await page.locator('#owgEventLogSimHourly').isVisible(),false,'weight fields appear only for entered weights');
   await page.locator('#owgEventLogSimArrivalMode').selectOption('weights');
   assert.equal(await page.locator('#owgEventLogSimTimezone').isVisible(),true,'the time zone is needed for an arrival pattern');
   await page.locator('#owgEventLogSimHourly').fill('0 0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 1 0 0 0 0 0 0 0');
   await page.locator('#owgEventLogSimDaily').fill('1 1 1 1 1 0 0');
   const [weightedResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const weighted=await weightedResponse.json();
   assert.equal(weightedResponse.status(),200);assert.equal(weighted.baseline_calendar,null);
   assert.deepEqual(weighted.baseline_arrival_pattern,weighted.scenario.arrival_pattern,'demand is shared by both runs');
   const byHour=weighted.result.arrivals_by_local_hour;
   assert.equal(byHour.slice(9,17).reduce((a,b)=>a+b,0),500);assert.equal(weighted.result.arrivals_by_local_weekday.sun,0);
   await page.getByText('Arrivals: your weights (Europe/Berlin); the busiest hour gets 4.2× the average rate').waitFor();
   await page.locator('#owgEventLogSimArrivalMode').selectOption('from_log');
   const [measuredResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const measured=(await measuredResponse.json()).scenario.arrival_pattern;
   assert.equal(measured.source,'measured_case_starts');assert.equal(measured.hourly_factors[3],0);
   (await measuredResponse.json()).result.arrivals_by_local_hour.forEach((n,h)=>assert.ok(n===0||measured.hourly_factors[h]>0,'arrivals only in measured hours'));
   await page.getByText(/Arrivals: measured from \d+ case starts over \d+ days \(Europe\/Berlin\)/).waitFor();
   assert.match(await page.locator('#owgEventLogSimRun').locator('xpath=../..').textContent(),/seasonal pattern/);
   const badArrivals=await page.evaluate(()=>{try{window.__owgEventLogModel.simArrivals({mode:'weights',hourly:'1 2'});return 'accepted';}catch(e){return e.message;}});
   assert.match(badArrivals,/24 non-negative hourly/);
   await page.locator('#owgEventLogSimArrivalMode').selectOption('');
   // Branch and arrival uncertainty: replications redraw what the log measured; edited values stay as entered.
   await page.locator('#owgEventLogSimReplications').fill('5');
   const [spreadResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const spreadRequest=JSON.parse(spreadResponse.request().postData());
   assert.deepEqual(spreadRequest.uncertainty,{replications:5});
   const spread=(await spreadResponse.json()).uncertainty;
   assert.equal(spreadResponse.status(),200);assert.equal(spread.replications,5);
   assert.ok(spread.measured_routing.length>0&&spread.measured_routing.every(p=>p.drawn.low<=p.drawn.high));
   await page.locator('#owgEventLogSimUncertainty').getByText('Uncertainty from routing and arrivals (5 replications, 95% range)').waitFor();
   assert.match(await page.locator('#owgEventLogSimUncertainty').textContent(),/The scenario median is faster in \d of 5 replications/);
   assert.ok(spread.measured_activity_minutes.length>0&&spread.measured_activity_minutes.every(a=>a.observed_gaps>0&&a.drawn.low<=a.drawn.high));
   assert.match(await page.locator('#owgEventLogSimUncertainty').textContent(),/Measured activityGaps measuredMinutes drawn \(95% range\)/);
   // Minutes still as measured are drawn by the server; minutes edited by hand stay in the request.
   const sentMinutes=Object.values(spreadRequest.activities||{}).filter(a=>'minutes' in a).length;
   assert.ok(sentMinutes<Object.keys((await spreadResponse.json()).calibration.activities).length,'unchanged measured minutes are left to the server');
   await page.locator('#owgEventLogSimReplications').fill('1');await page.locator('#owgEventLogSimRun').click();
   await page.getByText('Enter 2 to 50 replications, or leave it empty.').waitFor();
   await page.locator('#owgEventLogSimReplications').fill('');

   await page.locator('#owgEventLogModel').fill('Receive > Check > Approve > Close');
   await page.locator('#owgEventLogConformanceRun').click();
   await page.getByText('Skipped reference step').waitFor();
   const conformance=await page.locator('#owgEventLogConformanceRun').locator('xpath=..').textContent();
   assert.match(conformance,/Approve/);assert.match(conformance,/Fix/);

   await page.locator('#owgEventLogOutcomeHours').fill('5');
   await page.locator('#owgEventLogRootCauseRun').click();
   await page.getByText('Holds after correction').waitFor();
   const cause=await page.locator('#owgEventLogRootCauseRun').locator('xpath=../..').textContent();
   assert.match(cause,/activity: Fix/);assert.match(cause,/not causes/);

   await page.locator('#owgEventLogOrgRun').click();
   await page.locator('#owgEventLogOrgResults').getByText('Handovers').first().waitFor();
   let org=await page.locator('#owgEventLogOrgResults').textContent();
   assert.match(org,/Resource 01/);assert.doesNotMatch(org,/\bann\b|\bbob\b|\bbot\b/);assert.match(org,/No speed|not say who|Neither says/);
   await page.locator('#owgEventLogOrgNames').check();await page.locator('#owgEventLogOrgRun').click();
   await page.locator('#owgEventLogOrgResults').getByText('bot',{exact:true}).first().waitFor();
   const ruleBody=await page.evaluate(()=>window.__owgEventLogModel.signalRule({name:'Rework',kind:'kpi',metric:'rework_rate',operator:'above',threshold:'25'}));
   assert.deepEqual(ruleBody,{name:'Rework',when:{kind:'kpi',metric:'rework_rate',operator:'above',threshold:0.25}});
   await page.locator('#owgSignalName').fill('Skipped approval');await page.locator('#owgSignalCondition').selectOption('lacks_activity');
   await page.locator('#owgSignalActivity').selectOption('Approve');await page.locator('#owgSignalAdd').click();
   await page.locator('#owgSignalStatus').filter({hasText:'Signal saved.'}).waitFor();
   await page.locator('#owgSignalEvaluate').click();
   await page.locator('#owgSignalResults').getByText('signals hold now').waitFor();
   const signalText=await page.locator('#owgSignalResults').textContent();
   assert.match(signalText,/1 of 1 signals hold now/);assert.match(signalText,/6 cases/);assert.match(signalText,/C0\b/);assert.match(signalText,/first check/);
   await page.locator('#owgSignalEvaluate').click();
   await page.locator('#owgSignalResults').getByText('first check').waitFor({state:'detached'});

   await page.locator('#owgAnalysisDim1').selectOption('has_rework');await page.locator('#owgAnalysisMeasure2').selectOption('median:duration_seconds');
   await page.locator('#owgAnalysisRun').click();
   await page.locator('#owgAnalysisResults table').waitFor();
   const analysisText=await page.locator('#owgAnalysisResults').textContent();
   assert.match(analysisText,/60 matching cases/);assert.match(analysisText,/no rework40/);assert.match(analysisText,/rework20/);
   const [analysisCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await analysisCsv.path(),'utf8'),/^has_rework,count,median_duration_seconds\r\n/);
   await page.locator('#owgAnalysisName').fill('Rework split');await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   await page.locator('#owgAnalysisSaved').getByRole('button',{name:'Run saved analysis Rework split'}).click();
   const badSpec=await page.evaluate(()=>{try{window.__owgEventLogModel.analysisSpec({level:'cases',dim1:'attribute',attr1:' ',measure1:'count'});return 'accepted';}catch(e){return e.message;}});
   assert.equal(badSpec,'Enter the attribute name.');
   // A typed case metric: time from Receive to Approve, measured per rework group, then saved for reuse.
   await page.locator('#owgMetricName').fill('to_approval');
   await page.locator('#owgMetricFrom').selectOption('Receive');await page.locator('#owgMetricTo').selectOption('Approve');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricList').filter({hasText:'to_approval: time from the first Receive to the first Approve'}).waitFor();
   await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:to_approval');await page.locator('#owgAnalysisMeasure2').selectOption('');
   await page.locator('#owgAnalysisRun').click();
   await page.locator('#owgAnalysisResults').filter({hasText:'to_approval: 54 cases have a value, 6 do not'}).waitFor();
   const caseMetricText=await page.locator('#owgAnalysisResults').textContent();
   assert.match(caseMetricText,/no rework30 min/);assert.match(caseMetricText,/rework12\.0 h/);assert.match(caseMetricText,/6 without Approve/);
   await page.locator('#owgMetricSave').click();
   await page.getByRole('button',{name:'Add saved metric to_approval',exact:true}).waitFor();
   await page.getByRole('button',{name:'Add saved metric to_approval',exact:true}).click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'already has a metric named to_approval'}).waitFor();
   const badMetric=await page.evaluate(()=>{try{window.__owgEventLogModel.caseMetric({kind:'event_attribute_sum',name:'spend',attribute:'amount',unit:''});return 'accepted';}catch(e){return e.message;}});
   assert.match(badMetric,/unit/);
   // Arithmetic preserves source definitions, diagnoses invalid units and survives save/export/reuse.
   await page.locator('#owgMetricKind').selectOption('arithmetic');await page.locator('#owgMetricName').fill('half_wait');
   await page.locator('#owgMetricOperation').selectOption('divide');await page.locator('#owgMetricLeftMetric').selectOption('to_approval');
   await page.locator('#owgMetricRightKind').selectOption('constant');await page.locator('#owgMetricRightValue').fill('2');await page.locator('#owgMetricRightUnit').fill('ratio');
   await page.locator('#owgMetricAdd').click();await page.locator('#owgMetricList').filter({hasText:'half_wait: to_approval ÷ 2 ratio'}).waitFor();
   await page.getByRole('button',{name:'Remove metric to_approval',exact:true}).click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Remove dependent metric half_wait first.'}).waitFor();
   await page.locator('#owgMetricSave').click();await page.getByRole('button',{name:'Add saved metric half_wait',exact:true}).waitFor();
   await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:half_wait');
   const [arithmeticResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis')),page.locator('#owgAnalysisRun').click()]);
   const arithmeticResult=await arithmeticResponse.json();assert.equal(arithmeticResponse.status(),200);
   assert.deepEqual(arithmeticResult.rows,[['rework',21600],['no rework',900]]);
   assert.equal(arithmeticResult.metrics[1].unit,'seconds');assert.equal(arithmeticResult.metrics[1].cases_with_missing_operand,6);
   await page.locator('#owgAnalysisResults').filter({hasText:'6 with a missing operand, 0 with a zero divisor; unit seconds'}).waitFor();
   const [arithmeticJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisJson').click()]);
   assert.deepEqual(JSON.parse(await fs.readFile(await arithmeticJson.path(),'utf8')),arithmeticResult);
   const [arithmeticCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await arithmeticCsv.path(),'utf8'),/has_rework,median_metric:half_wait\r\nrework,21600\r\nno rework,900/);
   await page.locator('#owgAnalysisName').fill('Half approval wait');await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   await page.getByRole('button',{name:'Remove metric half_wait',exact:true}).click();
   await page.getByRole('button',{name:'Remove metric to_approval',exact:true}).click();
   await page.getByRole('button',{name:'Add saved metric half_wait',exact:true}).click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Add earlier metric to_approval first.'}).waitFor();
   const [rerunArithmetic]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis')),page.getByRole('button',{name:'Run saved analysis Half approval wait',exact:true}).click()]);
   assert.deepEqual((await rerunArithmetic.json()).rows,arithmeticResult.rows);
   await page.getByRole('button',{name:'Add saved metric to_approval',exact:true}).click();
   await page.locator('#owgMetricName').fill('bad_units');await page.locator('#owgMetricOperation').selectOption('add');await page.locator('#owgMetricRightUnit').fill('EUR');
   await page.locator('#owgMetricAdd').click();await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:bad_units');
   await page.locator('#owgAnalysisRun').click();await page.locator('#owgAnalysisStatus').filter({hasText:'identical units'}).waitFor();
   await page.getByRole('button',{name:'Remove metric bad_units',exact:true}).click();
   await page.locator('#owgMetricName').fill('zero_divide');await page.locator('#owgMetricOperation').selectOption('divide');await page.locator('#owgMetricRightValue').fill('0');await page.locator('#owgMetricRightUnit').fill('ratio');
   await page.locator('#owgMetricAdd').click();await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:zero_divide');await page.locator('#owgAnalysisRun').click();
   await page.locator('#owgAnalysisResults').filter({hasText:'6 with a missing operand, 54 with a zero divisor'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const arithmeticAxe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogAnalysis'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(arithmeticAxe.violations,[]);
   await page.locator('#owgPredictEnds').fill('Close');await page.locator('#owgPredictOutcome').selectOption('duration_over');
   await page.locator('#owgPredictHours').fill('5');await page.locator('#owgPredictRun').click();
   await page.locator('#owgPredictResults').getByText('open cases;').waitFor();
   const predictText=await page.locator('#owgPredictResults').textContent();
   assert.match(predictText,/0 open cases/);assert.match(predictText,/Backtest on \d+ later steps/);assert.match(predictText,/Brier score/);
   const predictSpec=await page.evaluate(()=>{try{window.__owgEventLogModel.predictionSpec({ends:' , ',outcome:''});return 'accepted';}catch(e){return e.message;}});
   assert.equal(predictSpec,'List the activities that end a case.');
   // Censoring-aware estimates: still-open cases are learned from, and the backtest replays the log as of a cutoff.
   await page.locator('#owgPredictOutcome').selectOption('');await page.locator('#owgPredictEstimator').selectOption('survival');
   const [survivalResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/predictions')),page.locator('#owgPredictRun').click()]);
   const survivalResult=await survivalResponse.json();
   assert.equal(survivalResponse.status(),200);assert.equal(survivalResult.estimator,'survival');assert.equal(survivalResult.backtest.design,'as_of_replay');
   await page.locator('#owgPredictResults').getByText(/As-of backtest at \d{4}-\d{2}-\d{2}: \d+ cases were in progress/).waitFor();
   if(survivalResult.predictions.length)assert.match(await page.locator('#owgPredictResults').textContent(),/from now|withheld/);
   assert.equal(await page.evaluate(()=>{try{window.__owgEventLogModel.predictionSpec({ends:'Close',estimator:'magic'});return 'accepted';}catch(e){return e.message;}}),'Choose what to learn from.');
   await page.locator('#owgPredictEstimator').selectOption('completed_cases');

   await page.locator('#owgComplianceTemplate').selectOption('response');
   await page.locator('#owgComplianceA').selectOption('Receive');await page.locator('#owgComplianceB').selectOption('Approve');
   await page.locator('#owgComplianceAdd').click();
   await page.locator('#owgComplianceTemplate').selectOption('segregation_of_duties');
   await page.locator('#owgComplianceA').selectOption('Check');await page.locator('#owgComplianceB').selectOption('Approve');
   await page.locator('#owgComplianceAdd').click();
   await page.locator('#owgComplianceRun').click();
   await page.locator('#owgComplianceResults table').waitFor();
   const complianceText=await page.locator('#owgComplianceResults').textContent();
   assert.match(complianceText,/1 of 2 rules have violations/);
   assert.match(complianceText,/every Receive is eventually followed by Approve606/,'60 cases apply, 6 skip Approve');
   assert.doesNotMatch(complianceText,/\bann\b|\bbob\b/);
   await page.getByRole('button',{name:'Save as signal: every Receive is eventually followed by Approve'}).click();
   await page.locator('#owgComplianceStatus').filter({hasText:'Saved as a signal.'}).waitFor();

   await page.evaluate(()=>{window.scrollTo(0,0);});
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   const found=audit.violations.map(v=>`${v.id}: ${v.nodes[0].target.join(' ')}`);
   assert.deepEqual(found,[],'event-log panel WCAG violations:\n'+found.join('\n'));

   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   await page.setViewportSize({width:1280,height:900});
   await page.locator('#owgEventLogExportFormat').selectOption('ocel');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
   const exported=JSON.parse(await fs.readFile(await download.path(),'utf8'));
   assert.equal(exported.events.length,274);assert.equal(exported.objects.length,60);

   // A newer, overlapping export: one known event is skipped, two new events (one new case) are added.
   const newer=['case_id;step;time;who',rows[1],'C200;Receive;2024-02-01T08:00:00Z;bob','C200;Check;2024-02-01T09:00:00Z;bot'].join('\n');
   await page.locator('#owgEventLogFile').setInputFiles({name:'claims-newer.csv',mimeType:'text/csv',buffer:Buffer.from(newer)});
   await page.locator('#owgEventLogDelimiter').selectOption(';');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogActivity').selectOption('step');
   assert.equal(await page.locator('#owgEventLogAppend').isDisabled(),true,'append needs explicit local-storage consent');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogAppend').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Added 2 new events'}).waitFor();
   assert.match(await page.locator('#owgEventLogStatus').textContent(),/skipped 1 already in the log/);
   assert.match(await page.locator('#owgEventLogPick option:checked').textContent(),/276 events/);
   await page.locator('#owgPipelineJourney').click();
   await page.locator('#owgPipelineJourneyResult').getByText(/Dataset and cohort → process: source data changed since snapshot/).waitFor();

   // The kept rejected row (C99, a time without an offset) is released under an explicit offset decision.
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgQuarantine').filter({hasText:'1 rows kept (1 time without a UTC offset)'}).waitFor();
   assert.match(await page.locator('#owgQuarantine').textContent(),/C99Receive2024-01-05 09:00:00time without a UTC offset/);
   await page.locator('#owgQuarantineOffset').fill('local');
   await page.locator('#owgQuarantineRelease').click();
   await page.locator('#owgQuarantineStatus').filter({hasText:'+01:00'}).waitFor();
   await page.locator('#owgQuarantineOffset').fill('+01:00');
   await page.locator('#owgQuarantineRelease').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Released 1 of 1 kept rows assuming UTC+01:00: 1 events added; 0 rows still kept.'}).waitFor();
   assert.match(await page.locator('#owgEventLogPick option:checked').textContent(),/277 events/);
   await page.locator('#owgQuarantine').filter({hasText:'Released with offset +01:00'}).waitFor();
   assert.equal(await page.locator('#owgQuarantineRelease').isDisabled(),true);
   const releaseAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgQuarantine'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(releaseAudit.violations.map(v=>v.id),[]);

   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   await page.locator('#owgPipelineJourney').click();
   await page.locator('#owgPipelineJourneyResult').getByText(/Dataset and cohort → process: snapshot unavailable/).waitFor();
   await page.getByRole('button',{name:'Inspect discovery source snapshots',exact:true}).click();
   await page.locator('#owgCatalogOriginResult').getByText('Source snapshot unavailable',{exact:true}).waitFor();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.locator('#owgEventLogManage').isHidden(),true);
   const listed=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length);
   assert.equal(listed,0);
   // Literal headers, case IDs, labels and resources survive the complete UI/API path.
   const literalCSV=' case , activity , time , who \n Case, Review > ,2024-01-01T00:00:00Z," bot,system "\nCase ,Review→ ,2024-01-01T00:00:00Z,bot \n';
   await page.locator('#owgEventLogFile').setInputFiles({name:'literal.csv',mimeType:'text/csv',buffer:Buffer.from(literalCSV)});
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').value===' case ');
   await page.locator('#owgEventLogActivity').selectOption(' activity ');
   await page.locator('#owgEventLogTime').selectOption(' time ');
   await page.locator('#owgEventLogResource').selectOption(' who ');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogModel').waitFor();
   const suggested=JSON.parse(await page.locator('#owgEventLogModel').inputValue());
   assert.ok([' Review > ','Review→ '].includes(suggested.paths[0][0]));
   await page.locator('#owgEventLogModel').fill(JSON.stringify({format:'openworkgraph.reference-model.v1',paths:[[' Review > '],['Review→ ']]}));
   await page.locator('#owgEventLogConformanceRun').click();
   await page.getByText('Fitting cases',{exact:true}).waitFor();
   assert.match(await page.locator('#owgEventLogConformanceRun').locator('xpath=../..').textContent(),/100\.0%/);
   await page.locator('#owgEventLogBots').fill(JSON.stringify([' bot,system ']));
   await page.locator('#owgEventLogKpiRun').click();
   await page.getByText('Automation rate',{exact:true}).waitFor();
   assert.match(await page.locator('#owgEventLogKpiRun').locator('xpath=../..').textContent(),/50\.0%/);
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   assert.equal(await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.length),0);
   const longRows=['case,activity,time,who',...Array.from({length:205},(_,i)=>'Long,Step '+i+',2024-01-01T00:00:00Z,bot')];
   await page.locator('#owgEventLogFile').setInputFiles({name:'long.csv',mimeType:'text/csv',buffer:Buffer.from(longRows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').value==='case');
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 205 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogCasesBrowse').waitFor();
   let release,seen;
   const gate=new Promise(resolve=>release=resolve),fetched=new Promise(resolve=>seen=resolve);
   await page.route('**/cases/query*',async route=>{const response=await route.fetch();seen();await gate;await route.fulfill({response});});
   await page.locator('#owgEventLogCasesBrowse').click();await fetched;
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogCasesBrowse').waitFor();
   const stale=page.waitForResponse(response=>response.url().includes('/cases/query'));
   release();await (await stale).finished();await page.unroute('**/cases/query*');
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.equal(await page.locator('#owgEventLogCasesStatus').textContent(),'','stale case response cannot repaint a new analysis');
   await page.locator('#owgEventLogCasesBrowse').click();
   await page.getByRole('button',{name:'Open case Long',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 100 of 205'}).waitFor();
   await page.locator('#owgEventLogCaseEventsMore').click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 200 of 205'}).waitFor();
   await page.locator('#owgEventLogCaseEventsMore').click();
   await page.locator('#owgEventLogCaseEvents [role=status]').filter({hasText:'Showing 205 of 205'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCaseEvents tbody tr').count(),205);
   assert.match(await page.locator('#owgEventLogCaseEvents').textContent(),/204 equal-timestamp events/);
   await page.locator('#owgEventLogSimLoad').click();
   await page.locator('#owgEventLogSimForm').filter({hasText:'No complete paths fit the simulation limits'}).waitFor();
   assert.match(await page.locator('#owgEventLogSimForm').textContent(),/Calibrated 0 of 1 complete cases/);
   assert.equal(await page.locator('#owgEventLogSimRun').count(),0,'a partial long case cannot become a simulated baseline');
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogManage').waitFor({state:'hidden'});
   // Object-centric log: separate maps per object type, linked by shared events.
   const ocel={objectTypes:[],eventTypes:[],objects:[{id:'o1',type:'order',attributes:[{name:'status',time:'1970-01-01T00:00:00Z',value:'open'},{name:'status',time:'2024-01-01T11:00:00Z',value:'shipped'}]},{id:'i1',type:'item'},{id:'i2',type:'item'}],events:[
     {id:'p',type:'Place order',time:'2024-01-01T08:00:00Z',relationships:[{objectId:'o1',qualifier:''},{objectId:'i1',qualifier:''},{objectId:'i2',qualifier:''}]},
     {id:'k1',type:'Pick item',time:'2024-01-01T09:00:00Z',relationships:[{objectId:'i1',qualifier:''}]},
     {id:'k2',type:'Pick item',time:'2024-01-01T10:00:00Z',relationships:[{objectId:'i2',qualifier:''}]},
     {id:'s',type:'Ship',time:'2024-01-01T12:00:00Z',relationships:[{objectId:'o1',qualifier:''},{objectId:'i1',qualifier:''},{objectId:'i2',qualifier:''}]}]};
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(ocel))});
   assert.equal(await page.locator('#owgEventLogFormat').inputValue(),'ocel');
   assert.equal(await page.locator('#owgEventLogMapping').isHidden(),true);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 4 events'}).waitFor();
   await page.locator('#owgEventLogCaseType').selectOption('item');
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogObjectGraph').waitFor();
   assert.equal(await page.locator('#owgEventLogObjectType').inputValue(),'item');
   const itemMap=await page.locator('#owgEventLogObjectMap').textContent();
   assert.match(itemMap,/Pick item/);assert.match(itemMap,/Linked to/);assert.match(itemMap,/order/);
   // What analysing items as the case does: shared events are copied into each item's case.
   await page.locator('#owgEventLogFlattening').getByText('2 events are linked to several item objects and are counted once per object: 2 extra event rows, 1.50× the linked events (most from Place order, Ship).',{exact:true}).waitFor();
   assert.match(await page.locator('#owgEventLogFlattening').textContent(),/No events are left out\./);
   await page.locator('#owgEventLogObjectGraph').getByRole('button',{name:'Inspect observed transition Place order → Pick item',exact:true}).focus();
   await page.keyboard.press('Enter');
   assert.match(await page.locator('#owgEventLogObjectSelection').textContent(),/2 recorded adjacent transitions across 2 item objects/);
   await page.locator('#owgEventLogObjectSelection').getByRole('button',{name:'Browse cases for adjacent transition',exact:true}).click();
   await page.locator('#owgEventLogObjectCasesStatus').filter({hasText:'2 matching cases'}).waitFor();
   await page.locator('#owgEventLogObjectCasesList').getByRole('button',{name:'Open case i1',exact:true}).click();
   await page.locator('#owgEventLogObjectCasesEvents [role=status]').filter({hasText:'Showing 3 of 3'}).waitFor();
   assert.match(await page.locator('#owgEventLogObjectCasesEvents').textContent(),/Pick item/);
   await page.locator('#owgEventLogObjectGraph').getByRole('button',{name:'Inspect observed activity Pick item',exact:true}).click();
   await page.locator('#owgEventLogObjectSelection').getByRole('button',{name:'Browse cases containing Pick item',exact:true}).click();
   await page.locator('#owgEventLogObjectCasesStatus').filter({hasText:'2 matching cases'}).waitFor();
   await page.locator('#owgEventLogObjectCasesFilterResource').fill('missing resource');
   await page.locator('#owgEventLogObjectCasesBrowse').click();
   await page.locator('#owgEventLogObjectCasesStatus').filter({hasText:'0 matching cases'}).waitFor();
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Activities shared by several object types/);
   let mapRelease,mapSeen;
   const mapGate=new Promise(resolve=>mapRelease=resolve),mapFetched=new Promise(resolve=>mapSeen=resolve);
   await page.route('**/cases/query*',async route=>{const response=await route.fetch();mapSeen();await mapGate;await route.fulfill({response});});
   await page.locator('#owgEventLogObjectCasesFilterResource').fill('');
   await page.locator('#owgEventLogObjectCasesBrowse').click();await mapFetched;
   await page.locator('#owgEventLogObjectType').selectOption('order');
   mapRelease();await page.unrouteAll({behavior:'wait'});
   await page.waitForFunction(()=>!/Pick item/.test(document.querySelector('#owgEventLogObjectMap').textContent));
   assert.equal(await page.locator('#owgEventLogObjectCasesList tbody tr').count(),0,'delayed item evidence cannot repaint the order browser');
   assert.match(await page.locator('#owgEventLogObjectMap').textContent(),/Place order.*Ship/s);
   await page.locator('#owgEventLogFlattening').getByText('2 events (50.0% of the log) are linked to no order and are left out: Pick item (2).',{exact:true}).waitFor();
   // Order status changes over time: the shipping event happened while it held another value than the stored one.
   await page.locator('#owgEventLogObjectAttributes').getByText('status changes on 1 of 1 order objects (1 changes; most often open → shipped (1)). 1 of 2 event links happened while it held another value than the one stored value analyses use.',{exact:true}).waitFor();
   assert.equal(await page.locator('#owgEventLogObjectMap #owgEventLogObjectAttributes').count(),0);
   // Object rules across types: the order ships after both items are picked; placing it involves only two items.
   await page.locator('#owgObjectRuleTemplate').selectOption('synchronization');
   await page.locator('#owgObjectRuleActivity').selectOption('Ship');await page.locator('#owgObjectRuleType').selectOption('order');
   await page.locator('#owgObjectRuleRelated').selectOption('item');await page.locator('#owgObjectRuleRequired').selectOption('Pick item');
   await page.locator('#owgObjectRuleAdd').click();
   await page.locator('#owgObjectRuleTemplate').selectOption('event_cardinality');
   assert.equal(await page.locator('#owgObjectRuleRequired').isHidden(),true);
   await page.locator('#owgObjectRuleActivity').selectOption('Place order');await page.locator('#owgObjectRuleType').selectOption('item');
   await page.locator('#owgObjectRuleMin').fill('3');await page.locator('#owgObjectRuleAdd').click();
   assert.equal(await page.locator('#owgObjectRuleList tbody tr').count(),2);
   await page.locator('#owgObjectRuleRun').click();
   await page.locator('#owgObjectRuleResults').getByText('1 of 2 object rules have violations.',{exact:true}).waitFor();
   const ruleText=await page.locator('#owgObjectRuleResults').textContent();
   assert.match(ruleText,/every Ship event of order objects comes after Pick item of each related item1 event–object pairs0/);
   assert.match(ruleText,/p \(2 linked\)/);
   // The global analysis still selects item; map evidence must explicitly select order.
   assert.equal(await page.locator('#owgEventLogCaseType').inputValue(),'item');
   await page.locator('#owgEventLogObjectGraph').getByRole('button',{name:'Inspect observed transition Place order → Ship',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgEventLogObjectSelection').getByRole('button',{name:'Browse cases for adjacent transition',exact:true}).click();
   await page.locator('#owgEventLogObjectCasesStatus').filter({hasText:'1 matching cases'}).waitFor();
   await page.locator('#owgEventLogObjectCasesList').getByRole('button',{name:'Open case o1',exact:true}).click();
   await page.locator('#owgEventLogObjectCasesEvents [role=status]').filter({hasText:'Showing 2 of 2'}).waitFor();
   assert.equal(await page.locator('#owgEventLogObjectCasesEvents tbody tr').count(),2);
   await page.addScriptTag({content:axeSource});
   const mapAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(mapAudit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'object map WCAG violations');
   // Owned response fixture for the explicit unknown-relations contract.
   await page.route('**/object-map',async route=>{
     const response=await route.fetch(),value=await response.json();
     value.relations=[];value.relations_calculation_complete=false;value.relations_unknown_reason='relation_pair_work_budget';
     await route.fulfill({status:200,contentType:'application/json',headers:{'Cache-Control':'no-store'},body:JSON.stringify(value)});
   });
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogObjectMap').getByText('Inter-object link counts are unknown within the analysis limits for this log. Lifecycle maps and case evidence remain available.',{exact:true}).waitFor();
   await page.locator('#owgEventLogObjectGraph').waitFor();
   assert.equal(await page.locator('#owgEventLogObjectMap th').filter({hasText:'Linked to'}).count(),0,'unknown relations are not rendered as zero-valued metrics');
   await page.unrouteAll({behavior:'wait'});
   page.once('dialog',dialog=>dialog.accept());
   await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   const unauthenticated=await fetch(base+'/v1/event-logs');assert.equal(unauthenticated.status,401);
   // Lifecycle CSV: the extra column is kept only because it is selected, then splits processing from waiting.
   const lifeRows=['case_id,activity,time,lifecycle:transition,comment'];
   for(let c=0;c<4;c++)for(const [a,from,to] of [['Receive',0,1],['Review',3,5]]){
     lifeRows.push(['L'+c,a,new Date(Date.UTC(2024,1,1+c,from)).toISOString(),'start','note']);
     lifeRows.push(['L'+c,a,new Date(Date.UTC(2024,1,1+c,to)).toISOString(),'complete','note']);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'lifecycle.csv',mimeType:'text/csv',buffer:Buffer.from(lifeRows.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogAttributes').options.length===5);
   assert.deepEqual(await page.locator('#owgEventLogAttributes').evaluate(n=>[...n.selectedOptions].map(o=>o.value)),['lifecycle:transition']);
   await page.locator('#owgEventLogCase').selectOption('case_id');await page.locator('#owgEventLogActivity').selectOption('activity');
   await page.locator('#owgEventLogTime').selectOption('time');await page.locator('#owgEventLogResource').selectOption('');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 16 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgWaitingRun').click();
   await page.locator('#owgWaitingStatus').filter({hasText:'8 of 8 activity instances have a start and an end'}).waitFor();
   const waitingText=await page.locator('#owgWaitingResults').textContent();
   assert.match(waitingText,/Review4\s*2\.0 h\s*2\.0 h\s*2\.0 h/,'Review: 2 h processing and 2 h waiting');
   assert.doesNotMatch(waitingText,/comment|note/);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   // Use the real 10,000-case history cap, without a server-side test override.
   const ageRows=['case_id,activity,time','ARecent,Receive,2024-01-01T00:00:00Z','ARecent,Check,2024-01-05T03:00:00Z'];
   for(let i=0;i<10000;i++)ageRows.push('B'+String(i).padStart(5,'0')+',Receive,2024-01-01T00:00:00Z');
   ageRows.push('ZClock,Receive,2024-01-05T04:00:00Z');
   await page.locator('#owgEventLogFile').setInputFiles({name:'open-age.csv',mimeType:'text/csv',buffer:Buffer.from(ageRows.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogCase').selectOption('case_id');
   await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:/Imported 10,?003 events/}).waitFor().catch(async error=>{
     throw new Error('Open-age import status: '+await page.locator('#owgEventLogStatus').textContent()+' '+error.message);});
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgSignalName').fill('Open age, not idle time');
   await page.locator('#owgSignalCondition').selectOption('open_longer_than');
   await page.locator('#owgSignalHours').fill('48');await page.locator('#owgSignalEnds').fill('Close');
   await page.locator('#owgSignalAdd').click();
   await page.locator('#owgSignalStatus').filter({hasText:'Signal saved.'}).waitFor();
   const firstAgeResponse=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/signals/evaluate'));
   await page.locator('#owgSignalEvaluate').click();const ageResult=await(await firstAgeResponse).json();
   assert.equal(ageResult.signals[0].matching_cases,10001);
   const recentCase=ageResult.signals[0].evidence_cases.find(c=>c.case_id==='ARecent');
   assert.equal(recentCase.age_since_first_event_seconds,100*3600);
   assert.equal(recentCase.idle_since_last_event_seconds,3600);
   await page.locator('#owgSignalResults').getByText('first check').waitFor();
   const secondAgeResponse=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/signals/evaluate'));
   await page.locator('#owgSignalEvaluate').click();const checkedAge=await(await secondAgeResponse).json();
   assert.equal(checkedAge.signals[0].new_cases_since_last_evaluation,null);
   assert.deepEqual([checkedAge.signals[0].new_cases_comparison.lower_bound,checkedAge.signals[0].new_cases_comparison.upper_bound],[0,1]);
   await page.locator('#owgSignalResults').getByText('0–1 (bounded history)',{exact:true}).waitFor();
   assert.match(await page.locator('#owgSignalResults').textContent(),/ARecent \(age .*idle /);
   const ageAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(ageAudit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   const literalRows=['case_id,activity,time','single,A > B,2024-01-01T00:00:00Z',
     'sequence,A,2024-01-01T00:00:00Z','sequence,B,2024-01-01T00:01:00Z',
     'repeat,A,2024-01-01T00:00:00Z','repeat,B,2024-01-01T00:01:00Z'];
   await page.locator('#owgEventLogFile').setInputFiles({name:'literal-variants.csv',mimeType:'text/csv',buffer:Buffer.from(literalRows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogCase').selectOption('case_id');
   await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 5 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgAnalysisLevel').selectOption('cases');
   await page.locator('#owgAnalysisDim1').selectOption('variant');
   await page.locator('#owgAnalysisDim2').selectOption('');
   await page.locator('#owgAnalysisMeasure1').selectOption('count');
   await page.locator('#owgAnalysisMeasure2').selectOption('');
   const literalResponse=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis'));
   await page.locator('#owgAnalysisRun').click();const literalResult=await(await literalResponse).json();
   assert.equal(literalResult.variant_encoding,'ordered_activity_json_array');
   assert.deepEqual(literalResult.rows,[['["A","B"]',2],['["A > B"]',1]]);
   await page.locator('#owgAnalysisResults tbody tr').nth(1).waitFor();
   assert.equal(await page.locator('#owgAnalysisResults tbody tr').count(),2);
   const [literalExport]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   const literalExportPath=path.join(scratch,'literal-variant-analysis.csv');await literalExport.saveAs(literalExportPath);
   assert.match(await fs.readFile(literalExportPath,'utf8'),/""A > B""/);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('Event-log workspace acceptance passed: consented CSV import with rejected naive time, a counts-only quality report and the rejected row kept, then released under an explicit offset decision, variants, Disco-style map sliders, case playback along the map, direct and eventual follower case filters saved and re-applied with a filtered case drill-down, filtered KPIs with CSV export, cohort benchmark with side-by-side map comparison, what-if simulation with an added activity, routing, arrival and activity-time uncertainty, and a held-out validation, working calendars, a shared resource pool and seasonal arrivals entered and measured from the log, conformance, discovered model drafts with BPMN export, processing vs waiting time from lifecycle columns, a dotted chart with hover, drift detection, root-cause associations, pseudonymous handovers, saved signals, saved ad-hoc analyses with a typed, saved case metric, compliance rules saved as signals, backtested predictions with a censoring-aware as-of replay, appending a newer export, WCAG audit, mobile width, OCEL export, verified deletion, OCEL import with per-object-type maps and what flattening onto each type leaves out or copies, which object attributes change over time, and object rules across types.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
