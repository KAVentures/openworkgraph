const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: an automatic insight points at a slow case-attribute group and acts on exactly its cases.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-insseg-'));
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
   const context=await browser.newContext();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // Twelve cases; the four in APAC take twelve hours, the rest one hour. Region comes from a separate case table.
   const rows=['case,activity,time'];
   for(let i=0;i<12;i++){const day=String(1+i).padStart(2,'0');rows.push(`c${i},Receive,2024-03-${day}T08:00:00Z`,`c${i},Pay,2024-03-${day}T${i%3===0?'20':'09'}:00:00Z`);}
   await page.locator('#owgEventLogFile').setInputFiles({name:'payments.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 24 events'}).waitFor();
   const table='id,Region,Batch\n'+Array.from({length:12},(_,i)=>`c${i},${i%3===0?'APAC':'EU'},${i<9?'early':'later'}`).join('\n')+'\n';
   const added=await page.evaluate(async table=>{await window.__owgAuthReady;const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='payments').log_id;
     return (await fetch('/v1/event-logs/'+id+'/case-attributes?id_column=id&column=Region&column=Batch',{method:'POST',body:table,headers:{'Content-Type':'text/csv','X-OWG-Local-Storage-Confirmed':'true'}})).json();},table);
   assert.equal(added.cases_updated,12);
   await page.locator('#owgEventLogAnalyze').click();
   const item=page.locator('#owgInsightResults li').filter({hasText:'Cases with Region=APAC (33%) take 12.0× as long'});
   await item.waitFor();await item.scrollIntoViewIfNeeded();
   await item.getByRole('button',{name:/^Slowest cases: /}).click();
   const caseButtons=item.getByRole('button',{name:/^Explain case c\d+$/});
   await caseButtons.first().waitFor();
   const names=await caseButtons.evaluateAll(bs=>bs.map(b=>b.getAttribute('aria-label').replace('Explain case ','')).sort());
   assert.deepEqual(names,['c0','c3','c6','c9']);
   await caseButtons.first().click();
   await page.locator('#owgCaseExplainResult').filter({hasText:'took'}).waitFor();
   page.once('dialog',d=>d.accept('APAC payments'));
   await item.getByRole('button',{name:/^Save these cases as a new log: /}).click();
   await item.filter({hasText:'Saved 8 events of 4 cases as “APAC payments”. This log is unchanged.'}).waitFor();
   await page.locator('#owgCaseFilterAttribute').fill('Batch=early');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').getByText(/9 of 12 cases/).waitFor();
   await item.getByRole('button',{name:/^Review cases for Cases with Region=APAC/}).click();
   const evidence=page.locator('#owgEventLogCasesList');
   await evidence.getByRole('button',{name:'Open case c6',exact:true}).waitFor();
   assert.deepEqual(await evidence.getByRole('button',{name:/^Open case /}).allTextContents(),['c0','c3','c6']);
   await item.getByRole('button',{name:/^Slowest cases: /}).click();
   await item.getByRole('button',{name:'Explain case c6',exact:true}).waitFor();
   assert.equal(await item.getByRole('button',{name:'Explain case c9',exact:true}).count(),0);
   page.once('dialog',d=>d.accept('Early APAC payments'));
   await item.getByRole('button',{name:/^Save these cases as a new log: /}).click();
   await item.filter({hasText:'Saved 6 events of 3 cases as “Early APAC payments”. This log is unchanged.'}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogInsights'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Insight segment browser acceptance passed: a joined case attribute group surfaced automatically, scoped evidence and slowest cases, derived logs preserve the active filter, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
