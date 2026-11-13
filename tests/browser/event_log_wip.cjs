const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: work in progress over time with a Little's law check.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-wip-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let d=0;d<10;d++)for(let k=0;k<2;k++){const t=new Date(Date.UTC(2024,2,4+d,1+k));rows.push(`c${d}_${k},Open,${t.toISOString()}`);
     if(!(d===9&&k===1))rows.push(`c${d}_${k},Close,${new Date(t.getTime()+2*86400e3).toISOString()}`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'wip.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 39 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   await page.locator('#owgEventLogWip').waitFor();
   await page.locator('#owgWipEnds').fill('Close');await page.locator('#owgWipRun').click();
   const out=page.locator('#owgWipResults');await out.locator('table').waitFor();
   const text=await out.textContent();
   assert.match(text,/20 cases; 1 never reached an end activity/,text);
   assert.match(text,/ratio (0\.9|1\.0|1\.1)\d*/,text);
   assert.equal(await out.locator('tbody tr').count(),12);
   assert.match(await out.locator('svg').getAttribute('aria-label'),/Open cases per day: from 2 to 1, peak 4/);
   await page.locator('#owgWipBucket').selectOption('week');await page.locator('#owgWipRun').click();
   await out.locator('svg[aria-label^="Open cases per week"]').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogWip'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Work-in-progress browser acceptance passed: daily table and chart, never-completed count, Little\'s law ratio, weekly buckets, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
