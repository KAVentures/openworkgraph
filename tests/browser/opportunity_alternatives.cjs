const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: CSV preview -> opportunity review -> side-by-side implementation alternatives.
(async()=>{
  const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-alternatives-browser-'));
  const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
  const server=spawn(python,['-m','uvicorn','process_logs_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
    env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
  let logs='',browser,spawnError;
  server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
  try{
    let ready=false;for(let i=0;i<100;i++){
      if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic local server exited: '+logs);
      try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
      await new Promise(resolve=>setTimeout(resolve,200));
    }if(!ready)throw new Error('Synthetic local server not ready: '+logs);
    browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
    const page=await browser.newPage(),errors=[];let writes=0;
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{const p=new URL(request.url()).pathname;if(request.method()==='POST'&&p!=='/v1/process-mining/preview')writes++;});
    const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
    await page.goto(base+'/#session='+session);
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.locator('#owgProcessCSV').waitFor();
    const csv='case_id,activity,timestamp\na,Open,2026-10-20T10:00:00Z\na,Review,2026-10-20T10:05:00Z\na,Review,2026-10-20T10:07:00Z\na,Close,2026-10-20T10:20:00Z\nb,Open,2026-10-20T11:00:00Z\nb,Close,2026-10-20T11:30:00Z\n';
    await page.getByLabel('Choose business event log CSV').setInputFiles({name:'log.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
    await page.getByRole('button',{name:'Analyze selected CSV',exact:true}).click();
    await page.locator('#owgOpportunityAlternatives').waitFor();
    const fill=async(label,value)=>page.getByLabel(label,{exact:true}).fill(String(value));
    await page.locator('[data-opportunity-field=currency]').selectOption('SEK');
    await fill('Cases per month',200);await fill('Loaded labor cost per hour',600);
    const rows={'Eliminate the step':[10,20,0,5000],'Deterministic automation (API or rules)':[8,60,1000,40000],
      'AI agent':[9,80,4000,60000],'AI agent with human approval':[9,80,4000,60000]};
    for(const [label,[minutes,pct,recurring,once]] of Object.entries(rows)){
      await fill(label+' minutes saved per eligible case',minutes);await fill(label+' cases eligible percent',pct);
      await fill(label+' recurring monthly cost',recurring);await fill(label+' one-time cost',once);
    }
    await page.getByRole('button',{name:'Compare alternatives',exact:true}).click();
    await page.locator('#owgOpportunityAlternativesResult').filter({hasText:'Missing assumption: ai_agent_with_approval.review_minutes_per_case'}).waitFor();
    await fill('AI agent with human approval human review minutes per case',3);
    await page.getByRole('button',{name:'Compare alternatives',exact:true}).click();
    const result=page.locator('#owgOpportunityAlternativesResult');
    await result.filter({hasText:'Ordered by your modeled net value only'}).waitFor();
    const rowsText=await result.locator('tr').allTextContents();
    assert.match(rowsText[1],/^AI agent9SEK 10400/);
    assert.match(rowsText[2],/^Deterministic automation \(API or rules\)8SEK 8600/);
    assert.match(rowsText[3],/^AI agent with human approval6SEK 5600.*No$/);
    assert.match(rowsText[5],/^Keep human-only \(baseline\)0SEK 0/);
    // Changing a shared assumption clears the stale comparison.
    await fill('Cases per month',300);
    assert.equal(await result.locator('tr').count(),0);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
    assert.equal(writes,0);assert.deepEqual(errors,[]);
    console.log('Opportunity alternatives browser acceptance passed: CSV preview → per-alternative assumptions → validation, ordered comparison, stale reset, mobile width, no writes.');
  }finally{
    if(browser)await browser.close();server.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
    if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
