/* Real Chromium acceptance of full OWG → OCEL relationship timing explorer journey. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');

(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-object-timing-'));
  const base='http://127.0.0.1:8787',python=process.env.OWG_BROWSER_PYTHON||'python';
  const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser',
    '--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},
    stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});
  for(const stream of [server.stdout,server.stderr])
    stream.on('data',chunk=>{logs=(logs+chunk).slice(-10000);});
  try{
    let ready=false;
    for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;
      if(server.exitCode!==null)throw Error('Local server exited: '+logs);
      try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}
      catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }
    assert.ok(ready,'Local browser server did not start: '+logs);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?
      {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const page=await browser.newPage(), errors=[],offOrigin=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(new URL(request.url()).origin!==base)offOrigin.push(request.url());});
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+encodeURIComponent(session));
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.locator('#owgProcessCSV').waitFor();
    const nav=page.locator('#owgProcessCSV a[href="/object-relationship-lead-times"]');
    assert.equal(await nav.count(),1);

    const data={
      objectTypes:[{name:'order',attributes:[]},{name:'delivery',attributes:[]}],eventTypes:[],
      objects:[
        {id:'o1',type:'order',attributes:[],relationships:[{objectId:'d1',qualifier:'ships'}]},
        {id:'d1',type:'delivery',attributes:[],relationships:[]},
        {id:'o2',type:'order',attributes:[],relationships:[{objectId:'d2',qualifier:'ships'}]},
        {id:'d2',type:'delivery',attributes:[],relationships:[]},
      ],
      events:[
        {id:'a1',type:'Approve',time:'2026-01-01T08:00:00Z',attributes:[],relationships:[{objectId:'o1',qualifier:''}]},
        {id:'s1',type:'Ship',time:'2026-01-01T10:00:00Z',attributes:[],relationships:[{objectId:'d1',qualifier:''}]},
        {id:'a2',type:'Approve',time:'2026-01-01T10:00:00Z',attributes:[],relationships:[{objectId:'o2',qualifier:''}]},
        {id:'s2',type:'Ship',time:'2026-01-01T09:00:00Z',attributes:[],relationships:[{objectId:'d2',qualifier:''}]},
      ]
    };
    const seed=await page.evaluate(async payload=>{
      const response=await fetch('/v1/event-logs/import?name=BrowserOCEL&format=ocel',{
        method:'POST',headers:{'Content-Type':'application/json',
          'X-OWG-Local-Storage-Confirmed':'true'},body:JSON.stringify(payload)});
      return {code:response.status,body:await response.text()};
    },data);
    assert.equal(seed.code,200,seed.body);

    await nav.click();
    await page.locator('#logSelect option[value^="log_"]').waitFor();
    await page.locator('#relationshipSelect option[value="1"]').waitFor();
    await page.waitForFunction(()=>document.querySelector('#sourceActivity')?.value==='Approve'&&
      document.querySelector('#targetActivity')?.value==='Ship');
    assert.equal(await page.locator('#sourceType').inputValue(),'order');
    assert.equal(await page.locator('#targetType').inputValue(),'delivery');
    assert.equal(await page.locator('#qualifierMode').inputValue(),'literal');
    assert.equal(await page.locator('#qualifierValue').inputValue(),'ships');

    const [analysis]=await Promise.all([
      page.waitForResponse(response=>response.url().includes('/object-relationships/lead-times')),
      page.locator('#runQuery').click()
    ]);
    assert.equal(analysis.status(),200,await analysis.text());
    assert.equal((await analysis.json()).forward_pairs,1);
    assert.equal((await analysis.json()).reversed_pairs,1);
    await page.locator('#resultSection:not([hidden])').waitFor();
    assert.match(await page.locator('#metrics').textContent(),/Distinct related pairs2/);
    assert.match(await page.locator('#metrics').textContent(),/Reversed ordering1/);
    assert.match(await page.locator('#metrics').textContent(),/Median forward elapsed time2\.00 h/);
    assert.equal(await page.locator('#sampleRows tr').count(),2);
    assert.ok(!(await page.locator('#resultSection').isHidden()));

    // Aggregate-only mode and literal empty-qualifier matching are independent.
    await page.locator('#sampleLimit').fill('0');
    await page.locator('#runQuery').click();
    await page.locator('#sampleStatus').getByText?.call; // no-op does not alter page
    await page.waitForFunction(()=>document.querySelector('#sampleStatus')?.textContent?.includes('No pair examples'));
    assert.equal(await page.locator('#sampleRows tr').count(),0);
    await page.locator('#qualifierMode').selectOption('empty');
    await page.locator('#runQuery').click();
    await page.waitForFunction(()=>document.querySelector('#status')?.textContent?.includes('complete'));
    assert.match(await page.locator('#metrics').textContent(),/Distinct related pairs0/);

    // The document never requires cloud destinations or scripts from other origins.
    await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
    const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('main'),{
      runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},
      resultTypes:['violations']}));
    assert.deepEqual(audit.violations.map(v=>v.id),[]);
    await page.setViewportSize({width:390,height:820});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2),
      'mobile page must not overflow; only the samples table may scroll');
    assert.deepEqual(errors,[]);
    assert.deepEqual(offOrigin,[]);

    const anonymous=await browser.newPage();
    await anonymous.goto(base+'/object-relationship-lead-times');
    assert.match(await anonymous.locator('#status').textContent(),/Open this page from the same browser tab/);
    assert.equal(await anonymous.locator('#resultSection').isHidden(),true);
    assert.equal(await anonymous.locator('#logSelect').isDisabled(),true);
    await anonymous.close();
  } finally {
    await browser?.close();
    server.kill('SIGTERM');
    await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
