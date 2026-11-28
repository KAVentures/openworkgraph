const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
async function reviewPrerequisites(browser,base,session,bearer){
 const page=await browser.newPage();
 try{
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const saveDesign=(title,prior=null)=>page.evaluate(async ({title,prior})=>{
    await window.__owgAuthReady;
    const body={title,user_confirmed:true,process_model:{
      goal:'Review the authorized case',baseline_source:'user_declared',baseline_path:['Review'],
      activities:[{id:'review',label:'Review'}],entry_activity_id:'review',terminal_activity_ids:['review']
    }};
    if(prior)Object.assign(body,{workflow_id:prior.workflow_id,expected_revision:prior.revision});
    const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!response.ok)throw new Error(await response.text());
    return response.json();
  },{title,prior});
  const output=await saveDesign('Purchase evidence');
  const consumer=await saveDesign('Finalize purchase');
  const authPath=base+'/v1/process-catalog/prerequisites?'+new URLSearchParams({workflow_id:consumer.workflow_id});
  const denied=await fetch(authPath,{headers:{Authorization:'Bearer '+bearer}});
  assert.equal(denied.status,403);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Finalize purchase · r1',exact:true}).click();
  const panel=page.locator('#owgCatalogPrerequisites');
  const review=panel.getByRole('button',{name:'Review prerequisites',exact:true});
  await review.click();
  await panel.getByRole('button',{name:'Load saved designs for prerequisites',exact:true}).click();
  await panel.getByLabel('Prerequisite design',{exact:true}).selectOption(output.workflow_id);
  await panel.getByRole('button',{name:'Add prerequisite',exact:true}).click();
  await panel.getByLabel('Reason for Purchase evidence',{exact:true}).fill('Uses the recorded output');
  const save=panel.getByRole('button',{name:'Save prerequisites',exact:true});
  assert.ok(await save.isDisabled());
  await panel.getByLabel('I reviewed these exact prerequisite revisions',{exact:true}).check();
  await save.click();
  await panel.getByText('Saved prerequisite revision 1.',{exact:true}).waitFor();
  await saveDesign('Purchase evidence',output);
  await review.click();
  await panel.getByText('Newer revision available',{exact:true}).waitFor();
  await panel.getByRole('button',{name:'Use latest Purchase evidence · r2',exact:true}).click();
  assert.ok(await save.isDisabled());
  await panel.getByLabel('I reviewed these exact prerequisite revisions',{exact:true}).check();
  await save.click();
  await panel.getByText('Saved prerequisite revision 2.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'Purchase evidence · r2',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Opened immutable revision 2.'}).waitFor();
  await review.click();
  await panel.getByText('Finalize purchase · r1 · Pins this design at r2 · Current',{exact:true}).waitFor();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
  await page.addScriptTag({content:axe});
  const violations=await page.evaluate(async()=>(
    await axe.run(document.querySelector('#owgCatalogPrerequisites'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})
  ).violations.map(item=>item.id));
  assert.deepEqual(violations,[]);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
 }finally{await page.close();}
}
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-local-decisions-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await reviewPrerequisites(browser,base,session,(await fs.readFile(path.join(scratch,'token.txt'),'utf8')).trim());
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const model={goal:'Review a purchase',baseline_source:'user_declared',baseline_path:['Review','Accept'],activities:[{id:'choose',label:'Review',kind:'decision'},{id:'accept',label:'Accept'},{id:'escalate',label:'Escalate'}],transitions:[{source:'choose',target:'accept',rule_id:'small'},{source:'choose',target:'escalate',rule_id:'large'}],entry_activity_id:'choose',terminal_activity_ids:['accept','escalate'],business_rules:[{id:'small',text:'Small purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'lt',expected:100}]}},{id:'large',text:'Large purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'gte',expected:100}]}}]};
  const saved=await page.evaluate(async model=>{await window.__owgAuthReady;const r=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Local purchase',process_model:model,user_confirmed:true})});if(!r.ok)throw new Error(await r.text());return r.json();},model);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();await page.getByRole('button',{name:'Local purchase · r1',exact:true}).click();
  await page.getByRole('button',{name:'Add decision table',exact:true}).click();
  await page.getByLabel('Effective start decision_table_1',{exact:true}).fill('2026-01-01');
  await page.getByLabel('Source document label decision_table_1',{exact:true}).fill('Reviewed reference');
  await page.getByLabel('Source document SHA256 decision_table_1',{exact:true}).fill('a'.repeat(64));
  const confirm=page.getByLabel('I confirm this exact table, referenced rules, source basis and effective dates decision_table_1',{exact:true});await confirm.check();
  await page.getByLabel('Business goal',{exact:true}).fill('Review the selected purchase');assert.equal(await confirm.isChecked(),false);await confirm.check();
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 2.'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).isDisabled(),false);
  const file={as_of_date:'2026-10-26',cases:[{case_id:'case_1',facts:{amount:99}},{case_id:'case_2',facts:{amount:100}},{case_id:'case_3',facts:{}}]};
  await page.getByLabel('Choose authorized case-facts JSON',{exact:true}).setInputFiles({name:'cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(file))});
  await page.getByLabel('I authorize local analysis of the case facts in this selected file.',{exact:true}).check();
  await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).click();
  const output=page.locator('#owgCatalogRuleCheckResult');await output.getByText('3 supplied case-fact reports',{exact:true}).waitFor();
  assert.match(await output.textContent(),/unique match → accept/);assert.match(await output.textContent(),/unique match → escalate/);assert.match(await output.textContent(),/Decision table decision_table_1 · unknown/);
  // The same real local upload should display only selected-case branch coverage:
  // two concrete branches chosen, plus a third case with unknown facts.
  const coverage=output.getByText('Decision table decision_table_1 · UNIQUE · 2/2 declared branches selected in these supplied cases',{exact:true});
  await coverage.waitFor();await coverage.click();
  const coverageDetails=coverage.locator('xpath=..');
  assert.match(await coverageDetails.textContent(),/2\/3 supplied cases selected an outgoing branch/);
  assert.match(await coverageDetails.textContent(),/unknown: 1/);
  assert.match(await coverageDetails.textContent(),/row_1 → accept · selected 1 case/);
  assert.match(await coverageDetails.textContent(),/row_2 → escalate · selected 1 case/);
  assert.match(await coverageDetails.textContent(),/not exhaustive rule coverage/);
  // Synthetic boundary points are computed separately from the three
  // caller-supplied cases and never contain their fact paths or identifiers.
  const boundary=output.locator('details.owgDecisionBoundaryProbes');
  await boundary.getByText(/Synthetic numeric boundaries · decision_table_1/).waitFor();
  await boundary.locator('summary').click();
  assert.match(await boundary.textContent(),/0 gaps · 0 overlaps/);
  assert.match(await boundary.textContent(),/not proof of exhaustive domain coverage/);

  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download minimized rule-check report',exact:true}).click()]);
  const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));assert.equal(report.process_reference.revision,2);assert.equal(report.decision_table_as_of_date,'2026-10-26');assert.equal(JSON.stringify(report).includes('amount'),false);
  const diagnostic=report.decision_table_boundary_probes[0];
  assert.equal(diagnostic.status,'sampled');
  assert.equal(diagnostic.status_counts.unique_match,diagnostic.sampled_numeric_points);
  assert.deepEqual(diagnostic.gap_witnesses,[]);
  assert.deepEqual(diagnostic.overlap_witnesses,[]);
  assert.equal(diagnostic.source_case_facts_used,false);
  assert.equal(diagnostic.whole_numeric_domain_coverage_verified,false);
  assert.equal(report.decision_table_boundary_probes_basis,'generated_synthetic_numeric_threshold_points_only');
  assert.equal(await page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).isDisabled(),false);
  const [projectDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download OpenAI Agents project',exact:true}).click()]);
  assert.equal((await fs.readFile(await projectDownload.path())).subarray(0,2).toString(),'PK');
  const result=await page.evaluate(async id=>{const r=await fetch('/v1/process-catalog/agent-project?'+new URLSearchParams({workflow_id:id,revision:2}));return {status:r.status,type:r.headers.get('content-type'),bytes:(await r.arrayBuffer()).byteLength};},saved.workflow_id);assert.equal(result.status,200);assert.match(result.type,/zip/);assert.ok(result.bytes>10000);
  // Compare missing-table r1 with the explicitly confirmed table in r2.
  await page.getByRole('button',{name:'Load revision history',exact:true}).click();
  await page.getByRole('button',{name:'Compare r1 with opened r2',exact:true}).click();
  const differential=page.locator('#owgDecisionDifferential');
  await differential.locator('summary').first().click();
  await differential.getByLabel('Differential test dates',{exact:true}).fill('2026-10-26,2026-01-01');
  await differential.getByLabel('Differential synthetic dimensions JSON',{exact:true}).fill(JSON.stringify([{path:'/amount',choices:[{value:99},{value:100},{missing:true}]}]));
  await differential.getByRole('button',{name:'Compare exact finite decision domains'}).click();
  await differential.getByText('6 / 6 dated input points changed. 6 table/input evaluations compared.',{exact:true}).waitFor();
  await differential.getByLabel('Differential evidence filter',{exact:true}).selectOption('selection_gained');
  await differential.getByText('4 matching points; 4 shown at offset 0.',{exact:true}).waitFor();
  const [diffDownload]=await Promise.all([page.waitForEvent('download'),differential.getByRole('button',{name:'Download minimized differential report'}).click()]);
  const diff=JSON.parse(await fs.readFile(await diffDownload.path(),'utf8'));
  assert.equal(diff.nonexclusive_flag_counts.selection_gained,4);assert.equal(diff.human_expectations_reviewed,false);
  await differential.getByLabel('Compact witness budget',{exact:true}).fill('1');
  assert.equal(await differential.getByRole('button',{name:'Download minimized differential report'}).count(),0);
  await differential.locator('summary').first().click();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgCatalogEditor'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  const multi={...model,
    baseline_path:['Review','Accept','Audit','Close'],
    activities:[...model.activities,{id:'audit',label:'Audit',kind:'decision'},{id:'close',label:'Close'},{id:'hold',label:'Hold'}],
    transitions:[...model.transitions,{source:'accept',target:'audit'},
      {source:'audit',target:'close',rule_id:'audit_ready'},{source:'audit',target:'hold',rule_id:'audit_wait'}],
    terminal_activity_ids:['escalate','close','hold'],
    business_rules:[...model.business_rules,{id:'audit_ready',text:'Audit ready'},{id:'audit_wait',text:'Audit pending'}]
  };
  const designed=await page.evaluate(async process_model=>{
    const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:'Two purchase decisions',process_model,user_confirmed:true})});
    if(!response.ok)throw new Error(await response.text());
    return response.json();
  },multi);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'Two purchase decisions · r1',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Opened immutable revision 1.'}).waitFor();
  const table=(name,path,rows)=>`<decision id="${name}" name="${name}"><decisionTable>
    <input><inputExpression><text>${path}</text></inputExpression></input><output/>
    ${rows.map(([test,next])=>`<rule><inputEntry><text>${test}</text></inputEntry>
      <outputEntry><text>"${next}"</text></outputEntry></rule>`).join('')}
    </decisionTable></decision>`;
  const xml=`<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/">
    ${table('Review','amount',[['&lt; 100','Accept'],['&gt;= 100','Escalate']])}
    ${table('Audit','reviewed',[['true','Close'],['false','Hold']])}
    </definitions>`;
  const dmn=page.locator('#owgCatalogDMN');
  await dmn.getByLabel('Choose DMN file',{exact:true}).setInputFiles({
    name:'two-tables.dmn',mimeType:'application/xml',buffer:Buffer.from(xml)
  });
  await dmn.getByRole('button',{name:'Inspect DMN file',exact:true}).click();
  await dmn.getByText('2 tables found.',{exact:true}).waitFor();
  await dmn.getByLabel('Design decision for Table 1 · Review',{exact:true}).selectOption('choose');
  await dmn.getByLabel('Design decision for Table 2 · Audit',{exact:true}).selectOption('audit');
  await dmn.getByLabel('DMN effective from',{exact:true}).fill('2026-01-01');
  await dmn.getByLabel('DMN effective until (optional)',{exact:true}).fill('2027-01-01');
  const preview=dmn.getByRole('button',{name:'Preview selected DMN tables',exact:true});
  await preview.click();
  await dmn.getByText('2 tables ready for review.',{exact:true}).waitFor();
  await dmn.getByText('Audit · UNIQUE · 2 unconfirmed rules',{exact:true}).waitFor();
  assert.deepEqual(await page.evaluate(async()=>{
    const audit=await axe.run(document.querySelector('#owgCatalogDMN'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
    return audit.violations.map(v=>v.id);
  }),[]);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  const open=dmn.getByRole('button',{name:'Open proposal in editor',exact:true});
  await dmn.getByLabel('DMN effective until (optional)',{exact:true}).fill('2028-01-01');
  assert.equal(await open.count(),0);
  await preview.click();
  await open.waitFor();
  page.once('dialog',dialog=>dialog.accept());
  await open.click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Opened the DMN tables as unsaved edits of revision 1.'}).waitFor();
  assert.deepEqual(await page.locator('input[data-table-confirmation]').evaluateAll(nodes=>nodes.map(node=>node.checked)),[false,false]);
  const stored=await page.evaluate(async workflow_id=>{
    const response=await fetch('/v1/process-catalog/revision?'+new URLSearchParams({workflow_id,revision:'1'}));
    return response.ok?response.json():null;
  },designed.workflow_id);
  assert.ok(stored);
  assert.deepEqual(stored.process_model.decision_tables||[],[]);
  assert.deepEqual(errors,[]);console.log('Local decision-table browser passed: shared editor, confirmation invalidation, exact save, numeric boundary/unknown checks, minimized export, disabled implementation-project export, multi-table DMN preview, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
