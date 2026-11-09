// Chromium acceptance: daily scheduled local backup opt-in, pause/resume/remove.
// Only synthetic local files; no worker, live recorder, source connection or upload.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-scheduled-backup-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const base='http://127.0.0.1:8791';
  const server=spawn(python,['-m','uvicorn','scheduled_local_backup_app:app',
    '--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],
    {env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',e=>{spawnError=e;});
  for(const stream of [server.stdout,server.stderr])
    stream.on('data',chunk=>{logs=(logs+chunk).slice(-9000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Fixture exited: '+logs);
      try{
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert(ready,'fixture not ready: '+logs);
    browser=await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),
      headless:true,args:['--no-sandbox']
    });
    const page=await browser.newPage();
    const errors=[],created=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{
      if(request.method()==='POST' && request.url().endsWith('/v1/schedules'))
        created.push(JSON.parse(request.postData()));
    });
    const token=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+token);
    await page.locator('#tab-privacy').click();
    const job=page.locator('#pvSchedules');
    await job.waitFor();
    assert.equal(await page.locator('#pvScheduleBackupAckRow').isHidden(),true);
    await page.locator('#pvScheduleKind').selectOption('local_backup');
    assert.equal(await page.locator('#pvScheduleBackupAckRow').isVisible(),true);
    assert.equal(await page.locator('#pvScheduleTarget').isDisabled(),true);
    assert.equal(await page.locator('#pvScheduleTarget').inputValue(),'local');
    assert.equal(await page.locator('#pvScheduleInterval').inputValue(),'1440');
    await page.locator('#pvScheduleAdd').click();
    await page.locator('#pvScheduleStatus').filter({hasText:/Acknowledge that scheduled backups are unencrypted/}).waitFor();
    assert.equal(created.length,0,'missing acknowledgement must never reach the API');

    await page.locator('#pvScheduleBackupAck').check();
    await page.locator('#pvScheduleInterval').fill('60');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvScheduleAdd').click();
    await page.locator('#pvScheduleStatus').filter({hasText:/at least 1440 minutes/}).waitFor();
    assert.equal(created.length,1);
    assert.equal(created[0].confirm_unencrypted_local_backup,true);
    assert.equal(created[0].target_id,'local');
    assert.equal(created[0].interval_minutes,60);
    assert.equal((await fs.readdir(scratch)).includes('backups'),false,
      'rejected schedule cannot create backup files');

    await page.locator('#pvScheduleInterval').fill('1440');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvScheduleAdd').click();
    await page.locator('#pvScheduleStatus').filter({hasText:'Schedule added.'}).waitFor();
    await page.locator('#pvScheduleList').getByText(/Unencrypted local backup/).waitFor();
    assert.equal(created.length,2);
    assert.equal(created[1].interval_minutes,1440);
    assert.equal(await page.locator('#pvScheduleBackupAck').isChecked(),false);
    assert.equal((await fs.readdir(scratch)).includes('backups'),false,
      'schedule creation must not create a snapshot until the due time');

    let lines=page.locator('#pvScheduleList .pv-row');
    assert.equal(await lines.count(),1);
    assert.match(await lines.first().textContent(),/every 1440 min/);
    await lines.first().getByRole('button',{name:'Pause'}).click();
    await lines.first().getByText(/paused/).waitFor();
    await lines.first().getByRole('button',{name:'Resume'}).click();
    await lines.first().getByRole('button',{name:'Pause'}).waitFor();
    await lines.first().getByRole('button',{name:'Remove'}).click();
    await page.locator('#pvScheduleList .pv-row').first().waitFor({state:'detached'});
    assert.equal((await fs.readdir(scratch)).includes('backups'),false);
    assert.deepEqual(errors,[]);

    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#pvSchedules'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
    }));
    assert.deepEqual(audit.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),
      'mobile width should not overflow');
    console.log('Scheduled backups Chromium passed: explicit acknowledgement, daily guard, no eager archive, pause/resume/remove, no network content, WCAG/mobile.');
  }finally{
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
