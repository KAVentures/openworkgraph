// R10 real Chromium acceptance: explicit quiet hours for an existing local job.
// Synthetic owner session and isolated files; no worker/source calls or cloud.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-quiet-hours-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const base='http://127.0.0.1:8792';
  const server=spawn(python,['-m','uvicorn','scheduled_local_backup_app:app',
    '--app-dir','tests/browser','--host','127.0.0.1','--port','8792'],
    {env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',e=>{spawnError=e;});
  for(const stream of [server.stdout,server.stderr])
    stream.on('data',chunk=>{logs=(logs+chunk).slice(-10000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Server exited: '+logs);
      try {
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(r=>setTimeout(r,200));
    }
    assert(ready,'Fixture startup failed: '+logs);
    browser=await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),
      headless:true,args:['--no-sandbox']
    });
    const page=await browser.newPage();
    const errors=[],writes=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{
      if(request.method()==='POST'&&request.url().includes('/v1/schedules')) {
        writes.push({url:request.url(),body:JSON.parse(request.postData())});
      }
    });
    const token=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+token);
    await page.locator('#tab-privacy').click();
    await page.locator('#pvSchedules').waitFor();
    assert.equal(await page.locator('#pvScheduleQuietEnabled').isChecked(),false);
    assert.equal(await page.locator('#pvScheduleQuietFields').isHidden(),true);
    await page.locator('#pvScheduleKind').selectOption('local_backup');
    await page.locator('#pvScheduleBackupAck').check();
    await page.locator('#pvScheduleQuietEnabled').check();
    assert.equal(await page.locator('#pvScheduleQuietFields').isVisible(),true);
    await page.locator('#pvScheduleQuietTimezone').fill('Europe/Stockholm');
    await page.locator('#pvScheduleQuietStart').fill('22:00');
    await page.locator('#pvScheduleQuietEnd').fill('07:00');

    // Local validation rejects an impossible full-day quiet window.
    await page.locator('#pvScheduleQuietEnd').fill('22:00');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvScheduleAdd').click();
    await page.locator('#pvScheduleStatus').filter({hasText:/two different valid 24-hour quiet-hour times/}).waitFor();
    assert.equal(writes.length,0);

    await page.locator('#pvScheduleQuietEnd').fill('07:00');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvScheduleAdd').click();
    await page.locator('#pvScheduleStatus').filter({hasText:'Schedule added.'}).waitFor();
    await page.locator('#pvScheduleList').getByText(/quiet 22:00–07:00 Europe\/Stockholm/).waitFor();
    assert.equal(writes.length,1);
    assert.deepEqual(writes[0].body.quiet_hours,{
      timezone:'Europe/Stockholm',start:'22:00',end:'07:00'
    });
    assert.equal(writes[0].body.confirm_unencrypted_local_backup,true);
    assert.equal(writes[0].body.interval_minutes,1440);
    assert.equal((await fs.readdir(scratch)).includes('backups'),false,
      'Creating a schedule never creates a backup eagerly');

    const rows=page.locator('#pvScheduleList .pv-row');
    assert.equal(await rows.count(),1);
    await page.locator('#pvScheduleQuietStart').fill('21:30');
    await page.locator('#pvScheduleQuietEnd').fill('06:30');
    await rows.first().getByRole('button',{name:/Update quiet hours/}).click();
    await page.locator('#pvScheduleStatus').filter({hasText:/Quiet hours saved/}).waitFor();
    await rows.first().getByText(/quiet 21:30–06:30 Europe\/Stockholm/).waitFor();
    assert.equal(writes.length,2);
    assert.deepEqual(writes[1].body.quiet_hours,{
      timezone:'Europe/Stockholm',start:'21:30',end:'06:30'
    });

    // Unchecking the creation field alone cannot silently clear an existing job.
    await page.locator('#pvScheduleQuietEnabled').uncheck();
    await rows.first().getByRole('button',{name:/Update quiet hours/}).click();
    await page.locator('#pvScheduleStatus').filter({hasText:/Tick the optional quiet-hours checkbox/}).waitFor();
    assert.equal(writes.length,2);
    await rows.first().getByRole('button',{name:/Clear quiet hours/}).click();
    await page.locator('#pvScheduleStatus').filter({hasText:'Quiet hours cleared'}).waitFor();
    await rows.first().getByRole('button',{name:/Apply quiet hours/}).waitFor();
    assert.equal(writes.length,3);
    assert.equal(writes[2].body.quiet_hours,null);
    assert.doesNotMatch(await rows.first().textContent(),/quiet \d\d:/);
    await rows.first().getByRole('button',{name:'Pause'}).click();
    await rows.first().getByRole('button',{name:'Resume'}).waitFor();
    await rows.first().getByRole('button',{name:'Remove'}).click();
    await rows.first().waitFor({state:'detached'});
    assert.equal((await fs.readdir(scratch)).includes('backups'),false);

    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#pvSchedules'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
    }));
    assert.deepEqual(audit.violations.map(x=>x.id),[]);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),
      'Privacy controls must fit a mobile viewport');
    assert.deepEqual(errors,[]);
    console.log('R10 quiet hours browser passed: opt-in, invalid-window refusal, exact IANA config, update/clear, no eager job, pause/delete, keyboard labels, WCAG/mobile.');
  }finally{
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await Promise.race([
      new Promise(r=>server.once('exit',r)),
      new Promise(r=>setTimeout(r,5000))
    ]);
    if(server.exitCode===null)server.kill('SIGKILL');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
