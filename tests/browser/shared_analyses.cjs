const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-shared-analyses-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','shared_analyses_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const p of [server.stdout,server.stderr])p.on('data',c=>logs=(logs+c).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw new Error(logs);try{if((await fetch(base+'/analyses')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  const file=path.join(scratch,'snapshot.json'),snapshot=JSON.parse(await fs.readFile(file,'utf8'));
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const owner=await browser.newPage({acceptDownloads:true}),viewer=await browser.newPage({acceptDownloads:true}),errors=[];
  for(const p of [owner,viewer])p.on('pageerror',e=>errors.push(e.message));
  async function connect(p,who){await p.goto(base+'/analyses');await p.locator('#token').fill(who);await p.locator('#connect').click();await p.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();}
  await connect(owner,'alice');await connect(viewer,'bob');
  let uploads=0;owner.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/v1/human/analyses'))uploads++;});
  await owner.locator('#file').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...snapshot,raw_events:['private']}))});
  await owner.locator('#status').filter({hasText:'cannot be uploaded'}).waitFor();assert.equal(uploads,0);assert.equal(await owner.locator('#publish').isDisabled(),true);
  await owner.locator('#file').setInputFiles(file);await owner.locator('#preview table').waitFor();
  assert.equal(await owner.locator('#publish').isDisabled(),true);await owner.locator('#title').fill('Aggregate case counts');await owner.locator('#confirm').check();
  const [madeResponse]=await Promise.all([owner.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/v1/human/analyses')),owner.locator('#publish').click()]);
  const made=await madeResponse.json();assert.equal(madeResponse.status(),200);const wid=made.view_id;
  await owner.locator('#status').filter({hasText:'Snapshot published.'}).waitFor();assert.equal(made.content_sha256.length,64);
  assert.equal(await viewer.evaluate(async({base,wid})=>(await fetch(base+'/v1/human/analyses/'+wid,{headers:{Authorization:'Bearer bob'}})).status,{base,wid}),404);
  await owner.locator('#addMember').click();await owner.locator('#members input').fill('🦉'.repeat(512));assert.equal(await owner.locator('#members input').inputValue(),'🦉'.repeat(512));await owner.locator('#members input').fill('bob');await owner.locator('#accessConfirm').check();await owner.locator('#saveAccess').click();
  await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory button').filter({hasText:'Aggregate case counts'}).click();await viewer.locator('#table table').waitFor();
  assert.equal(await viewer.locator('#access').isHidden(),true);
  const [exported]=await Promise.all([viewer.waitForEvent('download'),viewer.locator('#export').click()]);
  const report=JSON.parse(await fs.readFile(await exported.path(),'utf8'));assert.deepEqual(report.snapshot,snapshot);assert.equal(report.source_freshness,'unknown_source_not_shared');
  await owner.locator('#members select').selectOption('editor');await owner.locator('#accessConfirm').check();await owner.locator('#saveAccess').click();
  await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory button').filter({hasText:'Aggregate case counts'}).click();await viewer.locator('#table table').waitFor();
  await viewer.locator('#file').setInputFiles(file);await viewer.locator('#preview table').waitFor();await viewer.locator('#title').fill('Second revision');await viewer.locator('#confirm').check();await viewer.locator('#publish').click();await viewer.locator('#status').filter({hasText:'Snapshot published.'}).waitFor();
  await owner.locator('#refresh').click();await owner.locator('#inventory button').filter({hasText:'Second revision'}).click();await owner.locator('#basis').filter({hasText:'Viewing revision 2 of 2'}).waitFor();
  await owner.locator('#revision').fill('1');await owner.locator('#openRevision').click();await owner.locator('#basis').filter({hasText:'Viewing revision 1 of 2'}).waitFor();assert.equal(await owner.locator('#viewTitle').textContent(),'Aggregate case counts');
  await owner.getByRole('button',{name:'Remove member'}).click();await owner.locator('#accessConfirm').check();await owner.locator('#saveAccess').click();await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory').getByText('No authorized publications.').waitFor();assert.equal(await viewer.locator('#detail').isHidden(),true);assert.equal(await viewer.locator('#table').textContent(),'');
  assert.equal(await viewer.evaluate(async({base,wid})=>(await fetch(base+'/v1/human/analyses/'+wid+'/export?revision=1',{headers:{Authorization:'Bearer bob'}})).status,{base,wid}),404);
  // A response authorized to the old identity cannot repopulate a new identity's DOM.
  let release,seen;const held=new Promise(r=>seen=r);
  await owner.route('**/v1/human/analyses/'+wid,async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await owner.locator('#inventory button').filter({hasText:'Second revision'}).click();await held;
  await owner.locator('#disconnect').click();await owner.locator('#token').fill('bob');await owner.locator('#connect').click();await owner.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();
  const stale=owner.waitForResponse(r=>r.url().endsWith('/v1/human/analyses/'+wid));release();await stale;await owner.waitForTimeout(50);
  assert.equal(await owner.locator('#detail').isHidden(),true);assert.equal(await owner.locator('#table').textContent(),'');
  await connect(owner,'alice');await owner.locator('#inventory button').filter({hasText:'Second revision'}).click();await owner.locator('#table table').waitFor();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await owner.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);
  const audit=await owner.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));assert.deepEqual(audit.violations,[]);
  await owner.setViewportSize({width:390,height:844});assert.ok(await owner.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await owner.locator('#deleteConfirm').check();await owner.locator('#delete').click();await owner.locator('#status').filter({hasText:'Publication, revisions and member access deleted.'}).waitFor();
  assert.equal(await owner.evaluate(async({base,wid})=>(await fetch(base+'/v1/human/analyses/'+wid,{headers:{Authorization:'Bearer alice'}})).status,{base,wid}),404);
  await owner.locator('#disconnect').click();assert.equal(await owner.locator('#preview').textContent(),'');assert.equal(await owner.locator('#token').inputValue(),'');
  assert.deepEqual(errors,[]);console.log('Shared analyses acceptance passed: production snapshot → rejected hidden fields before transfer → private publication → viewer export → editor revision → historical access → revoke → denied old export → held old-identity response suppressed → delete; WCAG and mobile.');
 }catch(e){throw new Error(e.stack+'\nServer:\n'+logs);}finally{if(browser)await browser.close();server.kill('SIGTERM');await new Promise(r=>server.once('exit',r));await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
