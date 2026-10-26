const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-analysis-signin-')),base='http://127.0.0.1:8793';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','analysis_signin_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8793'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>spawnError=e);
 for(const stream of [server.stdout,server.stderr])stream.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error(logs);
   try{if((await fetch(base+'/analyses')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const errors=[];
  async function signin(person){
   const context=await browser.newContext({acceptDownloads:true}),page=await context.newPage();let authorization='';
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const h=r.headers().authorization||'';if(h.startsWith('OWG-Analysis-Session '))authorization=h;});
   await page.goto(base+'/analyses');await page.getByRole('button',{name:'Sign in with company account',exact:true}).click();
   await page.getByRole('button',{name:'Use synthetic '+person,exact:true}).click();
   await page.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();
   assert.equal(page.url(),base+'/analyses');assert(authorization.startsWith('OWG-Analysis-Session '));
   assert.equal(await page.locator('#token').inputValue(),'');
   assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
   assert.equal((await context.cookies()).some(c=>c.name==='owg_analysis_login'),false);
   assert.match(await page.locator('#identity').textContent(),new RegExp('actor '+person.toLowerCase()));
   assert.doesNotMatch(await page.locator('#identity').textContent(),/org:process:|evidence:/);
   return {context,page,authorization};
  }
  const alice=await signin('Alice'),bob=await signin('Bob');
  assert.equal(await bob.page.locator('#publisher').isHidden(),true);
  await alice.page.locator('#file').setInputFiles(path.join(scratch,'snapshot.json'));
  await alice.page.locator('#preview table').waitFor();await alice.page.locator('#title').fill('Company sign-in analysis');
  await alice.page.locator('#confirm').check();
  const [created]=await Promise.all([alice.page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/v1/human/analyses')),alice.page.locator('#publish').click()]);
  assert.equal(created.status(),200);const wid=(await created.json()).view_id;
  await alice.page.locator('#status').filter({hasText:'Snapshot published.'}).waitFor();
  assert.equal((await bob.page.request.get(base+'/v1/human/analyses/'+wid,{headers:{Authorization:bob.authorization}})).status(),404);
  await alice.page.locator('#addMember').click();await alice.page.locator('#members input').fill('bob');await alice.page.locator('#accessConfirm').check();await alice.page.locator('#saveAccess').click();
  await alice.page.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await bob.page.locator('#refresh').click();await bob.page.locator('#inventory button').filter({hasText:'Company sign-in analysis'}).click();await bob.page.locator('#table table').waitFor();
  assert.equal(await bob.page.locator('#access').isHidden(),true);
  const [download]=await Promise.all([bob.page.waitForEvent('download'),bob.page.locator('#export').click()]);
  const exported=JSON.parse(await fs.readFile(await download.path(),'utf8'));assert.equal(exported.view_id,wid);assert.equal(exported.source_freshness,'unknown_source_not_shared');
  for(const endpoint of ['/v1/human/processes','/v1/human/me','/v1/admin/auth/me'])assert.equal((await bob.page.request.get(base+endpoint,{headers:{Authorization:bob.authorization}})).status(),401);
  assert.equal((await bob.page.request.get(base+'/v1/human/analyses',{headers:{Authorization:'Bearer '+bob.authorization.split(' ')[1]}})).status(),401);
  // Logout clears the table immediately, revokes the actual server credential,
  // and a held successful old-identity response cannot reopen the publication.
  let seen,release;const held=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Held revision request was not reached')),15000);seen=()=>{clearTimeout(timer);resolve();};});
  await bob.page.route(url=>url.pathname==='/v1/human/analyses/'+wid,async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await bob.page.locator('#openRevision').click();await held;
  const logout=bob.page.waitForResponse(r=>r.url().endsWith('/v1/human/analyses/session/logout'));
  await bob.page.locator('#disconnect').click();assert.equal((await logout).status(),200);
  assert.equal(await bob.page.locator('#table').textContent(),'');assert.equal(await bob.page.locator('#workspace').isHidden(),true);
  const stale=bob.page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/human/analyses/'+wid);release();await stale;await bob.page.waitForTimeout(50);
  assert.equal(await bob.page.locator('#table').textContent(),'');assert.equal(await bob.page.locator('#workspace').isHidden(),true);
  assert.equal((await bob.page.request.get(base+'/v1/human/analyses/'+wid+'/export?revision=1',{headers:{Authorization:bob.authorization}})).status(),401);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
  await alice.page.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);
  const audit=await alice.page.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));assert.deepEqual(audit.violations,[]);
  await alice.page.setViewportSize({width:390,height:844});assert.ok(await alice.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await alice.page.reload();assert.equal(await alice.page.locator('#workspace').isHidden(),true);assert.equal(await alice.page.locator('#identity').textContent(),'Not connected.');
  // Replacing a company session with an invalid delegated credential still
  // revokes the previous session and leaves no old workspace visible.
  const switcher=await signin('Alice');
  await switcher.page.locator('#token').fill('invalid-token');await switcher.page.locator('#connect').click();
  await switcher.page.locator('#status').filter({hasText:'valid OIDC bearer token required'}).waitFor();
  assert.equal(await switcher.page.locator('#workspace').isHidden(),true);
  assert.equal((await switcher.page.request.get(base+'/v1/human/analyses',{headers:{Authorization:switcher.authorization}})).status(),401);
  // A network failure cannot keep page data or falsely claim revocation.
  const failure=await signin('Bob');
  await failure.page.route('**/v1/human/analyses/session/logout',route=>route.abort('failed'));
  await failure.page.locator('#disconnect').click();
  await failure.page.locator('#status').filter({hasText:'Server sign-out could not be confirmed'}).waitFor();
  assert.equal(await failure.page.locator('#workspace').isHidden(),true);
  assert.equal(await failure.page.locator('#identity').textContent(),'Not connected.');
  assert.equal((await failure.page.request.get(base+'/v1/human/analyses',{headers:{Authorization:failure.authorization}})).status(),200);
  assert.equal((await failure.page.request.post(base+'/v1/human/analyses/session/logout',{headers:{Authorization:failure.authorization}})).status(),200);
  assert.deepEqual(errors,[]);
  console.log('Analysis company sign-in passed: actual RSA/OIDC/PKCE, scoped memory-only credentials, private publication, explicit membership, viewer export, cross-portal/evidence denial, logout revocation, held-response suppression, reload clears, WCAG/mobile.');
 }catch(e){throw new Error(e.stack+'\nServer:\n'+logs);}finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
