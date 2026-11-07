// Real Chromium acceptance: one local review queue across two imported datasets.
// The data and all credentials are synthetic and remain on a temporary local app.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-signal-overview-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const base='http://127.0.0.1:8787';
  const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser',
    '--host','127.0.0.1','--port','8787'],{
      env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},
      stdio:['ignore','pipe','pipe']
    });
  let logs='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});
  for(const stream of [server.stdout,server.stderr])
    stream.on('data',chunk=>{logs=(logs+chunk).slice(-10000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Server exited: '+logs);
      try{
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert(ready,'Browser fixture not ready: '+logs);
    browser=await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),
      headless:true,args:['--no-sandbox']
    });
    const page=await browser.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+session);
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.locator('#owgEventLogCard').waitFor();

    const malicious='<img src=x onerror="window.signalOverviewInjected=true">';
    const csv='case,activity,time\nC1,Open,2024-01-01T00:00:00Z\nC1,Close,2024-01-02T00:00:00Z\n';
    const setup=await page.evaluate(async({csv,malicious})=>{
      async function json(url,options={}){
        const r=await fetch(url,{...options,cache:'no-store'});
        const d=await r.json().catch(()=>({}));
        if(!r.ok)throw Error(url+' '+r.status+' '+JSON.stringify(d));
        return d;
      }
      const create=async name=>json('/v1/event-logs/import?'+new URLSearchParams({
        format:'csv',name,case_column:'case',activity_column:'activity',timestamp_column:'time'
      }),{method:'POST',headers:{'x-owg-local-storage-confirmed':'true'},body:csv});
      const a=await create('Accounts A');
      const b=await create('Accounts B');
      const add=async(log,name,threshold)=>json('/v1/event-logs/'+log+'/signals',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name,when:{kind:'kpi',metric:'case_count',operator:'above',threshold}})
      });
      await add(a.log_id,malicious,0);
      await add(a.log_id,'Never triggered',99);
      await add(b.log_id,'Not evaluated',0);
      await json('/v1/event-logs/'+a.log_id+'/signals/evaluate',{method:'POST'});
      return {a:a.log_id,b:b.log_id};
    },{csv,malicious});

    // The two imported datasets are shown in the original selector. The
    // overview is built inside the existing Signals panel after analysis.
    await page.reload();
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.waitForFunction(()=>document.querySelector('#owgEventLogPick').options.length===2);
    await page.locator('#owgEventLogPick').selectOption(setup.b);
    await page.locator('#owgEventLogAnalyze').click();
    await page.locator('#owgSignalAllStatus').filter({hasText:/2 signal states need attention across 2 saved logs/}).waitFor();
    const rows=page.locator('#owgSignalAllResults tbody tr');
    assert.equal(await rows.count(),2);
    assert.equal(await page.locator('#owgSignalAllResults img').count(),0);
    assert.equal(await page.evaluate(()=>window.signalOverviewInjected),undefined);
    assert.match(await page.locator('#owgSignalAllResults').textContent(),/Not evaluated/);
    const serverState=await page.evaluate(async()=>{
      const response=await fetch('/v1/event-logs/signal-overview');
      return response.json();
    });
    assert.equal(serverState.total_rules,3);
    assert.equal(serverState.counts.unreviewed,1);
    assert.equal(serverState.counts.cleared,1);
    assert.equal(serverState.counts.not_evaluated,1);
    assert.equal(serverState.items.length,2);
    assert(!JSON.stringify(serverState).includes('evidence_cases'));
    assert(!JSON.stringify(serverState).includes('state_fingerprint'));

    await page.locator('#owgSignalAllFilter').selectOption('all');
    await page.locator('#owgSignalAllStatus').filter({hasText:'Showing 3 of 3 matching rules'}).waitFor();
    assert.equal(await rows.count(),3);
    await page.locator('#owgSignalAllFilter').selectOption('unreviewed');
    await page.locator('#owgSignalAllStatus').filter({hasText:'Showing 1 of 1 matching rules'}).waitFor();
    assert.equal(await rows.count(),1);

    // Deep-link from the cross-log list into the preexisting exact-fingerprint
    // per-log review UI. It must select the correct dataset first.
    await rows.first().getByRole('button',{name:/Open dataset Accounts A to review signal/}).click();
    await page.waitForFunction(id=>document.querySelector('#owgEventLogPick')?.value===id,setup.a);
    await page.locator('#owgSignalInbox').getByRole('button',{name:/Acknowledge signal/}).first().click();
    await page.locator('#owgSignalInbox').getByRole('button',{name:/Reopen signal/}).waitFor();
    await page.locator('#owgSignalAllRefresh').click();
    await page.locator('#owgSignalAllStatus').filter({hasText:/1 signal states need attention across 2 saved logs/}).waitFor();
    assert.equal(await page.locator('#owgSignalAllResults tbody tr').count(),0,
      'unreviewed filter must no longer show the acknowledged signal');

    await page.locator('#owgSignalAllFilter').selectOption('not_evaluated');
    await page.locator('#owgSignalAllStatus').filter({hasText:'Showing 1 of 1 matching rules'}).waitFor();
    await page.locator('#owgSignalAllResults').getByRole('button',{name:/Open dataset Accounts B/}).click();
    await page.waitForFunction(id=>document.querySelector('#owgEventLogPick')?.value===id,setup.b);
    await page.locator('#owgSignalInbox').filter({hasText:'not evaluated'}).waitFor();

    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(()=>window.axe.run(document.querySelector('#owgEventLogSignals'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}
    }));
    assert.deepEqual(audit.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
    assert.deepEqual(errors,[]);
    console.log('R10 cross-log signal overview Chromium passed: real two-log import, state counts, safe labels, status filtering, deep link, exact-state acknowledgement, WCAG and mobile.');
  }finally{
    if(browser)await browser.close();
    server.kill('SIGTERM');
    await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
