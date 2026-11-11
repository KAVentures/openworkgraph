/* Chromium E2E: OWG dashboard → authenticated OCEL binding evidence explorer. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-object-bindings-'));
  const base='http://127.0.0.1:8787';
  const python=process.env.OWG_BROWSER_PYTHON||'python';
  const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser',
    '--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},
    stdio:['ignore','pipe','pipe']});
  let log='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});
  [server.stdout,server.stderr].forEach(stream=>stream.on('data',chunk=>{
    log=(log+chunk).slice(-12000);
  }));
  try {
    let ready=false;
    for(let attempt=0;attempt<100;attempt++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Local server exited: '+log);
      try{
        await fs.access(path.join(scratch,'session.txt'));
        if((await fetch(base+'/')).ok){ready=true;break;}
      }catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert.ok(ready,'Local browser fixture did not start: '+log);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?
      {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const context=await browser.newContext({bypassCSP:true}); // Only for injecting Axe in the test.
    const page=await context.newPage(),errors=[],foreign=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(new URL(request.url()).origin!==base)foreign.push(request.url());});
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+encodeURIComponent(session));
    await page.locator('[role=tab][data-tab=evidence]').click();
    const entry=page.locator('#ocelBindingContinuityCard a[href="/object-binding-continuity"]');
    assert.equal(await entry.count(),1);
    const document={
      objectTypes:[{name:'order',attributes:[]},{name:'item',attributes:[]}],eventTypes:[],
      objects:[
        {id:'o1',type:'order',attributes:[],relationships:[{objectId:'i1',qualifier:'fulfils'}]},
        {id:'i1',type:'item',attributes:[],relationships:[]},
        {id:'o2',type:'order',attributes:[],relationships:[{objectId:'i2',qualifier:'fulfils'}]},
        {id:'i2',type:'item',attributes:[],relationships:[]}
      ],
      events:[
        {id:'j1',type:'Pack',time:'2026-01-01T09:00:00Z',attributes:[],relationships:[
          {objectId:'o1',qualifier:'from'},{objectId:'i1',qualifier:'to'},
          {objectId:'i1',qualifier:'duplicate-role'}]},
        {id:'j2',type:'Ship',time:'2026-01-01T10:00:00Z',attributes:[],relationships:[
          {objectId:'o1',qualifier:''},{objectId:'i1',qualifier:''}]},
        {id:'s1',type:'Pack',time:'2026-01-01T09:00:00Z',attributes:[],relationships:[
          {objectId:'o2',qualifier:''}]},
        {id:'s2',type:'Pack',time:'2026-01-01T09:00:00Z',attributes:[],relationships:[
          {objectId:'i2',qualifier:''}]},
        {id:'j3',type:'Ship',time:'2026-01-01T10:00:00Z',attributes:[],relationships:[
          {objectId:'o2',qualifier:''},{objectId:'i2',qualifier:''}]}
      ]
    };
    const importResult=await page.evaluate(async data=>{
      const resp=await fetch('/v1/event-logs/import?name=JointBindingBrowser&format=ocel',{
        method:'POST',headers:{'Content-Type':'application/json',
          'X-OWG-Local-Storage-Confirmed':'true'},body:JSON.stringify(data)});
      return {status:resp.status,body:await resp.text()};
    },document);
    assert.equal(importResult.status,200,importResult.body);
    await entry.click();
    await page.locator('#logSelect').waitFor();
    await page.waitForFunction(()=>{
      const relations=document.querySelector('#relationship');
      const activities=document.querySelector('#knownActivities');
      return relations && !relations.disabled && relations.options.length>1 &&
             activities && activities.options.length>=2;
    });
    await page.locator('#firstActivity').fill('Pack');
    await page.locator('#thenActivity').fill('Ship');
    const [response]=await Promise.all([
      page.waitForResponse(r=>r.url().endsWith('/object-bindings/continuity')),
      page.locator('#analyze').click()
    ]);
    assert.equal(response.status(),200,await response.text());
    const result=await response.json();
    assert.equal(result.directed_pairs,2);
    assert.equal(result.forward_pairs,1);
    assert.equal(result.then_only_pairs,1);
    assert.equal(result.joint_pair_event_witnesses,3);
    assert.equal(result.co_attendance_requires_same_event_id,true);
    assert.equal(result.supplied_relationship_valid_at_event_time_verified,false);
    assert.equal(result.full_object_centric_petri_net_binding_replay,false);
    await page.locator('#results:not([hidden])').waitFor();
    assert.match(await page.locator('#metrics').textContent(),/2Directed supplied pairs/);
    assert.match(await page.locator('#caveats').textContent(),/not full object-centric Petri-net conformance/);
    assert.equal(await page.locator('#rows tr').count(),2);
    assert.match(await page.locator('#rows').textContent(),/then_only/);

    await page.locator('#qualifierMode').selectOption('empty');
    assert.equal(await page.locator('#results').isHidden(),true,'edited inputs invalidate stale report');
    const [empty]=await Promise.all([
      page.waitForResponse(r=>r.url().endsWith('/object-bindings/continuity')),
      page.locator('#analyze').click()
    ]);
    assert.equal(empty.status(),200,await empty.text());
    assert.equal((await empty.json()).directed_pairs,0);

    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const axe=await page.evaluate(async()=>window.axe.run(document.querySelector('main'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},
      resultTypes:['violations']
    }));
    assert.deepEqual(axe.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:840});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2),
      'Mobile viewport must only horizontally scroll inside the table');
    assert.deepEqual(errors,[]);
    assert.deepEqual(foreign,[]);

    const guest=await browser.newContext();
    const anonymous=await guest.newPage();
    await anonymous.goto(base+'/object-binding-continuity');
    assert.match(await anonymous.locator('#status').textContent(),/Open from the same tab/);
    assert.equal(await anonymous.locator('#logSelect').isDisabled(),true);
    assert.equal(await anonymous.locator('#results').isHidden(),true);
    await guest.close();
  } finally {
    await browser?.close();
    server.kill('SIGTERM');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
