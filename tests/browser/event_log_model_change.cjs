const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: compare the discovered model before and after a date.
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
   // January: Order, Check, Approve, Ship. March: Approve before Check, and a new Notify step.
   const rows=['case,activity,time,who,region'];
   for(let i=0;i<20;i++)['Order','Check','Approve','Ship'].forEach((a,j)=>rows.push(`a${i},${a},2024-01-${String(1+i).padStart(2,'0')}T0${j}:00:00Z,ana,EU`));
   for(let i=0;i<20;i++)['Order','Approve','Check','Ship','Notify'].forEach((a,j)=>rows.push(`b${i},${a},2024-03-${String(1+i).padStart(2,'0')}T0${j}:00:00Z,ana,US`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
   await page.locator('#owgEventLogResource').selectOption('who');await page.locator('#owgEventLogAttributes').selectOption(['region']);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 180 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgModelChange');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgModelChangeSplit').fill('2024-02-15');await page.locator('#owgModelChangeNoise').selectOption('0');
   await page.locator('#owgModelChangeRun').click();
   await page.locator('#owgModelChangeStatus').filter({hasText:'Split at 2024-02-15: 20 cases before, 20 after.'}).waitFor();
   const status=await page.locator('#owgModelChangeStatus').textContent();
   assert(status.includes('the way the work runs changed'),status);
   const out=await page.locator('#owgModelChangeResults').textContent();
   assert(out.includes('New after the split: Notify')&&out.includes('Orderings that changed'),out);
   const later=box.getByText('Non-fitting variants · After cases on the Before model',{exact:true});
   await later.click();
   await box.getByRole('button',{name:/Review Order → Approve → Check → Ship → Notify in After cases/}).click();
   await page.locator('#owgEventLogCasesStatus').getByText(/20 matching cases/).waitFor();
   await page.locator('#owgEventLogCasesList').getByRole('button',{name:'Open case b0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByText(/Showing 5 of 5 events/).waitFor();
   // Fewer activities shown is sent with the request and still compares the periods.
   const [changeRequest]=await Promise.all([page.waitForRequest(r=>r.url().includes('/model-change?')),
     page.locator('#owgModelChangeCoverage').selectOption('0.9').then(()=>page.locator('#owgModelChangeRun').click())]);
   assert(changeRequest.url().includes('activities=0.9'),changeRequest.url());
   await page.locator('#owgModelChangeStatus').filter({hasText:'Split at'}).waitFor();
   await page.locator('#owgModelChangeCoverage').selectOption('1');
   await page.locator('#owgModelChangeSplit').fill('');await page.locator('#owgModelChangeRun').click();
   await page.locator('#owgModelChangeStatus').filter({hasText:'(median case start)'}).waitFor();
   // Two groups by the region column (event attribute): EU against US.
   await page.locator('#owgModelChangeMode').selectOption('groups');
   await page.locator('#owgModelChangeCohortAAttribute').fill('region=EU');await page.locator('#owgModelChangeCohortBAttribute').fill('region=US');
   await page.locator('#owgModelChangeRun').click();
   await page.locator('#owgModelChangeStatus').filter({hasText:'region=EU (20 cases) against region=US (20)'}).waitFor();
   const groups=await page.locator('#owgModelChangeResults').textContent();
   assert(groups.includes('Only in region=US: Notify')&&groups.includes('Orderings that differ'),groups);
   await box.getByRole('button',{name:'Review cases from region=US against the region=EU model',exact:true}).click();
   await page.locator('#owgEventLogCasesStatus').getByText(/20 matching cases/).waitFor();
   assert.equal(await page.locator('#owgEventLogCasesList').getByRole('button',{name:/Open case a/}).count(),0);
   await box.getByText('Non-fitting variants · region=US cases on the region=EU model',{exact:true}).click();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgModelChange'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Model change browser acceptance passed: given split with new activity, changed orderings and lower cross-fit, fewer activities shown, median split, two regions compared, non-fitting variant evidence, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
