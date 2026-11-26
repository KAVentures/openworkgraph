const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-signal-pages-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','signal_overview_pages_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;for(let i=0;i<200;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());await page.locator('[role=tab][data-tab=evidence]').click();
  await page.waitForFunction(()=>document.querySelector('#owgEventLogPick')?.options.length===5&&document.querySelector('#owgEventLogCaseType')?.value);
  await page.locator('#owgEventLogAnalyze').click();
  const status=page.locator('#owgSignalAllStatus'),rows=page.locator('#owgSignalAllResults tbody tr');
  await status.filter({hasText:'Showing 1–100 of 205 matching rules'}).waitFor();assert.equal(await rows.count(),100);
  const names=new Set(await rows.locator('td:nth-child(2)').allTextContents());
  await page.locator('#owgSignalAllNext').click();await status.filter({hasText:'Showing 101–200 of 205 matching rules'}).waitFor();
  for(const n of await rows.locator('td:nth-child(2)').allTextContents()){assert.ok(!names.has(n));names.add(n);}
  const [lastResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('signal-overview?')&&new URL(r.url()).searchParams.get('offset')==='200'),page.locator('#owgSignalAllNext').click()]);
  assert.match(new URL(lastResponse.url()).searchParams.get('expected_fingerprint'),/^[a-f0-9]{64}$/);
  const last=await lastResponse.json();await status.filter({hasText:'Showing 201–205 of 205 matching rules'}).waitFor();assert.equal(await rows.count(),5);
  for(const n of await rows.locator('td:nth-child(2)').allTextContents()){assert.ok(!names.has(n));names.add(n);}assert.equal(names.size,205);
  assert.equal(await page.locator('#owgSignalAllNext').isDisabled(),true);assert.equal(await page.locator('#owgSignalAllPrevious').isDisabled(),false);
  await page.locator('#owgSignalAllPrevious').click();await status.filter({hasText:'Showing 101–200 of 205 matching rules'}).waitFor();
  await page.locator('#owgSignalAllFilter').selectOption('cleared');await status.filter({hasText:'Showing 0 of 0 matching rules'}).waitFor();
  assert.equal(await page.locator('#owgSignalAllNext').isDisabled(),true);assert.equal(await page.locator('#owgSignalAllPrevious').isDisabled(),true);
  await page.locator('#owgSignalAllFilter').selectOption('attention');await status.filter({hasText:'Showing 1–100 of 205 matching rules'}).waitFor();
  let release,arrive,complete;const blocked=new Promise(r=>{release=r;}),arrived=new Promise(r=>{arrive=r;}),completed=new Promise(r=>{complete=r;});
  const pattern=/\/signal-overview\?/;await page.route(pattern,async route=>{if(new URL(route.request().url()).searchParams.get('offset')==='100'){arrive();await blocked;await route.continue();complete();}else await route.continue();});
  await page.locator('#owgSignalAllNext').click();await arrived;await page.locator('#owgSignalAllFilter').selectOption('cleared');await status.filter({hasText:'Showing 0 of 0 matching rules'}).waitFor();release();await completed;await page.unroute(pattern);
  await page.waitForTimeout(200);assert.equal(await rows.count(),0);assert.match(await status.textContent(),/Showing 0 of 0 matching rules/);
  await page.locator('#owgSignalAllFilter').selectOption('attention');await status.filter({hasText:'Showing 1–100 of 205 matching rules'}).waitFor();
  await page.locator('#owgSignalAllNext').click();await status.filter({hasText:'Showing 101–200 of 205 matching rules'}).waitFor();
  await page.locator('#owgSignalAllNext').click();await status.filter({hasText:'Showing 201–205 of 205 matching rules'}).waitFor();
  const target=last.items[0],oldPanel=await page.locator('#owgEventLogSignals').elementHandle();await rows.first().getByRole('button',{name:'Open dataset '+target.log_name+' to review signal '+target.name,exact:true}).click();
  await page.waitForFunction(id=>document.querySelector('#owgEventLogPick')?.value===id,target.log_id);
  await page.waitForFunction(node=>!node.isConnected,oldPanel);
  await page.locator('#owgSignalInbox').waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogSignals'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));assert.deepEqual(errors,[]);
  console.log('Signal overview pages browser passed: 205 signals, navigation, stale response, deep link, axe/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
