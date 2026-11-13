const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: automatic insights rank where case time concentrates and apply their filter.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-insights-'));
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
   const context=await browser.newContext({locale:'en-US'});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   const rows=['case,activity,time'];
   const at=(i,h)=>new Date(Date.UTC(2024,0,1+i,8)+h*3600000).toISOString().replace('.000Z','Z');
   for(let i=0;i<40;i++){let t=0;const steps=[['Receive',0]];t+=1;steps.push(['Check',t]);
     if(i%4===0){t+=1;steps.push(['Check',t]);} if(i%5===0){t+=30;steps.push(['Escalate',t]);}
     t+=i>=20?12:1;steps.push(['Approve',t]);t+=10;steps.push(['Pay',t]);
     steps.forEach(([a,h])=>rows.push(`c${String(i).padStart(3,'0')},${a},${at(i,h)}`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'insights.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===3);
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgEventLogInsights');await box.waitFor();
   const list=box.locator('ol');await list.waitFor();
   const items=await list.locator('li').allInnerTexts();
   assert.match(items[0],/^Approve → Pay takes 42% of all recorded case time/);
   assert(items.some(t=>/^Cases with Escalate \(20%\) take 2\.7× as long/.test(t)),JSON.stringify(items));
   assert(items.some(t=>/Contains activity/.test(t)),'activity findings explain how to filter');
   assert.match(await box.innerText(),/not causes, recommendations/);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgEventLogInsights'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   // Following an insight applies its filter to every analysis.
   await page.getByRole('button',{name:/^Filter to these cases: Approve → Pay/}).click();
   await page.locator('#owgEventLogResults').filter({hasText:/Active: .*Approve.*Pay/}).waitFor();
   const after=page.locator('#owgEventLogInsights ol');await after.waitFor();
   assert.equal(await page.getByRole('button',{name:/^Filter to these cases/}).count(),0,'no stacking of filters from insights');
   assert.deepEqual(errors,[]);
   console.log('Insights browser acceptance passed: ranked findings with caveats, filter applied from an insight, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
