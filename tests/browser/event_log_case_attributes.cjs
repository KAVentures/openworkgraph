const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: join a separate case table onto a saved log's cases, filter by it, and remove it again.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-caseattr-'));
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   for(let i=0;i<6;i++)['Receive','Pay'].forEach((a,k)=>rows.push(`c${i},${a},2024-03-0${1+i}T0${8+k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 12 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgCaseAttributes');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgCaseAttributeUploads').filter({hasText:'No case attributes added yet.'}).waitFor();
   // A semicolon case table: four cases known, one unknown id.
   const table='Region;Order ID;Segment\nEU;c0;retail\nEU;c1;wholesale\nUS;c2;retail\nUS;c3;\nEU;zz;retail\n';
   await page.locator('#owgCaseAttributeFile').setInputFiles({name:'customers.csv',mimeType:'text/csv',buffer:Buffer.from(table)});
   await page.locator('#owgCaseAttributeDelimiter').selectOption(';');
   await page.waitForFunction(()=>document.querySelector('#owgCaseAttributeId').value==='Order ID');
   assert.deepEqual(await page.locator('#owgCaseAttributeColumns option').allInnerTexts(),['Region','Segment']);
   await page.locator('#owgCaseAttributeColumns').selectOption(['Region','Segment']);
   assert.equal(await page.locator('#owgCaseAttributeAdd').isDisabled(),true,'needs consent to store');
   await page.locator('#owgCaseAttributeConsent').check();
   await page.locator('#owgCaseAttributeAdd').click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Added Region, Segment to 4 cases. 1 rows matched no case; 2 cases had no row.'}).waitFor();
   await page.locator('#owgCaseAttributeUploads tbody tr').first().waitFor();
   // The new attribute works as a case filter.
   const filtered=await page.evaluate(async()=>{const logs=(await (await fetch('/v1/event-logs')).json()).logs;
     const id=logs.find(l=>l.name==='orders').log_id;
     return (await fetch('/v1/event-logs/'+id+'/summary?attribute='+encodeURIComponent('Region=US'))).json();});
   assert.equal(filtered.cases,2,JSON.stringify(filtered));
   // Adding the same columns again is refused unless overwriting is chosen.
   await page.locator('#owgCaseAttributeConsent').check();await page.locator('#owgCaseAttributeAdd').click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:"cases already have 'Region'"}).waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseAttributes'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgCaseAttributeFile').setInputFiles({name:'overwrite.csv',mimeType:'text/csv',
     buffer:Buffer.from('id,Region\nc1,APAC\n')});
   await page.locator('#owgCaseAttributeDelimiter').selectOption(',');
   await page.locator('#owgCaseAttributeColumns').selectOption(['Region']);
   await page.locator('#owgCaseAttributeReplace').check();
   assert.equal(await page.locator('#owgCaseAttributeAdd').isDisabled(),true);
   await page.locator('#owgCaseAttributePreview').click();
   const preview=page.locator('#owgCaseAttributeResult');
   await preview.filter({hasText:'Ready to apply with storage confirmation.'}).waitFor();
   assert.deepEqual(await preview.locator('tbody td').allTextContents(),['Region','1','1']);

   await page.locator('#owgCaseAttributeConsent').check();
   const [applyRequest]=await Promise.all([
     page.waitForRequest(request=>request.method()==='POST'&&request.url().includes('expected_preview_sha256=')),
     page.locator('#owgCaseAttributeAdd').click()
   ]);
   assert.match(new URL(applyRequest.url()).searchParams.get('expected_preview_sha256'),/^[a-f0-9]{64}$/);
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Added Region to 1 cases.'}).waitFor();
   page.once('dialog',d=>d.accept());
   await page.locator('#owgCaseAttributeUploads').getByRole('button',{name:'Undo upload for Region, Segment',exact:true}).click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Undo the newer upload first'}).waitFor();
   page.once('dialog',d=>d.accept());
   await page.locator('#owgCaseAttributeUploads').getByRole('button',{name:'Undo upload for Region',exact:true}).click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Undid Region in 1 cases.'}).waitFor();
   const restored=await page.evaluate(async()=>{
     const id=document.querySelector('#owgEventLogPick').value;
     const response=await fetch('/v1/event-logs/'+id+'/summary?attribute='+encodeURIComponent('Region=EU'));
     return (await response.json()).cases;
   });
   assert.equal(restored,2);
   page.once('dialog',d=>d.accept());
   await page.locator('#owgCaseAttributeUploads').getByRole('button',{name:'Undo upload for Region, Segment'}).click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Undid Region, Segment in 4 cases.'}).waitFor();
   await page.locator('#owgCaseAttributeUploads').filter({hasText:'No case attributes added yet.'}).waitFor();
   assert.deepEqual(errors,[]);
   console.log('Case attribute table browser acceptance passed: columns joined by case ID with counts, usable as a filter, conflicts refused, removable, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
