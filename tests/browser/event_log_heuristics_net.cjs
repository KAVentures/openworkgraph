const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: discover a heuristics net with a parallel split, a choice and a loop.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-heuristics-'));
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
   // 40 claims: Register, then Check and Pay in either order, possibly Check repeated, then Approve or Reject, then Close.
   const rows=['case,activity,time,who'];
   for(let i=0;i<40;i++){const steps=['Register',...(i%2?['Check','Pay']:['Pay','Check']),...(i%5===0?['Check']:[]),i%3?'Approve':'Reject','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-01-01T${String(j).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'claims.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 208 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgHeuristics');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgHeuristicsSupport').fill('2');await page.locator('#owgHeuristicsRun').click();
   const out=page.locator('#owgHeuristicsResults');
   await page.locator('#owgHeuristicsStatus').filter({hasText:'between 6 activities'}).waitFor();
   assert.equal(await page.locator('#owgHeuristicsGraph svg').count(),1);
   const gates=await out.locator('table').first().textContent();
   assert(gates.includes('Split afterRegisterCheck, PayAND (parallel)'),gates);
   assert(gates.includes('Join beforeCloseApprove, RejectXOR (choice)'),gates);
   await out.locator('details summary').click();
   assert((await out.locator('details tbody tr').count())>=6);
   const review=page.locator('#owgHeuristicsCaseReview');
   await out.getByRole('button',{name:'Inspect cases for Check repeated',exact:true}).click();
   await review.getByRole('status').filter({hasText:'4 occurrences in 4 cases.'}).waitFor();
   await review.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 6 of 6 events'}).waitFor();
   await out.getByRole('button',{name:'Inspect cases for Start at Register',exact:true}).click();
   await review.getByRole('status').filter({hasText:'40 occurrences in 40 cases.'}).waitFor();
   await page.locator('#owgHeuristicsAnd').selectOption('1');
   await page.locator('#owgHeuristicsLoop').selectOption('0.9');
   await page.locator('#owgHeuristicsConnected').uncheck();
   const [configured]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/heuristics-net')),page.locator('#owgHeuristicsRun').click()]);
   assert.deepEqual((await configured.json()).parameters,{dependency_threshold:0.5,and_threshold:1,loop_two_threshold:0.9,min_occurrences:2,all_connected:false});
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgHeuristics'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Heuristics net browser acceptance passed: net and gateway labels, case evidence for loops and boundaries, event history, miner settings, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
