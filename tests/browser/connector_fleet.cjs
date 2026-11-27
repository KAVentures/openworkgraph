/* Actual Gateway owner fleet controls, using the existing synthetic server. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const os=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'owg-fleet-browser-'));
 const port=process.env.OWG_BROWSER_TEST_PORT||'8793',base='http://127.0.0.1:'+port;
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','connector_crosswalks_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port',port],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',err,browser;server.on('error',e=>err=e);
 for(const stream of [server.stdout,server.stderr])stream.on('data',x=>logs=(logs+x).slice(-8000));
 try{
  let ready=false;
  for(let i=0;i<100;i++){if(err)throw err;if(server.exitCode!==null)throw Error(logs);try{if((await fetch(base+'/admin/connector-jobs')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
  assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const context=await browser.newContext({acceptDownloads:true});
  await context.addInitScript(()=>{sessionStorage.setItem('owg_admin_token','test-admin');sessionStorage.setItem('owg_admin_org','customer');});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/admin/connector-jobs');
  await page.locator('#fleetPanel').waitFor({state:'visible'});
  assert.equal(await page.locator('#fleetRows article').count(),2);
  assert.ok(await page.locator('#fleetPreview').isDisabled());
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#fleetDownload').click()]);
  const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(report.summary.jobs,2);assert.equal(report.summary.reported_published_event_count,2);
  assert.equal(report.event_payloads_read,false);
  assert.ok(!JSON.stringify(report).includes('profile_sha256'));
  await page.locator('#fleetProvider').selectOption('rest');
  await page.locator('#fleetSelect').click();
  await page.locator('#fleetPreview').click();
  await page.locator('#fleetPlan').getByText('Eligible jobs',{exact:true}).waitFor();
  assert.ok(await page.locator('#fleetApply').isDisabled());
  await page.locator('#fleetConfirm').check();
  await page.locator('#fleetApply').click();
  await page.locator('#fleetStatus').filter({hasText:'2 jobs changed atomically'}).waitFor();
  await page.locator('#fleetRows').getByText('paused',{exact:true}).nth(1).waitFor();
  await page.locator('#fleetAction').selectOption('resume');
  await page.locator('#fleetSelect').click();
  await page.locator('#fleetPreview').click();
  await page.locator('#fleetPlan').getByText('Eligible jobs',{exact:true}).waitFor();
  await page.locator('#fleetConfirm').check();
  // Selection changes invalidate confirmation.
  await page.locator('#fleetRows input').first().uncheck();
  assert.ok(await page.locator('#fleetApply').isDisabled());
  assert.equal(await page.locator('#fleetConfirm').isChecked(),false);
  await page.locator('#fleetSelect').click();
  await page.locator('#fleetPreview').click();
  await page.locator('#fleetPlan').getByText('Eligible jobs',{exact:true}).waitFor();
  await page.locator('#fleetConfirm').check();
  await page.locator('#fleetApply').click();
  await page.locator('#fleetRows').getByText('due',{exact:true}).nth(1).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('Fleet browser acceptance: report, explicit pause/resume, stale consent invalidation, mobile layout.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(resolve=>{if(server.exitCode!==null||err)resolve();else server.once('exit',resolve);});await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
