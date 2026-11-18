const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: replay a log on the discovered Petri net and on an uploaded PNML net.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-petri-'));
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
   // 30 orders: Register, Check, Pay, Close; 6 skip Check.
   const rows=['case,activity,time,who'];
   for(let i=0;i<30;i++){const steps=['Register',...(i%5===0?[]:['Check']),'Pay','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-0${1+i%2}-01T${String(j).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 114 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgPetri');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgPetriDiscovered').click();
   await page.locator('#owgPetriStatus').filter({hasText:'Fitness 100% on the discovered net (every path kept): 30 of 30 cases'}).waitFor();
   // A strict sequence net, as another tool might export it: Check cannot be skipped.
   const labels=['Register','Check','Pay','Close'];
   const places=labels.map((_,i)=>`<place id="p${i}">${i===0?'<initialMarking><text>1</text></initialMarking>':''}</place>`).join('')+`<place id="p${labels.length}"/>`;
   const transitions=labels.map((a,i)=>`<transition id="t${i}"><name><text>${a}</text></name></transition>`).join('');
   const arcs=labels.map((_,i)=>`<arc id="i${i}" source="p${i}" target="t${i}"/><arc id="o${i}" source="t${i}" target="p${i+1}"/>`).join('');
   const xml=`<?xml version="1.0"?><pnml><net id="n" type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel"><page id="g">${places}${transitions}${arcs}</page><finalmarkings><marking><place idref="p4"><text>1</text></place></marking></finalmarkings></net></pnml>`;
   await page.locator('#owgPetriFile').setInputFiles({name:'strict.pnml',mimeType:'application/xml',buffer:Buffer.from(xml)});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on strict.pnml: 24 of 30 cases'}).waitFor();
   assert((await page.locator('#owgPetriStatus').textContent()).includes('Precision 100%'));
   const out=page.locator('#owgPetriResults');
   const placesTable=await out.locator('table').first().textContent();
   assert(placesTable.includes('p2'),placesTable);assert(placesTable.includes('p1'),placesTable);
   assert((await out.locator('table').nth(1).textContent()).includes('Pay'));
   await out.locator('details summary').click();
   assert((await out.locator('details').textContent()).includes('Register → Pay → Close'));
   // Alignments name exactly which step each non-fitting case skipped.
   await page.locator('#owgPetriAlign').click();
   await page.locator('#owgPetriAlignStatus').filter({hasText:'Alignment fitness 97.4%: 24 of 30 cases follow the net exactly'}).waitFor();
   assert((await page.locator('#owgPetriAlignStatus').textContent()).includes('needs 0.2 extra or skipped steps'));
   const aligned=page.locator('#owgPetriAlignResults');
   const skippedTable=await aligned.locator('table').nth(1).textContent();
   assert(skippedTable.includes('Check')&&skippedTable.includes('6'),skippedTable);
   assert((await aligned.locator('details').textContent()).includes('Register → −Check (skipped) → Pay → Close'));
   // Cases i%5===0 skip Check; they alternate months, so each month has 3 non-fitting cases of 15.
   const months=await page.locator('#owgPetriAlignMonths').textContent();
   assert(months.includes('2024-011580%97.4%')&&months.includes('2024-021580%97.4%'),months);
   // Root causes of the deviation: the six cases without Check are the deviating ones.
   await page.locator('#owgPetriCauses').click();
   await page.locator('#owgPetriCausesResults').filter({hasText:'6 of 30 cases deviate'}).waitFor();
   const causes=await page.locator('#owgPetriCausesResults').textContent();
   assert(causes.includes('activity: Check')&&causes.includes('not causes'),causes);
   await page.addScriptTag({content:axeSource});
   const alignedAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPetri'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(alignedAudit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.locator('#owgPetriFile').setInputFiles({name:'bad.pnml',mimeType:'application/xml',buffer:Buffer.from('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'DOCTYPE'}).waitFor();
   assert.equal(await page.locator('#owgPetriAlign').isDisabled(),true);
   assert.equal(await page.locator('#owgPetriAlignResults').textContent(),'');
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPetri'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Petri net replay browser acceptance passed: discovered net fits, uploaded strict net shows missing and remaining tokens, alignments name the skipped step, deviation root causes, unsafe XML refused, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
