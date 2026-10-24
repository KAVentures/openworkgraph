const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: exported design → step screenshot → drawn mask → PDD ZIP with burned-in redaction.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-pdd-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const design={format:'openworkgraph.proposed-process-design.v1',status:'human_reviewed_draft_not_approved_policy',
   business_goal_declared_by_user:'Resolve refund requests',user_reviewed_export:true,approved_business_policy:false,
   authorized_agent_actions:false,agent_execution_triggered:false,source_case_ids_included:false,uploaded_data_retained_by_owg:false,
   as_is:{path:['Receive','Review','Refund'],source_observation_not_policy:true},
   to_be:{proposed_path:['Receive','Validate','Refund'],proposed_not_deployed:true}};
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const context=await browser.newContext({acceptDownloads:true,permissions:['clipboard-read','clipboard-write']});
   const page=await context.newPage(),errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgPddCard').waitFor();
   await page.locator('#owgPddDesign').setInputFiles({name:'openworkgraph-proposed-process.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(design))});
   await page.locator('#owgPddStatus').filter({hasText:'3 steps loaded'}).waitFor();
   assert.equal(await page.locator('#owgPddBuild').isDisabled(),false,'a document without screenshots needs no extra confirmation');

   // A 400x200 "screenshot": left half red (sensitive), right half blue.
   const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=400;c.height=200;const x=c.getContext('2d');
     x.fillStyle='rgb(220,0,0)';x.fillRect(0,0,200,200);x.fillStyle='rgb(0,0,220)';x.fillRect(200,0,200,200);return c.toDataURL('image/png').split(',')[1];});
   await page.locator('#owgPddShot_step_002').setInputFiles({name:'screen.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
   const canvas=page.locator('#owgPddCard canvas');await canvas.waitFor();
   await canvas.scrollIntoViewIfNeeded();const box=await canvas.boundingBox();
   await page.mouse.move(box.x+box.width*0.05,box.y+box.height*0.05);await page.mouse.down();
   await page.mouse.move(box.x+box.width*0.45,box.y+box.height*0.95,{steps:5});await page.mouse.up();
   await page.locator('#owgPddCard').getByText('1 masked area').waitFor({timeout:5000}).catch(async e=>{throw new Error('editor: '+await page.locator('.owg-pdd-editor').textContent()+' errors='+errors.join(';')+' '+e.message);});
   await page.locator('#owgPddCaption').fill('Refund <form>');
   await page.locator('#owgPddUseShot').click();
   await page.locator('#owgPddCard figcaption').filter({hasText:'1 masked'}).waitFor();
   assert.equal(await page.locator('#owgPddBuild').isDisabled(),true,'screenshots require the review confirmation');
   await page.locator('#owgPddCopyPrompt').click();
   await page.locator('#owgPddStatus').filter({hasText:'Prompt copied'}).waitFor();
   const prompt=await page.evaluate(()=>navigator.clipboard.readText());
   assert.match(prompt,/step_002: Validate/);assert.equal(prompt.includes('base64'),false);
   await page.locator('#owgPddNarrative').fill('Refunds are received, validated and paid.');
   await page.locator('#owgPddAiDrafted').check();await page.locator('#owgPddReviewed').check();

   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPddCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'PDD card WCAG violations');

   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPddBuild').click()]);
   const zipPath=path.join(scratch,'pdd.zip');await download.saveAs(zipPath);
   await page.locator('#owgPddStatus').filter({hasText:'Nothing was stored'}).waitFor();
   const check=spawnSync(python,['-c',`
import sys, zipfile, io
from PIL import Image
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
assert "images/step_002_1.png" in names, names
md = z.read("PDD.md").decode()
assert "AI-drafted text, not reviewed" in md and "Refund &lt;form&gt;" in md, md
image = Image.open(io.BytesIO(z.read("images/step_002_1.png"))).convert("RGB")
assert image.size == (400, 200), image.size
assert image.getpixel((100, 100)) == (0, 0, 0), image.getpixel((100, 100))      # masked
assert image.getpixel((300, 100)) == (0, 0, 220), image.getpixel((300, 100))    # untouched
html = z.read("PDD.html").decode()
assert "<script" not in html and "Refund &lt;form&gt;" in html
print("zip ok")
`,zipPath],{encoding:'utf8'});
   assert.equal(check.status,0,check.stderr+check.stdout);

   const saved=await page.evaluate(async()=>{
     const process_model={goal:'Review both declared checks',baseline_source:'user_declared',baseline_coverage:'user_declared_design',baseline_path:['Declared design'],
       activities:[{id:'fork',label:'Both checks',kind:'parallel_split'},{id:'A',label:'Check account'},{id:'B',label:'Check invoice'},
         {id:'join',label:'All checks',kind:'parallel_join'},{id:'close',label:'Close'}],
       transitions:[['fork','A'],['fork','B'],['A','join'],['B','join'],['join','close']].map(([source,target])=>({source,target})),
       entry_activity_id:'fork',terminal_activity_ids:['close']};
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Parallel PDD',process_model,user_confirmed:true})});
     if(!response.ok)throw new Error(await response.text());return response.json();
   });
   await page.locator('#owgProcessCatalog').getByRole('button',{name:'Load saved designs',exact:true}).click();
   await page.locator('#owgCatalogList').getByRole('button',{name:/Parallel PDD/}).click();
   await page.locator('#owgCatalogEditor').getByRole('button',{name:'Add screenshots to saved PDD',exact:true}).waitFor();
   await page.evaluate(async saved=>{
     const response=await fetch('/v1/process-catalog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workflow_id:saved.workflow_id,expected_revision:1,title:'Parallel PDD v2',process_model:{...saved.process_model,goal:'Newer revision goal'},user_confirmed:true})});
     if(!response.ok)throw new Error(await response.text());
   },saved);
   await page.locator('#owgCatalogEditor').getByRole('button',{name:'Add screenshots to saved PDD',exact:true}).click();
   await page.locator('#owgPddStatus').filter({hasText:'Saved revision 1 loaded (5 nodes)'}).waitFor();
   assert.equal(await page.locator('#owgPddNarrative').inputValue(),'','a different source clears the old narrative');
   assert.equal(await page.locator('#owgPddCard figure').count(),0,'old screenshots cannot move to another graph');
   await page.locator('#owgPddShot_B').setInputFiles({name:'check.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
   await page.locator('#owgPddCard').getByRole('button',{name:'Mask entire screenshot',exact:true}).click();
   await page.locator('#owgPddUseShot').click();await page.locator('#owgPddReviewed').check();
   await page.locator('#owgPddCopyPrompt').click();
   await page.locator('#owgPddStatus').filter({hasText:'Prompt copied'}).waitFor();
   const graphPrompt=await page.evaluate(()=>navigator.clipboard.readText());
   assert.match(graphPrompt,/parallel_split/);assert.match(graphPrompt,/parallel_join/);assert.match(graphPrompt,/fork → B/);
   assert.equal(graphPrompt.includes('Newer revision goal'),false,'the selected historical revision remains pinned');
   const [savedDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPddBuild').click()]);
   const savedZip=path.join(scratch,'saved-pdd.zip');await savedDownload.saveAs(savedZip);
   const savedCheck=spawnSync(python,['-c',`
import json, sys, zipfile, io
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image
z=zipfile.ZipFile(sys.argv[1]);packet=json.loads(z.read('process.json'));manifest=json.loads(z.read('manifest.json'))
assert packet['revision']==manifest['process_reference']['revision']==1
assert packet['process_model']['goal']=='Review both declared checks'
assert manifest['stored_by_owg'] is False and manifest['execution_authorized'] is False
xml=ET.fromstring(z.read('process.bpmn'));ns={'b':'http://www.omg.org/spec/BPMN/20100524/MODEL'}
assert len(xml.findall('.//b:parallelGateway',ns))==2
assert len(xml.findall('.//b:sequenceFlow',ns))==5
image=Image.open(io.BytesIO(z.read('images/B_1.png'))).convert('RGB')
assert image.getpixel((100,100))==(0,0,0) and image.getpixel((300,100))==(0,0,0)
assert 'attachment index, not an execution sequence' in z.read('PDD.md').decode()
Path(sys.argv[1]).with_suffix('.html').write_bytes(z.read('PDD.html'))
`,savedZip],{encoding:'utf8'});
   assert.equal(savedCheck.status,0,savedCheck.stderr+savedCheck.stdout);
   const savedAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPddCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(savedAudit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'saved PDD WCAG violations');
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'saved PDD card fits phone width');
   const artifact=await context.newPage();await artifact.setViewportSize({width:390,height:844});
   await artifact.setContent(await fs.readFile(savedZip.replace(/\.zip$/,'.html'),'utf8'));
   assert.equal(await artifact.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'self-contained saved PDD fits phone width');
   await artifact.close();await page.setViewportSize({width:1280,height:720});
   let releaseSource,seenSource;
   const sourceGate=new Promise(resolve=>releaseSource=resolve),sourceFetched=new Promise(resolve=>seenSource=resolve);
   await page.route('**/v1/process-catalog/revision*',async route=>{const response=await route.fetch();seenSource();await sourceGate;await route.fulfill({response});});
   await page.locator('#owgCatalogEditor').getByRole('button',{name:'Add screenshots to saved PDD',exact:true}).click();await sourceFetched;
   await page.locator('#owgPddDesign').setInputFiles({name:'proposed-process.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(design))});
   await page.locator('#owgPddStatus').filter({hasText:'3 steps loaded'}).waitFor();
   const oldSource=page.waitForResponse(r=>r.url().includes('/v1/process-catalog/revision?'));
   releaseSource();await oldSource;await page.unrouteAll({behavior:'wait'});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.match(await page.locator('#owgPddStatus').textContent(),/3 steps loaded/,'a delayed saved graph cannot replace a newly selected file');
   assert.equal(await page.locator('#owgPddShot_B').count(),0);

   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   assert.deepEqual(errors,[]);
   console.log('PDD package acceptance passed: legacy design with burned masks, saved catalog → pinned historical parallel graph → cleared old attachments → reviewed screenshot → ZIP with preserved BPMN/JSON and pixel verification, WCAG and mobile.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
