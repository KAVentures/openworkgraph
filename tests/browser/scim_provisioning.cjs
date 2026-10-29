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
  const manualResponse=await request('POST','/v1/admin/employees/acme',{email:'manual@acme.test',display_name:'Manual worker',teams:['ops']});
  assert.equal(manualResponse.status,200);const manual=await manualResponse.json();
  const assignmentRow=page.locator('article[data-provider="'+pid+'"]'),assignmentPrefix='#scimAssign-'+pid;
  await assignmentRow.getByRole('button',{name:'Assign existing employee',exact:true}).click();
  await page.locator(assignmentPrefix+'-employee').fill('manual@acme.test');
  await page.locator(assignmentPrefix+'-external').fill('native-manual');
  const reviewAssignment=assignmentRow.getByRole('button',{name:'Review assignment',exact:true});
  const confirmAssignment=assignmentRow.getByRole('button',{name:'Confirm assignment',exact:true});
  assert.ok(await confirmAssignment.isDisabled());
  const assignmentUrl='/v1/admin/scim-providers/acme/'+pid+'/assignment-preview';
  let releasePreview,previewArrived,previewFinished;
  const previewGate=new Promise(r=>releasePreview=r),previewSeen=new Promise(r=>previewArrived=r),previewDone=new Promise(r=>previewFinished=r);
  await page.route('**'+assignmentUrl,async route=>{
   if(route.request().method()==='POST'){const response=await route.fetch();previewArrived();await previewGate;await route.fulfill({response});previewFinished();}
   else await route.continue();
  });
  await reviewAssignment.click();await previewSeen;
  await page.locator(assignmentPrefix+'-external').fill('native-manual-current');
  releasePreview();await previewDone;await page.unroute('**'+assignmentUrl);await page.waitForLoadState('networkidle');
  assert.ok(await page.locator(assignmentPrefix+'-confirm').isDisabled(),'edited form rejects held preview');
  assert.ok(await confirmAssignment.isDisabled());
  await reviewAssignment.click();
  await assignmentRow.getByRole('status').filter({hasText:'Assign manual@acme.test'}).waitFor();
  assert.ok(await confirmAssignment.isDisabled(),'preview alone never assigns lifecycle authority');
  // A concurrent ordinary roster update must invalidate the reviewed state.
  assert.equal((await request('POST','/v1/admin/employees/acme',{email:'manual@acme.test',display_name:'Updated manual worker'})).status,200);
  await page.locator(assignmentPrefix+'-confirm').check();await confirmAssignment.click();
  await assignmentRow.getByRole('status').filter({hasText:'review a new assignment preview'}).waitFor();
  assert.ok(await confirmAssignment.isDisabled());
  await reviewAssignment.click();await page.locator(assignmentPrefix+'-confirm').waitFor();
  await assignmentRow.getByRole('status').filter({hasText:'Assign manual@acme.test'}).waitFor();
  await page.locator(assignmentPrefix+'-confirm').check();await confirmAssignment.click();
  await page.locator('#scimStatus').filter({hasText:'Existing employee assigned.'}).waitFor();
  const assigned=await(await request('GET',source+'/Users/'+manual.employee_id,undefined,token)).json();
  assert.equal(assigned.id,manual.employee_id);assert.equal(assigned.userName,'manual@acme.test');
  assert.equal(assigned.displayName,'Updated manual worker');assert.equal(assigned.active,true);assert.equal(assigned.externalId,'native-manual-current');
  const preserved=(await(await request('GET','/v1/admin/people/acme')).json()).employees.find(p=>p.employee_id===manual.employee_id);
  assert.deepEqual(preserved.teams,['ops']);
  const created=await request('POST',source+'/Users',{schemas:['urn:ietf:params:scim:schemas:core:2.0:User'],userName:'operator@acme.test',displayName:'Owned operator',externalId:'native-operator',active:true},token);
  assert.equal(created.status,201);const user=await created.json(),uid=user.id;
  await page.locator('nav button[data-tab=people]').click();await page.locator('#ppl').filter({hasText:'operator@acme.test'}).waitFor();
  const patch={schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'replace',path:'active',value:false}]};
  const inactive=await request('PATCH',source+'/Users/'+uid,patch,token,{'If-Match':created.headers.get('etag')});assert.equal(inactive.status,200);
  patch.Operations[0].value=true;
  const active=await request('PATCH',source+'/Users/'+uid,patch,token,{'If-Match':inactive.headers.get('etag')});assert.equal(active.status,200);
  assert.equal((await active.json()).id,uid);
  await identity();
  const policyUserResponse=await request('POST',source+'/Users',{schemas:['urn:ietf:params:scim:schemas:core:2.0:User'],userName:'policy@acme.test',active:true},token);
  assert.equal(policyUserResponse.status,201);const policyUser=await policyUserResponse.json(),policyUserUrl=source+'/Users/'+policyUser.id;
  const policyPatch={schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'replace',value:{active:false}}]};
  assert.equal((await request('PATCH',policyUserUrl,policyPatch,token)).status,428);
  const policyUrl='/v1/admin/scim-providers/acme/'+pid+'/client-policy';
  const policyOpen=()=>page.locator('[data-scim-policy="'+pid+'"]');
  const policySave=()=>page.locator('[data-scim-policy-save="'+pid+'"]');
  let releasePolicy,policyArrived,policyFinished;
  const policyGate=new Promise(r=>releasePolicy=r),policySeen=new Promise(r=>policyArrived=r),policyDone=new Promise(r=>policyFinished=r);
  await page.route('**'+policyUrl,async route=>{
   if(route.request().method()==='GET'){const response=await route.fetch();policyArrived();await policyGate;await route.fulfill({response});policyFinished();}
   else await route.continue();
  });
  await policyOpen().click();await policySeen;
  await page.getByRole('button',{name:'Close policy',exact:true}).click();
  releasePolicy();await policyDone;await page.unroute('**'+policyUrl);await page.waitForLoadState('networkidle');
  assert.ok(await policySave().isHidden(),'cancelled policy response cannot restore an old preview');
  await policyOpen().click();await page.getByRole('status').filter({hasText:'Strict versions are required.'}).waitFor();
  assert.ok(await policySave().isDisabled());
  const reviewedPolicy=await(await request('GET',policyUrl)).json();
  assert.equal((await request('PATCH','/v1/admin/scim-providers/acme/'+pid,{revision:reviewedPolicy.revision,enabled:true})).status,200);
  await page.locator('#scimPolicyConsent-'+pid).check();await policySave().click();
  await page.locator('#scimStatus').filter({hasText:'Provider changed'}).waitFor();
  await policyOpen().click();await page.getByRole('status').filter({hasText:'Strict versions are required.'}).waitFor();
  assert.ok(await policySave().isDisabled(),'stale confirmation must be reviewed again');
  await page.locator('#scimPolicyConsent-'+pid).check();await policySave().click();
  await page.locator('#scimStatus').filter({hasText:'Unversioned standalone User PATCH enabled'}).waitFor();
  assert.equal((await request('PATCH',policyUserUrl,policyPatch,token,{'If-Match':'stale'})).status,412);
  const clientDeactivated=await request('PATCH',policyUserUrl,policyPatch,token);assert.equal(clientDeactivated.status,200);
  const clientNoop=await request('PATCH',policyUserUrl,policyPatch,token);assert.equal(clientNoop.headers.get('etag'),clientDeactivated.headers.get('etag'));
  const strictBulk=await request('POST',source+'/Bulk',{schemas:['urn:ietf:params:scim:api:messages:2.0:BulkRequest'],Operations:[{method:'PATCH',path:'/Users/'+policyUser.id,data:policyPatch}]},token);
  assert.equal((await strictBulk.json()).Operations[0].status,'428');
  policyPatch.Operations[0].value.active=true;assert.equal((await request('PATCH',policyUserUrl,policyPatch,token)).status,200);
  await policyOpen().click();await page.getByRole('status').filter({hasText:'Unversioned standalone User PATCH is enabled.'}).waitFor();
  await policySave().click();await page.locator('#scimStatus').filter({hasText:'Strict client versions restored.'}).waitFor();
  assert.equal((await request('PATCH',policyUserUrl,policyPatch,token)).status,428);
  assert.match(await page.locator('#scimAdmin').textContent(),/50 Users\/Groups operations/);
  assert.match(await page.locator('#scimAdmin').textContent(),/Operations commit separately/);
  const config=await(await request('GET',source+'/ServiceProviderConfig',undefined,token)).json();assert.equal(config.bulk.maxOperations,50);assert.equal(config.bulk.supported,true);
  const schema='urn:ietf:params:scim:api:messages:2.0:BulkRequest',userSchema='urn:ietf:params:scim:schemas:core:2.0:User';
  const newUser=(bulkId,email)=>({method:'POST',path:'/Users',bulkId,data:{schemas:[userSchema],userName:email,active:true}});
  patch.Operations[0].value=false;
  const batch=await request('POST',source+'/Bulk',{schemas:[schema],failOnErrors:1,Operations:[
   newUser('created','zbulk@acme.test'),{method:'POST',path:'/Groups',bulkId:'owned-group',data:{schemas:['urn:ietf:params:scim:schemas:core:2.0:Group'],displayName:'Bulk group',members:[{value:'bulkId:created',type:'User'}]}},{method:'PATCH',path:'/Users/'+uid,version:active.headers.get('etag'),data:patch},
   newUser('conflict','operator@acme.test'),newUser('skipped','unprocessed@acme.test')]},token);
  assert.equal(batch.status,200);const bulk=(await batch.json()).Operations;
  assert.deepEqual(bulk.map(r=>r.status),['201','201','200','409']);assert.equal(bulk[0].bulkId,'created');
  const savedGroup=await(await request('GET',bulk[1].location.replace(base,''),undefined,token)).json();
  assert.deepEqual(savedGroup.members,[{value:bulk[0].location.split('/').pop(),type:'User'}]);
  // Exercise selected reads from Chromium itself; tokens remain memory-only.
  const selectedReads=await page.evaluate(async ({source,token,uid})=>{
   const headers={Authorization:'Bearer '+token,'Content-Type':'application/scim+json'};
   const filter='displayName eq "Bulk group" AND displayName eq "Bulk group"';
   const get=await fetch(source+'/Groups?attributes=displayName,members.value&filter='+encodeURIComponent(filter),{headers});
   const post=await fetch(source+'/Groups/.search',{method:'POST',headers,body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:SearchRequest'],filter,attributes:['displayName','members.value']})});
   const users=await fetch(source+'/Users/.search',{method:'POST',headers,body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:SearchRequest'],filter:'userName eq "operator@acme.test" and externalId eq "native-operator"',attributes:['id']})});
   const single=await fetch(source+'/Users/'+encodeURIComponent(uid),{headers});
   return {getStatus:get.status,postStatus:post.status,get:await get.json(),post:await post.json(),usersStatus:users.status,users:await users.json(),single:await single.json()};
  },{source,token,uid});
  assert.equal(selectedReads.getStatus,200);assert.equal(selectedReads.postStatus,200);
  assert.deepEqual(selectedReads.post,selectedReads.get);
  assert.deepEqual(Object.keys(selectedReads.post.Resources[0]).sort(),['displayName','id','members','schemas']);
  assert.deepEqual(selectedReads.post.Resources[0].members,[{value:bulk[0].location.split('/').pop()}]);
  assert.equal(selectedReads.single.id,uid);
  assert.equal(selectedReads.usersStatus,200);assert.equal(selectedReads.users.totalResults,1);
  assert.deepEqual(selectedReads.users.Resources,[{schemas:[userSchema],id:uid}]);
  const namedReads=await page.evaluate(async ({source,token,uid})=>{
   const headers={Authorization:'Bearer '+token,'Content-Type':'application/scim+json'},url=source+'/Users/'+encodeURIComponent(uid);
   const original=await fetch(url,{headers}),originalValue=await original.json();
   const first=await fetch(url,{method:'PATCH',headers:{...headers,'If-Match':original.headers.get('etag')},
    body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'replace',path:'name',value:{givenName:'Zoë',familyName:'雪',formatted:'Literal  full name'}}]})});
   const firstValue=await first.json();
   const partial=await fetch(url,{method:'PATCH',headers:{...headers,'If-Match':first.headers.get('etag')},
    body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'replace',path:'NAME',value:{GIVENNAME:'Jane'}}]})});
   const partialValue=await partial.json();
   const stale=await fetch(url,{method:'PATCH',headers:{...headers,'If-Match':first.headers.get('etag')},
    body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:PatchOp'],Operations:[{op:'remove',path:'name'}]})});
   const selected=await fetch(url+'?attributes=name.givenName,name.familyName',{headers});
   const post=await fetch(source+'/Users/.search',{method:'POST',headers,
    body:JSON.stringify({schemas:['urn:ietf:params:scim:api:messages:2.0:SearchRequest'],filter:'userName eq "operator@acme.test"',attributes:['name.givenName','name.familyName']})});
   return {original:originalValue,firstStatus:first.status,first:firstValue,partialStatus:partial.status,partial:partialValue,staleStatus:stale.status,
    selectedStatus:selected.status,selected:await selected.json(),postStatus:post.status,post:await post.json(),etag:selected.headers.get('etag'),fullEtag:partial.headers.get('etag')};
  },{source,token,uid});
  assert.equal(namedReads.firstStatus,200);assert.equal(namedReads.partialStatus,200);assert.equal(namedReads.staleStatus,412);
  assert.deepEqual(namedReads.partial.name,{givenName:'Jane',familyName:'雪',formatted:'Literal  full name'});
  for(const key of ['id','userName','displayName','active'])assert.equal(namedReads.partial[key],namedReads.original[key],'name metadata preserves '+key);
  assert.equal(namedReads.selectedStatus,200);assert.equal(namedReads.postStatus,200);assert.equal(namedReads.etag,namedReads.fullEtag);
  assert.deepEqual(namedReads.selected,{schemas:[userSchema],id:uid,name:{givenName:'Jane',familyName:'雪'}});
  assert.deepEqual(namedReads.post.Resources,[namedReads.selected]);
  const unknownRef=await request('POST',source+'/Bulk',{schemas:[schema],Operations:[{method:'POST',path:'/Groups',bulkId:'refused',data:{schemas:['urn:ietf:params:scim:schemas:core:2.0:Group'],displayName:'Not created',members:[{value:'bulkId:created'}]}}]},token);
  assert.equal((await unknownRef.json()).Operations[0].status,'400');
  assert.equal((await(await request('GET',source+'/Groups',undefined,token)).json()).totalResults,1);
  const offboarded=await(await request('GET',source+'/Users/'+uid,undefined,token)).json();assert.equal(offboarded.active,false);
  patch.Operations[0].value=true;
  const blind=await request('POST',source+'/Bulk',{schemas:[schema],Operations:[{method:'PATCH',path:'/Users/'+uid,data:patch}]},token);
  assert.equal((await blind.json()).Operations[0].status,'428');
  const staleRehire=await request('POST',source+'/Bulk',{schemas:[schema],Operations:[{method:'PATCH',path:'/Users/'+uid,version:bulk[2].version,data:patch}]},token);
  assert.equal((await staleRehire.json()).Operations[0].status,'412','name changes invalidate earlier Bulk versions');
  const rehire=await request('POST',source+'/Bulk',{schemas:[schema],Operations:[{method:'PATCH',path:'/Users/'+uid,version:offboarded.meta.version,data:patch}]},token);
  assert.equal((await rehire.json()).Operations[0].status,'200');
  await identity();
  await page.locator('[data-scim-assign="'+pid+'"]').click();
  await page.locator(assignmentPrefix+'-employee').waitFor();
  await policyOpen().click();await page.getByRole('status').filter({hasText:'Strict versions are required.'}).waitFor();
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
  assert.equal((await request('POST',source+'/Users/.search',{schemas:['urn:ietf:params:scim:api:messages:2.0:SearchRequest'],attributes:['id']},token)).status,401);
  const roster=await request('GET','/v1/admin/people/acme');const people=(await roster.json()).employees;assert.deepEqual(people.map(p=>p.actor_id).sort(),['manual@acme.test','operator@acme.test','policy@acme.test','zbulk@acme.test']);
  assert.equal((await request('POST',source+'/Bulk',{schemas:[schema],Operations:[newUser('revoked','refused@acme.test')]},token)).status,401);
  assert.deepEqual(errors,[]);
  console.log('SCIM browser passed: disabled creation, one-view credential without persistence, explicit enablement, explicit employee assignment with held-preview/stale-roster refusal and preserved identity/manual teams, reviewed provider client policy with cancelled/stale preview refusal and current delta/no-op/strict-supplied-version/Bulk checks, real Users create/deactivate/rehire, mixed Users/Groups bulk with typed request-local member references, Chromium GET/POST selected search equivalence and minimized complex member fields, partial commits/failOnErrors/version refusal, roster, escaped labels, WCAG/mobile, stale401/held-credential/new-session guards and permanent credential revocation.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
