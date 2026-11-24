const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: group activities under a higher-level name for every analysis (R9 event abstraction).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-amap-'));
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
   const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   const paths={a:['Open form','Type name','Type address','Submit','Approve'],b:['Open form','Type address','Submit','Reject'],c:['Open form','Type name','Submit','Approve']};
   for(const [c,steps] of Object.entries(paths))steps.forEach((s,k)=>rows.push(`${c},${s},2024-03-01T0${k}:00:00Z`));
   await page.locator('#owgEventLogFile').setInputFiles({name:'forms.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 13 events'}).waitFor();
   // Simulate a label outside the initial bounded list; search must add it without losing selection.
   await page.route('**/activity-mappings',async route=>{
     const response=await route.fetch(),body=await response.json();
     body.original_activities=body.original_activities.filter(item=>item.activity!=='Type address');
     await route.fulfill({response,json:body});
   },{times:1});
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgActivityMappings');await box.waitFor();
   await box.getByText('No saved mappings.').waitFor();
   assert.equal(await page.locator('#owgActivityGroupPick option[value="Type address"]').count(),0);
   await page.locator('#owgActivityGroupPick').selectOption('Type name');
   await page.locator('#owgActivityMappingSearch').fill('Type');
   await page.locator('#owgActivityMappingFind').click();
   await page.locator('#owgActivityMappingSearchStatus').filter({hasText:'2 matching labels found; 1 added'}).waitFor();
   assert.deepEqual(await page.locator('#owgActivityGroupPick').evaluate(el=>[...el.selectedOptions].map(o=>o.value)),['Type name']);
   await page.locator('#owgActivityMappingName').fill('Data entry');
   await page.locator('#owgActivityGroupLabel').fill('Enter data');
   await page.locator('#owgActivityGroupPick').selectOption(['Type name','Type address']);
   await page.locator('#owgActivityGroupAdd').click();
   assert.match(await page.locator('#owgActivityMappingDraft').textContent(),/Enter data ← Type name, Type address|Enter data ← Type address, Type name/);
   await page.locator('#owgActivityMappingCollapse').check();
   await page.locator('#owgActivityMappingSave').click();
   await page.locator('#owgActivityMappingStatus').filter({hasText:'Saved “Data entry”'}).waitFor();
   assert.equal(await page.locator('#owgActivityMappingNotice').count(),0,'saving does not apply it');
   await page.getByRole('button',{name:'Edit mapping Data entry'}).click();
   await page.locator('#owgActivityMappingName').fill('Revised entry');
   await page.locator('#owgActivityMappingSave').click();
   await page.locator('#owgActivityMappingStatus').filter({hasText:'Saved “Revised entry”'}).waitFor();
   assert.equal(await page.locator('#owgActivityMappingNotice').count(),0);
   await page.getByRole('button',{name:'Edit mapping Revised entry'}).click();
   await page.locator('#owgActivityMappingCancel').click();
   assert.equal(await page.locator('#owgActivityMappingDraft li').count(),0);
   await page.getByRole('button',{name:'Apply Revised entry'}).click();
   await page.locator('#owgActivityMappingNotice').waitFor();
   assert.match(await page.locator('#owgActivityMappingNotice').textContent(),/grouped by the mapping “Revised entry”: 4 events relabelled, 1 consecutive repeats collapsed/);
   // Object-centric views keep the original activities by design (the notice says so), and that map
   // renders asynchronously; check every other section.
   const variants=await page.evaluate(()=>{const copy=document.querySelector('#owgEventLogResults').cloneNode(true);
     copy.querySelector('#owgObjectCentricMap')?.remove();return copy.textContent;});
   assert.match(variants,/Open form → Enter data → Submit → Approve/);
   assert.doesNotMatch(variants,/Type name →/);
   await page.getByRole('button',{name:'Stop applying Revised entry'}).click();
   await page.getByRole('button',{name:'Apply Revised entry'}).waitFor();
   assert.equal(await page.locator('#owgActivityMappingNotice').count(),0);
   assert.match(await page.locator('#owgEventLogResults').textContent(),/Type name/);
   await page.getByRole('button',{name:'Delete mapping Revised entry'}).click();
   await box.getByText('No saved mappings.').waitFor();
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgActivityMappings'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Activity mapping browser acceptance passed: grouped two activities with repeats collapsed, not applied on save, applied to variants with a notice, removed, deleted, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
