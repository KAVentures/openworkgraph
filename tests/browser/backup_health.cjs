// Real Chromium acceptance of the explicit owner-only backup integrity inventory.
// All archives, credentials and work data are synthetic and contained in scratch.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-backup-health-'));
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
      if(server.exitCode!==null)throw Error('Privacy fixture exited: '+logs);
      try{
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert(ready,'Browser fixture not ready: '+logs);
    await fs.mkdir(path.join(scratch,'data'),{recursive:true});
    await fs.writeFile(path.join(scratch,'data','synthetic_work.txt'),'OWNED_SYNTHETIC_WORK_ACTIVITY');
    browser=await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),
      headless:true,args:['--no-sandbox']});
    const page=await browser.newPage(),errors=[],posted=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',request=>{
      if(request.url().endsWith('/v1/local-backups/health'))
        posted.push({method:request.method(),body:JSON.parse(request.postData())});
    });
    const token=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+token);
    await page.locator('#tab-privacy').click();
    await page.locator('#pvBackupHealthPanel').waitFor();
    assert.equal(await page.locator('#pvBackupHealthRun').isDisabled(),true);
    await page.locator('#pvBackupCreate').click();
    await page.locator('#pvBackupStatus').filter({hasText:/Saved \d+ files/}).waitFor();
    await page.locator('#pvBackupChoice option[value^="owg-backup-"]').first().waitFor({state:'attached'});
    const first=await page.locator('#pvBackupChoice option[value^="owg-backup-"]').first().getAttribute('value');
    assert.equal(await page.locator('#pvBackupHealthRun').isEnabled(),true);
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvBackupHealthRun').click();
    await page.locator('#pvBackupHealthStatus').filter({hasText:/1 of 1 listed backup archives verified/}).waitFor();
    assert.match(await page.locator('#pvBackupHealthRows').textContent(),/Verified intact at this check/);
    assert.equal(posted.length,1);
    assert.equal(posted[0].method,'POST');
    assert.deepEqual(posted[0].body,{names:[first],confirm_local_verification:true});
    assert.doesNotMatch(await page.locator('#pvBackupHealthPanel').textContent(),/OWNED_SYNTHETIC_WORK_ACTIVITY/);

    const secret='synthetic encrypted browser backup phrase';
    await page.locator('#pvBackupPass').fill(secret);
    await page.locator('#pvBackupCreate').click();
    await page.locator('#pvBackupStatus').filter({hasText:/encrypted, includes keys/}).waitFor();
    await page.locator('#pvBackupChoice option').filter({hasText:/encrypted — offline CLI/}).first().waitFor({state:'attached'});
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvBackupHealthRun').click();
    await page.locator('#pvBackupHealthStatus').filter({hasText:/1 of 2 listed backup archives verified/}).waitFor();
    assert.match(await page.locator('#pvBackupHealthRows').textContent(),/Encrypted; verify using offline CLI/);
    assert(!String(await page.locator('#pvBackupHealthRows').textContent()).includes(secret));
    assert.equal(posted.length,2);
    assert.equal(Object.keys(posted[1].body).includes('passphrase'),false);

    // Produce a valid ZIP structure with a tampered payload while retaining
    // its original manifest hashes; the server must not mark it verified.
    const folder=path.join(scratch,'owg-backups');
    const bad='owg-backup-tampered.zip';
    const forge=String.raw`
import sys,zipfile
src,dst=sys.argv[1:3]
with zipfile.ZipFile(src) as old, zipfile.ZipFile(dst,'w',zipfile.ZIP_DEFLATED) as new:
    for info in old.infolist():
        payload=old.read(info.filename)
        if info.filename.startswith('data/') and not info.is_dir():
            payload=b'TAMPERED_SYNTHETIC_BYTES'
        new.writestr(info,payload)
`;
    execFileSync(python,['-c',forge,path.join(folder,first),path.join(folder,bad)]);
    await page.locator('#pvBackupRefresh').click();
    await page.locator('#pvBackupChoice option[value="owg-backup-tampered.zip"]').waitFor({state:'attached'});
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvBackupHealthRun').click();
    await page.locator('#pvBackupHealthStatus').filter({hasText:/1 of 3 listed backup archives verified/}).waitFor();
    assert.match(await page.locator('#pvBackupHealthRows').textContent(),/Integrity check failed/);
    assert.doesNotMatch(await page.locator('#pvBackupHealthPanel').textContent(),/TAMPERED_SYNTHETIC_BYTES/);
    assert.equal(posted.length,3);
    assert.equal(posted[2].body.names.length,3);
    // Refresh invalidates in-flight verification, rather than re-displaying an
    // old green result for a changed archive after the list was refreshed.
    await page.route('**/v1/local-backups/health',async route=>{
      await new Promise(resolve=>setTimeout(resolve,350));
      await route.continue();
    });
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#pvBackupHealthRun').click();
    await page.locator('#pvBackupHealthStatus').filter({hasText:/Verifying local archive/}).waitFor();
    await page.locator('#pvBackupRefresh').click();
    await page.locator('#pvBackupHealthStatus').filter({hasText:/Backup listing refreshed/}).waitFor();
    await page.waitForTimeout(600);
    assert.equal(await page.locator('#pvBackupHealthRows').textContent(),'');
    assert.match(await page.locator('#pvBackupHealthStatus').textContent(),/Backup listing refreshed/);
    assert(!String(await page.locator('#pvBackupHealthPanel').textContent()).includes(secret));
    assert(!String(await page.locator('#pvBackupHealthPanel').textContent()).includes('TAMPERED_SYNTHETIC_BYTES'));
    assert(!(await fs.readdir(scratch)).some(name=>name.includes('.pre-restore-')||name.includes('.failed-restore-')));
    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#pvBackup'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}));
    assert.deepEqual(audit.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),
      'Backup inspection must not overflow the mobile viewport');
    assert.deepEqual(errors,[]);
    console.log('M7 backup health browser passed: owner-only click, full ZIP integrity, encrypted offline-only, synthetic corruption, no restore/passphrase transport, stale-result suppression, WCAG/mobile.');
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
})().catch(e=>{console.error(e);process.exit(1);});
