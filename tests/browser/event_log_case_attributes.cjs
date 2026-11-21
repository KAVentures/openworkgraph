const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
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
   await page.locator('#owgCaseAttributeConsent').check();
   await page.locator('#owgCaseAttributeAdd').click();
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
   await page.locator('#owgCaseTableColumns').fill('Department');
   const [template]=await Promise.all([page.waitForEvent('download'),page.locator('#owgCaseTableDownload').click()]);
   assert.match(template.suggestedFilename(),/-case-table\.csv$/);
   const templateRows=(await fs.readFile(await template.path(),'utf8')).trimEnd().split(/\r?\n/);
   assert.deepEqual(templateRows,['case_id,Department','c0,','c1,','c2,','c3,','c4,','c5,']);
   templateRows[1]='c0,Sales';
   await page.locator('#owgCaseAttributeFile').setInputFiles({name:'edited-template.csv',mimeType:'text/csv',
     buffer:Buffer.from(templateRows.join('\n'))});
   await page.locator('#owgCaseAttributeDelimiter').selectOption(',');
   await page.locator('#owgCaseAttributeColumns').selectOption(['Department']);
   await page.locator('#owgCaseAttributeConsent').check();
   await page.locator('#owgCaseAttributeAdd').click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Added Department to 1 cases.'}).waitFor();
   await page.locator('#owgCaseTableIncludeValues').check();
   const [filled]=await Promise.all([page.waitForEvent('download'),page.locator('#owgCaseTableDownload').click()]);
   assert.match(await fs.readFile(await filled.path(),'utf8'),/^case_id,Department\r?\nc0,Sales/);
   const templateAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgCaseTableExport'),
     {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(templateAudit.violations.map(violation=>violation.id),[]);
   assert.deepEqual(errors,[]);
   console.log('Case attribute table browser acceptance passed: columns joined by case ID with counts, usable as a filter, conflicts refused, removable, WCAG audit.');
   const workbook=path.join(scratch,'enrichment.xlsx');
   execFileSync(python,['-c',`
import sys, xlsxwriter
with xlsxwriter.Workbook(sys.argv[1]) as book:
    draft = book.add_worksheet("Draft")
    draft.write_row(0, 0, ["case_id", "Department"])
    draft.write_row(1, 0, ["c0", "DRAFT"])
    approved = book.add_worksheet("Approved")
    approved.write_row(0, 0, ["case_id", "Department"])
    approved.write_row(1, 0, ["c1", "Finance"])
`,workbook]);
   const sheetStatus=page.locator('#owgCaseAttributeWorkbookStatus');
   await page.locator('#owgCaseAttributeFile').setInputFiles(workbook);
   await sheetStatus.filter({hasText:'Using worksheet "Draft"'}).waitFor();
   await page.locator('#owgCaseAttributeColumns').selectOption(['Department']);
   await page.locator('#owgCaseAttributeConsent').check();
   await page.locator('#owgCaseAttributeWorksheet').selectOption('Approved');
   await sheetStatus.filter({hasText:'Using worksheet "Approved"'}).waitFor();
   assert.equal(await page.locator('#owgCaseAttributeConsent').isChecked(),false);
   await page.locator('#owgCaseAttributeColumns').selectOption(['Department']);
   await page.locator('#owgCaseAttributeConsent').check();
   await page.locator('#owgCaseAttributeAdd').click();
   await page.locator('#owgCaseAttributeResult').filter({hasText:'Added Department to 1 cases.'}).waitFor();
   const counts=await page.evaluate(async()=>{
     const id=document.querySelector('#owgEventLogPick').value;
     const caseCount=async value=>{
       const response=await fetch('/v1/event-logs/'+id+'/summary?attribute='+encodeURIComponent('Department='+value));
       return (await response.json()).cases;
     };
     return [await caseCount('Finance'),await caseCount('DRAFT')];
   });
   assert.deepEqual(counts,[1,0]);
   let reached,releaseWorkbook;
   const held=new Promise(resolve=>{reached=resolve;});
   const gate=new Promise(resolve=>{releaseWorkbook=resolve;});
   await page.route('**/xlsx-to-csv',async route=>{
     const response=await route.fetch();reached();await gate;await route.fulfill({response});
   },{times:1});
   const delayed=path.join(scratch,'delayed.xlsx');
   await fs.copyFile(workbook,delayed);
   await Promise.all([
     page.waitForRequest(request=>request.url().endsWith('/xlsx-to-csv'),{timeout:10000}),
     page.locator('#owgCaseAttributeFile').setInputFiles(delayed)
   ]);
   await held;
   await page.locator('#owgCaseAttributeFile').setInputFiles({name:'fresh.csv',mimeType:'text/csv',
     buffer:Buffer.from('case_id,Fresh\nc0,value\n')});
   await page.locator('#owgCaseAttributeColumns option[value=Fresh]').waitFor({state:'attached'});
   const delivered=page.waitForResponse(response=>response.url().endsWith('/xlsx-to-csv'));
   releaseWorkbook();await (await delivered).finished();
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   assert.deepEqual(await page.locator('#owgCaseAttributeColumns option').allInnerTexts(),['Fresh']);
   assert.equal(await page.locator('#owgCaseAttributeWorksheet').isVisible(),false);
   await page.locator('#owgCaseAttributeFile').setInputFiles({name:'bad.xlsx',mimeType:'application/octet-stream',
     buffer:Buffer.from('not a workbook')});
   await sheetStatus.filter({hasText:'not an .xlsx workbook'}).waitFor();
   assert.equal(await page.locator('#owgCaseAttributeAdd').isDisabled(),true);
   assert.deepEqual(errors,[]);
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
