const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {spawn,spawnSync} = require('node:child_process');
const {tmpdir} = require('node:os');
const path = require('node:path');
(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-log-browser-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const base='http://127.0.0.1:8787';
  const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
    env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});
  for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw new Error('Synthetic local server exited: '+logs);
      try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    if(!ready)throw new Error('Synthetic local server not ready: '+logs);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const page=await browser.newPage();const errors=[];let datasetRequests=0,saveRequests=0;
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/v1/process-mining/datasets')){
      datasetRequests++;if(request.method()==='POST'&&!request.url().includes('/forget'))saveRequests++;
    }});
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+session);
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.locator('#owgProcessCSV').waitFor();
    assert.equal(datasetRequests,0);
    const rows=['case_id,activity,timestamp,source'];
    for(let c=0;c<35;c++)for(let p=0;p<130;p++)rows.push('PRIVATE_CASE_'+c+',Step '+(p%3)+','+new Date(Date.UTC(2026,9,20,0,0,p)).toISOString()+',PRIVATE_SOURCE');
    await page.getByLabel('Choose business event log CSV').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
    assert.equal(datasetRequests,0);
    await page.getByRole('button',{name:'Analyze selected CSV',exact:true}).click();
    await page.locator('#owgProcessStatus').filter({hasText:'Local analysis complete.'}).waitFor();
    assert.equal(saveRequests,0);
    await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
    assert(await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).isDisabled());
    await page.getByLabel('I authorize retaining this selected event log locally.').check();
    await page.getByRole('button',{name:'Save analyzed log locally',exact:true}).click();
    await page.locator('#owgProcessStatus').filter({hasText:'Saved a minimized local snapshot.'}).waitFor();
    assert.equal(saveRequests,1);
    const open=page.getByRole('button',{name:/^Open log_/});await open.waitFor();
    const id=(await open.textContent()).slice(5);
    await page.reload();await page.locator('[role=tab][data-tab=evidence]').click();
    await page.getByText('Retain or reopen a local event-log snapshot',{exact:true}).click();
    await page.getByRole('button',{name:'List saved local logs',exact:true}).click();
    await page.getByRole('button',{name:'Open '+id,exact:true}).click();
    await page.locator('#owgProcessStatus').filter({hasText:'Opened retained local snapshot.'}).waitFor();
    await page.locator('#owgLogCases').getByText('Case 1 · 130 events · variant_1',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Next cases',exact:true}).click();
    const case35=page.locator('#owgLogCases details').filter({hasText:'Case 35 · 130 events'});
    await case35.locator('summary').click();
    await case35.getByRole('button',{name:'Next events',exact:true}).click();
    await case35.getByText(/^130\. Step 0/).waitFor();
    assert.equal(await case35.locator('li').count(),30);
    assert(!(await page.locator('#owgLogCases').textContent()).includes('PRIVATE_'));
    const disk=spawnSync(python,['-c',`import sqlite3,sys
c=sqlite3.connect(sys.argv[1]);text='\\n'.join(c.iterdump())
assert 'PRIVATE_CASE' not in text and 'PRIVATE_SOURCE' not in text
assert c.execute('SELECT COUNT(*) FROM process_log_events').fetchone()[0]==4550
`,path.join(scratch,'data','workflow_observer.db')],{encoding:'utf8'});
    assert.equal(disk.status,0,disk.stderr);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
    page.once('dialog',dialog=>dialog.accept());
    await page.getByRole('button',{name:'Delete '+id,exact:true}).click();
    await page.locator('#owgProcessStatus').filter({hasText:'Deleted local event-log snapshot.'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Open '+id,exact:true}).count(),0);
    assert.deepEqual(errors,[]);
    console.log('Local log browser acceptance passed: ephemeral preview, explicit retention, reload, all-case/event paging, minimized SQLite, deletion and mobile.');
  }finally{
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
