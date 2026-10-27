const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-dependencies-')),base='http://127.0.0.1:8791';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_collaboration_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const stream of [server.stdout,server.stderr])stream.on('data',v=>logs=(logs+v).slice(-6000));
 const api=async(url,body,who='alice')=>{const r=await fetch(base+url,{headers:{Authorization:'Bearer '+who,...(body?{'Content-Type':'application/json'}:{})},...(body?{method:'POST',body:JSON.stringify(body)}:{})});assert.ok(r.ok,await r.clone().text());return r.json();};
 const pin=p=>({workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
 const model={goal:'Resolve a selected case',baseline_source:'user_declared',baseline_path:['Review','Done'],activities:[{id:'review',label:'Review'},{id:'done',label:'Done',kind:'end'}],transitions:[{source:'review',target:'done'}],entry_activity_id:'review',terminal_activity_ids:['done']};
 const deps=(root,targets,revision=0,access=2)=>({expected_revision:revision,expected_access_revision:access,user_confirmed:true,organization_sharing_confirmed:true,dependencies:{organization_id:'acme',root_process:pin(root),links:targets.map((p,i)=>({id:'fixture_'+i,prerequisite:pin(p),relationship:'uses_output',reason:'Explicit fixture relationship'}))}});
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  const records=[];for(const title of ['Customer activation','Account setup','Identity policy','Billing profile']){
   const p=(await api('/v1/human/processes',{title,process_model:model,user_confirmed:true,organization_sharing_confirmed:true})).process;records.push(p);
   await api('/v1/human/processes/'+p.workflow_id+'/access',{expected_access_revision:1,members:[{actor_id:'bob',role:'reviewer'}],user_confirmed:true});
   await api('/v1/human/processes/'+p.workflow_id+'/reviews',{revision:1,content_sha256:p.content_sha256,decision:'reviewed_design',user_confirmed:true},'bob');
  }
  const [a,b,c,d]=records,route=p=>'/v1/human/processes/'+p.workflow_id;
  await api(route(c)+'/dependencies',deps(c,[]));await api(route(d)+'/dependencies',deps(d,[b]));
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const login=async who=>{await page.locator('#token').fill(who);await page.getByRole('button',{name:'Connect',exact:true}).click();await page.locator('#status').filter({hasText:'Loaded organization process inventory.'}).waitFor();};
  const open=async(title,revision=1)=>{await page.locator('#inventory').getByRole('button',{name:title+' · r'+revision,exact:true}).click();await page.locator('#selectedTitle').filter({hasText:'revision '+revision}).waitFor();await page.locator('#dependencyLoad').click();await page.locator('#dependencyEditor').waitFor();};
  const add=async target=>{await page.locator('#dependencyChoices').click();await page.locator('#dependencyChoice option[value="'+target.workflow_id+'"]').waitFor({state:'attached'});await page.locator('#dependencyChoice').selectOption(target.workflow_id);await page.locator('#dependencyAdd').click();};
  const save=async()=>{await page.locator('#dependencyConfirm').check();await page.locator('#dependencySave').click();await page.locator('#dependencyStatus').filter({hasText:'Dependencies saved.'}).waitFor();assert.equal(await page.locator('#dependencyConfirm').isChecked(),false);};
  const loadGraph=async()=>{await page.locator('#dependencyGraphLoad').click();await page.locator('#dependencyGraphResult svg').waitFor();};
  await page.goto(base+'/processes');await login('alice');await open('Account setup');await add(c);await save();
  await open('Customer activation');await add(b);
  const reason=page.getByLabel('Reason dependency_1',{exact:true});await page.locator('#dependencyConfirm').check();await reason.fill('Uses account output <script>not code</script>');assert.equal(await page.locator('#dependencyConfirm').isChecked(),false);await save();
  const original=(await api(route(a)+'/dependencies')).artifact;assert.equal(original.revision,1);
  await loadGraph();assert.match(await page.locator('#dependencyGraphStatus').textContent(),/current declared basis/);
  await page.locator('#dependencyFocus').selectOption(c.workflow_id);await page.locator('#dependencyInspect').click();
  const consumers=page.locator('#dependencyGraphResult section').filter({hasText:'Potential consumers to review'});await consumers.waitFor();
  assert.match(await consumers.textContent(),/Customer activation · distance 2/);assert.match(await consumers.textContent(),/Account setup · distance 1/);assert.match(await consumers.textContent(),/Billing profile · distance 2/);
  const node=page.locator('svg').getByRole('button',{name:'Inspect impact of Account setup',exact:true});await node.focus();await node.press('Enter');await page.waitForFunction(id=>document.querySelector('#dependencyFocus').value===id,b.workflow_id);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#dependencyGraphExport').click()]);const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(report.impact.focus,b.workflow_id);assert.equal(report.graph.edges.length,3);assert.equal(report.current_basis,true);assert.equal(report.business_dependency_completeness_verified,false);assert.equal(JSON.stringify(report).includes('<script>'),false);
  // Drafts clear sharing and graph/export state; exact history retains the earlier reason.
  await reason.fill('Updated relationship explanation');assert.equal(await page.locator('#dependencyExport').isDisabled(),true);assert.equal(await page.locator('#dependencyGraphResult').textContent(),'');await save();
  await page.locator('#dependencyRevision').selectOption('1');await page.locator('#dependencyHistory').click();await page.locator('#dependencyStatus').filter({hasText:'Opened dependency revision 1.'}).waitFor();
  assert.equal(await page.locator('#dependencyConfirm').isDisabled(),true);
  const [manifestDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#dependencyExport').click()]);const manifest=JSON.parse(await fs.readFile(await manifestDownload.path(),'utf8'));assert.deepEqual(manifest.artifact,original);
  // A cycle cannot be saved even when all individually selected models are valid.
  await open('Identity policy');await add(a);await page.locator('#dependencyConfirm').check();await page.locator('#dependencySave').click();await page.locator('#dependencyStatus').filter({hasText:'cycle'}).waitFor();assert.equal((await api(route(c)+'/dependencies')).artifact.revision,1);
  await page.locator('#dependencyLoad').click();await page.locator('#dependencyStatus').filter({hasText:'Opened dependency revision 1.'}).waitFor();await loadGraph();
  // An external actor's model update invalidates a cached report on fresh export.
  const revised=(await api(route(b),{title:'Account setup revised',process_model:model,expected_revision:1,user_confirmed:true,organization_sharing_confirmed:true})).process;
  await page.locator('#dependencyGraphExport').click();await page.locator('#dependencyGraphStatus').filter({hasText:'changed; refresh before exporting'}).waitFor();assert.equal(await page.locator('#dependencyGraphResult').textContent(),'');
  await loadGraph();assert.match(await page.locator('#dependencyGraphStatus').textContent(),/needs reassessment/);
  await page.locator('#dependencyFocus').selectOption(b.workflow_id);await page.locator('#dependencyInspect').click();await page.locator('#dependencyGraphResult section').filter({hasText:'Potential consumers to review'}).waitFor();
  assert.match(await page.locator('#dependencyGraphResult').textContent(),/pins need reassessment/);
  // Refresh each exact saved basis explicitly; reviews do not propagate.
  await api(route(b)+'/dependencies',deps(revised,[c],1));await api(route(d)+'/dependencies',deps(d,[revised],1));
  await page.locator('#disconnect').click();await login('alice');await open('Customer activation');await page.getByRole('button',{name:'Refresh prerequisite dependency_1',exact:true}).click();await page.locator('#dependencyStatus').filter({hasText:'Reference refreshed in the draft'}).waitFor();await save();
  await loadGraph();assert.match(await page.locator('#dependencyGraphStatus').textContent(),/needs reassessment/);
  await api(route(b)+'/reviews',{revision:2,content_sha256:revised.content_sha256,decision:'reviewed_design',user_confirmed:true},'bob');await loadGraph();assert.match(await page.locator('#dependencyGraphStatus').textContent(),/current declared basis/);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.evaluate(source=>{const script=document.createElement('script');script.nonce=document.querySelector('script[nonce]').nonce;script.textContent=source;document.head.append(script);},axe);
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#dependencyInventory'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  // Archived/deleted prerequisites remain visible as unavailable declarations.
  await api(route(c)+'/archive',{expected_access_revision:2,archived:true,user_confirmed:true});await api(route(c)+'/forget',{expected_revision:1,expected_access_revision:3,user_confirmed:true});await loadGraph();
  await page.locator('#dependencyFocus').selectOption(c.workflow_id);await page.locator('#dependencyInspect').click();await page.locator('#dependencyGraphResult section').filter({hasText:'Potential consumers to review'}).waitFor();assert.match(await page.locator('#dependencyGraphResult').textContent(),/unavailable/);
  // Fresh root erasure removes only its declaration history.
  page.once('dialog',dialog=>dialog.accept());await page.locator('#dependencyDelete').click();await page.locator('#dependencyStatus').filter({hasText:'Dependency history erased.'}).waitFor();assert.equal((await api(route(a)+'/dependencies')).artifact,null);assert.ok((await api(route(b)+'/dependencies')).artifact);
  await page.locator('#disconnect').click();await login('reader');await open('Account setup revised',2);assert.equal(await page.locator('#dependencyConfirm').isDisabled(),true);assert.equal(await page.locator('#dependencyDelete').isDisabled(),true);
  // A held old-identity graph response cannot repopulate a new tenant.
  await page.locator('#disconnect').click();await login('alice');let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/v1/human/process-dependency-graph',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#dependencyGraphLoad').click();await held;await page.locator('#disconnect').click();await login('foreign');const stale=page.waitForResponse(r=>r.url().endsWith('/v1/human/process-dependency-graph'));release();await stale;
  assert.equal(await page.locator('#dependencyGraphResult').textContent(),'');assert.equal(await page.locator('#dependencyDetail').textContent(),'');
  await page.locator('#dependencyGraphLoad').click();await page.locator('#dependencyGraphStatus').filter({hasText:'empty'}).waitFor();assert.equal(await page.locator('#dependencyGraphResult svg g').count(),0);
  assert.deepEqual(errors,[]);console.log('Process dependencies passed: explicit versioned links, current reviews, global graph/keyboard impact, exact history, cycle refusal, drift/fresh export, erasure, reader/tenant/held identity, WCAG and mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
