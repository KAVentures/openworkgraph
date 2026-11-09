const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: plan owners, teams, budgets and dependencies, and see the portfolio roll-up (R15).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-portfolio-'));
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
   await page.locator('#owgPipelineCounts').filter({hasText:'No ideas yet.'}).waitFor();
   for(const title of ['Clean vendor master','Auto-match invoices']){
     await page.locator('#owgPipelineTitle').fill(title);await page.locator('#owgPipelineAdd').click();
     await page.getByRole('button',{name:'Open idea '+title}).waitFor();
   }
   await page.getByRole('button',{name:'Open idea Auto-match invoices'}).click();
   const planning=page.locator('#owgPipelinePlanning');await planning.waitFor();
   await page.getByLabel('Owner').fill('Sam');await page.getByLabel('Team',{exact:true}).fill('Accounts payable');
   await page.getByLabel('Target quarter').fill('2027-Q1');await page.getByLabel('Budget',{exact:true}).fill('12000');
   await page.getByLabel('Currency (ISO code)').fill('eur');
   await planning.getByLabel(/Clean vendor master/).check();
   await page.locator('#owgPlanSave').click();
   await page.locator('#owgPlanStatus').filter({hasText:'Planning saved.'}).waitFor();
   assert.equal(await page.getByLabel('Owner').inputValue(),'Sam');
   assert(await planning.getByLabel(/Clean vendor master/).isChecked());
   // A cycle is refused with the server's reason.
   await page.getByRole('button',{name:'Open idea Clean vendor master'}).click();
   await page.locator('#owgPipelinePlanning').getByLabel(/Auto-match invoices/).check();
   await page.locator('#owgPlanSave').click();
   await page.locator('#owgPlanStatus').filter({hasText:/cycle/}).waitFor();
   await page.locator('#owgPipelinePortfolio').click();
   const team=page.locator('#owgPipelinePortfolio_team');await team.waitFor();
   const rows=await team.locator('tbody tr').allTextContents();
   assert(rows.some(r=>r.startsWith('Accounts payable')&&r.includes('12,000 EUR estimated')),JSON.stringify(rows));
   assert(rows.some(r=>r.startsWith('Not set')),JSON.stringify(rows));
   assert.match(await page.locator('#owgPipelineBlocked').textContent(),/Auto-match invoices waits on Clean vendor master \(Idea\)/);
   assert.match(await page.locator('#owgPipelinePortfolioResult').textContent(),/Open ideas by target quarter: 2027-Q1: 1\. 1 ideas have no planning yet\./);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPipelineCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Automation portfolio browser acceptance passed: owner, team, quarter, budget and dependency saved, cycle refused, portfolio by team with per-currency budget, blocked dependency, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
