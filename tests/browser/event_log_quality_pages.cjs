const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-quality-pages-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','quality_pages_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  let csv='case,activity,time\nvalid,Start,2024-01-01T00:00:00Z\n';
  for(let i=1;i<=25;i++)csv+='reject'+i+',Review,2024-01-01 10:00:00\n';
  await page.locator('#owgEventLogFile').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();
  await page.locator('#owgEventLogCase').selectOption('case');await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');
  await page.locator('#owgEventLogKeepRejected').check();await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCaseType')?.value);
  const logId=await page.locator('#owgEventLogPick').inputValue();
  const owner={'Authorization':'OWG-Session '+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim()};
  const seeded=await page.request.post(base+'/test-seed-quality-batches/'+encodeURIComponent(logId),{headers:owner});assert.equal(seeded.status(),200,await seeded.text());
  await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgQualityTrendsRun').waitFor();
  const [response]=await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/quality/batch-history/page')),
    page.locator('#owgQualityTrendsRun').click()
  ]);
  assert.equal(response.status(),200);const data=await response.json();
  assert.equal(data.reports.length,100);assert.equal(data.window.total_batches,205);
  await page.locator('#owgQualityTrendsStatus').filter({hasText:'Showing 100 of 205 batches'}).waitFor();
  const rows=page.locator('#owgQualityTrendsRows');assert.ok(await rows.getByText('Append 105',{exact:true}).count());
  assert.equal(await rows.getByText('Append 104',{exact:true}).count(),0);
  await page.locator('#owgQualityOlderBatches').click();
  await rows.getByText('Append 5',{exact:true}).first().waitFor();
  await page.locator('#owgQualityOlderBatches').click();
  await page.locator('#owgQualityTrendsStatus').filter({hasText:'Showing 5 of 205 batches'}).waitFor();
  assert.equal(await page.locator('#owgQualityOlderBatches').isDisabled(),true);
  await page.locator('#owgQualityNewestBatches').click();
  await page.locator('#owgQualityTrendsStatus').filter({hasText:'Showing 100 of 205 batches'}).waitFor();
  assert.equal(await page.locator('#owgQualityTrendsCsv').isDisabled(),false);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgQualityBatchTrends'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));assert.deepEqual(errors,[]);
  console.log('Batch quality pages browser passed: three pages, newest, CSV, axe/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
