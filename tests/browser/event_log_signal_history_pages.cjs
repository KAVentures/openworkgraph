const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-signal-history-pages-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','signal_history_pages_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;for(let i=0;i<200;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'history-test-logs.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  const ids=JSON.parse(await fs.readFile(path.join(scratch,'history-test-logs.json'),'utf8'));
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());await page.locator('[role=tab][data-tab=evidence]').click();
  await page.waitForFunction(()=>document.querySelector('#owgEventLogPick')?.options.length===5);await page.locator('#owgEventLogPick').selectOption(ids[0]);
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCaseType')?.value);const [initial]=await Promise.all([page.waitForResponse(r=>r.url().includes('/signal-history?')&&r.status()===200),page.locator('#owgEventLogAnalyze').click()]);
  const first=await initial.json(),status=page.locator('#owgSignalTransitionStatus'),rows=page.locator('#owgSignalTransitions tbody tr');
  await status.filter({hasText:'205 saved transitions in this log; showing 100'}).waitFor();assert.equal(await rows.count(),100);
  const seen=new Set(first.items.map(x=>x.event_id));
  const [next]=await Promise.all([page.waitForResponse(r=>r.url().includes('/signal-history?')&&new URL(r.url()).searchParams.has('before')),page.locator('#owgSignalTransitionNext').click()]);
  const second=await next.json();await status.filter({hasText:'100 (older page)'}).waitFor();for(const item of second.items){assert.ok(!seen.has(item.event_id));seen.add(item.event_id);}
  const [lastResponse]=await Promise.all([page.waitForResponse(r=>r.url().includes('/signal-history?')&&new URL(r.url()).searchParams.get('before')===second.next_before),page.locator('#owgSignalTransitionNext').click()]);
  const last=await lastResponse.json();await status.filter({hasText:'showing 5 (older page)'}).waitFor();for(const item of last.items){assert.ok(!seen.has(item.event_id));seen.add(item.event_id);}assert.equal(seen.size,205);
  assert.equal(await page.locator('#owgSignalTransitionNext').isDisabled(),true);await page.locator('#owgSignalTransitionPrevious').click();await status.filter({hasText:'100 (older page)'}).waitFor();
  await page.locator('#owgSignalTransitionRefresh').click();await status.filter({hasText:'showing 100.'}).waitFor();assert.equal(await page.locator('#owgSignalTransitionPrevious').isDisabled(),true);
  let release,arrive,complete;const blocked=new Promise(r=>{release=r;}),arrived=new Promise(r=>{arrive=r;}),completed=new Promise(r=>{complete=r;});
  const pattern=/\/signal-history\?/;await page.route(pattern,async route=>{if(new URL(route.request().url()).searchParams.has('before')){arrive();await blocked;await route.continue();complete();}else await route.continue();});
  await page.locator('#owgSignalTransitionNext').click();await arrived;await page.locator('#owgEventLogPick').selectOption(ids[1]);await page.waitForFunction(()=>document.querySelector('#owgEventLogCaseType')?.value);
  await page.locator('#owgEventLogAnalyze').click();await page.locator('#owgSignalTransitionStatus').filter({hasText:'0 saved transitions in this log'}).waitFor();release();await completed;await page.unroute(pattern);await page.waitForTimeout(200);
  assert.equal(await page.locator('#owgSignalTransitions tbody tr').count(),0);assert.match(await page.locator('#owgSignalTransitionStatus').textContent(),/0 saved transitions/);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogSignals'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));assert.deepEqual(errors,[]);
  console.log('Signal history browser passed: 205 tied events, navigation, refresh, log-switch stale response, axe/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
