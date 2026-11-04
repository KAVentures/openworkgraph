const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-decision-deps-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const model={"goal": "Approve a loan", "baseline_source": "user_declared", "baseline_path": ["Credit", "Approve"], "baseline_coverage": "user_declared_design", "activities": [{"id": "credit", "label": "Credit", "kind": "decision"}, {"id": "good", "label": "Good credit"}, {"id": "poor", "label": "Poor credit"}, {"id": "approval", "label": "Approval", "kind": "decision"}, {"id": "approve", "label": "Approve"}, {"id": "decline", "label": "Decline"}], "transitions": [{"source": "credit", "target": "good", "rule_id": "score_high"}, {"source": "credit", "target": "poor", "rule_id": "score_low"}, {"source": "good", "target": "approval"}, {"source": "poor", "target": "approval"}, {"source": "approval", "target": "approve", "rule_id": "credit_good"}, {"source": "approval", "target": "decline", "rule_id": "credit_poor"}], "entry_activity_id": "credit", "terminal_activity_ids": ["approve", "decline"], "business_rules": [{"id": "score_high", "text": "score high", "user_confirmed": true, "predicate": {"conditions": [{"path": "/score", "operator": "gte", "expected": 700}]}}, {"id": "score_low", "text": "score low", "user_confirmed": true, "predicate": {"conditions": [{"path": "/score", "operator": "lt", "expected": 700}]}}, {"id": "credit_good", "text": "credit good", "user_confirmed": true, "predicate": {"conditions": [{"path": "/decisions/credit", "operator": "eq", "expected": "good"}]}}, {"id": "credit_poor", "text": "credit poor", "user_confirmed": true, "predicate": {"conditions": [{"path": "/decisions/credit", "operator": "eq", "expected": "poor"}]}}], "decision_tables": [{"id": "approval_table", "decision_activity_id": "approval", "effective_from": "2026-01-01", "source_basis": {"label": "Credit policy", "document_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, "rows": [{"id": "approval_table_0", "rule_id": "credit_good", "target_activity_id": "approve"}, {"id": "approval_table_1", "rule_id": "credit_poor", "target_activity_id": "decline"}], "user_confirmed": true}, {"id": "credit_table", "decision_activity_id": "credit", "effective_from": "2026-01-01", "source_basis": {"label": "Credit policy", "document_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, "rows": [{"id": "credit_table_0", "rule_id": "score_high", "target_activity_id": "good"}, {"id": "credit_table_1", "rule_id": "score_low", "target_activity_id": "poor"}], "user_confirmed": true}]};
  await page.evaluate(async model=>{await window.__owgAuthReady;const r=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Loan approval',process_model:model,user_confirmed:true})});if(!r.ok)throw new Error(await r.text());},model);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();await page.getByRole('button',{name:'Loan approval · r1',exact:true}).click();
  await page.getByLabel('Required decision for approval_table',{exact:true}).selectOption('credit_table');
  await page.getByLabel('Fact path for required decision approval_table',{exact:true}).fill('/decisions/credit');
  await page.getByRole('button',{name:'Add required decision approval_table',exact:true}).click();
  await page.getByText('Requires credit_table as fact /decisions/credit',{exact:false}).waitFor();
  for(const id of ['approval_table','credit_table'])await page.getByLabel('I confirm this exact table, referenced rules, source basis and effective dates '+id,{exact:true}).check();
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 2.'}).waitFor();
  const file={as_of_date:'2026-06-01',cases:[{case_id:'case_1',facts:{score:720}},{case_id:'case_2',facts:{score:650}},{case_id:'case_3',facts:{}}]};
  await page.getByLabel('Choose authorized case-facts JSON',{exact:true}).setInputFiles({name:'cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(file))});
  await page.getByLabel('I authorize local analysis of the case facts in this selected file.',{exact:true}).check();
  await page.getByRole('button',{name:'Check facts against saved rules',exact:true}).click();
  const output=page.locator('#owgCatalogRuleCheckResult');await output.getByText('3 supplied case-fact reports',{exact:true}).waitFor();
  const text=await output.textContent();
  assert.match(text,/Decision table approval_table · unique match → approve/);assert.match(text,/Decision table approval_table · unique match → decline/);
  assert.match(text,/Decision table approval_table · dependency unresolved · waiting on credit_table/);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgCatalogEditor'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Decision dependency browser passed: add required decision, confirm, save, dependency-ordered case checks, unresolved withholding, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
