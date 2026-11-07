// Real Chromium acceptance for local backup safety review (M7).
// No restore, worker, source connector, or production credentials are touched.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-backup-preflight-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const base='http://127.0.0.1:8787';
  const server=spawn(python,['-m','uvicorn','privacy_app:app','--app-dir','tests/browser',
      '--host','127.0.0.1','--port','8787'],{
    env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},
    stdio:['ignore','pipe','pipe']
  });
  let logs='',browser,spawnError;
  server.on('error',e=>{spawnError=e;});
  for(const stream of [server.stdout,server.stderr])
    stream.on('data',chunk=>{logs=(logs+chunk).slice(-10000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Privacy app exited: '+logs);
      try{
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert(ready,'Browser fixture not ready: '+logs);
    browser=await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?
        {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),
      headless:true,args:['--no-sandbox']
    });
    const page=await browser.newPage(),errors=[],preflightRequests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{
      if(request.url().includes('/v1/local-backups/preflight'))
        preflightRequests.push(request.postData());
    });
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+session);
    await page.locator('#tab-privacy').click();
    const card=page.locator('#pvBackup');
    await card.waitFor();
    await page.locator('#pvBackupChoice').waitFor();
    await page.locator('#pvBackupRefresh').click();
    await page.locator('#pvBackupPlan').getByText(/No backups found/).waitFor();

    // A normal backup becomes visible without any custom refresh of the page.
    await page.locator('#pvBackupCreate').click();
    await page.locator('#pvBackupStatus').filter({hasText:/Saved \d+ files/}).waitFor();
    await page.locator('#pvBackupChoice option[value^="owg-backup-"]').first().waitFor();
    const normal=await page.locator('#pvBackupChoice option[value^="owg-backup-"]').first().getAttribute('value');
    await page.locator('#pvBackupChoice').selectOption(normal);
    assert.equal(await page.locator('#pvBackupInspect').isEnabled(),true);
    await page.locator('#pvBackupInspect').click();
    await page.locator('#pvBackupPlan').filter({hasText:/Verified \d+ archived files/}).waitFor();
    const status=await page.locator('#pvBackupPlan').textContent();
    assert.match(status,/No files were changed/);
    assert.match(status,/offline restore/i);
    assert.match(status,/run the CLI preflight again while closed/);
    assert.equal(preflightRequests.length,1);
    assert.deepEqual(JSON.parse(preflightRequests[0]),{name:normal});
    const preRestore=await fs.readdir(scratch);
    assert(!preRestore.some(name=>name.includes('.pre-restore-')||name.includes('.failed-restore-')));

    // An encrypted backup appears, but the browser never submits a passphrase
    // or attempts online verification/restoration.
    const secret='owned synthetic encrypted backup passphrase';
    await page.locator('#pvBackupPass').fill(secret);
    await page.locator('#pvBackupCreate').click();
    await page.locator('#pvBackupStatus').filter({hasText:/encrypted, includes keys/}).waitFor();
    assert.equal(await page.locator('#pvBackupPass').inputValue(),'');
    await page.locator('#pvBackupChoice option').filter({hasText:/encrypted — offline CLI/}).first().waitFor();
    const encrypted=await page.locator('#pvBackupChoice option').filter({hasText:/encrypted — offline CLI/}).first().getAttribute('value');
    await page.locator('#pvBackupChoice').selectOption(encrypted);
    await page.locator('#pvBackupInspect').click();
    await page.locator('#pvBackupPlan').filter({hasText:/Encrypted backup: close OpenWorkGraph/}).waitFor();
    assert.equal(preflightRequests.length,1,'encrypted inspection never requests a passphrase');
    assert(!String(await page.locator('#pvBackupPlan').textContent()).includes(secret));

    // Refresh must preserve a valid selection, not replay an old plan.
    await page.locator('#pvBackupRefresh').click();
    assert.equal(await page.locator('#pvBackupChoice').inputValue(),encrypted);
    const after=await page.locator('#pvBackupPlan').textContent();
    assert.match(after,/Choose a backup and inspect/);
    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#pvBackup'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
    }));
    assert.deepEqual(audit.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),
      'no horizontal page overflow at mobile width');
    assert.deepEqual(errors,[]);
    console.log('Local backup preflight browser passed: create/list, selected verified plan, encrypted CLI-only, no restoration, stale-plan reset, accessibility and mobile.');
  }finally{
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>server.once('exit',resolve)),
      new Promise(resolve=>setTimeout(resolve,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
