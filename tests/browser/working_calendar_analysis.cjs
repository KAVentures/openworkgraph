const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-working-calendar-browser-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();


  const csv='case_id,activity,time,finished,resource\nCASE_PRIVATE,Receive,2024-01-05T15:00:00Z,2024-01-05T16:00:00Z,PERSON_PRIVATE\nCASE_PRIVATE,Review,2024-01-08T10:00:00Z,2024-01-08T11:00:00Z,PERSON_PRIVATE\n';
  await page.locator('#owgEventLogFile').setInputFiles({name:'FILE_PRIVATE.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
  await page.locator('#owgEventLogDelimiter').selectOption(',');await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===5);
  await page.locator('#owgEventLogCase').selectOption('case_id');await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogTime').selectOption('time');await page.locator('#owgEventLogResource').selectOption('resource');await page.locator('#owgEventLogAttributes').selectOption(['finished']);
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();await page.locator('#owgEventLogStatus').filter({hasText:'Imported 2 events'}).waitFor();await page.locator('#owgEventLogAnalyze').click();await page.locator('#owgWaitingEnd').fill('finished');
  const output=page.locator('#owgWaitingResults'),toggle=page.getByLabel('Add a declared working-calendar lens',{exact:true}),exportButton=page.locator('#owgWaitingCalendarExport');
  assert.equal(await toggle.isChecked(),false);
  const elapsedResponse=page.waitForResponse(r=>r.url().includes('/waiting-time?')&&r.request().method()==='GET');await page.locator('#owgWaitingRun').click();
  const original=await(await elapsedResponse).json();assert.equal(original.working_calendar,undefined);assert.equal(original.activities.find(a=>a.activity==='Review').waiting_before.total_seconds,237600);
  await toggle.check();assert.equal(await output.textContent(),'');assert.equal(await exportButton.isDisabled(),true);
  await page.locator('#owgWaitingRun').click();await output.getByRole('heading',{name:'Declared working-calendar lens',exact:true}).waitFor();
  assert.match(await output.textContent(),/Calendar flow efficiency: 50(?:\.0)?%/);
  const artifact=async()=>{const [download]=await Promise.all([page.waitForEvent('download'),exportButton.click()]);return JSON.parse(await fs.readFile(await download.path(),'utf8'));};
  const first=await artifact();assert.equal(first.working_calendar.flow_efficiency.median,.5);assert.equal(first.working_calendar.activities.find(a=>a.activity==='Review').waiting_before.total_seconds,7200);
  for(const key of ['raw_events_exported','raw_case_ids_exported','resource_names_exported','execution_authorized'])assert.equal(first[key],false);
  assert.ok(!JSON.stringify(first).includes('PRIVATE'));assert.equal(first.working_calendar.actual_working_hours_verified,false);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgWaiting'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.setViewportSize({width:1280,height:900});
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/waiting-time*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#owgWaitingRun').click();await held;await page.locator('#owgWaitingCalendarHolidays').fill('2024-01-08');assert.equal(await output.textContent(),'');assert.equal(await exportButton.isDisabled(),true);
  await page.locator('#owgWaitingRun').click();await output.getByRole('heading',{name:'Declared working-calendar lens',exact:true}).waitFor();
  let latest=await artifact();assert.deepEqual(latest.working_calendar.calendar.holidays,['2024-01-08']);assert.equal(latest.working_calendar.instances_crossing_closed_time,1);
  assert.notEqual(latest.working_calendar.calendar_sha256,first.working_calendar.calendar_sha256);assert.notEqual(latest.working_calendar.utc_opening_windows_sha256,first.working_calendar.utc_opening_windows_sha256);
  const old=page.waitForResponse(r=>r.url().includes('/waiting-time')&&r.request().method()==='POST'&&!JSON.parse(r.request().postData()).working_calendar.holidays);release();await old;await page.waitForLoadState('networkidle');
  latest=await artifact();assert.deepEqual(latest.working_calendar.calendar.holidays,['2024-01-08']);
  const workingDays=page.getByLabel('Calendar working days',{exact:true});await workingDays.selectOption([]);await page.locator('#owgWaitingRun').click();await page.locator('#owgWaitingStatus').filter({hasText:'Choose at least one calendar working day.'}).waitFor();assert.equal(await exportButton.isDisabled(),true);
  await workingDays.selectOption(['mon','sun']);await page.locator('#owgWaitingRun').click();await output.getByRole('heading',{name:'Declared working-calendar lens',exact:true}).waitFor();
  latest=await artifact();assert.deepEqual(Object.keys(latest.working_calendar.calendar.hours).sort(),['mon','sun']);assert.equal(latest.working_calendar.flow_efficiency.elapsed_seconds,28800);assert.equal(latest.working_calendar.flow_efficiency.median,0);assert.equal(latest.mode_used,'end_attribute');
  await toggle.uncheck();assert.equal(await output.textContent(),'');assert.equal(await exportButton.isDisabled(),true);await page.locator('#owgWaitingRun').click();await page.locator('#owgWaitingStatus').filter({hasText:'2 of 2 activity instances'}).waitFor();assert.equal(await output.getByRole('heading',{name:'Declared working-calendar lens',exact:true}).count(),0);
  assert.deepEqual(errors,[]);console.log('Working calendar browser passed: explicit opt-in, retained elapsed results, schedule/holiday clock, pinned aggregate-only local downloads without cases/resources/file names, held-old-calendar guard, default GET, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
