const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: find the cases most like a chosen one and hand one to case compare.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-explain-'));
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
   const context=await browser.newContext({locale:'en-US'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   const at=(d,h)=>new Date(Date.UTC(2024,0,1+d,8)+h*3600000).toISOString().replace('.000Z','Z');
   for(let i=0;i<20;i++)[['Receive',0],['Check',1],['Approve',3],['Pay',4]].forEach(([a,h])=>rows.push(`c${String(i).padStart(2,'0')},${a},${at(i,h)}`));
   for(let i=0;i<3;i++)[['Receive',0],['Check',1],['Escalate',2],['Approve',20+i],['Pay',21+i]].forEach(([a,h])=>rows.push(`e${i},${a},${at(22+i,h)}`));
   [['Receive',0],['Check',1],['Check',2],['Escalate',3],['Approve',33],['Pay',34]].forEach(([a,h])=>rows.push(`c99,${a},${at(30,h)}`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'similar.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 101 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgCaseExplain');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgSimilarRun').click();
   const out=page.locator('#owgSimilarResult');
   await out.filter({hasText:'Enter a case ID.'}).waitFor();
   await page.locator('#owgCaseExplainId').fill('c99');await page.locator('#owgSimilarRun').click();
   await out.locator('table').waitFor();
   const text=await out.innerText();
   assert.match(text,/0 other cases follow exactly the same path\. The 10 most similar cases took a median of 4 h; this case took 34 h\./);
   const cells=await out.locator('tbody tr').first().locator('td').allTextContents();
   // The three escalated cases differ by one repeated Check; the longest of them (23 h) is closest in duration.
   assert.deepEqual(cells.slice(0,5),['e2','91%','−Check','23 h','2024-01-25'],cells.join('|'));
   const second=await out.locator('tbody tr').nth(3).locator('td').allTextContents();
   assert.deepEqual(second.slice(1,3),['80%','−Check, −Escalate'],second.join('|'));
   await page.getByRole('button',{name:'Compare with case e2'}).click();
   await page.locator('#owgCaseCompareResult').filter({hasText:'Case c99 took'}).waitFor();
   assert.equal(await page.locator('#owgCaseCompareId').inputValue(),'e2');
   await page.locator('#owgCaseExplainId').fill('nope');await page.locator('#owgSimilarRun').click();
   await out.filter({hasText:'this case is not in the log'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseExplain'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Similar cases browser acceptance passed: empty input, nearest cases ranked by path then duration with differences, compare hand-off, unknown case, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
