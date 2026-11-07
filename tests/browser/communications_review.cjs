const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: consented review examples, labelling, local training, learned analysis, deletion.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-comms-review-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const PHRASES={'Refund request':{en:['I want a refund','please give me my money back'],sv:['jag vill ha återbetalning','ge mig pengarna tillbaka']},
   'Account access':{en:['I cannot log in','locked out of my account'],sv:['jag kan inte logga in','utelåst från kontot']}};
 const FILLER={en:['hello','thanks','regards','today'],sv:['hej','tack','hälsningar','idag']};
 let seed=7;const rand=()=>{seed=(seed*16807)%2147483647;return seed/2147483647;};const pick=a=>a[Math.floor(rand()*a.length)];
 const corpus=[];
 for(let i=0;i<220;i++){
   const lang=rand()<0.5?'en':'sv';const r=rand();const labels=r<0.2?[]:r<0.55?['Refund request']:r<0.9?['Account access']:['Refund request','Account access'];
   const text=[...labels.map(l=>pick(PHRASES[l][lang])),pick(FILLER[lang]),pick(FILLER[lang]),'case '+i].join(' ');
   corpus.push({text,labels,lang});
 }
 const rows=['ticket,text',...corpus.slice(0,40).map((c,i)=>`T${i},${c.text} mail k${i}@client.example`)];
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[],bodies=[];
   page.on('pageerror',error=>errors.push(error.message));page.on('dialog',d=>d.accept());
   page.on('response',async response=>{if(new URL(response.url()).pathname.startsWith('/v1/communications/analyze'))bodies.push(await response.text());});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgCommsCard').waitFor();
   // Off by default; turning on needs the acknowledgement checkbox and an explicit save.
   await page.locator('#owgCommsReview summary').click();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'Review examples are off.'}).waitFor();
   await page.locator('#owgCommsReviewOn').check();await page.locator('#owgCommsReviewRetention').fill('7');
   await page.locator('#owgCommsReviewSave').click();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'0 examples kept'}).waitFor();
   // Sample the least certain messages from an export.
   await page.locator('#owgCommsFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelectorAll('#owgCommsTextColumns input').length===2);
   await page.locator('#owgCommsConsent').check();await page.locator('#owgCommsSample').check();await page.locator('#owgCommsSampleCount').fill('10');
   await page.locator('#owgCommsRun').click();
   await page.locator('#owgCommsResults').getByText(/Kept 10 redacted review examples/).waitFor();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'10 examples kept'}).waitFor();
   const firstBox=page.locator('#owgCommsReviewExamples fieldset').first();
   assert(!(await firstBox.textContent()).includes('client.example'),'kept texts are redacted');
   // Label one example in the UI, the rest through the same API.
   await firstBox.getByLabel('Account access').check();await firstBox.getByLabel('Language').fill('en');
   await firstBox.getByRole('button',{name:/Save labels/}).click();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'Labels saved.'}).waitFor();
   await page.evaluate(async corpus=>{
     const h={'Content-Type':'application/json'};
     const add=async items=>{const r=await fetch('/v1/communications/review/examples',{method:'POST',headers:{...h,'X-OWG-Review-Examples-Confirmed':'true'},body:JSON.stringify({examples:items})});if(!r.ok)throw new Error(await r.text());};
     await add(corpus.slice(40,200).map(c=>({text:c.text,language:c.lang})));await add(corpus.slice(200).map(c=>({text:c.text,language:c.lang})));
     const byText=new Map(corpus.map(c=>[c.text,c.labels]));
     for(let offset=0;;offset+=200){
       const page=await (await fetch('/v1/communications/review/examples?status=unlabelled&limit=200')).json();
       if(!page.examples.length)break;
       for(const x of page.examples){const labels=byText.get(x.text.replace(/ mail \[EMAIL\]$/,''))||[];
         const r=await fetch('/v1/communications/review/label',{method:'POST',headers:h,body:JSON.stringify({example_id:x.example_id,labels})});if(!r.ok)throw new Error(await r.text());}
     }
   },corpus);
   await page.locator('#owgCommsTrain').click();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'Model trained.'}).waitFor({timeout:60000});
   const modelText=await page.locator('#owgCommsModel').textContent();
   assert.match(modelText,/Refund request/);assert.match(modelText,/Account access/);assert.match(modelText,/en .*sv|sv .*en/);
   // Use the learned model for an analysis; reports stay aggregate.
   await page.locator('#owgCommsFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelectorAll('#owgCommsTextColumns input').length===2);
   await page.locator('#owgCommsClassifier').selectOption('learned');await page.locator('#owgCommsConsent').check();
   await page.locator('#owgCommsRun').click();
   await page.locator('#owgCommsResults').getByText('Learned intents (a message can have several or none)').waitFor();
   for(const b of bodies)assert(!b.includes('client.example')&&!b.includes(corpus[0].text),'no message text in analysis responses');
   // Correct a previously reviewed label, then verify the old model can no
   // longer classify new messages and its old holdout score is not displayed.
   await page.evaluate(async()=>{
     const page=await (await fetch('/v1/communications/review/examples?status=labelled&limit=1')).json();
     const example=page.examples[0];
     const changed=await fetch('/v1/communications/review/label',{
       method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({example_id:example.example_id,labels:example.labels,
                            language:example.language==='sv'?'en':'sv'})});
     if(!changed.ok)throw new Error(await changed.text());
   });
   await page.locator('#owgCommsReview summary').click();
   await page.locator('#owgCommsReview summary').click();
   await page.locator('#owgCommsModel').getByText('Learned model needs retraining').waitFor();
   assert.doesNotMatch(await page.locator('#owgCommsModel').textContent(),/Macro F1/);
   await page.locator('#owgCommsFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelectorAll('#owgCommsTextColumns input').length===2);
   await page.locator('#owgCommsConsent').check();
   await page.locator('#owgCommsRun').click();
   await page.locator('#owgCommsStatus').filter({hasText:/stale/}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCommsCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   // Turning off deletes every example and the model.
   await page.locator('#owgCommsReviewOn').uncheck();await page.locator('#owgCommsReviewSave').click();
   await page.locator('#owgCommsReviewStatus').filter({hasText:'Review examples are off.'}).waitFor();
   const after=await page.evaluate(async()=>(await fetch('/v1/communications/review/settings')).json());
   assert.equal(after.example_count,0);assert.equal(after.model_trained,false);
   assert.deepEqual(errors,[]);
   console.log('Communications review acceptance passed: off by default, acknowledged opt-in, redacted sampled examples, UI labelling, local training with per-language holdout quality, learned aggregate analysis, review correction invalidation, deletion on turn-off, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
