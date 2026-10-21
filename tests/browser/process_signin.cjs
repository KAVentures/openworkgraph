const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-process-signin-'));
  const base='http://127.0.0.1:8793';
  const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_signin_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8793'],{
    env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});
  for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw new Error('Synthetic sign-in server exited: '+logs);
      try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    if(!ready)throw new Error('Synthetic sign-in server not ready: '+logs);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const errors=[];
    async function signIn(person){
      const context=await browser.newContext(),page=await context.newPage();let authorization='';
      page.on('pageerror',error=>errors.push(error.message));
      page.on('request',request=>{const value=request.headers().authorization||'';if(value.startsWith('OWG-Process-Session '))authorization=value;});
      await page.goto(base+'/processes');
      await page.getByRole('button',{name:'Sign in with company account',exact:true}).click();
      await page.getByRole('button',{name:'Use synthetic '+person,exact:true}).click();
      await page.getByRole('status').filter({hasText:'Loaded organization process inventory.'}).waitFor();
      assert.equal(page.url(),base+'/processes');
      assert(authorization.startsWith('OWG-Process-Session '));
      assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
      assert.equal((await context.cookies()).some(cookie=>cookie.name==='owg_process_login'),false);
      return {page,context,authorization};
    }
    const alice=await signIn('Alice');
    assert.match(await alice.page.locator('#identity').textContent(),/actor alice/);
    await alice.page.getByLabel('Reviewed process export').setInputFiles(path.join(scratch,'process.json'));
    await alice.page.getByRole('status').filter({hasText:'File previewed in this browser.'}).waitFor();
    await alice.page.locator('#reviewed').check();await alice.page.locator('#sharing').check();
    await alice.page.getByRole('button',{name:'Publish as a new shared process',exact:true}).click();
    await alice.page.locator('#selectedTitle').filter({hasText:'Signed-in support process'}).waitFor();
    const bob=await signIn('Bob');
    assert.match(await bob.page.locator('#identity').textContent(),/actor bob/);
    assert.equal(await bob.page.getByRole('button',{name:'Publish as a new shared process',exact:true}).isDisabled(),true);
    await bob.page.getByRole('button',{name:'Signed-in support process · r1',exact:true}).click();
    assert.equal(await bob.page.getByRole('button',{name:'Edit selected design',exact:true}).isDisabled(),true);
    const refused=await bob.page.request.get(base+'/v1/human/me',{headers:{Authorization:bob.authorization}});
    assert.equal(refused.status(),401);
    const signedOut=alice.page.waitForResponse(response=>response.url().endsWith('/v1/human/processes/session/logout'));
    await alice.page.getByRole('button',{name:'Disconnect and clear page',exact:true}).click();
    assert.equal((await signedOut).status(),200);
    await alice.page.locator('#identity').filter({hasText:'Not connected.'}).waitFor();
    const revoked=await alice.page.request.get(base+'/v1/human/processes',{headers:{Authorization:alice.authorization}});
    assert.equal(revoked.status(),401);
    assert.deepEqual(errors,[]);
    console.log('Process company sign-in browser acceptance passed: actual RSA OIDC/PKCE redirects, same-browser binding, scoped identities, explicit publication, read-only access, memory-only session and server logout.');
  }finally{
    if(browser)await browser.close();server.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
