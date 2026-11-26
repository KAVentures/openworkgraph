const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: unusual cases with reasons and a link to Explain a case.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-footprints-'));
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
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // 30 ordinary tickets (Open, Review, Close) and one that was escalated.
   const rows=['case,activity,time,who'];
   for(let i=0;i<31;i++){const steps=i===30?['Open','Escalate','Review','Close']:['Open','Review','Close'];
     steps.forEach((a,j)=>rows.push(`t${String(i).padStart(2,'0')},${a},2024-01-${String(1+(i%28)).padStart(2,'0')}T${String(j*2+(i%3)).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 94 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   // The navigator finds a section by name or description and moves focus to its heading.
   await page.locator('#owgAnalysisNav').waitFor();
   assert.match(await page.locator('#owgAnalysisNavCount').textContent(),/^\d+ analyses on this page\.$/);
   await page.locator('#owgAnalysisNavSearch').fill('stand out');
   await page.locator('#owgAnalysisNavCount').filter({hasText:'1 of '}).waitFor();
   const visible=page.locator('#owgAnalysisNavList li:not([hidden]) button');
   assert.equal(await visible.count(),1);assert.equal(await visible.textContent(),'Unusual cases');
   await visible.click();
   assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Unusual cases');
   await page.locator('#owgAnalysisNavSearch').fill('');
   const box=page.locator('#owgUnusual');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgUnusualRun').click();
   await page.locator('#owgUnusualStatus').filter({hasText:'1 of 31 cases stand out'}).waitFor();
   const status=await page.locator('#owgUnusualStatus').textContent();
   assert(status.includes('Rare activity 1')&&status.includes('Rare path 1')&&status.includes('Unusually slow 1'),status);
   const cells=await page.locator('#owgUnusualResults tbody tr').first().locator('td').allTextContents();
   assert.equal(cells[0],'t30');assert(cells[2].includes('Rare activity: contains Escalate (1 cases)'),cells[2]);
   // Every unusual case downloads as a CSV worklist with its reasons and path.
   const [unusualCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgUnusualCsv').click()]);
   const unusualLines=(await fs.readFile(await unusualCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.equal(unusualLines[0],'case_id,score,signals,reasons,events,hours,path,path_truncated');
   assert.equal(unusualLines.length,2,unusualLines.join('|'));
   assert.match(unusualLines[1],/^t30,[\d.]+,[a-z_;]*rare_activity[a-z_;]*,.*contains Escalate \(1 cases\).*,\d+,[\d.]+,.*Escalate.*,False$/);
   await page.locator('#owgUnusualStatus').filter({hasText:'Downloaded every matching unusual case (rows=1, unusual=1, cases=31)'}).waitFor();
   const reason=page.locator('#owgUnusualSignal');
   await reason.selectOption('heavy_rework');await page.locator('#owgUnusualRun').click();
   await box.getByText('No unusual cases match this selection.').waitFor();
   await reason.selectOption('rare_activity');await page.locator('#owgUnusualRun').click();
   await box.getByText('Showing 1–1 of 1 matching cases.').waitFor();
   assert.equal(await box.getByRole('button',{name:'Next page'}).isDisabled(),true);
   const selected=box.getByRole('checkbox',{name:'Select case t30'});
   await box.getByRole('button',{name:'Clear selection'}).click();
   assert.equal(await page.locator('#owgUnusualTag').isDisabled(),true);
   await selected.check();
   await page.locator('#owgUnusualTag').click();
   await page.locator('#owgUnusualTagMessage').filter({hasText:'Tagged 1 cases “unusual”.'}).waitFor();
   await page.locator('#owgCaseTags').filter({hasText:'unusual'}).waitFor();
   await page.locator('#owgUnusualResults button[aria-label="Explain case t30"]').click();
   assert.equal(await page.locator('#owgCaseExplainId').inputValue(),'t30');
   await page.locator('#owgCaseExplainResult').filter({hasText:/\S/}).waitFor();
   await page.locator('#owgCaseExplainModel').filter({hasText:'Against the discovered model: did Escalate where the model does not allow it'}).waitFor();
   await page.locator('#owgCaseCompareId').fill('t01');await page.locator('#owgCaseCompareRun').click();
   await page.locator('#owgCaseCompareResult').filter({hasText:'3 steps are shared.'}).waitFor();
   assert.match(await page.locator('#owgCaseCompareResult').textContent(),/Escalateonly t30/);
   await page.addScriptTag({content:axeSource});
   const navAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgAnalysisNav'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(navAudit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgUnusual'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Unusual cases browser acceptance passed: the escalated case is found with its reasons, Explain opens it, tagged for filtering, the analysis navigator finds and focuses it, CSV worklist, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
