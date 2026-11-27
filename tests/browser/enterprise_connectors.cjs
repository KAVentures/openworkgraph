/* Actual local dashboard acceptance. No provider request is made by this fixture. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-enterprise-connectors-'));
  const base='http://127.0.0.1:8787',endpoint='https://customer.example.com/events';
  const env={...process.env,OWG_BROWSER_TEST_DIR:scratch,OWG_REST_ALLOWED_ENDPOINTS:JSON.stringify([endpoint]),OWG_REST_TOKEN_BROWSER:'browser-fixture-only'};
  const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env,stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});
  for(const pipe of [server.stdout,server.stderr])pipe.on('data',c=>{logs=(logs+c).slice(-8000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw Error(logs);
      try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
      await new Promise(r=>setTimeout(r,200));}
    assert.ok(ready,logs);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const context=await browser.newContext({bypassCSP:true}),page=await context.newPage(),errors=[],foreign=[];
    page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
    page.on('request',r=>{if(new URL(r.url()).origin!==base)foreign.push(r.url());});
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+encodeURIComponent(session));
    await page.goto(base+'/enterprise-connectors');
    await page.locator('#status').filter({hasText:'No provider reads performed.'}).waitFor();
    assert.match(await page.locator('#inventory').textContent(),/Live provider health is unknown/);
    await page.locator('summary').filter({hasText:'Create a feed'}).click();
    await page.locator('#name').fill('Browser fixture');
    await page.locator('#endpoint').fill(endpoint);
    await page.locator('#credential').fill('OWG_REST_TOKEN_BROWSER');
    await page.locator('#since').fill(new Date(Date.now()-86400000).toISOString());
    await page.locator('#confirmConfig').check();await page.locator('#confirmLabels').check();
    await page.locator('#createButton').click();
    await page.locator('#restStatus').filter({hasText:'Feed saved. No source was read.'}).waitFor();
    await page.getByRole('heading',{name:'Browser fixture'}).waitFor();
    assert.ok(await page.getByRole('button',{name:'Run next window'}).isDisabled());
    await page.getByLabel('Authorize recurring reads and local retention for this exact feed revision while recording is enabled.').check();
    await page.getByRole('button',{name:'Create recurring schedule'}).click();
    await page.locator('#restStatus').filter({hasText:'Recurring schedule created'}).waitFor();
    await page.getByRole('button',{name:'Remove schedule'}).waitFor();
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download').click()]);
    const report=JSON.parse(await fs.readFile(await download.path(),'utf8'));
    assert.equal(report.provider_reads_performed,false);
    assert.equal(report.profiles.find(p=>p.provider==='rest').schedules.length,1);
    assert.ok(!JSON.stringify(report).includes(endpoint));
    await page.getByRole('button',{name:'Disable feed'}).click();
    await page.locator('#restStatus').filter({hasText:'Feed revision changed.'}).waitFor();
    await page.getByRole('button',{name:'Enable feed'}).waitFor();
    assert.match(await page.locator('#inventory').textContent(),/schedule profile revision mismatch/);
    await page.getByRole('button',{name:'Remove schedule'}).click();
    await page.locator('#restStatus').filter({hasText:'Schedule removed.'}).waitFor();
    await page.getByRole('button',{name:'Remove configuration'}).click();
    await page.locator('#profiles').getByText('No configured REST feeds.').waitFor();
    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const axe=await page.evaluate(()=>window.axe.run(document.querySelector('main'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}));
    assert.deepEqual(axe.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:840});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
    assert.deepEqual(errors,[]);assert.deepEqual(foreign,[]);
  }finally{await browser?.close();server.kill('SIGTERM');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
