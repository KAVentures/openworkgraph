const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-object-map-edges-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let browser,logs='';for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-10000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const objects=[{id:'order1',type:'order'},{id:'item1',type:'item'},{id:'parcel1',type:'parcel'}],events=[];
  for(let i=0;i<41;i++)events.push({id:`event${String(i).padStart(2,'0')}`,type:`Step${String(i).padStart(2,'0')}`,time:new Date(Date.UTC(2024,0,1,0,i)).toISOString(),relationships:[{objectId:'order1',qualifier:''},{objectId:'item1',qualifier:''},...(i<2?[{objectId:'parcel1',qualifier:''}]:[])]});
  await page.locator('#owgEventLogFile').setInputFiles({name:'objects.jsonocel',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({objectTypes:[],eventTypes:[],objects,events}))});
  await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready.'}).waitFor();await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
  await page.locator('#owgEventLogCaseType').selectOption('order');await page.locator('#owgEventLogAnalyze').click();
  const shared=page.locator('#owgObjectMapSharedActivities');await shared.waitFor();assert.equal(await shared.locator('tbody tr').count(),15);
  await page.getByRole('button',{name:'Next shared activities'}).click();await shared.getByText('Step15',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Previous shared activities'}).click();await shared.getByText('Step00',{exact:true}).waitFor();
  const status=page.locator('#owgObjectMapTransitionStatus'),rows=page.locator('#owgObjectMapTransitions'),search=page.locator('#owgObjectMapTransitionSearch');
  await status.filter({hasText:'Showing 1–25 of 40 matching retained transitions'}).waitFor();assert.equal(await rows.locator('tbody tr').count(),25);
  await page.getByRole('button',{name:'Next transitions',exact:true}).click();await status.filter({hasText:'Showing 26–40 of 40'}).waitFor();assert.equal(await rows.locator('tbody tr').count(),15);
  await rows.getByRole('button',{name:'Inspect transition from "Step39" to "Step40"'}).click();
  await page.locator('#owgEventLogObjectSelection').getByRole('button',{name:'Browse cases for adjacent transition'}).click();
  await page.locator('#owgEventLogObjectCasesStatus').filter({hasText:'1 matching cases'}).waitFor();await page.locator('#owgEventLogObjectCasesList').getByRole('button',{name:'Open case order1',exact:true}).waitFor();
  await search.fill('step39');await status.filter({hasText:'Showing 1–2 of 2'}).waitFor();assert.equal(await rows.locator('tbody tr').count(),2);assert.equal(await page.getByRole('button',{name:'Previous transitions',exact:true}).isDisabled(),true);
  await search.fill('missing');await status.filter({hasText:'Showing 0 of 0'}).waitFor();await rows.getByText('No matching retained transitions.').waitFor();
  await page.locator('#owgEventLogObjectType').selectOption('parcel');assert.equal(await search.inputValue(),'');await status.filter({hasText:'Showing 1–1 of 1'}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogObjectMap'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));assert.deepEqual(errors,[]);
  console.log('Object map transition browser passed: paging, search, case evidence, type changes, accessibility.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
