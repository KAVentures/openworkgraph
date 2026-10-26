const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-decision-')),base='http://127.0.0.1:8791';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_collaboration_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));

 const api=async(url,body,who='alice',extra={})=>{const r=await fetch(base+url,{headers:{Authorization:'Bearer '+who,...(body?{'Content-Type':'application/json'}:{}),...extra},...(body?{method:'POST',body:JSON.stringify(body)}:{})});assert.ok(r.ok,await r.clone().text());return r.json();};
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/processes')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  const model={goal:'Review an authorized purchase',baseline_source:'user_declared',baseline_path:['Review','Accept'],activities:[{id:'choose',label:'Review',kind:'decision'},{id:'accept',label:'Accept'},{id:'escalate',label:'Escalate'}],transitions:[{source:'choose',target:'accept',rule_id:'small'},{source:'choose',target:'escalate',rule_id:'large'}],entry_activity_id:'choose',terminal_activity_ids:['accept','escalate'],business_rules:[{id:'small',text:'Small purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'lt',expected:100}]}},{id:'large',text:'Large purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'gte',expected:100}]}}]};
  const original=(await api('/v1/human/processes',{title:'Purchase design',process_model:model,user_confirmed:true,organization_sharing_confirmed:true})).process,baseURL='/v1/human/processes/'+original.workflow_id;
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const login=async who=>{await page.locator('#token').fill(who);await page.getByRole('button',{name:'Connect',exact:true}).click();await page.locator('#status').filter({hasText:'Loaded organization process inventory.'}).waitFor();};
  const open=async revision=>{await page.locator('#inventory').getByRole('button',{name:'Purchase design · r'+revision,exact:true}).click();await page.locator('#selectedTitle').filter({hasText:'revision '+revision}).waitFor();};
  await page.goto(base+'/processes');await login('alice');await open(1);
  await page.getByRole('button',{name:'Edit selected design',exact:true}).click();
  await page.getByLabel('Decision activity for new table',{exact:true}).selectOption('choose');
  await page.getByRole('button',{name:'Add decision table',exact:true}).click();
  const confirm=page.getByLabel('I confirm this exact table, referenced rules, source basis and effective dates decision_table_1',{exact:true});
  await page.getByLabel('Effective start decision_table_1',{exact:true}).fill('2026-01-01');
  await page.getByLabel('Exclusive effective end decision_table_1',{exact:true}).fill('2027-01-01');
  await page.getByLabel('Source document label decision_table_1',{exact:true}).fill('Purchase policy <script>not code</script>');
  await page.getByLabel('Source document SHA256 decision_table_1',{exact:true}).fill('a'.repeat(64));
  await confirm.check();await page.getByLabel('Source document label decision_table_1',{exact:true}).fill('Purchase policy');assert.equal(await confirm.isChecked(),false);
  await confirm.check();await page.getByLabel('Draft rule small',{exact:true}).fill('Small purchase amended');assert.equal(await confirm.isChecked(),false);
  await page.getByLabel('I confirm declared rule small',{exact:true}).check();await confirm.check();
  await page.getByRole('button',{name:'Review edited design',exact:true}).click();
  assert.match(await page.locator('#preview').textContent(),/decision_table_1 · choose · user confirmed/);
  assert.equal((await api(baseURL)).process.revision,1,'preview is not a model write');
  await page.locator('#reviewed').check();await page.locator('#sharing').check();
  await page.locator('#update').click();await page.locator('#status').filter({hasText:'New revision saved.'}).waitFor();
  const saved=(await api(baseURL)).process;assert.equal(saved.revision,2);assert.equal(saved.process_model.decision_tables[0].user_confirmed,true);assert.equal(saved.process_model.decision_tables[0].rows.length,2);
  assert.equal((await api(baseURL+'?revision=1')).process.content_sha256,original.content_sha256);
  const facts={as_of_date:'2026-10-26',cases:[{case_id:'case_1',facts:{amount:99}},{case_id:'case_2',facts:{amount:100}},{case_id:'case_3',facts:{}},{case_id:'case_4',facts:{amount:100},activity_ids:['choose','accept']}]};
  const runFacts=async data=>{
    await page.locator('#ruleFactsFile').setInputFiles({name:'facts.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(data))});
    assert.equal(await page.locator('#ruleFactsConsent').isChecked(),false);assert.equal(await page.locator('#checkRuleFacts').isDisabled(),true);
    await page.locator('#ruleFactsConsent').check();await page.locator('#checkRuleFacts').click();await page.locator('#ruleFactsResult h3').waitFor();
  };
  await runFacts(facts);const report=page.locator('#ruleFactsResult');
  assert.match(await report.textContent(),/unique match → accept/);assert.match(await report.textContent(),/unique match → escalate/);assert.match(await report.textContent(),/unknown/);assert.match(await report.textContent(),/proposed branch disagrees/);
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download minimized shared rule-check report',exact:true}).click()]);const artifact=JSON.parse(await fs.readFile(await download.path(),'utf8'));
  assert.equal(artifact.process_reference.content_sha256,saved.content_sha256);assert.equal(artifact.cases[1].decision_tables[0].selected_target_activity_id,'escalate');assert.equal(artifact.facts_retained,false);assert.equal(JSON.stringify(artifact).includes('amount'),false);
  await runFacts({...facts,as_of_date:'2027-01-01'});assert.match(await report.textContent(),/expired/);
  const withoutDate={cases:facts.cases};await runFacts(withoutDate);assert.match(await report.textContent(),/date required/);
  // Held old-identity facts must not restore reports after disconnect/tenant switch.
  let seen,release;const held=new Promise(r=>seen=r);
  await page.route('**/check-rules?*',async route=>{const response=await route.fetch();seen();await new Promise(r=>release=r);await route.fulfill({response});},{times:1});
  await page.locator('#ruleFactsFile').setInputFiles({name:'facts.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(facts))});await page.locator('#ruleFactsConsent').check();await page.locator('#checkRuleFacts').click();await held;
  await page.locator('#disconnect').click();await login('foreign');const stale=page.waitForResponse(r=>r.url().includes('/check-rules?'));release();await stale;assert.equal(await report.textContent(),'');assert.equal(await page.locator('#design').textContent(),'');
  await page.locator('#disconnect').click();await login('reader');await open(2);assert.equal(await page.locator('#revise').isDisabled(),true);assert.match(await page.locator('#design').textContent(),/Declared decision tables/);
  // A late identity lookup must neither restore Alice nor clear the new tenant.
  await page.locator('#disconnect').click();
  let identitySeen,identityRelease;const identityHeld=new Promise(r=>identitySeen=r);
  await page.route('**/v1/human/me',async route=>{const response=await route.fetch();identitySeen();await new Promise(r=>identityRelease=r);await route.fulfill({response});},{times:1});
  await page.locator('#token').fill('alice');await page.getByRole('button',{name:'Connect',exact:true}).click();await identityHeld;
  await page.locator('#disconnect').click();await login('foreign');const identityResponse=page.waitForResponse(r=>r.url().endsWith('/v1/human/me'));identityRelease();await identityResponse;
  assert.match(await page.locator('#identity').textContent(),/Organization elsewhere/);assert.equal(await page.locator('#inventory button').count(),0);
  await page.locator('#disconnect').click();await login('alice');await open(2);await page.getByRole('button',{name:'Edit selected design',exact:true}).click();
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.evaluate(source=>{const s=document.createElement('script');s.nonce=document.querySelector('script[nonce]').nonce;s.textContent=source;document.head.append(s);},axe);
  assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#preview'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.getByRole('button',{name:'Remove table decision_table_1',exact:true}).click();await page.getByRole('button',{name:'Review edited design',exact:true}).click();assert.doesNotMatch(await page.locator('#preview').textContent(),/Declared decision tables/);
  assert.deepEqual(errors,[]);console.log('Decision-table browser passed: explicit editing/confirmation reset, exact revision, boundary/unknown/expiry/date checks, minimized exports, held identity, reader isolation, WCAG/mobile and removal.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
