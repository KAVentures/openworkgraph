const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: consented ticket analysis, aggregates only, taxonomy edit, intent event log.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-comms-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 const SECRET='CUSTOMER_SECRET_42';
 const rows=['ticket_id,created_at,author_type,subject,body'];
 for(let i=0;i<12;i++){
   const day=String(1+i%10).padStart(2,'0');
   const ask=i%3===0?'Please cancel my subscription':i%3===1?'Where is my order? Tracking says not arrived':'The app shows an error and does not work';
   rows.push(`T${i},2024-03-${day}T09:00:00Z,customer,Help,"${ask}. Ref ${SECRET} 19811218-9876 mail me at k${i}@client.example"`);
   rows.push(`T${i},2024-03-${day}T11:00:00Z,agent,Re: Help,Thanks we are on it`);
 }
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[],bodies=[];
   page.on('pageerror',error=>errors.push(error.message));
   page.on('response',async response=>{if(new URL(response.url()).pathname.startsWith('/v1/communications/analyze'))bodies.push(await response.text());});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   await page.locator('#owgCommsCard').waitFor();
   // The strict language holdout gate is opt-in, and never runs for keyword classification.
   assert.equal(await page.locator('#owgCommsQualityControls').isHidden(),true);
   await page.locator('#owgCommsClassifier').selectOption('learned');
   assert.equal(await page.locator('#owgCommsQualityControls').isVisible(),true);
   assert.equal(await page.locator('#owgCommsGateLang').isHidden(),true);
   await page.locator('#owgCommsQualityGate').check();
   assert.equal(await page.locator('#owgCommsGateLang').isVisible(),true);
   await page.locator('#owgCommsGateLang').fill('sv');
   await page.locator('#owgCommsClassifier').selectOption('keyword');
   assert.equal(await page.locator('#owgCommsQualityControls').isHidden(),true);

   await page.locator('#owgCommsTaxonomyStatus').filter({hasText:'Default taxonomy.'}).waitFor({state:'attached'});

   await page.locator('#owgCommsFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   assert.equal(await page.locator('#owgCommsFormat').inputValue(),'csv');
   await page.waitForFunction(()=>document.querySelectorAll('#owgCommsTextColumns input').length===5);
   assert.equal(await page.locator('#owgCommsTextColumns input:checked').count(),2,'subject and body are preselected');
   assert.equal(await page.locator('#owgCommsTime').inputValue(),'created_at');
   assert.equal(await page.locator('#owgCommsThread').inputValue(),'ticket_id');
   await page.locator('#owgCommsDirection').selectOption('author_type');
   assert.equal(await page.locator('#owgCommsRun').isDisabled(),true,'analysis needs explicit consent');
   assert.equal(await page.locator('#owgCommsEpisodeControls').isHidden(),true);
   await page.locator('#owgCommsEpisodes').check();
   await page.locator('#owgCommsEpisodeCutoff').fill('2024-03-12T00:00:00Z');
   await page.locator('#owgCommsEpisodeDeadlines').fill('60,120,240');
   await page.locator('#owgCommsConsent').check();await page.locator('#owgCommsSave').check();
   await page.locator('#owgCommsRun').click();
   await page.locator('#owgCommsResults h3',{hasText:'Intents'}).waitFor();
   const text=await page.locator('#owgCommsResults').textContent();
   assert.match(text,/24 messages; 12 classified/);assert.match(text,/Cancellation/);assert.match(text,/Technical issue/);
   assert.match(text,/personnummer/);assert.match(text,/email address/);assert.match(text,/2\.0 h \(12 threads\)/);
   assert.match(text,/Saved the intent event log "tickets intents" with 24 events/);
   await page.locator('#owgCommsEpisodeResults').getByRole('heading',{name:'Observed response backlog and workload'}).waitFor();
   await page.locator('#owgCommsEpisodeResults').getByText(/12 eligible response episodes across 12 threads/).waitFor();
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgCommsEpisodeDownload').click()]);
   const episode=JSON.parse(await fs.readFile(await download.path(),'utf8'));
   assert.equal(episode.overall.episodes,12);
   assert.equal(episode.overall.deadlines[0].counts.observed_after_deadline,12);
   assert.equal(episode.overall.deadlines[1].counts.observed_within_deadline,12);
   assert.equal(episode.workload.overall.episode_wait_seconds,12*7200);
   assert.equal(episode.workload.overall.closing_observed_backlog,0);
   assert.equal(episode.by_first_inbound_intent.rows.length,0);
   assert(!JSON.stringify(episode).includes(SECRET));
   // Export a second actual response report using the same conventions.
   await page.locator('#owgCommsSave').uncheck();
   const fasterRows=rows.map(row=>row.replace('T11:00:00Z,agent','T10:00:00Z,agent'));
   await page.locator('#owgCommsFile').setInputFiles({name:'faster-tickets.csv',mimeType:'text/csv',buffer:Buffer.from(fasterRows.join('\n'))});
   await page.locator('#owgCommsDirection').selectOption('author_type');
   await page.locator('#owgCommsConsent').check();
   await page.locator('#owgCommsRun').click();
   await page.waitForFunction(()=>document.querySelector('#owgCommsEpisodeResults')?.textContent.includes('1.0 h')||document.querySelector('#owgCommsEpisodeResults')?.textContent.includes('60 min'));
   const [download2]=await Promise.all([page.waitForEvent('download'),page.locator('#owgCommsEpisodeDownload').click()]);
   const episode2=JSON.parse(await fs.readFile(await download2.path(),'utf8'));
   assert.equal(episode2.overall.deadlines[0].counts.observed_within_deadline,12);
   const [comparePage]=await Promise.all([page.context().waitForEvent('page'),page.getByRole('link',{name:'Compare two response episode reports'}).click()]);
   const compareErrors=[];comparePage.on('pageerror',error=>compareErrors.push(error.message));
   await comparePage.waitForLoadState('domcontentloaded');
   const requests=[];comparePage.on('request',req=>requests.push(req.url()));
   await comparePage.locator('#baseline').setInputFiles({name:'baseline.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(episode))});
   await comparePage.locator('#comparison').setInputFiles({name:'comparison.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(episode2))});
   assert.equal(await comparePage.locator('#compare').isDisabled(),true);
   await comparePage.locator('#confirm').check();await comparePage.locator('#compare').click();
   await comparePage.locator('#status').filter({hasText:'Comparison ready. No files were uploaded.'}).waitFor();
   const [comparisonDownload]=await Promise.all([comparePage.waitForEvent('download'),comparePage.locator('#download').click()]);
   const compared=JSON.parse(await fs.readFile(await comparisonDownload.path(),'utf8'));
   assert.equal(compared.deadlines[0].conditional_change_percentage_points.lower,100);
   assert.equal(compared.deadlines[0].conditional_change_percentage_points.upper,100);
   assert.equal(compared.distinct_populations_verified,false);
   assert.equal(compared.causal_effect_verified,false);
   assert.equal(compared.baseline.measures.episodes,12);
   assert.equal(compared.by_intent.rows.length,0);
   assert.equal(requests.length,0,'comparison must not issue network requests');
   await comparePage.setViewportSize({width:390,height:844});
   assert.equal(await comparePage.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'comparison with tables must fit phone width');
   await comparePage.locator('#confirm').uncheck();
   assert.equal(await comparePage.locator('#download').isDisabled(),true);
   assert.equal(await comparePage.locator('#results').textContent(),'');
   await comparePage.locator('#clear').click();
   assert.equal(await comparePage.locator('#baseline').inputValue(),'');
   assert.equal(await comparePage.locator('#comparison').inputValue(),'');
   await comparePage.setViewportSize({width:390,height:844});
   assert.equal(await comparePage.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
   assert.deepEqual(compareErrors,[]);
   await comparePage.close();
   await page.locator('#owgCommsEpisodes').uncheck();
   for(const leaked of [SECRET,'19811218','client.example','Tracking says'])assert.equal(text.includes(leaked)||bodies.some(b=>b.includes(leaked)),false,leaked+' must not leave the analysis');

   await page.locator('#owgCommsCard summary',{hasText:'Intent taxonomy'}).click();
   await page.locator('#owgCommsTaxonomy').fill('Churn risk: cancel, subscription\nWhere is it: where is my order, tracking');
   await page.locator('#owgCommsTaxonomySave').click();
   await page.locator('#owgCommsTaxonomyStatus').filter({hasText:'saved'}).waitFor();
   await page.locator('#owgCommsFile').setInputFiles({name:'tickets.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.locator('#owgCommsDirection').selectOption('author_type');
   await page.locator('#owgCommsConsent').check();await page.locator('#owgCommsRun').click();
   await page.locator('#owgCommsResults').filter({hasText:'Churn risk'}).waitFor();
   const custom=await page.locator('#owgCommsResults').textContent();
   assert.match(custom,/unclassified433\.3%/);
   assert.equal(/Frequent phrases/.test(custom),false,'phrases from fewer than 5 messages are never shown');

   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCommsCard'),{
     runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes[0].target.join(' ')),[],'communications WCAG violations');
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false,'no horizontal overflow at phone width');
   await page.locator('#owgCommsTaxonomyReset').click();
   await page.locator('#owgCommsTaxonomyStatus').filter({hasText:'Default taxonomy restored.'}).waitFor();
   const stored=await page.evaluate(async()=>(await (await fetch('/v1/event-logs')).json()).logs.map(l=>l.name));
   assert.deepEqual(stored,['tickets intents']);
   const leftovers=(await fs.readdir(path.join(scratch,'data'))).filter(name=>name.startsWith('owg-comms-'));
   assert.deepEqual(leftovers,[],'uploaded exports are deleted after analysis');
   assert.deepEqual(errors,[]);
   console.log('Communications mining acceptance passed: consented CSV analysis, aggregates only (no text, identifiers or domains leave the analysis), explicit intent event log, taxonomy edit/reset, WCAG audit, mobile width, temporary upload removed.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
