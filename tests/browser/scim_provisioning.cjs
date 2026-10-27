const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {spawn}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-scim-browser-'));
 const base='http://127.0.0.1:8791',admin='admin-'+'x'.repeat(40);
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','fleet_status_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>spawnError=e);
 for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-8000));
 const request=async(method,url,body,token=admin,extra={})=>fetch(base+url,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/scim+json',...extra},...(body===undefined?{}:{body:JSON.stringify(body)})});
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }assert.ok(ready,logs);
  const boot=await request('POST','/v1/admin/auth/bootstrap',{bootstrap_token:admin,email:'owner@acme.test',display_name:'Owned admin'});assert.ok(boot.ok,await boot.text());
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/admin');
  async function login(){
   await page.locator('#orgIn').fill('acme');
   if(!await page.locator('#tokenIn').isVisible())await page.locator('#tokenBox summary').click();
   await page.locator('#tokenIn').fill(admin);await page.locator('#tokenSignIn').click();await page.locator('#stats').waitFor();
  }
  async function identity(){await page.locator('nav button[data-tab=identity]').click();await page.locator('#scimProviders').waitFor();}
  await login();await identity();
  assert.match(await page.locator('#scimProviders').textContent(),/No provisioning providers/);
  await page.locator('#scimLabel').fill('Owned <img src=x onerror=alert(1)>');
  const madeResponse=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/v1/admin/scim-providers/acme');
  await page.locator('#scimCreate').click();const made=await(await madeResponse).json();
  const token=made.token,pid=made.provider.provider_id,source='/scim/v2/'+pid;
  await page.locator('#scimOneTimeToken').waitFor();assert.equal(await page.locator('#scimOneTimeToken').textContent(),token);
  await page.locator('#scimProviders article').waitFor();assert.equal(await page.locator('#scimProviders img').count(),0);
  assert.equal((await request('GET',source+'/Users',undefined,token)).status,401);
  const storage=await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}}));assert.ok(!storage.includes(token));assert.ok(!page.url().includes(token));
  const toggle=page.locator('[data-scim-toggle="'+pid+'"]');assert.ok(await toggle.isDisabled());
  await page.locator('#scimConsent-'+pid).check();await toggle.click();
  await page.locator('#scimStatus').filter({hasText:'Provider enabled.'}).waitFor();assert.ok(await page.locator('#scimCredential').isHidden());
  const created=await request('POST',source+'/Users',{schemas:['urn:ietf:params:scim:schemas:core:2.0:User'],userName:'operator@acme.test',displayName:'Owned operator',externalId:'native-operator',active:true},token);
  assert.equal(created.status,201);const user=await created.json(),uid=user.id;
  await page.locator('nav button[data-tab=people]').click();await page.locator('#ppl').filter({hasText:'operator@acme.test'}).waitFor();
  const patch={schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'replace',path:'active',value:false}]};
  const inactive=await request('PATCH',source+'/Users/'+uid,patch,token,{'If-Match':created.headers.get('etag')});assert.equal(inactive.status,200);
  patch.Operations[0].value=true;
  const active=await request('PATCH',source+'/Users/'+uid,patch,token,{'If-Match':inactive.headers.get('etag')});assert.equal(active.status,200);
  assert.equal((await active.json()).id,uid);
  await identity();
  await page.evaluate(source=>{const script=document.createElement('script');script.nonce=document.querySelector('script[nonce]').nonce;script.textContent=source;document.head.append(script);},await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8'));
  const audit=await page.evaluate(()=>axe.run(document.querySelector('#scimAdmin'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(audit.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile width');
  await page.setViewportSize({width:1280,height:900});
  // A delayed old-session 401 cannot sign out a newly signed-in context, even
  // when the owned bootstrap fixture deliberately uses the same credential.
  let release,arrived,finished,held=false;const gate=new Promise(r=>release=r),seen=new Promise(r=>arrived=r),completed=new Promise(r=>finished=r);
  await page.route('**/v1/admin/scim-providers/acme',async route=>{
   if(route.request().method()==='GET'&&!held){held=true;arrived();await gate;await route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Old held session'})});finished();}
   else await route.continue();
  });
  await page.locator('#scimRefresh').click();await seen;
  await page.locator('#signOut').click();await page.locator('#login').waitFor();await login();
  release();await completed;await page.unroute('**/v1/admin/scim-providers/acme');
  await page.waitForLoadState('networkidle');assert.ok(await page.locator('#app').isVisible());assert.equal(await page.locator('#scimOneTimeToken').count(),0);
  // A completed creation whose response arrives after sign-out must not
  // reveal the old context's one-time credential to the new view.
  await identity();
  let releaseCreate,createdArrived,finishedCreate,heldCredential;
  const createGate=new Promise(r=>releaseCreate=r),createSeen=new Promise(r=>createdArrived=r),createDone=new Promise(r=>finishedCreate=r);
  await page.route('**/v1/admin/scim-providers/acme',async route=>{
   if(route.request().method()==='POST'){
    const response=await route.fetch();assert.equal(response.status(),201);heldCredential=(await response.json()).token;
    createdArrived();await createGate;await route.fulfill({response});finishedCreate();
   }else await route.continue();
  });
  await page.locator('#scimLabel').fill('Held old-context creation');await page.locator('#scimCreate').click();await createSeen;
  await page.locator('#signOut').click();await page.locator('#login').waitFor();await login();
  releaseCreate();await createDone;await page.unroute('**/v1/admin/scim-providers/acme');await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('#scimOneTimeToken').count(),0);
  assert.ok(!await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}})).then(text=>text.includes(heldCredential)));
  await identity();await page.locator('#scimProviders article').filter({hasText:'Held old-context creation'}).filter({hasText:'Disabled'}).waitFor();
  page.once('dialog',d=>d.accept());await page.locator('[data-scim-revoke="'+pid+'"]').click();
  await page.locator('#scimStatus').filter({hasText:'Credential permanently revoked'}).waitFor();
  assert.equal((await request('GET',source+'/Users',undefined,token)).status,401);
  const roster=await request('GET','/v1/admin/people/acme');assert.equal((await roster.json()).employees[0].actor_id,'operator@acme.test');
  assert.deepEqual(errors,[]);
  console.log('SCIM browser passed: disabled creation, one-view credential without persistence, explicit enablement, real Users create/deactivate/rehire, roster, escaped labels, WCAG/mobile, stale401/held-credential/new-session guards and permanent credential revocation.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
