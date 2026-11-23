const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,execFileSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: an Excel workbook is converted to CSV locally and imported with an offset for its zone-less dates.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-logxlsx-'));
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
   const context=await browser.newContext({timezoneId:'Asia/Kolkata'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // A real workbook: a title-free header row, Excel date cells (no time zone) and a second sheet.
   const xlsxPath=path.join(scratch,'orders.xlsx');
   execFileSync(python,['-c',`
import sys, datetime, xlsxwriter
book = xlsxwriter.Workbook(sys.argv[1]); sheet = book.add_worksheet('Orders'); fmt = book.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'})
sheet.write_row(0, 0, ['Order', 'Step', 'When', 'Who'])
row = 1
for i in range(6):
    for k, step in enumerate(['Receive order', 'Check stock', 'Ship']):
        sheet.write(row, 0, f'o{i}'); sheet.write(row, 1, step)
        sheet.write_datetime(row, 2, datetime.datetime(2024, 3, 1 + i, 9 + k, 30, 15), fmt); sheet.write(row, 3, 'ana' if k else 'bot'); row += 1
returns = book.add_worksheet('Returns'); returns.write_row(0, 0, ['Ticket', 'Action', 'At'])
for i in range(2):
    for k, step in enumerate(['Return requested', 'Refund']):
        returns.write_row(1 + 2 * i + k, 0, [f'r{i}', step]); returns.write_datetime(1 + 2 * i + k, 2, datetime.datetime(2024, 4, 1 + i, 10 + k), fmt)
book.close()`,xlsxPath]);
   await page.locator('#owgEventLogFile').setInputFiles(xlsxPath);
   await page.locator('#owgEventLogStatus').filter({hasText:'Read worksheet “Orders” (1 of 2; choose another under Worksheet): 18 rows.'}).waitFor();
   // The workbook was swapped for its CSV and the mapping was read from it.
   assert.equal(await page.locator('#owgEventLogFile').evaluate(f=>f.files[0].name),'orders.csv');
   assert.equal(await page.locator('#owgEventLogFormat').inputValue(),'csv');
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'Order');
   assert.equal(await page.locator('#owgEventLogTime').inputValue(),'When');
   assert.equal(await page.locator('#owgEventLogNaiveOffset').inputValue(),'+05:30','prefilled with the browser offset (no daylight saving in this zone)');
   assert.equal(await page.locator('#owgEventLogName').inputValue(),'orders');
   assert.deepEqual(await page.locator('#owgEventLogSheet option').allInnerTexts(),['Orders','Returns']);
   // Another sheet is converted from the same workbook on request.
   await page.locator('#owgEventLogSheet').selectOption('Returns');
   await page.locator('#owgEventLogStatus').filter({hasText:'Read worksheet “Returns” (2 of 2; choose another under Worksheet): 4 rows.'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'Ticket');
   await page.locator('#owgEventLogSheet').selectOption('Orders');
   await page.locator('#owgEventLogStatus').filter({hasText:'Read worksheet “Orders” (1 of 2'}).waitFor();
   assert.equal(await page.locator('#owgEventLogCase').inputValue(),'Order');
   await page.locator('#owgEventLogNaiveOffset').fill('+01:00');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 18 events'}).waitFor();
   // 09:30:15 at +01:00 is 08:30:15 UTC.
   const logs=await page.evaluate(async()=>{await window.__owgAuthReady;return (await fetch('/v1/event-logs')).json();});
   const log=logs.logs.find(l=>l.name==='orders');assert(log,'the log was saved');
   assert.equal(log.events,18);
   const summary=await page.evaluate(async id=>{return (await fetch('/v1/event-logs/'+id+'/summary')).json();},log.log_id);
   assert.equal(summary.cases,6);
   assert.equal(summary.first_event_at,'2024-03-01T08:30:15Z');
   // Export the imported log as a workbook, then round-trip it through the importer.
   await page.locator('#owgEventLogExportFormat').selectOption('xlsx');
   const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#owgEventLogExport').click()]);
   assert.match(download.suggestedFilename(),/\.xlsx$/);
   const exported=execFileSync(python,['-c',`import sys
from server import event_log_xlsx
with open(sys.argv[1],'rb') as f:
  result=event_log_xlsx.to_csv(f)
print(result['rows'], result['columns'])`,await download.path()]).toString().trim();
   assert.equal(exported,'18 4');
   // A file that is not a workbook is refused with a clear message, and nothing is selected.
   await page.locator('#owgEventLogFile').setInputFiles({name:'broken.xlsx',mimeType:'application/octet-stream',buffer:Buffer.from('not a zip')});
   await page.locator('#owgEventLogStatus').filter({hasText:'not an .xlsx workbook'}).waitFor();
   assert.equal(await page.locator('#owgEventLogFile').evaluate(f=>f.files.length),0);
   // A CSV file hides the worksheet choice.
   await page.locator('#owgEventLogFile').setInputFiles({name:'plain.csv',mimeType:'text/csv',buffer:Buffer.from('case,activity,time\nx,A,2024-03-04T08:00:00Z\n')});
   await page.locator('#owgEventLogStatus').filter({hasText:'File mapping ready'}).waitFor();
   assert.equal(await page.locator('#owgEventLogSheet').isVisible(),false);
   assert.equal(await page.locator('#owgEventLogImport').isDisabled(),true);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run({include:[['#owgEventLogFile']]},{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Event-log Excel import browser acceptance passed: workbook converted locally, mapped, imported with a chosen offset, and a broken file refused.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
