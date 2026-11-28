// R11: real Chromium multi-document source reconciliation and human decision review.
const {chromium}=require('playwright'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),{spawn}=require('node:child_process');
const {tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-multi-source-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',
   ['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],
   {env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){
    try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}
    catch{}await new Promise(r=>setTimeout(r,100));
  }assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);
  await page.locator('[role=tab][data-tab=evidence]').click();
  const model={goal:'Approve refunds',baseline_source:'user_declared',baseline_path:['Review','Approve'],
    activities:[{id:'review',label:'Review refund',kind:'decision'},
      {id:'approve',label:'Manager approval',kind:'activity'},
      {id:'deny',label:'Reject refund',kind:'activity'}],
    transitions:[{source:'review',target:'approve',rule_id:'big'},
      {source:'review',target:'deny',rule_id:'small'}],
    entry_activity_id:'review',terminal_activity_ids:['approve','deny'],
    business_rules:[{id:'big',text:'Refund amount above 500 EUR needs manager approval'},
      {id:'small',text:'Refund amount of 500 EUR or less is paid directly',user_confirmed:true}]};
  const wid=await page.evaluate(async model=>{
    await window.__owgAuthReady;
    const r=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({title:'Reconcile refund SOPs',process_model:model,user_confirmed:true})});
    if(!r.ok)throw new Error(await r.text());
    return (await r.json()).workflow_id;
  },model);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
  await page.getByRole('button',{name:'Reconcile refund SOPs · r1',exact:true}).click();
  const panel=page.locator('#owgCatalogClarification');
  const titles=panel.locator('[data-clarify-title]'),texts=panel.locator('[data-clarify-text]');
  // The clarification panel renders after the saved design loads; wait for it before counting.
  await texts.first().waitFor();
  assert.equal(await texts.count(),1);
  await titles.first().fill('SOP 2025');
  await texts.first().fill('A refund amount above 500 EUR must get manager approval.');
  await panel.locator('#owgCatalogClarifyAddDocument').click();
  await titles.nth(1).fill('SOP 2026');
  await texts.nth(1).fill('A refund amount above 1000 EUR requires manager approval.');
  await panel.locator('#owgCatalogClarifyAddDocument').click();
  await titles.nth(2).fill('Management override');
  await texts.nth(2).fill('Manager approval is never required for a refund amount.');
  await panel.getByRole('button',{name:'Find open questions across sources'}).click();
  await panel.locator('#owgCatalogClarifyResult').getByText(/source statements/).first().waitFor();
  const q=panel.locator('#owgCatalogClarifyResult section').filter({hasText:"documents disagree about rule 'big'"}).first();
  await q.waitFor();
  assert.equal(await q.locator('.owgClarifyConflicts input[type=radio]').count(),3);
  assert.match(await q.textContent(),/SOP 2025/);
  assert.match(await q.textContent(),/SOP 2026/);
  assert.match(await q.textContent(),/Management override/);
  await q.getByRole('button',{name:'Confirm',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Select one governing statement'}).waitFor();
  const before=await page.evaluate(async id=>(await (await fetch(
    '/v1/process-catalog/clarify/decisions?workflow_id='+id)).json()).decisions,wid);
  assert.deepEqual(before,[]);
  await q.locator('.owgClarifyConflicts input[type=radio]').first().check();
  await q.getByRole('button',{name:'Confirm',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Explain your resolution'}).waitFor();
  await q.locator('input[aria-label^="Human explanation"]').fill('2025 policy owner confirmed this revision for the reviewed case type.');
  await q.getByRole('button',{name:'Confirm',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Human decision recorded'}).waitFor();
  const decisions=await page.evaluate(async id=>(await (await fetch(
    '/v1/process-catalog/clarify/decisions?workflow_id='+id)).json()).decisions,wid);
  assert.equal(decisions.length,1);
  assert.equal(decisions[0].cited_statements.length,1);
  assert.equal(decisions[0].cited_statements[0].document_title,'SOP 2025');
  assert.equal(await panel.locator('#owgCatalogClarifyResult').getByText(
    "The documents disagree about rule 'big'",{exact:false}).count(),0);
  await texts.nth(0).fill('A refund amount above 700 EUR must get manager approval.');
  await panel.locator('#owgCatalogClarifyResult').getByText('Documents changed.',{exact:false}).waitFor();
  await panel.getByRole('button',{name:'Find open questions across sources'}).click();
  await panel.locator('#owgCatalogClarifyResult').getByText(
    "The documents disagree about rule 'big'",{exact:false}).waitFor();
  await texts.nth(0).fill('A refund amount above 700 EUR must get manager approval. '.repeat(6));
  await panel.getByRole('button',{name:'Find open questions across sources'}).click();
  await panel.locator('#owgCatalogClarifyResult').getByText('Source review incomplete:',{exact:false}).waitFor();
  const incomplete=panel.locator('#owgCatalogClarifyResult section').filter({hasText:"documents disagree about rule 'big'"}).first();
  await incomplete.getByText('Confirmation is unavailable',{exact:false}).waitFor();
  assert.equal(await incomplete.getByRole('button',{name:'Confirm',exact:true}).count(),0);
  await incomplete.getByRole('button',{name:'Defer',exact:true}).click();
  await incomplete.getByText('Previous decision: defer.',{exact:false}).waitFor();
  await panel.locator('#owgCatalogClarifyAddDocument').click();
  await panel.locator('#owgCatalogClarifyAddDocument').click();
  assert.equal(await texts.count(),5);
  assert.equal(await panel.locator('#owgCatalogClarifyAddDocument').isDisabled(),true);
  // Effective dates are a separate temporary review. The previous ordinary
  // clarification decision remains unchanged and is not imported.
  const effective=panel.locator('#owgEffectivePolicyPanel');
  await effective.locator('summary').first().click();
  await effective.getByLabel('Timeline start date',{exact:true}).fill('2024-01-01');
  await effective.getByLabel('Timeline end date exclusive',{exact:true}).fill('2024-02-01');
  await effective.getByLabel('Policy family identifier',{exact:true}).fill('refund');
  await effective.getByLabel('Policy family label',{exact:true}).fill('Refund policy');
  await effective.getByLabel('Policy version identifier',{exact:true}).fill('old');
  await effective.getByLabel('Version family identifier',{exact:true}).fill('refund');
  await effective.getByLabel('Version document title',{exact:true}).fill('Old refund policy');
  await effective.getByLabel('Version effective from',{exact:true}).fill('2024-01-01');
  await effective.getByLabel('Version effective until exclusive',{exact:true}).fill('2024-01-15');
  await effective.getByLabel('Exact version source text',{exact:true}).fill('A refund amount above 500 EUR must get manager approval.');
  await effective.getByRole('button',{name:'Add policy version',exact:true}).click();
  await effective.getByLabel('Policy version identifier',{exact:true}).nth(1).fill('new');
  await effective.getByLabel('Version family identifier',{exact:true}).nth(1).fill('refund');
  await effective.getByLabel('Version document title',{exact:true}).nth(1).fill('New refund policy');
  await effective.getByLabel('Version effective from',{exact:true}).nth(1).fill('2024-01-15');
  await effective.getByLabel('Exact version source text',{exact:true}).nth(1).fill('A refund amount above 1000 EUR must get manager approval.');
  await effective.getByRole('button',{name:'Prepare effective policy timeline',exact:true}).click();
  const timeline=effective.locator('#owgEffectivePolicyTimeline');
  await timeline.getByText('31 days · 31 resolved · 0 incomplete · 0 ambiguous.',{exact:true}).waitFor();
  await effective.getByLabel('Policy review date',{exact:true}).fill('2024-01-15');
  await effective.getByLabel('Optional previous review date',{exact:true}).fill('2024-01-14');
  await effective.getByRole('button',{name:'Review effective policies on date',exact:true}).click();
  const dated=effective.locator('#owgEffectivePolicyDateReview');
  await dated.getByText('Policy review for 2024-01-15',{exact:true}).waitFor();
  await dated.getByText(/1 added and 1 removed indexed statement occurrences/).waitFor();
  assert.match(await dated.textContent(),/New refund policy/);
  const oldLedger=await page.evaluate(async id=>(await(await fetch('/v1/process-catalog/clarify/decisions?workflow_id='+id)).json()).decisions,wid);
  assert.ok(oldLedger.some(d=>JSON.stringify(d)===JSON.stringify(decisions[0]))); // original decision is intact
  // Edit an interval and verify stale date-review results disappear.
  await effective.getByLabel('Version effective from',{exact:true}).nth(1).fill('2024-01-14');
  assert.equal(await dated.textContent(),'');
  await effective.getByRole('button',{name:'Prepare effective policy timeline',exact:true}).click();
  await timeline.getByText('31 days · 30 resolved · 0 incomplete · 1 ambiguous.',{exact:true}).waitFor();
  await effective.getByLabel('Policy review date',{exact:true}).fill('2024-01-14');
  await effective.getByRole('button',{name:'Review effective policies on date',exact:true}).click();
  await dated.getByText(/unresolved policy coverage/).waitFor();
  assert.equal(await dated.getByRole('button',{name:'Load selected versions into ordinary clarification'}).count(),0);
  await effective.locator('summary').first().click();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
  await page.addScriptTag({content:axe});
  const violations=await page.evaluate(async()=>{
    const report=await axe.run(document.querySelector('#owgCatalogClarification'),
      {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});
    return report.violations.map(v=>v.id);
  });assert.deepEqual(violations,[]);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);
  console.log('R11 multi-SOP browser accepted: 3 conflicting sources, explicit one-source decision, untrusted data, revision/source pin, 5-document bound and WCAG.');
 }finally{
  if(browser)await browser.close();
  server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');
  await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
