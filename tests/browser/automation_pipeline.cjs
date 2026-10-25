const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: add an idea → assess → approve with a note → implemented, CSV export, stale-edit refusal.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pipeline-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const context=await browser.newContext({acceptDownloads:true});
   const page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgPipelineCard').waitFor();
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();

   await page.locator('#owgPipelineTitle').fill('Copy approved prices into renewals');
   await page.locator('#owgPipelineSource').selectOption('process_catalog');await page.locator('#owgPipelineReference').fill('Renewals r3');
   await page.locator('#owgPipelineAdd').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Idea added.'}).waitFor();
   await page.getByRole('button',{name:'Move to Assessing'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   assert.equal(await page.getByRole('button',{name:'Move to Approved'}).count(),1);
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'assess the idea before approving it'}).waitFor();

   for(const key of ['rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate'])
     await page.locator('#owgPipelineQ_'+key).selectOption('4');
   await page.locator('#owgPipelineCases').fill('300');await page.locator('#owgPipelineMinutes').fill('10');await page.locator('#owgPipelineShare').fill('50');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Assessment saved.'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Ease 75\/100 · 25 modeled hours per month · Quick win/);
   assert.match(await page.locator('#owgPipelineList').textContent(),/Quick win/);

   // A second tab edits the same idea; the stale page must be refused, not overwrite it.
   const ideaId=(await (await page.evaluate(async()=>{const r=await fetch('/v1/automation-pipeline');return r.json();})).ideas[0]).idea_id;
   await page.evaluate(async id=>{const r=await fetch('/v1/automation-pipeline/'+id);const idea=await r.json();
     await fetch('/v1/automation-pipeline/'+id+'/transition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({to:'on_hold',expected_revision:idea.revision})});},ideaId);
   await page.locator('#owgPipelineNote').fill('Owner agreed');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'reload it before editing'}).waitFor();
   await page.getByRole('button',{name:'Open idea Copy approved prices into renewals'}).click();
   await page.getByRole('button',{name:'Move to Assessing'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   await page.locator('#owgPipelineNote').fill('Owner agreed');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   const history=await page.locator('#owgPipelineDetail ol').textContent();
   assert.match(history,/Idea.*Assessing.*On hold.*Assessing.*Approved.*Owner agreed/s);
   assert.match(await page.locator('#owgPipelineCard').textContent(),/nothing is run, built or sent/);

   const [csv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPipelineCsv').click()]);
   const text=await fs.readFile(await csv.path(),'utf8');
   assert.match(text,/^idea_id,title,state,/);assert.match(text,/Copy approved prices into renewals,approved,process_catalog,Renewals r3,75,25\.0,quick_win/);

   // Reassessment must invalidate the old approval and prevent implementation
   // until the new assumptions have been reviewed. Inspect both saved bases.
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Saving an assessment clears this approval/);
   await page.locator('#owgPipelineMinutes').fill('1');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Approval cleared; review the new assessment.'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/State: Assessing/);
   assert.equal(await page.getByRole('button',{name:'Move to Implemented'}).count(),0);
   const bases=page.locator('#owgPipelineDetail ol details');
   await bases.last().locator('summary').click();
   assert.match(await bases.last().textContent(),/Saved assessment: 300 cases\/month × 1 minutes × 50% eligible/);
   assert.match(await bases.last().textContent(),/Previous assessment: 300 cases\/month × 10 minutes × 50% eligible/);
   await page.locator('#owgPipelineNote').fill('Re-reviewed lower estimate');
   await page.getByRole('button',{name:'Move to Approved'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   await page.getByRole('button',{name:'Move to Implemented'}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Implemented.'}).waitFor();
   assert.equal(await page.locator('#owgPipelineAssess').count(),0);
   const implemented=await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id)).json(),ideaId);
   assert.equal(implemented.execution_authorized,false);
   assert.equal(implemented.history.filter(h=>h.state==='approved').at(-1).assessment.minutes_per_case,1);

   await page.locator('#owgPipelineFilter').selectOption('rejected');
   await page.locator('#owgPipelineList').getByText('No ideas in this view.').waitFor();
   await page.locator('#owgPipelineFilter').selectOption('');

   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'pipeline card WCAG violations');

   page.once('dialog',dialog=>dialog.accept());await page.locator('#owgPipelineDelete').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Idea deleted.'}).waitFor();
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();


   // Catalog → idea → pinned source navigation. Unsaved editor changes must
   // never become the source of a saved-process opportunity.
   const saved=await page.evaluate(async()=>{
     const model={goal:'Review the selected renewal',baseline_source:'user_declared',baseline_coverage:'user_declared_design',baseline_path:['Review'],
       activities:[{id:'review',label:'Review'}],transitions:[],entry_activity_id:'review',terminal_activity_ids:['review']};
     return (await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:'renewal_review',title:'Saved renewal review',process_model:model,user_confirmed:true,expected_revision:0})})).json();
   });
   await page.getByRole('button',{name:'Load saved designs',exact:true}).click();
   await page.locator('#owgCatalogList').getByRole('button',{name:'Saved renewal review · r1',exact:true}).click();
   await page.getByRole('textbox',{name:'Business goal',exact:true}).fill('UNSAVED goal must stay outside the idea');
   await page.getByRole('button',{name:'Add saved revision to automation pipeline',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Current saved revision'}).waitFor();
   assert.match(await page.locator('#owgPipelineDetail').textContent(),/Review the selected renewal/);
   assert.doesNotMatch(await page.locator('#owgPipelineDetail').textContent(),/UNSAVED/);
   const linked=(await page.evaluate(async()=> (await fetch('/v1/automation-pipeline')).json())).ideas[0];
   assert.deepEqual(linked.process_reference,{workflow_id:saved.workflow_id,revision:1,content_sha256:saved.content_sha256});
   // Reopen exactly r1 (explicitly discard the unsaved editor draft).
   page.once('dialog',dialog=>dialog.accept());
   await page.getByRole('button',{name:'Open linked saved process',exact:true}).click();
   await page.locator('#owgCatalogStatus').filter({hasText:'Opened immutable revision 1.'}).waitFor();
   assert.equal(await page.getByRole('textbox',{name:'Business goal',exact:true}).inputValue(),'Review the selected renewal');
   await page.getByRole('button',{name:'Move to Assessing',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Assessing.'}).waitFor();
   for(const key of ['rules_based','structured_inputs','process_stability','digital_inputs','low_exception_rate'])
     await page.locator('#owgPipelineQ_'+key).selectOption('4');
   await page.locator('#owgPipelineCases').fill('300');await page.locator('#owgPipelineMinutes').fill('10');await page.locator('#owgPipelineShare').fill('50');
   await page.locator('#owgPipelineAssess').click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Assessment saved.'}).waitFor();
   await page.getByRole('button',{name:'Move to Approved',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'Moved to Approved.'}).waitFor();
   await page.evaluate(async record=>{
     await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:record.workflow_id,title:record.title,process_model:{...record.process_model,goal:'Changed renewal policy'},expected_revision:1,user_confirmed:true})});
   },saved);
   await page.getByRole('button',{name:'Refresh process status',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Process changed since this idea was created'}).waitFor();
   await page.getByRole('button',{name:'Move to Implemented',exact:true}).click();
   await page.locator('#owgPipelineStatus').filter({hasText:'linked process changed or is unavailable'}).waitFor();
   assert.equal((await page.evaluate(async id=>(await fetch('/v1/automation-pipeline/'+id)).json(),linked.idea_id)).state,'approved');
   const linkedAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(linkedAudit.violations.map(v=>v.id),[]);
   await page.evaluate(async()=>{
     await fetch('/v1/process-catalog/forget',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({workflow_id:'renewal_review',user_confirmed:true})});
   });
   await page.getByRole('button',{name:'Refresh process status',exact:true}).click();
   await page.locator('#owgPipelineProcessStatus').filter({hasText:'Linked revision is unavailable'}).waitFor();
   assert(await page.getByRole('button',{name:'Open linked saved process',exact:true}).isDisabled());
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   assert.deepEqual(errors,[]);
   console.log('Automation pipeline acceptance passed: idea → assessment gate → stale-edit refusal → approval → reassessment clears approval → fresh review → implemented, assessment snapshots, CSV export, filters, catalog pin → exact revision reopen → changed/deleted source refusal, deletion, WCAG and mobile.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
