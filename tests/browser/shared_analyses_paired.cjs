const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-paired-analyses-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',
  ['-m','uvicorn','shared_analyses_paired_app:app','--app-dir','tests/browser',
   '--host','127.0.0.1','--port','8787'],
  {env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;
 for(const s of [server.stdout,server.stderr])s.on('data',c=>logs=(logs+c).slice(-8000));
 try{
  let ready=false;
  for(let i=0;i<100;i++){
    if(server.exitCode!==null)throw Error(logs);
    try{if((await fetch(base+'/analyses')).ok){ready=true;break;}}catch{}
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  assert.ok(ready,logs);
  const snapshotFile=path.join(scratch,'paired-snapshot.json');
  const casesFile=path.join(scratch,'paired-cases.json');
  const snapshot=JSON.parse(await fs.readFile(snapshotFile,'utf8'));
  const cases=JSON.parse(await fs.readFile(casesFile,'utf8'));
  assert.equal(snapshot.level,'cases');assert.equal(snapshot.matching_cases,3);
  assert.equal(cases.cases.length,3);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?
      {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const owner=await browser.newPage(),viewer=await browser.newPage(),errors=[];
  for(const page of [owner,viewer])page.on('pageerror',e=>errors.push(e.message));
  const connect=async(page,token)=>{
    await page.goto(base+'/analyses');
    await page.locator('#token').fill(token);
    await page.locator('#connect').click();
    await page.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();
  };
  const check=async(page,who,url,method='GET',body)=>{
    return page.evaluate(async args=>{
      const r=await fetch(args.url,{method:args.method,headers:{Authorization:'Bearer '+args.who,
        ...(args.body?{'Content-Type':'application/json'}:{})},body:args.body?JSON.stringify(args.body):undefined});
      return {status:r.status,data:await r.json()};
    },{who,url,method,body});
  };
  await connect(owner,'alice');await connect(viewer,'bob');
  assert.equal(await owner.locator('#pairedSection').isHidden(),false);
  let pairedUploads=0;
  owner.on('request',req=>{if(req.method()==='POST'&&req.url().endsWith('/with-cases'))pairedUploads++;});
  await owner.locator('#file').setInputFiles(snapshotFile);
  await owner.locator('#preview table').waitFor();
  await owner.locator('#title').fill('Reviewed operational cases');
  const poisoned={...cases,cases:cases.cases.map((one,i)=>i?one:{...one,original_person:'DO_NOT_LEAK'})};
  await owner.locator('#pairedCaseFile').setInputFiles({
    name:'poison.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(poisoned))});
  await owner.locator('#status').filter({hasText:'Unexpected case fields cannot be uploaded.'}).waitFor();
  assert.equal(pairedUploads,0);
  const mismatched={...cases,cases:cases.cases.slice(0,2)};
  await owner.locator('#pairedCaseFile').setInputFiles({
    name:'mismatch.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(mismatched))});
  await owner.locator('#status').filter({hasText:'must match the reviewed aggregate'}).waitFor();
  assert.equal(pairedUploads,0);

  await owner.locator('#pairedCaseFile').setInputFiles(casesFile);
  await owner.locator('#pairedCasePreview details').first().waitFor();
  assert.equal(await owner.locator('#pairedCasePreview details').count(),3);
  assert.equal(await owner.locator('#publishPaired').isDisabled(),true);
  assert.equal((await owner.locator('#pairedCasePreview').textContent()).includes('private-person'),false);
  await owner.locator('#confirm').check();
  assert.equal(await owner.locator('#publishPaired').isDisabled(),true);
  await owner.locator('#pairedCaseConfirm').check();
  assert.equal(await owner.locator('#publishPaired').isDisabled(),false);
  const [reply]=await Promise.all([
    owner.waitForResponse(r=>r.url().endsWith('/with-cases')&&r.request().method()==='POST'),
    owner.locator('#publishPaired').click()
  ]);
  assert.equal(reply.status(),200,await reply.text());
  assert.equal(pairedUploads,1);
  const result=await reply.json();
  assert.equal(result.atomic_publication,true);
  assert.equal(result.case_viewer_grants_created,false);
  assert.equal(result.retained_source_log_uploaded,false);
  assert.equal(result.execution_authorized,false);
  const id=result.publication.view_id,uri=base+'/v1/human/analyses/'+id;
  await owner.locator('#status').filter({hasText:'published atomically'}).waitFor();
  assert.equal(await owner.locator('#pairedSection').isHidden(),false);
  assert.equal(await owner.locator('#publishPaired').textContent(),'Publish revised pair atomically');
  assert.equal((await check(owner,'alice',uri+'/case-data?revision=1')).status,200);
  assert.equal((await check(viewer,'bob',uri)).status,404);
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=1')).status,404);

  await owner.locator('#addMember').click();
  await owner.locator('#members input').fill('bob');
  await owner.locator('#accessConfirm').check();
  await owner.locator('#saveAccess').click();
  await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  assert.equal((await check(viewer,'bob',uri)).status,200);
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=1')).status,404);
  const caseInfo=await check(owner,'alice',uri+'/case-data/access?revision=1');
  assert.equal(caseInfo.status,200);
  const grant=await check(owner,'alice',uri+'/case-data/access','POST',{
    revision:1,content_sha256:caseInfo.data.content_sha256,
    expected_access_revision:caseInfo.data.access_revision,
    expected_parent_access_revision:caseInfo.data.parent_access_revision,
    members:[{actor_id:'bob',role:'viewer'}],user_confirmed:true
  });
  assert.equal(grant.status,200,JSON.stringify(grant.data));
  const reading=await check(viewer,'bob',uri+'/case-data?revision=1');
  assert.equal(reading.status,200,JSON.stringify(reading.data));
  assert.equal(reading.data.matching_cases,3);
  assert.equal(JSON.stringify(reading).includes('private-person'),false);
  assert.equal(JSON.stringify(reading).includes('private-001'),false);

  // Same owner can atomically publish a successor revision + new immutable
  // case dataset. The old explicit case grant never propagates to revision 2.
  await owner.locator('#file').setInputFiles(snapshotFile);
  await owner.locator('#preview table').waitFor();
  await owner.locator('#title').fill('Reviewed operational cases v2');
  await owner.locator('#pairedCaseFile').setInputFiles(casesFile);
  await owner.locator('#pairedCasePreview details').first().waitFor();
  await owner.locator('#confirm').check();
  await owner.locator('#pairedCaseConfirm').check();
  const [successorReply]=await Promise.all([
    owner.waitForResponse(r=>r.url().endsWith('/'+id+'/with-cases')&&r.request().method()==='POST'),
    owner.locator('#publishPaired').click()
  ]);
  assert.equal(successorReply.status(),200,await successorReply.text());
  const successor=await successorReply.json();
  assert.equal(successor.publication.view_id,id);
  assert.equal(successor.publication.revision,2);
  assert.equal(successor.case_data.revision,2);
  assert.equal(successor.case_viewer_grants_created,false);
  await owner.locator('#status').filter({hasText:'published atomically'}).waitFor();
  assert.equal((await check(viewer,'bob',uri+'?revision=2')).status,200);
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=1')).status,200);
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=2')).status,404);
  assert.equal((await check(owner,'alice',uri+'/case-data?revision=2')).status,200);
  assert.equal((await check(owner,'alice',uri+'?revision=1')).data.title,'Reviewed operational cases');
  assert.equal((await check(owner,'alice',uri+'?revision=2')).data.title,'Reviewed operational cases v2');

  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
  await owner.evaluate(source=>{
    const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;
    s.textContent=source;document.head.append(s);
  },axe);
  const audit=await owner.evaluate(()=>axe.run(document,{
    runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}
  }));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await owner.setViewportSize({width:390,height:844});
  assert.ok(await owner.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  await owner.locator('#deleteConfirm').check();
  await owner.locator('#delete').click();
  await owner.locator('#status').filter({hasText:'Publication, revisions and member access deleted.'}).waitFor();
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=1')).status,404);
  assert.equal((await check(viewer,'bob',uri+'/case-data?revision=2')).status,404);
  assert.equal((await check(owner,'alice',uri)).status,404);
  await owner.locator('#disconnect').click();
  assert.equal(await owner.locator('#pairedCasePreview').textContent(),'');
  assert.equal(await owner.locator('#token').inputValue(),'');
  assert.deepEqual(errors,[]);
  console.log('Atomic paired sharing passed: real local export, initial + successor atomic publication, independent revision-specific case grants, delete, WCAG/mobile.');
 }catch(e){throw Error(e.stack+'\nServer:\n'+logs);}
 finally{
  if(browser)await browser.close();
  server.kill('SIGTERM');await Promise.race([
    new Promise(resolve=>server.once('exit',resolve)),
    new Promise(resolve=>setTimeout(resolve,5000))
  ]);
  if(server.exitCode===null)server.kill('SIGKILL');
  await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
