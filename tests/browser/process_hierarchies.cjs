const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-hierarchy-')),base='http://127.0.0.1:8791';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_collaboration_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 const api=async(url,body,who='alice')=>{const r=await fetch(base+url,{headers:{Authorization:'Bearer '+who,...(body?{'Content-Type':'application/json'}:{})},...(body?{method:'POST',body:JSON.stringify(body)}:{})});assert.ok(r.ok,await r.clone().text());return r.json();};
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  const example=JSON.parse(await fs.readFile(path.join(scratch,'process.json'),'utf8'));
  const records=[];for(const title of ['Root 雪','Child B','Branch C','Reusable D','Leaf E'])records.push((await api('/v1/human/processes',{title,process_model:example.process_model,user_confirmed:true,organization_sharing_confirmed:true})).process);
  const [root,b,c,d,e]=records,rootURL='/v1/human/processes/'+root.workflow_id;
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const login=async who=>{await page.locator('#token').fill(who);await page.getByRole('button',{name:'Connect',exact:true}).click();await page.locator('#status').filter({hasText:'Loaded organization process inventory.'}).waitFor();};
  const selectRoot=async()=>{await page.locator('#inventory').getByRole('button',{name:'Root 雪 · r1',exact:true}).click();await page.locator('#selectedTitle').filter({hasText:'Root 雪'}).waitFor();};
  const load=async()=>{await page.locator('#hierarchyLoad').click();await page.locator('#hierarchyEditor').waitFor();};
  const status=()=>page.locator('#hierarchyStatus');
  await page.goto(base+'/processes');await login('alice');await selectRoot();await load();
  assert.ok(await page.locator('#hierarchySave').isDisabled());
  await page.locator('#hierarchyChoices').click();await page.locator('#hierarchyChoice option').nth(4).waitFor({state:'attached'});
  const add=async(parent,p,count)=>{await page.locator('#hierarchyParent').selectOption(parent);await page.locator('#hierarchyChoice').selectOption(p.workflow_id);await page.locator('#hierarchyAdd').click();assert.equal(await page.locator('#hierarchyTree li').count(),count);};
  await add('root',b,2);await add('root',c,3);await add('n1',d,4);await add('n3',e,5);await add('n2',d,6);
  await page.locator('#hierarchyParent').selectOption('n4');await page.locator('#hierarchyChoice').selectOption(c.workflow_id);await page.locator('#hierarchyAdd').click();await status().filter({hasText:'four total levels'}).waitFor();assert.equal(await page.locator('#hierarchyTree li').count(),6);
  await page.locator('#hierarchyParent').selectOption('n3');await page.locator('#hierarchyChoice').selectOption(root.workflow_id);await page.locator('#hierarchyAdd').click();await status().filter({hasText:'own ancestry'}).waitFor();
  await page.locator('#hierarchyTitle').fill('Support landscape 雪');await page.locator('#hierarchyCategory').fill('Customer service');await page.locator('#hierarchyCapability').fill('Case management');
  await page.locator('#hierarchyConfirm').check();await page.locator('#hierarchySave').click();await status().filter({hasText:'Repository map saved.'}).waitFor();
  const [firstDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#hierarchyExport').click()]);
  const first=JSON.parse(await fs.readFile(await firstDownload.path(),'utf8'));assert.equal(first.artifact.hierarchy.nodes.length,6);assert.equal(first.unique_referenced_processes,5);assert.equal(first.artifact.revision,1);assert.equal(first.execution_authorized,false);
  assert.deepEqual((await api(rootURL)).process,root,'map metadata did not change model/hash/review');
  await page.locator('#hierarchyInventory').click();await page.locator('#repositoryInventory').getByText(/Support landscape 雪/).waitFor();
  await page.locator('#hierarchyFilter').fill('not a category');assert.equal(await page.locator('#repositoryInventory #hierarchyFilter').count(),1);assert.equal(await page.locator('#repositoryInventory').getByRole('button',{name:'Open repository root',exact:true}).count(),0);
  await page.locator('#hierarchyFilter').fill('Customer service');assert.equal(await page.locator('#repositoryInventory').getByRole('button',{name:'Open repository root',exact:true}).count(),1);
  // Current child changes never silently replace the historical title or digest.
  await api('/v1/human/processes/'+b.workflow_id,{title:'Child B latest',process_model:b.process_model,expected_revision:1,user_confirmed:true,organization_sharing_confirmed:true});
  await page.locator('#hierarchyLoad').click();await status().filter({hasText:'Some references have changed'}).waitFor();
  assert.match(await page.locator('#hierarchyTree').textContent(),/Child B · pinned r1 · revision_changed/);
  await page.getByRole('button',{name:'Refresh reference n1',exact:true}).click();await status().filter({hasText:'Updated n1 to r2'}).waitFor();assert.ok(await page.locator('#hierarchyExport').isDisabled());
  await page.locator('#hierarchyConfirm').check();await page.locator('#hierarchySave').click();await status().filter({hasText:'Repository map saved.'}).waitFor();
  await page.locator('#hierarchyRevision').selectOption('1');await page.locator('#hierarchyHistory').click();await status().filter({hasText:'Opened map revision 1.'}).waitFor();assert.ok(await page.locator('#hierarchySave').isDisabled());
  const [oldDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#hierarchyExport').click()]);assert.deepEqual(JSON.parse(await fs.readFile(await oldDownload.path(),'utf8')).artifact,first.artifact);
  await page.locator('#hierarchyRevision').selectOption('2');await page.locator('#hierarchyHistory').click();await status().filter({hasText:'Opened map revision 2.'}).waitFor();
  // Move/remove applies to complete branches and is an unsaved draft until confirmed.
  await page.locator('li[data-node-id="n3"] > select').selectOption('n2');await page.getByRole('button',{name:'Move n3',exact:true}).click();await status().filter({hasText:'already under that parent'}).waitFor();
  await page.getByRole('button',{name:'Remove branch n3',exact:true}).click();assert.equal(await page.locator('#hierarchyTree li').count(),4);assert.ok(await page.locator('#hierarchyExport').isDisabled());
  await page.locator('#hierarchyLoad').click();await status().filter({hasText:'Opened map revision 2.'}).waitFor();assert.equal(await page.locator('#hierarchyTree li').count(),6);
  await api('/v1/human/processes/'+e.workflow_id+'/forget',{expected_revision:1,expected_access_revision:1,user_confirmed:true});
  await page.locator('#hierarchyLoad').click();await status().filter({hasText:'Some references have changed'}).waitFor();assert.match(await page.locator('li[data-node-id="n4"] > span').textContent(),/unavailable/);assert.doesNotMatch(await page.locator('li[data-node-id="n4"] > span').textContent(),/Leaf E/);
  // Reader can inspect/export exact definitions but cannot edit metadata or acquire model permissions.
  await page.locator('#disconnect').click();await login('reader');await selectRoot();await load();
  assert.ok(await page.locator('#hierarchySave').isDisabled());assert.ok(await page.locator('#hierarchyDelete').isDisabled());assert.equal(await page.locator('#hierarchyAdd').count(),0);
  const [readerDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#hierarchyExport').click()]);assert.equal(JSON.parse(await fs.readFile(await readerDownload.path(),'utf8')).artifact.revision,2);
  // A revoked editor cannot save from an already-open draft, and the page clears its protected state.
  await api(rootURL+'/access',{expected_access_revision:1,members:[{actor_id:'bob',role:'editor'}],user_confirmed:true});
  await page.locator('#disconnect').click();await login('bob');await selectRoot();await load();
  await page.locator('#hierarchyTitle').fill('Revoked edit');await page.locator('#hierarchyConfirm').check();
  await api(rootURL+'/access',{expected_access_revision:2,members:[],user_confirmed:true});
  await page.locator('#hierarchySave').click();await page.locator('#status').filter({hasText:'Repository access changed'}).waitFor();
  assert.equal(await page.locator('#hierarchyTree').count(),0);assert.ok(await page.locator('#selected').isHidden());
  await login('reader');await selectRoot();await load();
  // Held old-identity detail cannot restore names/drafts after disconnect and another tenant signs in.
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/hierarchy',async route=>{if(route.request().method()!=='GET')return route.continue();const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#hierarchyLoad').click();await held;await page.locator('#disconnect').click();await login('foreign');
  const stale=page.waitForResponse(r=>r.url().endsWith('/hierarchy'));release();await stale;
  assert.equal(await page.locator('#hierarchyTree').count(),0);assert.equal(await page.locator('#hierarchyTitle').count(),0);assert.equal(await page.locator('#repositoryInventory').getByRole('button',{name:'Open repository root',exact:true}).count(),0);
  await page.locator('#disconnect').click();await login('alice');await selectRoot();await load();
  // Open the exact pinned child from the nested hierarchy.
  await page.locator('li[data-node-id="n1"] > button').first().click();await page.locator('#selectedTitle').filter({hasText:'Child B latest · revision 2'}).waitFor();assert.equal(await page.locator('#hierarchyTree').count(),0);
  await selectRoot();await load();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);
  const violations=await page.evaluate(async()=>{const result=await axe.run(document.querySelector('#repositoryDetail'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return result.violations.map(v=>({id:v.id,nodes:v.nodes.length}));});assert.deepEqual(violations,[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  page.once('dialog',dialog=>dialog.accept());await page.locator('#hierarchyDelete').click();await status().filter({hasText:'Map history erased.'}).waitFor();assert.equal((await api(rootURL+'/hierarchy')).latest_revision,0);assert.deepEqual((await api(rootURL)).process,root);
  assert.deepEqual(errors,[]);console.log('Repository hierarchy browser passed: four levels/reuse/cycle/depth, explicit sharing, pinned history/export, drift and deletion, branch edits, reader/tenant/held identity, navigation, erasure, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
