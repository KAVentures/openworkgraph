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

   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   assert.deepEqual(errors,[]);
   console.log('PDD package acceptance passed: exported design → screenshot → drawn mask burned into pixels → review gate → AI-drafted narrative label → downloaded ZIP verified, WCAG audit, mobile width.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
