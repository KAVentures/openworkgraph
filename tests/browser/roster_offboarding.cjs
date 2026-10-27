const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-offboarding-')),base='http://127.0.0.1:8793';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','roster_offboarding_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8793'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const stream of [server.stdout,server.stderr])stream.on('data',v=>logs=(logs+v).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const errors=[];
  async function signin(kind,person='Alice',success=true){
   const context=await browser.newContext(),page=await context.newPage();let authorization='';
   const plural=kind==='process'?'processes':'analyses',prefix=kind==='process'?'OWG-Process-Session ':'OWG-Analysis-Session ';
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const value=r.headers().authorization||'';if(value.startsWith(prefix))authorization=value;});
   await page.goto(base+'/'+plural);await page.getByRole('button',{name:'Sign in with company account',exact:true}).click();await page.getByRole('button',{name:'Use synthetic '+person,exact:true}).click();
   if(success){await page.locator('#status').filter({hasText:kind==='process'?'Loaded organization process inventory.':'Connected to authorized analyses.'}).waitFor();assert.ok(authorization.startsWith(prefix));assert.match(await page.locator('#identity').textContent(),new RegExp('actor '+(person==='Alice'?'alice@synthetic.test':'bob')));}
   else{await page.waitForURL(base+'/'+plural);await page.locator('#status').filter({hasText:'Company sign-in failed'}).waitFor();assert.equal(authorization,'');}
   assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
   return {page,context,authorization,plural,kind};
  }
  const processSession=await signin('process'),analysis=await signin('analysis'),bob=await signin('process','Bob');
  const request=async(s)=>s.page.request.get(base+'/v1/human/'+s.plural,{headers:{Authorization:s.authorization}});
  assert.equal((await fetch(base+'/synthetic/disable',{method:'POST'})).status,200);
  for(const session of [processSession,analysis]){
   assert.equal((await request(session)).status(),401);
   await session.page.locator(session.kind==='process'?'#load':'#refresh').click();await session.page.locator('#identity').filter({hasText:'Not connected.'}).waitFor();assert.equal(await session.page.locator('#inventory button').count(),0);
   await signin(session.kind,'Alice',false);
  }
  assert.equal((await request(bob)).status(),200,'an unregistered peer retains independently granted access');
  assert.equal((await fetch(base+'/synthetic/rehire',{method:'POST'})).status,200);
  for(const session of [processSession,analysis]){assert.equal((await request(session)).status(),401);const fresh=await signin(session.kind);assert.equal((await request(fresh)).status(),200);}
  // A delayed old-identity 401 must not clear a newly authenticated identity.
  for(const kind of ['process','analysis']){
   const s=await signin(kind);let seen,release;const held=new Promise(r=>seen=r);
   await s.page.route('**/v1/human/'+s.plural+'?*',async route=>{const response=await route.fetch();assert.equal(response.status(),401);seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
   await fetch(base+'/synthetic/disable',{method:'POST'});await s.page.locator(kind==='process'?'#load':'#refresh').click();await held;
   await s.page.locator('#disconnect').click();
   const bearer=(await (await fetch(base+'/synthetic/delegated')).json()).token;
   await s.page.locator('#token').fill(bearer);await s.page.getByRole('button',{name:'Connect',exact:true}).click();
   await s.page.locator('#status').filter({hasText:kind==='process'?'Loaded organization process inventory.':'Connected to authorized analyses.'}).waitFor();
   const denied=s.page.waitForResponse(r=>r.status()===401&&r.url().includes('/v1/human/'+s.plural+'?'));release();await denied;
   assert.match(await s.page.locator('#identity').textContent(),/actor bob/);
   await fetch(base+'/synthetic/rehire',{method:'POST'});
  }
  assert.deepEqual(errors,[]);
  console.log('Roster offboarding browser passed: actual signed RSA OIDC/PKCE for both portals, exact roster actor, live disable clears UI/rejects old and fresh credentials, unregistered peer isolation, rehire requires fresh session, no persistent browser storage.');
 }catch(e){throw new Error(e.stack+'\n'+logs);}finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
