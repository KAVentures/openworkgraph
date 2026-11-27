const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {createHash}=require('node:crypto');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: score the discovered model at several noise levels.
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
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
   const box=page.locator('#owgModelQuality');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgModelQualityRun').click();
   await page.locator('#owgModelQualityStatus').filter({hasText:'Scored 5 models on 40 cases.'}).waitFor();
   const status=await page.locator('#owgModelQualityStatus').textContent();
   assert(status.includes('The best balance of fitness and precision is at noise'),status);
   const scored=page.locator('#owgModelQualityResults tbody tr');
   assert.equal(await scored.count(),5);
   const first=await scored.first().locator('td').allTextContents();
   assert.equal(first[0].startsWith('0%'),true,first.join('|'));assert.equal(first[1],'100%',first.join('|'));
   assert.equal((await page.locator('#owgModelQualityResults tbody').textContent()).split('(best F-score)').length,2);
   const [netFile]=await Promise.all([page.waitForEvent('download'),box.getByRole('button',{name:'Download scored net at 0% noise as PNML',exact:true}).click()]);
   const pnml=await fs.readFile(await netFile.path(),'utf8');
   const [scores]=await Promise.all([page.waitForEvent('download'),page.locator('#owgModelQualityExportJson').click()]);
   const report=JSON.parse(await fs.readFile(await scores.path(),'utf8'));
   assert.equal(report.levels[0].pnml_sha256,createHash('sha256').update(pnml).digest('hex'));
   assert.equal(report.levels[0].pnml,undefined);
   const [replay]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/petri-net-replay')),
     box.getByRole('button',{name:'Replay scored net at 0% noise',exact:true}).click()]);
   assert.equal(replay.status(),200,await replay.text());assert.equal(replay.request().postData(),pnml);
   await page.locator('#owgPetriStatus').filter({hasText:'Fitness 100% on scored net at 0% noise'}).waitFor();
   const [sheet]=await Promise.all([page.waitForEvent('download'),page.locator('#owgModelQualityExportCsv').click()]);
   assert.match(await fs.readFile(await sheet.path(),'utf8'),new RegExp(report.levels[0].pnml_sha256));
   await page.locator('#owgModelQualityLevels').selectOption('0,0.05,0.1,0.15,0.2');await page.locator('#owgModelQualityRun').click();
   await page.locator('#owgModelQualityStatus').filter({hasText:'Scored 5 models'}).waitFor();
   assert((await scored.nth(1).locator('td').first().textContent()).startsWith('5%'));
   await page.locator('#owgModelQualityCoverage').selectOption('0.75');assert.equal(await scored.count(),0);
   const [partial]=await Promise.all([page.waitForResponse(r=>r.url().includes('/model-quality?')),page.locator('#owgModelQualityRun').click()]);
   assert.equal(partial.status(),200,await partial.text());assert.equal((await partial.json()).activities_share,0.75);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgModelQuality'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Model quality browser acceptance passed: five noise levels scored, noise 0 fits fully, one best F-score marked, finer levels, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
