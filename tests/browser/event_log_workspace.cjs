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

   const qualityResponsePromise=page.waitForResponse(response=>response.status()===200&&
     response.url().includes('/v1/event-logs/')&&response.url().split('?')[0].endsWith('/quality'));
   await page.locator('#owgEventLogAnalyze').click();
   const qualityPayload=await (await qualityResponsePromise).json();
   await page.locator('#owgEventLogResults h3',{hasText:'Most common variants'}).waitFor();
   const [coverageResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/variant-coverage')),page.locator('#owgVariantCoverageRun').click()]);
   const coverageResult=await coverageResponse.json();
   assert.equal(coverageResult.cases,60);
   assert.equal(coverageResult.displayed_variants.at(-1).cumulative_cases,60);
   const coverageBox=page.locator('#owgVariantCoverageRun').locator('..');
   await coverageBox.getByText('Target case coverage').waitFor();
   const [coverageJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgVariantCoverageJson').click()]);
   const coveragePacket=JSON.parse(await fs.readFile(await coverageJson.path(),'utf8'));
   assert.deepEqual(coveragePacket.coverage_targets,coverageResult.coverage_targets);
   await page.getByRole('button',{name:'Inspect variant 1 cases',exact:true}).click();
   await page.locator('#owgVariantCoverageCasesStatus').filter({hasText:coverageResult.displayed_variants[0].cases+' matching cases'}).waitFor();
   await page.locator('#owgVariantCoverageLimit').selectOption('50');
   const [expandedCoverage]=await Promise.all([page.waitForResponse(r=>r.url().includes('/variant-coverage')&&r.url().includes('limit=50')),page.locator('#owgVariantCoverageRun').click()]);
   assert.equal((await expandedCoverage.json()).cases,60);


   assert.match(await page.locator('#owgEventLogResults').textContent(),/Receive → Check → Approve → Close/);
   await page.locator('#owgQuality summary').click();
   await page.locator('#owgQualityResults table').first().waitFor();
   const qualityText=await page.locator('#owgQualityResults').textContent();
   assert.match(qualityText,/Import\s*csv\s*274\s*1 \(1 time without a UTC offset\)/);
   assert.match(qualityText,/Case columncase_id/);assert.match(qualityText,/not known to be complete/);
   assert.doesNotMatch(qualityText,/C99/,'rejected rows are not shown');
   const reference=qualityPayload.current.diagnostics.future_event_reference;
   assert.equal(reference.basis,'current_query','live diagnostics use query time');
   const futureRow=page.locator('#owgQualityResults tr').filter({hasText:'Events dated after reference (current query)'});
   assert.equal(await futureRow.count(),1,'current diagnostics disclose their actual basis');
   const futureText=await futureRow.textContent();
   assert.ok(futureText.includes('UTC reference: '+reference.at),'visible reference matches the API instant');
   assert.ok(futureText.includes(Number(qualityPayload.current.diagnostics.events_dated_after_reference).toLocaleString()),
     'visible count matches the current API count');
   assert.doesNotMatch(qualityText,/Events dated after the import/,'live data must not be labeled import-time');
   const unlinkedRow=page.locator('#owgQualityResults tr').filter({hasText:'Events without any retained object link'});
   assert.equal(await unlinkedRow.count(),1,'object-centric diagnostic is not mislabeled as a literal case-type check');
   assert.equal((await unlinkedRow.locator('td').last().textContent()).trim(),
     Number(qualityPayload.current.diagnostics.events_without_case).toLocaleString(),
     'the unlinked-event count matches the local API without changing its legacy key');
   assert.doesNotMatch(qualityText,/Events without a case/,'an OCEL object link need not have type case');
   const currentV2=qualityPayload.current.content_sha256_v2;
   assert.match(currentV2,/^[0-9a-f]{64}$/,'current quality response supplies the delimiter-safe digest');
   assert.ok(qualityText.includes('Current content digest v2 (delimiter-safe): '+currentV2),
     'the full v2 digest is visible, not only the delimiter-ambiguous v1 digest');
   assert.match(qualityText,/Duplicate event payloads \(including attributes and qualified links; IDs excluded\)/,
     'duplicate label describes the current exact payload-and-link comparison');
   assert.ok(qualityText.includes('Showing all '+qualityPayload.current.diagnostics.attribute_coverage_total_keys.toLocaleString()+
     ' attribute keys in the supplied rows.'),'the displayed key count agrees with the real API');
   const reportHeader=page.locator('#owgQualityResults table').first();
   assert.equal(await reportHeader.getByRole('columnheader',{name:'Content v2'}).count(),1);
   assert.equal(await reportHeader.locator('tbody tr').first().locator('td').last().textContent(),
     qualityPayload.reports[0].content_sha256_v2.slice(0,12)+'…','the import row shows its own v2 digest');

   // Synthetic response only: exercise the capped-key disclosure without creating
   // a large source log. Backend DuckDB tests separately establish the real 35-key count.
   const qualityPattern='**/v1/event-logs/*/quality';
   await page.route(qualityPattern,async route=>{
     const response=await route.fetch(),payload=await response.json();
     payload.current.diagnostics.attribute_coverage=Array.from({length:32},(_,i)=>({
       attribute:'fixture_'+i,events_with_value:1,events_without_value:273
     }));
     payload.current.diagnostics.attribute_coverage_total_keys=35;
     payload.current.diagnostics.attribute_coverage_truncated=true;
     await route.fulfill({response,json:payload});
   });
   const truncatedQuality=page.waitForResponse(response=>response.status()===200&&
     response.url().includes('/v1/event-logs/')&&response.url().split('?')[0].endsWith('/quality'));
   await page.locator('#owgEventLogAnalyze').click();
   await truncatedQuality;
   await page.locator('#owgQuality summary').click();
   await page.locator('#owgQualityResults').getByText('Showing 32 of 35 attribute keys; omitted keys are not absent.').waitFor();
   assert.match(await page.locator('#owgQualityResults').textContent(),/fixture_31/);
   await page.unroute(qualityPattern);

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
   // Variant coverage evidence preserves the same whole-case filter, not just its displayed path.
   const [filteredCoverage]=await Promise.all([page.waitForResponse(r=>r.url().includes('/variant-coverage')),page.locator('#owgVariantCoverageRun').click()]);
   assert.equal((await filteredCoverage.json()).cases,2);
   await page.getByRole('button',{name:'Inspect variant 1 cases',exact:true}).click();
   await page.locator('#owgVariantCoverageCasesStatus').filter({hasText:'2 matching cases (case filter applied)'}).waitFor();
   assert.deepEqual((await page.locator('#owgVariantCoverageCasesList button').allTextContents()).sort(),['C0','C30']);
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
   // Distribution uses whole selected cases, and its export preserves exact boundaries.
   await page.locator('#owgDurationBounds').fill('1, 4, 24');
   const [filteredDurations]=await Promise.all([page.waitForResponse(r=>r.url().includes('/duration-distribution')),page.locator('#owgDurationRun').click()]);
   const filteredDurationResult=await filteredDurations.json();
   assert.equal(filteredDurationResult.cases,2);assert.equal(filteredDurationResult.cases_before_filter,60);
   assert.deepEqual(filteredDurationResult.bands.map(b=>b.cases),[0,0,2,0]);
   await page.locator('#owgDurationResults').getByText('Filter applied: 2 of 60 cases.',{exact:false}).waitFor();
   const [durationCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgDurationDistributionCsv').click()]);
   const durationText=await fs.readFile(await durationCsv.path(),'utf8');
   assert.match(durationText,/^lower_seconds_inclusive,upper_seconds_exclusive,cases,share\r\n/);
   assert.match(durationText,/14400,86400,2,1\r\n/);
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
   await page.locator('#owgDurationBounds').fill('1, 4, 24');
   const [allDurations]=await Promise.all([page.waitForResponse(r=>r.url().includes('/duration-distribution')),page.locator('#owgDurationRun').click()]);
   const allDurationResult=await allDurations.json();
   assert.equal(allDurationResult.cases,60);
   assert.deepEqual(allDurationResult.bands.map(b=>b.cases),[40,0,20,0]);
   await page.locator('#owgDurationResults').getByText('60 selected cases with retained events',{exact:false}).waitFor();
   assert.equal(await page.locator('#owgDurationResults progress').count(),4);
   await page.locator('#owgDurationBounds').fill('4, 1');
   assert.equal(await page.locator('#owgDurationDistributionCsv').count(),0,'edited thresholds invalidate prior downloads');
   await page.locator('#owgDurationRun').click();
   await page.locator('#owgDurationResults').getByText('Thresholds must be positive, strictly increasing, and at most 100 years.').waitFor();

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
   const [simJson]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogSimulationJson').click()]);
   const simReport=JSON.parse(await fs.readFile(await simJson.path(),'utf8'));
   assert.equal(simReport.format,'openworkgraph.simulation-report.v1');
   assert.equal(simReport.submitted_scenario.label,'Validate first');
   assert.equal(simReport.analysis.scenario.cases,500);
   assert.ok(simReport.analysis.assumptions.length);
   assert.match(simReport.interpretation,/not measured savings/);
   const [simCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogSimulationCsv').click()]);
   const simCsvText=await fs.readFile(await simCsv.path(),'utf8');
   assert.match(simCsvText,/^section,run,metric,value,unit\r\n/);
   assert.match(simCsvText,/cycle_time,baseline,p50,/);
   assert.match(simCsvText,/assumption,both,/);

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
   assert.doesNotMatch(await page.locator('#owgEventLogSimValidation').textContent(),/first-order/);
   // Step-by-step held-out check: the request carries the routing and the view states that routing is first-order.
   await page.locator('#owgEventLogSimHoldoutRouting').selectOption('transitions');
   const [stepCheck]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulation/validation')),page.locator('#owgEventLogSimValidate').click()]);
   const stepResult=await stepCheck.json();
   assert.equal(stepCheck.status(),200,JSON.stringify(stepResult));assert.equal(JSON.parse(stepCheck.request().postData()).routing,'transitions');
   assert.equal(stepResult.routing,'transitions');assert.equal(typeof stepResult.simulated_routes.stopped_at_path_limit,'number');
   await page.locator('#owgEventLogSimValidation').getByText(/first-order: the next activity depends only on the current one/).waitFor();
   await page.locator('#owgEventLogSimHoldoutRouting').selectOption('paths');
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
   // Priority classes in the scenario: urgent cases are served first; the baseline stays first come, first served.
   await page.locator('#owgEventLogSimPriorities').fill('urgent 30% 0, standard 70% 1');
   const [rankedResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const ranked=await rankedResponse.json();
   assert.deepEqual(ranked.scenario.priorities,[{name:'urgent',share:0.3,rank:0},{name:'standard',share:0.7,rank:1}]);
   assert.equal(ranked.baseline.priority_classes,undefined);assert.equal(ranked.result.priority_classes.length,2);
   await page.getByRole('cell',{name:'urgent',exact:true}).waitFor();
   await page.locator('#owgEventLogSimPriorities').fill('');
   // Batch one activity in the scenario (one outside the shared pool, which a batch cannot join).
   const batchable=(await page.locator('#owgEventLogSimBatchActivity option').evaluateAll(o=>o.map(x=>x.value))).find(v=>v&&!['Check','Approve'].includes(v));
   await page.locator('#owgEventLogSimBatchActivity').selectOption(batchable);
   await page.locator('#owgEventLogSimBatchSize').fill('3');await page.locator('#owgEventLogSimBatchWait').fill('8');
   const [batchResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const batchedRun=await batchResponse.json();
   assert.equal(batchResponse.status(),200,JSON.stringify(batchedRun));
   assert.deepEqual(batchedRun.scenario.activities[batchable].batch,{size:3,max_wait_minutes:480});
   assert.equal(batchedRun.baseline.activities.find(a=>a.activity===batchable).batch,undefined);
   await page.getByRole('columnheader',{name:'Batched activity',exact:true}).waitFor();
   await page.locator('#owgEventLogSimBatchActivity').selectOption('');
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
   // Step-by-step routing from every case: no path list is sent, and visits per case are shown for both runs.
   await page.locator('#owgEventLogSimRouting').selectOption('transitions');
   assert.equal(await page.locator('#owgEventLogSimPaths').isHidden(),true);
   const [routedResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/simulate')),page.locator('#owgEventLogSimRun').click()]);
   const routedRequest=JSON.parse(routedResponse.request().postData());
   assert.equal(routedRequest.routing,'transitions');assert.equal('paths' in routedRequest,false);
   const routed=await routedResponse.json();
   assert.equal(routedResponse.status(),200);assert.equal(routed.scenario.routing,'transitions');
   assert.ok(Object.keys(routed.result.visits_per_case).length>0);
   await page.getByText('Visits per case (step-by-step routing)').waitFor();
   await page.locator('#owgEventLogSimRouting').selectOption('paths');
   assert.equal(await page.locator('#owgEventLogSimPaths').isVisible(),true);

   await page.locator('#owgEventLogModel').fill('Receive > Check > Approve > Close');
   await page.locator('#owgEventLogConformanceRun').click();
   await page.getByText('Skipped reference step').waitFor();
   const conformance=await page.locator('#owgEventLogConformanceRun').locator('xpath=..').textContent();
   assert.match(conformance,/Approve/);assert.match(conformance,/Fix/);

   // Trace clusters: two requested clusters that together cover every case, each described.
   await page.locator('#owgClusterK').selectOption('2');
   const clusterResponse=page.waitForResponse(r=>r.url().includes('/trace-clusters'));
   await page.locator('#owgClusterRun').click();
   const clustered=await (await clusterResponse).json();
   assert.equal(clustered.k,2);assert.equal(clustered.clusters.reduce((n,c)=>n+c.cases,0),clustered.cases);
   await page.locator('#owgClusterResults table').waitFor();
   assert.match(await page.locator('#owgClusterResults').textContent(),/2 clusters as requested/);

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
   // Manual reviews belong to the current evaluated state. They do not grant
   // business-action authority and cannot occur before an explicit check.
   const reviewPanel=page.locator('#owgSignalInbox');
   await reviewPanel.getByRole('button',{name:'Snooze 1 hour for signal Skipped approval'}).click();
   await reviewPanel.getByText('snoozed',{exact:true}).waitFor();
   await reviewPanel.getByRole('button',{name:'Resume review for signal Skipped approval'}).click();
   await reviewPanel.getByText('unreviewed',{exact:true}).waitFor();
   await reviewPanel.getByRole('button',{name:'Acknowledge signal Skipped approval'}).click();
   await reviewPanel.getByText('acknowledged',{exact:true}).waitFor();
   assert.match(await reviewPanel.textContent(),/No automatic actions or delivery/);
   await reviewPanel.getByRole('button',{name:'Reopen signal Skipped approval'}).click();
   await reviewPanel.getByText('unreviewed',{exact:true}).waitFor();
   await page.locator('#owgSignalReviewRefresh').click();
   await reviewPanel.getByRole('button',{name:'Acknowledge signal Skipped approval'}).waitFor();

   await page.locator('#owgAnalysisDim1').selectOption('has_rework');await page.locator('#owgAnalysisMeasure2').selectOption('median:duration_seconds');
   // Keep the established full builder/export journey on the compatible synchronous path.
   // Dedicated analysis_jobs.cjs covers the default cancellable job path and cancellation races.
   await page.locator('#owgAnalysisBackground').uncheck();
   await page.locator('#owgAnalysisRun').click();
   await page.locator('#owgAnalysisResults table').waitFor();
   const analysisText=await page.locator('#owgAnalysisResults').textContent();
   assert.match(analysisText,/60 matching cases/);assert.match(analysisText,/no rework40/);assert.match(analysisText,/rework20/);
   const [analysisCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await analysisCsv.path(),'utf8'),/^has_rework,count,median_duration_seconds\r\n/);
   const [sharingResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis-sharing-preview')),page.locator('#owgAnalysisPrepareShare').click()]);
   const sharingDoc=await sharingResponse.json();assert.equal(sharingResponse.status(),200);
   await page.locator('#owgAnalysisSharingPreview').waitFor();
   assert.equal(await page.locator('#owgAnalysisSharingDownload').isDisabled(),true);
   assert.equal(sharingDoc.matching_cases,60);assert.equal(sharingDoc.group_labels_included,false);
   for(const secret of ['C0','ann','bob','has_rework','resource','cohort'])assert.ok(!JSON.stringify(sharingDoc).includes('"'+secret+'"'));
   await page.locator('#owgAnalysisSharingConsent').check();
   const [sharingFile]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisSharingDownload').click()]);
   const sharingText=await fs.readFile(await sharingFile.path(),'utf8');assert.deepEqual(JSON.parse(sharingText),sharingDoc);
   if(process.env.OWG_ANALYSIS_SNAPSHOT_OUTPUT)await fs.writeFile(process.env.OWG_ANALYSIS_SNAPSHOT_OUTPUT,sharingText);
   assert.equal(await page.locator('#owgAnalysisSharingConsent').isChecked(),false);
   const [pairResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/case-sharing-preview')),page.locator('#owgAnalysisPrepareCases').click()]);
   const pair=await pairResponse.json();assert.equal(pairResponse.status(),200,JSON.stringify(pair));
   assert.equal(pair.case_dataset.cases.length,60);assert.equal(pair.case_dataset.source_content_sha256,pair.aggregate_snapshot.source_content_sha256);
   assert.equal(await page.locator('#owgAnalysisCaseDownload').isDisabled(),true);
   await page.locator('#owgAnalysisCaseConsent').check();
   const [caseDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCaseDownload').click()]);
   assert.deepEqual(JSON.parse(await fs.readFile(await caseDownload.path(),'utf8')),pair.case_dataset);
   assert.equal(await page.locator('#owgAnalysisCaseConsent').isChecked(),false);
   assert.equal(await page.locator('#owgAnalysisSharingConsent').isChecked(),false);
   await page.locator('#owgAnalysisSharingConsent').check();
   const [pairedAggregate]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisSharingDownload').click()]);
   assert.deepEqual(JSON.parse(await fs.readFile(await pairedAggregate.path(),'utf8')),pair.aggregate_snapshot);
   if(process.env.OWG_SHARED_CASE_PAIR_OUTPUT)await fs.writeFile(process.env.OWG_SHARED_CASE_PAIR_OUTPUT,JSON.stringify(pair));
   // An older prepare response cannot re-open a sharing preview after a new analysis runs.
   let releaseSharing,sharingHeld;const sharingSeen=new Promise(resolve=>sharingHeld=resolve);
   await page.route('**/analysis-sharing-preview',async route=>{const response=await route.fetch();sharingHeld();await new Promise(resolve=>releaseSharing=resolve);await route.fulfill({response});},{times:1});
   await page.locator('#owgAnalysisPrepareShare').click();await sharingSeen;
   const rerunSeen=page.waitForResponse(r=>r.url().endsWith('/analysis'));await page.locator('#owgAnalysisRun').click();await rerunSeen;
   const staleSharingResponse=page.waitForResponse(r=>r.url().endsWith('/analysis-sharing-preview'));releaseSharing();await staleSharingResponse;
   await page.waitForTimeout(50);assert.equal(await page.locator('#owgAnalysisSharingPreview').isHidden(),true);
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
   await page.locator('#owgAnalysisBackground').uncheck();
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
   // Saved arithmetic templates now restore the complete earlier-first metric
   // graph from the authenticated per-log library in one atomic UI action.
   await page.getByRole('button',{name:'Add saved metric half_wait',exact:true}).click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Added 2 saved metrics in dependency order'}).waitFor();
   await page.locator('#owgMetricList').filter({hasText:'to_approval: time from the first Receive to the first Approve'}).waitFor();
   await page.locator('#owgMetricList').filter({hasText:'half_wait: to_approval ÷ 2 ratio'}).waitFor();
   await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:half_wait');
   assert.equal(await page.locator('#owgAnalysisSave').isDisabled(),true);
   const [bundledResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis')),page.locator('#owgAnalysisRun').click()]);
   assert.equal(bundledResponse.status(),200);
   assert.deepEqual((await bundledResponse.json()).rows,arithmeticResult.rows);
   const [bundledCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await bundledCsv.path(),'utf8'),/rework,21600/);
   await page.locator('#owgAnalysisName').fill('Bundled approval wait');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [bundledReplay]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis')),page.getByRole('button',{name:'Run saved analysis Bundled approval wait',exact:true}).click()]);
   assert.deepEqual((await bundledReplay.json()).rows,arithmeticResult.rows);
   await page.getByRole('button',{name:'Add saved metric to_approval',exact:true}).click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'already has a metric named to_approval'}).waitFor();
   // Missing, conflicting, cyclic and over-budget dependencies never partially
   // modify a draft; identical existing definitions are safely reused.
   const bundleCases=await page.evaluate(()=>{
     const resolve=window.__owgEventLogModel.savedMetricBundle;
     const base={name:'base',kind:'activity_count',activity:'Receive'};
     const derived={name:'derived',kind:'arithmetic',expression_version:'1',operation:'add',
       left:{kind:'metric',name:'base'},right:{kind:'constant',value:1,unit:'ratio'}};
     const check=(entries,current=[])=>{try{return resolve(derived,entries,current).map(m=>m.name);}catch(e){return e.message;}};
     const cycleA={...derived,name:'derived',left:{kind:'metric',name:'base'}};
     const cycleB={...derived,name:'base',left:{kind:'metric',name:'derived'}};
     const existing=[{name:'other1',kind:'activity_count',activity:'Start'},
       {name:'other2',kind:'activity_count',activity:'Start'},
       {name:'other3',kind:'activity_count',activity:'Start'}];
     const before=JSON.stringify(existing);
     const capacity=check([base,derived],existing);
     return {complete:check([base,derived]),missing:check([derived]),
       conflict:check([base,derived],[{...base,activity:'Approve'}]),
       ambiguous:check([base,{...base,activity:'Approve'},derived]),
       cycle:check([cycleA,cycleB]),capacity,unchanged:JSON.stringify(existing)===before,
       reuse:check([base,derived],[{activity:'Receive',kind:'activity_count',name:'base'}])};
   });
   assert.deepEqual(bundleCases.complete,['base','derived']);
   assert.deepEqual(bundleCases.reuse,['derived']);
   assert.match(bundleCases.missing,/Missing saved dependency base/);
   assert.match(bundleCases.conflict,/different definition/);
   assert.match(bundleCases.ambiguous,/conflicting templates/);
   assert.match(bundleCases.cycle,/dependency cycle/);
   assert.match(bundleCases.capacity,/exceed four case metrics/);
   assert.equal(bundleCases.unchanged,true);
   const [rerunArithmetic]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/analysis')),page.getByRole('button',{name:'Run saved analysis Half approval wait',exact:true}).click()]);
   assert.deepEqual((await rerunArithmetic.json()).rows,arithmeticResult.rows);
   await page.getByRole('button',{name:'Add saved metric to_approval',exact:true}).click();
   await page.locator('#owgMetricName').fill('bad_units');await page.locator('#owgMetricOperation').selectOption('add');await page.locator('#owgMetricRightUnit').fill('EUR');
   await page.locator('#owgMetricAdd').click();await page.locator('#owgAnalysisMeasure1').selectOption('median:metric:bad_units');
   await page.locator('#owgAnalysisBackground').uncheck();
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
   // Suggested rules: candidates the log already follows, added to the rule list for checking.
   const pendingBefore=await page.locator('#owgComplianceRules tbody tr').count();
   await page.locator('#owgComplianceConfidence').fill('90');
   await page.locator('#owgComplianceSuggest').click();
   await page.locator('#owgComplianceSuggestions table').waitFor();
   assert.match(await page.locator('#owgComplianceSuggestions').textContent(),/suggestions from \d+ cases.*not policy/);
   await page.locator('#owgComplianceSuggestions tbody tr').first().getByRole('button').click();
   assert.equal(await page.locator('#owgComplianceRules tbody tr').count(),pendingBefore+1);

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
   // Release is enabled only by a preview of the exact offset (#530).
   assert.equal(await page.locator('#owgQuarantineRelease').isDisabled(),true,'release needs a preview');
   await page.locator('#owgQuarantineOffset').fill('local');
   await page.locator('#owgQuarantinePreview').click();
   await page.locator('#owgQuarantineStatus').filter({hasText:'+01:00'}).waitFor();
   assert.equal(await page.locator('#owgQuarantineRelease').isDisabled(),true,'an unconfirmed offset cannot be released');
   await page.locator('#owgQuarantineOffset').fill('+01:00');
   await page.locator('#owgQuarantinePreview').click();
   await page.locator('#owgQuarantineStatus').filter({hasText:'Preview: 1 of 1'}).waitFor();
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
   // Object process model: discover per-type lifecycles and arcs, expect one item per Place order, and check.
   const [modelFound]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/object-process-model/discovery')),page.locator('#owgObjectModelDiscover').click()]);
   assert.equal(modelFound.status(),200,await modelFound.text());
   const placeItems=page.locator('#owgObjectModelArcs').getByRole('combobox',{name:'Place order involves item',exact:true});
   assert.equal(await placeItems.inputValue(),'variable');
   await placeItems.selectOption('single');
   const [modelChecked]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/object-process-model/conformance')),page.locator('#owgObjectModelCheck').click()]);
   assert.equal(modelChecked.status(),200,await modelChecked.text());
   const sentArcs=JSON.parse(modelChecked.request().postData()).model.arcs;
   assert.deepEqual(sentArcs.find(a=>a.activity==='Place order'&&a.object_type==='item'),{activity:'Place order',object_type:'item',variable:false});
   await page.locator('#owgObjectModelResults').getByRole('cell',{name:'Several objects where one is expected',exact:true}).waitFor();
   assert.match(await page.locator('#owgObjectModelResults').textContent(),/replayed objects fit their type’s model; \d+ of \d+ events match their arcs/);
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
   await page.locator('#owgAnalysisBackground').uncheck();
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
   // R9 dashboard authoring: an actual numeric CSV, not a mocked API or hand-written spec.
   const statValidation=await page.evaluate(()=>{
     const build=window.__owgEventLogModel.caseMetric;
     const base={kind:'event_attribute_statistic',name:'amounts',attribute:'amount',unit:'EUR',
       statistic:'population_stddev',missingPolicy:'available',stageFrom:'Start',stageTo:'End',stageOccurrence:'last_completed'};
     const good=build(base);
     const invalid=values=>{try{build({...base,...values});return 'accepted';}catch(e){return e.message;}};
     return {good,missingEnd:invalid({stageTo:''}),unknownStat:invalid({statistic:'arbitrary'}),
       invalidPolicy:invalid({missingPolicy:'ignore'}),invalidUnit:invalid({unit:''})};
   });
   assert.deepEqual(statValidation.good,{name:'amounts',kind:'event_attribute_statistic',attribute:'amount',unit:'EUR',
     statistic:'population_stddev',missing_policy:'available',from_activity:'Start',to_activity:'End',stage_occurrence:'last_completed'});
   assert.match(statValidation.missingEnd,/both stage start and end/);
   assert.match(statValidation.unknownStat,/supported within-case statistic/);
   assert.match(statValidation.invalidPolicy,/missing numeric values/);
   assert.match(statValidation.invalidUnit,/unit/);

   const amountRows=['case_id,step,time,amount',
     'A,Start,2024-01-01T00:00:00Z,2','A,Work,2024-01-01T00:01:00Z,4','A,End,2024-01-01T00:02:00Z,',
     'A,Start,2024-01-01T00:03:00Z,8','A,Work,2024-01-01T00:04:00Z,10','A,End,2024-01-01T00:05:00Z,',
     'A,Start,2024-01-01T00:06:00Z,100',
     'B,Start,2024-01-02T00:00:00Z,5','B,Work,2024-01-02T00:01:00Z,invalid','B,End,2024-01-02T00:02:00Z,'];
   await page.locator('#owgEventLogFile').setInputFiles({name:'numeric-stage.csv',mimeType:'text/csv',buffer:Buffer.from(amountRows.join('\n'))});
   await page.locator('#owgEventLogDelimiter').selectOption(',');
   await page.waitForFunction(()=>document.querySelector('#owgEventLogAttributes')?.options.length===4);
   await page.locator('#owgEventLogName').fill('Observed numeric stages');
   await page.locator('#owgEventLogCase').selectOption('case_id');
   await page.locator('#owgEventLogActivity').selectOption('step');
   await page.locator('#owgEventLogTime').selectOption('time');
   await page.locator('#owgEventLogAttributes').selectOption(['amount']);
   await page.locator('#owgEventLogConsent').check();
   await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 10 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgMetricKind').selectOption('event_attribute_statistic');
   assert.equal(await page.locator('#owgMetricStatistic').isVisible(),true);
   assert.equal(await page.locator('#owgMetricStageFrom').isVisible(),true);
   await page.locator('#owgMetricName').fill('last_review_avg');
   await page.locator('#owgMetricAttribute').fill('amount');await page.locator('#owgMetricUnit').fill('EUR');
   await page.locator('#owgMetricStatistic').selectOption('average');
   await page.locator('#owgMetricMissingPolicy').selectOption('complete');
   await page.locator('#owgMetricStageFrom').selectOption('Start');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose both stage start and end'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').textContent(),'');
   await page.locator('#owgMetricStageTo').selectOption('End');
   await page.locator('#owgMetricStageOccurrence').selectOption('last_completed');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricList').filter({hasText:'last_review_avg: average of event attribute amount from Start to End (last_completed completed stage)'}).waitFor();
   await page.locator('#owgAnalysisMeasure1').selectOption('avg:metric:last_review_avg');
   await page.locator('#owgAnalysisMeasure2').selectOption('');
   await page.locator('#owgAnalysisBackground').uncheck();
   const [lastStageResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),page.locator('#owgAnalysisRun').click()]);
   assert.equal(lastStageResponse.status(),200);
   const lastSpec=JSON.parse(lastStageResponse.request().postData());
   assert.deepEqual(lastSpec.metrics[0],{name:'last_review_avg',kind:'event_attribute_statistic',
     attribute:'amount',unit:'EUR',statistic:'average',missing_policy:'complete',
     from_activity:'Start',to_activity:'End',stage_occurrence:'last_completed'});
   const lastStage=await lastStageResponse.json();
   assert.deepEqual(lastStage.rows,[[9]],'only the last completed A stage is valid; unfinished A and invalid B do not contribute');
   assert.equal(lastStage.metrics[0].cases_with_value,1);
   assert.equal(lastStage.metrics[0].cases_without_value,1);
   await page.locator('#owgMetricSave').click();
   await page.getByRole('button',{name:'Add saved metric last_review_avg',exact:true}).waitFor();
   await page.locator('#owgAnalysisName').fill('Last completed review amounts');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [savedStatResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
     page.getByRole('button',{name:'Run saved analysis Last completed review amounts',exact:true}).click()]);
   assert.deepEqual((await savedStatResponse.json()).rows,[[9]]);
   const [numericCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await numericCsv.path(),'utf8'),/avg_metric:last_review_avg\r\n9/);

   // Available policy keeps observed values but discloses invalid ones; totals are not inferred completeness.
   await page.locator('#owgMetricName').fill('first_review_total');
   await page.locator('#owgMetricStatistic').selectOption('total');
   await page.locator('#owgMetricMissingPolicy').selectOption('available');
   await page.locator('#owgMetricStageOccurrence').selectOption('first');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgAnalysisMeasure1').selectOption('sum:metric:first_review_total');
   const [firstStageResponse]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),page.locator('#owgAnalysisRun').click()]);
   assert.equal(firstStageResponse.status(),200);
   const firstStage=await firstStageResponse.json();
   assert.deepEqual(firstStage.rows,[[11]],'A first-stage 2+4 and B observed 5; invalid B Work omitted');
   assert.equal(firstStage.metrics[1].cases_with_value,2);
   assert.ok(firstStage.metrics[1].event_rows_with_missing_invalid_or_nonfinite_attribute>=1);
   // R9: four backend-supported pairwise operations are now authored without JSON.
   // A: first-stage total 6 EUR, last completed stage mean 9 EUR.
   // B: first-stage total 5 EUR but last completed mean is unknown (invalid Work).
   await page.locator('#owgMetricKind').selectOption('arithmetic');
   assert.deepEqual(await page.locator('#owgMetricOperation option').evaluateAll(nodes=>nodes.map(n=>n.value)),
     ['add','subtract','multiply','divide','minimum','maximum','average','absolute_difference']);
   await page.locator('#owgMetricLeftMetric').selectOption('first_review_total');
   await page.locator('#owgMetricRightMetric').selectOption('last_review_avg');
   for(const [operation,expected,description] of [
     ['minimum',6,'Smaller of'],['maximum',9,'Larger of'],
     ['average',7.5,'Average of'],['absolute_difference',3,'Absolute difference between']]){
     const metricName='pair_'+operation;
     await page.locator('#owgMetricName').fill(metricName);
     await page.locator('#owgMetricOperation').selectOption(operation);
     await page.locator('#owgMetricAdd').click();
     await page.locator('#owgMetricList').filter({hasText:metricName+': '+description+' first_review_total and last_review_avg'}).waitFor();
     await page.locator('#owgAnalysisMeasure1').selectOption('avg:metric:'+metricName);
     const [pairResponse]=await Promise.all([
       page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
       page.locator('#owgAnalysisRun').click()]);
     assert.equal(pairResponse.status(),200,operation+' should be accepted by authenticated analysis');
     const spec=JSON.parse(pairResponse.request().postData());
     assert.deepEqual(spec.metrics[2],{name:metricName,kind:'arithmetic',expression_version:'1',
       operation,left:{kind:'metric',name:'first_review_total'},right:{kind:'metric',name:'last_review_avg'}});
     const result=await pairResponse.json();
     assert.deepEqual(result.rows,[[expected]],operation+' must not turn missing B into zero');
     assert.equal(result.metrics[2].cases_with_value,1);
     assert.equal(result.metrics[2].cases_with_missing_operand,1);
     assert.equal(result.metrics[2].unit,'EUR');
     await page.locator('#owgAnalysisResults').filter({hasText:metricName+': 1 cases have a value'}).waitFor();
     if(operation==='absolute_difference'){
       await page.locator('#owgMetricSave').click();
       await page.getByRole('button',{name:'Add saved metric '+metricName,exact:true}).waitFor();
       const [pairCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
       assert.match(await fs.readFile(await pairCsv.path(),'utf8'),/avg_metric:pair_absolute_difference\r\n3/);
       await page.locator('#owgAnalysisName').fill('Pairwise recorded EUR difference');
       await page.locator('#owgAnalysisSave').click();
       await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
       const [savedPair]=await Promise.all([
         page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
         page.getByRole('button',{name:'Run saved analysis Pairwise recorded EUR difference',exact:true}).click()]);
       assert.deepEqual((await savedPair.json()).rows,[[3]]);
     }
     await page.getByRole('button',{name:'Remove metric '+metricName,exact:true}).click();
   }
   // New same-unit operations must fail closed; EUR and seconds are never converted.
   await page.locator('#owgMetricName').fill('bad_pair_units');
   await page.locator('#owgMetricOperation').selectOption('minimum');
   await page.locator('#owgMetricRightKind').selectOption('constant');
   await page.locator('#owgMetricRightValue').fill('1');
   await page.locator('#owgMetricRightUnit').fill('seconds');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgAnalysisMeasure1').selectOption('avg:metric:bad_pair_units');
   await page.locator('#owgAnalysisRun').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'identical units'}).waitFor();
   await page.getByRole('button',{name:'Remove metric bad_pair_units',exact:true}).click();
   const unknownPairOperation=await page.evaluate(()=>{
     try{window.__owgEventLogModel.caseMetric({kind:'arithmetic',name:'bad_op',
       operation:'sql',leftKind:'constant',leftValue:'1',leftUnit:'EUR',
       rightKind:'constant',rightValue:'2',rightUnit:'EUR'});return 'accepted';}
     catch(error){return error.message;}
   });
   assert.match(unknownPairOperation,/Choose the arithmetic operation/);
   await page.locator('#owgMetricKind').selectOption('arithmetic');
   assert.equal(await page.locator('#owgMetricStatistic').isVisible(),false);
   await page.locator('#owgMetricKind').selectOption('event_attribute_statistic');
   // R9: one user action builds a signed, same-unit first/last completed stage
   // comparison from the selected numeric attribute and missing-value policy.
   await page.locator('#owgMetricName').fill('review');
   await page.locator('#owgMetricStatistic').selectOption('average');
   await page.locator('#owgMetricMissingPolicy').selectOption('complete');
   await page.locator('#owgMetricStageFrom').selectOption('Start');
   await page.locator('#owgMetricStageTo').selectOption('End');
   assert.equal(await page.locator('#owgMetricCompareStages').isVisible(),true);
   await page.locator('#owgMetricCompareStages').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'three free metric slots'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),2,
     'capacity refusal must not insert any comparison metrics');
   await page.getByRole('button',{name:'Remove metric first_review_total',exact:true}).click();
   await page.getByRole('button',{name:'Remove metric last_review_avg',exact:true}).click();
   await page.locator('#owgMetricStageTo').selectOption('');
   await page.locator('#owgMetricCompareStages').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose both stage start and end'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricStageTo').selectOption('End');
   await page.locator('#owgMetricName').fill('a'.repeat(35));
   await page.locator('#owgMetricCompareStages').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'at most 34 characters'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricName').fill('review_first');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricName').fill('review');
   await page.locator('#owgMetricCompareStages').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'comparison metric name already exists'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),1,
     'name collision must leave existing metric untouched');
   await page.getByRole('button',{name:'Remove metric review_first',exact:true}).click();
   await page.locator('#owgAnalysisLevel').selectOption('events');
   await page.locator('#owgMetricCompareStages').click();
   assert.equal(await page.locator('#owgAnalysisLevel').inputValue(),'cases',
     'the comparison must return to case-level analysis even if the user selected Events');
   await page.locator('#owgAnalysisStatus').filter({hasText:'signed last minus first'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),3);
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:review_delta');
   assert.match(await page.locator('#owgMetricList').textContent(),/review_delta: review_last − review_first/);
   const [stageDeltaResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(stageDeltaResponse.status(),200);
   const deltaSpec=JSON.parse(stageDeltaResponse.request().postData());
   assert.equal(deltaSpec.metrics.length,3);
   assert.deepEqual(deltaSpec.metrics.slice(0,2).map(m=>[m.name,m.statistic,m.stage_occurrence,m.missing_policy,m.unit]),
     [['review_first','average','first','complete','EUR'],['review_last','average','last_completed','complete','EUR']]);
   assert.deepEqual(deltaSpec.metrics[2],{name:'review_delta',kind:'arithmetic',expression_version:'1',
     operation:'subtract',left:{kind:'metric',name:'review_last'},right:{kind:'metric',name:'review_first'}});
   const deltaResult=await stageDeltaResponse.json();
   assert.deepEqual(deltaResult.rows,[[6]],'A: last mean 9 minus first mean 3; B has an invalid in-stage value, so complete policy keeps it unknown');
   assert.equal(deltaResult.metrics[2].cases_with_value,1);
   assert.equal(deltaResult.metrics[2].cases_with_missing_operand,1);
   assert.equal(deltaResult.metrics[2].unit,'EUR');
   await page.locator('#owgAnalysisName').fill('First to last review change');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [replayedDelta]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
     page.getByRole('button',{name:'Run saved analysis First to last review change',exact:true}).click()]);
   assert.deepEqual((await replayedDelta.json()).rows,[[6]]);
   const [deltaCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await deltaCsv.path(),'utf8'),/avg_metric:review_delta\r\n6/);
   // R9: one action also produces a dimensionless fractional change:
   // (last completed stage - first stage) / first stage. 2 means +200%, not 2%.
   await page.locator('#owgMetricCompareRelative').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'four free metric slots'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),3,
     'relative comparison capacity refusal must leave existing metrics unchanged');
   for(const n of ['review_delta','review_last','review_first'])
     await page.getByRole('button',{name:'Remove metric '+n,exact:true}).click();
   await page.locator('#owgMetricName').fill('a'.repeat(32));
   await page.locator('#owgMetricCompareRelative').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'at most 31 characters'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricName').fill('review_relative');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricName').fill('review');
   await page.locator('#owgMetricCompareRelative').click();
   // Four comparison metrics fill all four slots, so any existing metric is
   // refused for capacity before a name collision can be reached.
   await page.locator('#owgAnalysisStatus').filter({hasText:'four free metric slots'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),1);
   await page.getByRole('button',{name:'Remove metric review_relative',exact:true}).click();
   await page.locator('#owgMetricStageTo').selectOption('');
   await page.locator('#owgMetricCompareRelative').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose both stage start and end'}).waitFor();
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricStageTo').selectOption('End');
   await page.locator('#owgAnalysisLevel').selectOption('events');
   await page.locator('#owgMetricCompareRelative').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'fractional change metrics'}).waitFor();
   assert.equal(await page.locator('#owgAnalysisLevel').inputValue(),'cases');
   assert.equal(await page.locator('#owgMetricList').locator('button[aria-label^="Remove metric "]').count(),4);
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:review_relative');
   assert.match(await page.locator('#owgMetricList').textContent(),/review_relative: review_delta ÷ review_first/);
   const [relativeResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(relativeResponse.status(),200);
   const relativeSpec=JSON.parse(relativeResponse.request().postData());
   assert.deepEqual(relativeSpec.metrics.map(m=>m.name),
     ['review_first','review_last','review_delta','review_relative']);
   assert.deepEqual(relativeSpec.metrics[3],{name:'review_relative',kind:'arithmetic',expression_version:'1',
     operation:'divide',left:{kind:'metric',name:'review_delta'},right:{kind:'metric',name:'review_first'}});
   const relativeResult=await relativeResponse.json();
   assert.deepEqual(relativeResult.rows,[[2]],'A: (9-3)/3 = 2, while B has no valid last-stage value');
   assert.equal(relativeResult.metrics[3].cases_with_value,1);
   assert.equal(relativeResult.metrics[3].cases_with_missing_operand,1);
   assert.equal(relativeResult.metrics[3].unit,'ratio');
   await page.locator('#owgAnalysisName').fill('First to last review fractional change');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [replayedRelative]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
     page.getByRole('button',{name:'Run saved analysis First to last review fractional change',exact:true}).click()]);
   assert.deepEqual((await replayedRelative.json()).rows,[[2]]);
   const [relativeCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await relativeCsv.path(),'utf8'),/avg_metric:review_relative\r\n2/);
   // The same production divide operator must withhold a zero denominator
   // rather than emit infinity or falsely report a zero-percent change.
   for(const n of ['review_relative','review_delta','review_last','review_first'])
     await page.getByRole('button',{name:'Remove metric '+n,exact:true}).click();
   await page.locator('#owgMetricKind').selectOption('arithmetic');
   await page.locator('#owgMetricName').fill('zero_denominator');
   await page.locator('#owgMetricOperation').selectOption('divide');
   await page.locator('#owgMetricLeftKind').selectOption('constant');
   await page.locator('#owgMetricLeftValue').fill('2');
   await page.locator('#owgMetricLeftUnit').fill('EUR');
   await page.locator('#owgMetricRightKind').selectOption('constant');
   await page.locator('#owgMetricRightValue').fill('0');
   await page.locator('#owgMetricRightUnit').fill('EUR');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgAnalysisMeasure1').selectOption('avg:metric:zero_denominator');
   const [zeroResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(zeroResponse.status(),200);
   const zeroResult=await zeroResponse.json();
   assert.equal(zeroResult.metrics[0].unit,'ratio');
   assert.equal(zeroResult.metrics[0].cases_with_value,0);
   assert.equal(zeroResult.metrics[0].cases_with_zero_divisor,2);
   assert.ok(!JSON.stringify(zeroResult.rows).includes('Infinity'));
   assert.ok(!JSON.stringify(zeroResult.rows).includes('NaN'));
   // R9: a keyboard-operable single action compares exact activity-specific
   // recorded totals, using the same authenticated typed case-analysis API.
   // A: Start 2+8+100=110, Work 4+10=14, signed B minus A = -96 EUR.
   // B: Start 5, Work invalid => unknown, not a false zero or -5 EUR.
   await page.getByRole('button',{name:'Remove metric zero_denominator',exact:true}).click();
   await page.locator('#owgMetricKind').selectOption('event_attribute_statistic');
   await page.locator('#owgMetricName').fill('step');
   await page.locator('#owgMetricStatistic').selectOption('total');
   await page.locator('#owgMetricMissingPolicy').selectOption('available');
   await page.locator('#owgMetricStageFrom').selectOption('');
   await page.locator('#owgMetricStageTo').selectOption('');
   await page.locator('#owgMetricStatActivity').selectOption('');
   assert.equal(await page.locator('#owgMetricCompareActivities').isVisible(),true);
   await page.locator('#owgMetricCompareActivityA').selectOption('Start');
   await page.locator('#owgMetricCompareActivityB').selectOption('Start');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose two different activities'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricCompareActivityB').selectOption('');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose two different activities'}).waitFor();
   await page.locator('#owgMetricCompareActivityB').selectOption('Work');
   await page.locator('#owgMetricStageFrom').selectOption('Start');
   await page.locator('#owgMetricStageTo').selectOption('End');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Clear stage start and end'}).waitFor();
   await page.locator('#owgMetricStageFrom').selectOption('');
   await page.locator('#owgMetricStageTo').selectOption('');
   await page.locator('#owgMetricStatActivity').selectOption('End');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Clear Only this activity'}).waitFor();
   await page.locator('#owgMetricStatActivity').selectOption('');
   await page.locator('#owgMetricName').fill('a'.repeat(35));
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'at most 34 characters'}).waitFor();
   await page.locator('#owgMetricName').fill('step_a');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricName').fill('step');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'activity comparison metric name already exists'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),1,'collision is atomic');
   await page.getByRole('button',{name:'Remove metric step_a',exact:true}).click();
   for(const name of ['capacity_one','capacity_two']){
     await page.locator('#owgMetricName').fill(name);
     await page.locator('#owgMetricAdd').click();
   }
   await page.locator('#owgMetricName').fill('step');
   await page.locator('#owgMetricCompareActivities').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'three free metric slots'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),2,'capacity refusal is atomic');
   for(const name of ['capacity_one','capacity_two'])
     await page.getByRole('button',{name:'Remove metric '+name,exact:true}).click();
   const activityPure=await page.evaluate(()=>window.__owgEventLogModel.activityComparisonMetrics(
     window.__owgEventLogModel.caseMetric({kind:'event_attribute_statistic',name:'step',attribute:'amount',
       unit:'EUR',statistic:'total',missingPolicy:'available'}),'Start','Work'));
   assert.deepEqual(activityPure.map(m=>m.name),['step_a','step_b','step_delta']);
   assert.equal(activityPure[0].activity,'Start');
   assert.equal(activityPure[1].activity,'Work');
   assert.deepEqual(activityPure[2],{name:'step_delta',kind:'arithmetic',expression_version:'1',
     operation:'subtract',left:{kind:'metric',name:'step_b'},right:{kind:'metric',name:'step_a'}});
   await page.locator('#owgAnalysisLevel').selectOption('events');
   await page.locator('#owgMetricCompareActivities').focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgAnalysisStatus').filter({hasText:'signed B minus A metrics'}).waitFor();
   assert.equal(await page.locator('#owgAnalysisLevel').inputValue(),'cases');
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),3);
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:step_delta');
   const [activityComparisonResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(activityComparisonResponse.status(),200);
   const activitySpec=JSON.parse(activityComparisonResponse.request().postData());
   assert.deepEqual(activitySpec.metrics,activityPure);
   const activityResult=await activityComparisonResponse.json();
   assert.deepEqual(activityResult.rows,[[-96]],'only A has both finite activity totals');
   assert.equal(activityResult.metrics[2].cases_with_value,1);
   assert.equal(activityResult.metrics[2].cases_with_missing_operand,1);
   assert.equal(activityResult.metrics[2].unit,'EUR');
   assert.equal(activityResult.metrics[1].cases_with_no_finite_attribute,1);
   await page.locator('#owgAnalysisName').fill('Recorded Start versus Work totals');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [activitySaved]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
     page.getByRole('button',{name:'Run saved analysis Recorded Start versus Work totals',exact:true}).click()]);
   assert.deepEqual((await activitySaved.json()).rows,[[-96]]);
   const [activityCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await activityCsv.path(),'utf8'),/avg_metric:step_delta\r\n-96/);
   // R9: one keyboard action composes the selected activity's contribution
   // to the whole case from ordinary version-1 typed metrics. A has Work 14
   // out of all observed 124 EUR; B has no finite Work value, so no fraction.
   for(const n of ['step_delta','step_b','step_a'])
     await page.getByRole('button',{name:'Remove metric '+n,exact:true}).click();
   await page.locator('#owgMetricKind').selectOption('event_attribute_statistic');
   await page.locator('#owgMetricName').fill('work');
   await page.locator('#owgMetricStatistic').selectOption('average');
   await page.locator('#owgMetricStatActivity').selectOption('Work');
   assert.equal(await page.locator('#owgMetricCompareActivityShare').isVisible(),true);
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose Total'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricStatistic').selectOption('total');
   await page.locator('#owgMetricStatActivity').selectOption('');
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Choose Only this activity'}).waitFor();
   await page.locator('#owgMetricStatActivity').selectOption('Work');
   await page.locator('#owgMetricStageFrom').selectOption('Start');
   await page.locator('#owgMetricStageTo').selectOption('End');
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Clear stage start and end'}).waitFor();
   await page.locator('#owgMetricStageFrom').selectOption('');
   await page.locator('#owgMetricStageTo').selectOption('');
   await page.locator('#owgMetricName').fill('x'.repeat(32));
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'at most 31 characters'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),0);
   await page.locator('#owgMetricName').fill('work_selected');
   await page.locator('#owgMetricAdd').click();
   await page.locator('#owgMetricName').fill('work');
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'activity fraction metric name already exists'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),1,'collision cannot partially add a fraction');
   await page.getByRole('button',{name:'Remove metric work_selected',exact:true}).click();
   for(const n of ['fraction_cap_one','fraction_cap_two']){
     await page.locator('#owgMetricName').fill(n);
     await page.locator('#owgMetricAdd').click();
   }
   await page.locator('#owgMetricName').fill('work');
   await page.locator('#owgMetricCompareActivityShare').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'three free metric slots'}).waitFor();
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),2,'capacity refusal cannot partially add');
   for(const n of ['fraction_cap_one','fraction_cap_two'])
     await page.getByRole('button',{name:'Remove metric '+n,exact:true}).click();
   const sharePure=await page.evaluate(()=>window.__owgEventLogModel.activityShareMetrics(
     window.__owgEventLogModel.caseMetric({kind:'event_attribute_statistic',name:'work',
       attribute:'amount',unit:'EUR',statistic:'total',missingPolicy:'available',statActivity:'Work'})));
   assert.deepEqual(sharePure.map(m=>m.name),['work_selected','work_all','work_fraction']);
   assert.equal(sharePure[0].activity,'Work');
   assert.equal(Object.hasOwn(sharePure[1],'activity'),false);
   assert.deepEqual(sharePure[2],{name:'work_fraction',kind:'arithmetic',expression_version:'1',
     operation:'divide',left:{kind:'metric',name:'work_selected'},right:{kind:'metric',name:'work_all'}});
   await page.locator('#owgAnalysisLevel').selectOption('events');
   await page.locator('#owgMetricCompareActivityShare').focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgAnalysisStatus').filter({hasText:'whole-case total and their fraction'}).waitFor();
   assert.equal(await page.locator('#owgAnalysisLevel').inputValue(),'cases');
   assert.equal(await page.locator('#owgMetricList button[aria-label^="Remove metric "]').count(),3);
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:work_fraction');
   const [shareResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(shareResponse.status(),200);
   const shareSpec=JSON.parse(shareResponse.request().postData());
   assert.deepEqual(shareSpec.metrics,sharePure);
   const shareResult=await shareResponse.json();
   assert.equal(shareResult.rows.length,1);
   assert.ok(Math.abs(shareResult.rows[0][0]-14/124)<1e-6,'Work contributes 14 of 124 observed EUR in A');
   assert.equal(shareResult.metrics[2].unit,'ratio');
   assert.equal(shareResult.metrics[2].cases_with_value,1);
   assert.equal(shareResult.metrics[2].cases_with_missing_operand,1,'invalid B Work must stay unknown');
   assert.equal(shareResult.metrics[0].cases_with_no_finite_attribute,1);
   await page.locator('#owgAnalysisName').fill('Observed Work contribution');
   await page.locator('#owgAnalysisSave').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'Analysis saved.'}).waitFor();
   const [savedShare]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')),
     page.getByRole('button',{name:'Run saved analysis Observed Work contribution',exact:true}).click()]);
   assert.ok(Math.abs((await savedShare.json()).rows[0][0]-14/124)<1e-6);
   const [shareCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgAnalysisCsv').click()]);
   assert.match(await fs.readFile(await shareCsv.path(),'utf8'),/avg_metric:work_fraction\r\n0\.1129/);
   // Saved analyses are editable, not just runnable. Loading is explicit and
   // restores the full ordered typed metric graph, dimensions, filters and measure.
   const originalDraft=await page.evaluate(spec=>window.__owgEventLogModel.analysisDraft(spec,spec.case_type||''),shareSpec);
   assert.deepEqual(originalDraft.metrics,sharePure);
   assert.equal(originalDraft.measure1,'avg:metric:work_fraction');
   const refusedDraft=await page.evaluate(spec=>{
     const invalid={...spec,metrics:spec.metrics.map(m=>({...m}))};
     invalid.metrics[2].left={kind:'metric',name:'not_an_earlier_metric'};
     try{window.__owgEventLogModel.analysisDraft(invalid,invalid.case_type||'');return 'accepted';}
     catch(error){return error.message;}
   },shareSpec);
   assert.match(refusedDraft,/out-of-order metric dependency/);
   const refusedFilter=await page.evaluate(spec=>{
     try{window.__owgEventLogModel.analysisDraft({...spec,where:{source_only:true}},spec.case_type||'');return 'accepted';}
     catch(error){return error.message;}
   },shareSpec);
   assert.match(refusedFilter,/filters not supported/);
   for(const n of ['work_fraction','work_all','work_selected'])
     await page.getByRole('button',{name:'Remove metric '+n,exact:true}).click();
   await page.locator('#owgAnalysisLevel').selectOption('events');
   await page.locator('#owgAnalysisDim1').selectOption('activity');
   await page.getByRole('button',{name:'Load saved analysis Observed Work contribution into builder',exact:true}).focus();
   await page.keyboard.press('Enter');
   await page.locator('#owgAnalysisStatus').filter({hasText:'Edit and run it, then save under a new name'}).waitFor();
   assert.equal(await page.locator('#owgAnalysisLevel').inputValue(),'cases');
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:work_fraction');
   assert.equal(await page.getByRole('button',{name:'Edit metric work_selected',exact:true}).count(),1);
   assert.equal(await page.locator('#owgAnalysisSave').isDisabled(),true,'loading cannot save a stale result');
   assert.equal(await page.locator('#owgAnalysisJson').isDisabled(),true,'loading clears stale exports');
   // A rename is a graph refactor: exact arithmetic references and selected
   // measures follow it, while the saved original remains unchanged.
   const pureRefactor=await page.evaluate(ms=>{
     const model=window.__owgEventLogModel;
     const result=model.refactorMetricGraph(ms,'work_selected',{...ms[0],name:'renamed_selected'},['avg:metric:work_selected','sum:metric:work_fraction']);
     let refused='';
     try{model.refactorMetricGraph(ms,'work_selected',{...ms[0],name:'work_all'},['avg:metric:work_selected']);}
     catch(error){refused=error.message;}
     return {result,refused,original:ms};
   },sharePure);
   assert.equal(pureRefactor.result.metrics[2].left.name,'renamed_selected');
   assert.deepEqual(pureRefactor.result.measures,['avg:metric:renamed_selected','sum:metric:work_fraction']);
   assert.match(pureRefactor.refused,/already has that name/);
   assert.deepEqual(pureRefactor.original,sharePure,'a refused or successful pure refactor never mutates its source');
   await page.getByRole('button',{name:'Edit metric work_selected',exact:true}).click();
   assert.equal(await page.locator('#owgMetricMissingPolicy').inputValue(),'available');
   await page.locator('#owgMetricName').fill('work_all');
   await page.locator('#owgMetricUpdate').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'already has that name'}).waitFor();
   assert.match(await page.locator('#owgMetricList').textContent(),/work_selected/);
   await page.locator('#owgMetricName').fill('renamed_selected');
   await page.locator('#owgMetricMissingPolicy').selectOption('complete');
   await page.locator('#owgMetricUpdate').click();
   await page.locator('#owgAnalysisStatus').filter({hasText:'dependent arithmetic and selected measures'}).waitFor();
   assert.equal(await page.getByRole('button',{name:'Edit metric renamed_selected',exact:true}).count(),1);
   assert.match(await page.locator('#owgMetricList').textContent(),/renamed_selected/);
   await page.getByRole('button',{name:'Edit metric work_fraction',exact:true}).click();
   await page.locator('#owgMetricName').fill('renamed_fraction');
   await page.locator('#owgMetricUpdate').click();
   assert.equal(await page.locator('#owgAnalysisMeasure1').inputValue(),'avg:metric:renamed_fraction',
     'the selected measure follows the renamed arithmetic result');
   assert.equal(await page.locator('#owgMetricUpdate').isDisabled(),true);
   assert.equal(await page.locator('#owgAnalysisSave').isDisabled(),true,'editing requires a fresh run before saving');
   assert.equal(await page.locator('#owgAnalysisJson').isDisabled(),true,'editing cannot export an older result');
   const [loadedResponse]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.locator('#owgAnalysisRun').click()]);
   assert.equal(loadedResponse.status(),200);
   const editedSpec=JSON.parse(loadedResponse.request().postData());
   assert.equal(editedSpec.measures[0].of,'metric:renamed_fraction');
   assert.deepEqual(editedSpec.metrics,[{...sharePure[0],name:'renamed_selected',missing_policy:'complete'},
     sharePure[1],{...sharePure[2],name:'renamed_fraction',left:{kind:'metric',name:'renamed_selected'}}]);
   assert.deepEqual((await loadedResponse.json()).rows,shareResult.rows);
   await page.locator('#owgAnalysisName').fill('Revised Work contribution');
   await page.locator('#owgAnalysisSave').click();
   await page.getByRole('button',{name:'Run saved analysis Revised Work contribution',exact:true}).waitFor();
   await page.getByRole('button',{name:'Run saved analysis Observed Work contribution',exact:true}).waitFor();
   const [replayedRevision]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.getByRole('button',{name:'Run saved analysis Revised Work contribution',exact:true}).click()]);
   assert.equal(replayedRevision.status(),200);
   const replayedSpec=JSON.parse(replayedRevision.request().postData());
   // Stored specs echo optional fields as null and the stage selector default.
   const echoedDefaultsRemoved=metrics=>metrics.map(m=>Object.fromEntries(Object.entries(m).filter(([k,v])=>
     v!==null&&!(k==='stage_occurrence'&&v==='first'&&!m.from_activity&&!m.to_activity))));
   assert.deepEqual(echoedDefaultsRemoved(replayedSpec.metrics),echoedDefaultsRemoved(editedSpec.metrics),
     'saved revision keeps rewritten dependencies');
   assert.deepEqual(replayedSpec.measures,editedSpec.measures,'saved revision keeps rewritten measure');
   const [replayedOriginal]=await Promise.all([
     page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/analysis')&&r.request().method()==='POST'),
     page.getByRole('button',{name:'Run saved analysis Observed Work contribution',exact:true}).click()]);
   assert.deepEqual(echoedDefaultsRemoved(JSON.parse(replayedOriginal.request().postData()).metrics),sharePure,'original saved analysis is unchanged');
   const numericAxe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogAnalysis'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(numericAxe.violations,[]);
   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgEventLogDelete').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Event log deleted.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('Event-log workspace acceptance passed: consented CSV import with rejected naive time, a counts-only quality report and the rejected row kept, then released under an explicit offset decision, variants, Disco-style map sliders, case playback along the map, direct and eventual follower case filters saved and re-applied with a filtered case drill-down, filtered KPIs with CSV export, cohort benchmark with side-by-side map comparison, what-if simulation with an added activity, routing, arrival and activity-time uncertainty, step-by-step routing, and a held-out validation, working calendars, a shared resource pool and seasonal arrivals entered and measured from the log, conformance, discovered model drafts with BPMN export, processing vs waiting time from lifecycle columns, a dotted chart with hover, drift detection, root-cause associations, pseudonymous handovers, saved signals, saved ad-hoc analyses with a typed, saved case metric, compliance rules saved as signals, backtested predictions with a censoring-aware as-of replay, appending a newer export, WCAG audit, mobile width, OCEL export, verified deletion, OCEL import with per-object-type maps and what flattening onto each type leaves out or copies, which object attributes change over time, and object rules across types, and an object process model checked with an edited arc.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
