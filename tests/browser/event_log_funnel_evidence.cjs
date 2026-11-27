const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-funnel-pages-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','funnel_evidence_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let browser,logs='';for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-10000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  const option=page.locator('#owgEventLogPick option').filter({hasText:'Funnel evidence'});await option.waitFor({state:'attached'});
  await page.locator('#owgEventLogPick').selectOption(await option.getAttribute('value'));await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgFunnelStep1').selectOption('A');await page.locator('#owgFunnelStep2').selectOption('B');await page.locator('#owgFunnelStep3').selectOption('');
  await page.locator('#owgFunnelEvidenceStep').selectOption('2');await page.locator('#owgFunnelRun').click();
  const evidence=page.locator('#owgFunnelEvidence');await evidence.getByText('Showing 50 of 105 cases reaching step 2.').waitFor();
  assert.equal(await evidence.locator('tbody tr').first().locator('td').first().textContent(),'c000');
  await page.locator('#owgFunnelEvidenceNext').click();await evidence.getByRole('button',{name:'Open case c050'}).waitFor();
  await page.locator('#owgFunnelEvidenceNext').click();await evidence.getByText('Showing 5 of 105 cases reaching step 2.').waitFor();
  assert.equal(await page.locator('#owgFunnelEvidenceNext').isDisabled(),true);
  await evidence.getByRole('button',{name:'Open case c100'}).click();
  await page.locator('#owgEventLogCaseEvents').getByText('c100',{exact:false}).first().waitFor();
  await page.locator('#owgFunnelEvidencePrevious').click();await evidence.getByRole('button',{name:'Open case c050'}).waitFor();
  await page.locator('#owgFunnelWindow').selectOption('24');assert.equal(await evidence.count(),0);
  assert.match(await page.locator('#owgFunnelStatus').textContent(),/Settings changed/);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgFunnel'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);assert.deepEqual(errors,[]);
  console.log('Funnel evidence browser passed: three pages, case opening, stale settings and accessibility.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
