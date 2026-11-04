const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-rule-library-')),base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser;for(const pipe of [server.stdout,server.stderr])pipe.on('data',v=>logs=(logs+v).slice(-6000));
 try{
  let ready=false;for(let i=0;i<100;i++){try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
  await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
  const model={goal:'Review a purchase',baseline_source:'user_declared',baseline_path:['Review','Accept'],activities:[{id:'choose',label:'Review',kind:'decision'},{id:'accept',label:'Accept'},{id:'escalate',label:'Escalate'}],transitions:[{source:'choose',target:'accept',rule_id:'small'},{source:'choose',target:'escalate',rule_id:'large'}],entry_activity_id:'choose',terminal_activity_ids:['accept','escalate'],business_rules:[{id:'small',text:'Small purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'lt',expected:100}]}},{id:'large',text:'Large purchase',user_confirmed:true,predicate:{conditions:[{path:'/amount',operator:'gte',expected:100}]}}]};
  await page.evaluate(async model=>{await window.__owgAuthReady;const r=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Local purchase',process_model:model,user_confirmed:true})});if(!r.ok)throw new Error(await r.text());},model);
  await page.getByRole('button',{name:'Load saved designs',exact:true}).click();await page.getByRole('button',{name:'Local purchase · r1',exact:true}).click();
  const section=page.locator('#owgCatalogRuleLibrary');
  await section.getByRole('button',{name:'Load rule sets',exact:true}).click();
  await section.getByLabel('Rule set name',{exact:true}).fill('Purchasing');
  await section.getByLabel('Rule set source',{exact:true}).fill('Purchasing policy v3');
  await section.getByRole('button',{name:'Save design rules to library',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 1 of rule set Purchasing.'}).waitFor();
  await section.getByLabel('Library rule',{exact:true}).selectOption('small');
  await section.getByLabel('Design rule to link',{exact:true}).selectOption('small');
  await section.getByRole('button',{name:'Link rule from library',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Linked rule small to the library'}).waitFor();
  await page.locator('#owgCatalogReviewed').check();await page.getByRole('button',{name:'Save new revision',exact:true}).click();
  await page.locator('#owgCatalogStatus').filter({hasText:'Saved revision 2.'}).waitFor();
  const linked=await page.evaluate(async()=>{const list=await (await fetch('/v1/process-catalog')).json();const id=list.processes[0].workflow_id;return (await (await fetch('/v1/process-catalog/revision?workflow_id='+id)).json()).process_model.business_rules[0];});
  assert.equal(linked.user_confirmed,false);assert.equal(linked.library_ref.revision,1);
  const drift=page.locator('#owgCatalogRuleLibrary').getByRole('button',{name:'Show rule drift',exact:true});
  await drift.click();await page.locator('#owgCatalogRuleLibraryResult').getByText('0 drifted linked rules across 1 saved designs',{exact:true}).waitFor();
  await page.evaluate(async rule=>{const sets=await (await fetch('/v1/process-catalog/rule-library')).json();const s=sets.rule_sets[0];
    const r=await fetch('/v1/process-catalog/rule-library',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rule_set_id:s.rule_set_id,expected_revision:1,name:'Purchasing',rules:[rule]})});if(!r.ok)throw new Error(await r.text());},
    {id:'small',text:'Purchases under 50',predicate:{conditions:[{path:'/amount',operator:'lt',expected:50}]}});
  await page.locator('#owgCatalogRuleLibrary').getByRole('button',{name:'Show rule drift',exact:true}).click();
  const output=page.locator('#owgCatalogRuleLibraryResult');await output.getByText('1 drifted linked rules across 1 saved designs',{exact:true}).waitFor();
  assert.match(await output.textContent(),/rule small · linked to revision 1 \(latest 2\) · library has a newer version/);
  const axe=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');await page.addScriptTag({content:axe});assert.deepEqual(await page.evaluate(async()=>{const r=await axe.run(document.querySelector('#owgCatalogRuleLibrary'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>v.id);}),[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  assert.deepEqual(errors,[]);console.log('Rule library browser passed: save set, link unconfirmed copy, save revision, drift after library change, WCAG/mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
