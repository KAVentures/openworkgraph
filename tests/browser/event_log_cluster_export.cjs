const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-cluster-export-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',error=>{spawnError=error;});
 for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error('Server exited: '+logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }if(!ready)throw Error('Server not ready: '+logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();
  const rows=['case,activity,time'];
  for(let i=0;i<8;i++){const steps=i%2?['Open','Check','Close']:['Open','Reject','Archive'];
   steps.forEach((activity,k)=>rows.push(`c${i},${activity},2024-01-0${i+1}T0${k}:00:00Z`));}
  await page.locator('#owgEventLogFile').setInputFiles({name:'cluster.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  const box=page.locator('#owgEventLogClusters');await box.waitFor();
  await page.locator('#owgClusterK').selectOption('2');await page.locator('#owgClusterRun').click();
  const out=page.locator('#owgClusterResults'),review=page.locator('#owgClusterCaseReview');
  const [pageResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/trace-clusters/cases')),
    out.getByRole('button',{name:'Inspect cases in cluster 1'}).click()]);
  const selected=await pageResponse.json();assert.equal(selected.total,4);
  const [download]=await Promise.all([page.waitForEvent('download'),review.getByRole('button',{name:'Download cases in cluster 1 as CSV'}).click()]);
  const lines=(await fs.readFile(await download.path(),'utf8')).trim().split('\n');
  assert.equal(lines[0],'case_id,duration_seconds,events,first_activity,last_activity');
  assert.deepEqual(lines.slice(1).map(line=>line.split(',')[0]),selected.cases.map(item=>item.case_id));
  await review.getByRole('status').filter({hasText:'Downloaded 4 of 4 cases.'}).waitFor();
  await page.locator('#owgClusterK').selectOption('3');assert.equal(await out.textContent(),'');assert.equal(await review.textContent(),'');
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogClusters'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Trace cluster case export browser acceptance passed.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
