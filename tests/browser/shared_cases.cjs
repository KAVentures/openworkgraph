const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),{spawn}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-case-data-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','shared_cases_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});let logs='',browser;
 for(const p of [server.stdout,server.stderr])p.on('data',c=>logs=(logs+c).slice(-8000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw new Error(logs);try{if((await fetch(base+'/analyses')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const owner=await browser.newPage({acceptDownloads:true}),viewer=await browser.newPage({acceptDownloads:true}),errors=[];for(const p of [owner,viewer])p.on('pageerror',e=>errors.push(e.message));
  async function connect(p,actor){await p.goto(base+'/analyses');await p.locator('#token').fill(actor);await p.locator('#connect').click();await p.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();}
  await connect(owner,'alice');await connect(viewer,'bob');
  await owner.locator('#file').setInputFiles(path.join(scratch,'aggregate.json'));await owner.locator('#preview table').waitFor();await owner.locator('#title').fill('Shared case cohort');await owner.locator('#confirm').check();
  const [made]=await Promise.all([owner.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/v1/human/analyses')),owner.locator('#publish').click()]);const wid=(await made.json()).view_id;
  await owner.locator('#caseFile').waitFor();let uploads=0;owner.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/case-data'))uploads++;});
  const doc=JSON.parse(await fs.readFile(path.join(scratch,'cases.json'),'utf8'));
  await owner.locator('#caseFile').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...doc,people:['private']}))});
  await owner.locator('#caseWorkspace').getByRole('status').filter({hasText:'cannot be uploaded'}).waitFor();assert.equal(uploads,0);
  await owner.locator('#caseFile').setInputFiles(path.join(scratch,'cases.json'));await owner.locator('#caseConfirm').check();await owner.locator('#attachCaseData').click();
  await owner.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case data loaded'}).waitFor();
  await owner.locator('#addMember').click();await owner.locator('#members input').fill('bob');await owner.locator('#accessConfirm').check();await owner.locator('#saveAccess').click();await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory button').filter({hasText:'Shared case cohort'}).click();await viewer.locator('#table table').waitFor();
  await viewer.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case dataset not found'}).waitFor();
  await owner.locator('#addCaseViewer').click();await owner.getByLabel('Case viewer actor ID').fill('bob');await owner.locator('#caseAccessConfirm').check();await owner.locator('#saveCaseAccess').click();await owner.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case access updated'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory button').filter({hasText:'Shared case cohort'}).click();await viewer.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case data loaded'}).waitFor();
  await viewer.locator('#caseWorkspace button').filter({hasText:/^C1 ·/}).click();await viewer.locator('#caseWorkspace h3').filter({hasText:'Case C1'}).waitFor();
  await viewer.locator('#caseActivity').selectOption('A1');await viewer.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case data loaded'}).waitFor();
  const [download]=await Promise.all([viewer.waitForEvent('download'),viewer.locator('#exportCaseData').click()]);assert.deepEqual(JSON.parse(await fs.readFile(await download.path(),'utf8')).dataset,doc);
  // An authorized old case response must not repopulate a new identity's page.
  let seen,release;const held=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Held case request not reached')),15000);seen=()=>{clearTimeout(timer);resolve();};});
  await viewer.route(url=>url.pathname==='/v1/human/analyses/'+wid+'/case-data/cases/C1',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await viewer.locator('#caseWorkspace button').filter({hasText:/^C1 ·/}).click();await held;
  await viewer.locator('#disconnect').click();await viewer.locator('#token').fill('read-only');await viewer.locator('#connect').click();await viewer.locator('#status').filter({hasText:'Connected to authorized analyses.'}).waitFor();
  const old=viewer.waitForResponse(r=>new URL(r.url()).pathname==='/v1/human/analyses/'+wid+'/case-data/cases/C1');release();await old;await viewer.waitForTimeout(50);
  assert.equal(await viewer.locator('#caseWorkspace').isHidden(),true);assert.equal(await viewer.locator('#caseWorkspace ol').count(),0);
  await connect(viewer,'bob');await viewer.locator('#inventory button').filter({hasText:'Shared case cohort'}).click();await viewer.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case data loaded'}).waitFor();
  // Any parent ACL change invalidates the separate case grant even if Bob stays a member.
  await owner.locator('#accessConfirm').check();await owner.locator('#saveAccess').click();await owner.locator('#status').filter({hasText:'Access updated;'}).waitFor();
  await viewer.locator('#refresh').click();await viewer.locator('#inventory button').filter({hasText:'Shared case cohort'}).click();await viewer.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case dataset not found'}).waitFor();
  assert.equal(await viewer.locator('#caseWorkspace ol').count(),0);assert.equal((await viewer.request.get(base+'/v1/human/analyses/'+wid+'/case-data/export?revision=1',{headers:{Authorization:'Bearer bob'}})).status(),404);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await owner.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);assert.deepEqual((await owner.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}))).violations,[]);
  await owner.setViewportSize({width:390,height:844});assert(await owner.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await owner.locator('#caseDeleteConfirm').check();await owner.locator('#deleteCaseData').click();await owner.locator('#caseWorkspace').getByRole('status').filter({hasText:'Case data and grants deleted'}).waitFor();assert.equal(await owner.locator('#table table').count(),1);
  await owner.locator('#disconnect').click();assert.equal(await owner.locator('#caseWorkspace').isHidden(),true);assert.equal(await owner.locator('#caseWorkspace ol').count(),0);assert.deepEqual(errors,[]);
  console.log('Shared case data acceptance passed: actual paired local export, hidden fields refused before transfer, exact aggregate attachment, separate grants, coded timeline/filter/export, parent-ACL invalidation, deletion preserving aggregate, clear, WCAG/mobile.');
 }catch(e){throw new Error(e.stack+'\nServer:\n'+logs);}finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
