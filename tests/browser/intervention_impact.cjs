const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-impact-'));
 const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
 // One case every 6 hours from 1 January. Team A's cases take 48 h before 1 March and 24 h after; team B's always 48 h.
 const rows=['case_id,activity,time,who'],start=Date.parse('2024-01-01T00:00:00Z'),cutoff=Date.parse('2024-03-01T00:00:00Z');
 for(let i=0;i<480;i++){
  const t=start+i*6*3600e3,team=i%2?'A':'B',hours=(team==='A'&&t>=cutoff?24:48)+(i%3-1);
  rows.push(`c${i},Open,${new Date(t).toISOString()},${team}`,`c${i},Close,${new Date(t+hours*3600e3).toISOString()},${team}`);
 }
 const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>{spawnError=e;});for(const stream of [server.stdout,server.stderr])stream.on('data',c=>{logs=(logs+c).slice(-10000);});
 try{
  let ready=false;
  for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
   try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  await page.locator('#owgEventLogFile').setInputFiles({name:'impact.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
  await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
  await page.locator('#owgEventLogActivity').selectOption('activity');await page.locator('#owgEventLogResource').selectOption('who');
  await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
  await page.locator('#owgEventLogStatus').filter({hasText:'Imported 960 events'}).waitFor();
  await page.locator('#owgEventLogAnalyze').click();
  const out=page.locator('#owgImpactResults');await page.locator('#owgImpact').waitFor();
  // A missing date is caught before any request.
  await page.locator('#owgImpactRun').click();
  await out.getByText('Enter the date the change went live.',{exact:true}).waitFor();
  await page.locator('#owgImpactDate').fill('2024-03-01');
  const [plain]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/impact')),page.locator('#owgImpactRun').click()]);
  assert.equal(plain.status(),200,await plain.text());
  assert.deepEqual(JSON.parse(plain.request().postData()),{intervention_at:'2024-03-01T00:00:00Z',metric:'cycle_time'});
  await out.getByText(/^The mean cycle time went down after the intervention/).waitFor();
  assert.match(await out.textContent(),/Placebo check at .*no earlier change\./);
  // Team A only: compared with team B over the same periods, about 24 h faster.
  await page.locator('#owgImpactTreatedResource').fill('A');
  const [treated]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/impact')),page.locator('#owgImpactRun').click()]);
  const body=await treated.json();
  assert.ok(Math.abs(body.control_adjusted.difference_in_differences+24*3600)<0.5*3600,JSON.stringify(body.control_adjusted));
  await out.getByText(/^Against the control cohort: −2[34]\.\d h/).waitFor();
  assert.equal(await out.getByRole('cell',{name:'Treated',exact:true}).count(),1);
  // The SLA field appears only for SLA breaches.
  assert.equal(await page.locator('#owgImpactSla').isVisible(),false);
  await page.locator('#owgImpactMetric').selectOption('sla_breach');
  assert.equal(await page.locator('#owgImpactSla').isVisible(),true);
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgImpact'),{
   runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},resultTypes:['violations']}));
  assert.deepEqual(audit.violations.map(v=>v.id),[]);
  await page.setViewportSize({width:390,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'no horizontal page scroll at 390 px');
  assert.deepEqual(errors,[]);
  console.log('Intervention impact browser passed: local validation, before/after with placebo, control-adjusted change for a treated resource, metric-dependent fields, WCAG and 390 px width.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exit(1);});
