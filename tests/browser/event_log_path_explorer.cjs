const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: walk the process one step at a time.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-footprints-'));
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
   // 6 approved, 3 rejected, 1 closed at once; 2 drafts that open later and get approved.
   const plan=[...Array(6).fill([['Open',0],['Check',1],['Approve',3],['Close',4]]),...Array(3).fill([['Open',0],['Check',2],['Reject',5]]),
     [['Open',0],['Close',1]],...Array(2).fill([['Draft',0],['Open',1],['Check',2],['Approve',6]])];
   const rows=['case,activity,time,who'];
   plan.forEach((steps,i)=>steps.forEach(([a,h])=>rows.push(`c${i},${a},2024-01-${String(1+i).padStart(2,'0')}T${String(h).padStart(2,'0')}:00:00Z,ana`)));
   await page.locator('#owgEventLogFile').setInputFiles({name:'requests.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 43 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   // The plain-words summary: figures from this log, and a Show button that jumps to the path explorer.
   await page.locator('#owgStoryRun').click();
   await page.locator('#owgStoryText').filter({hasText:'The log has 12 cases with 43 events across 6 activities'}).waitFor();
   await page.locator('#owgStoryText li',{hasText:'The most common path'}).getByRole('button',{name:/Show the analysis/}).click();
   assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Path explorer');
   await page.locator('#owgActivityProfilePick').selectOption('Check');await page.locator('#owgActivityProfileRun').click();
   await page.locator('#owgActivityProfileStatus').filter({hasText:'Check happens in 11 of 12 cases'}).waitFor();
   const profile=page.locator('#owgActivityProfileResults');
   assert.match(await profile.textContent(),/Comes afterActivityTimesShareMedian gapEvidenceOpen11/);
   await profile.getByRole('button',{name:'Cases without Check',exact:true}).click();
   await page.locator('#owgEventLogCasesStatus').getByText('1 matching cases. Showing 1 on this page.').waitFor();
   await profile.getByRole('button',{name:'Cases with Open → Check',exact:true}).click();
   await page.locator('#owgEventLogCasesStatus').getByText('11 matching cases. Showing 11 on this page.').waitFor();
   await profile.getByRole('button',{name:'Cases repeating Check',exact:true}).waitFor();
   const box=page.locator('#owgPaths');await box.waitFor();await box.scrollIntoViewIfNeeded();
   const rowsText=async()=>(await page.locator('#owgPathsResults tbody tr').allTextContents()).join('|');
   await page.locator('#owgPathsStart').click();
   await page.locator('#owgPathsStatus').filter({hasText:'12 cases start with these activities.'}).waitFor();
   await page.locator('#owgPathsResults button',{hasText:'Go to Open'}).click();
   await page.locator('#owgPathsStatus').filter({hasText:'10 cases (83.3%) reach this point'}).waitFor();
   assert.equal(await page.locator('#owgPathsTrail').textContent(),'Path: (start) → Open');
   await page.locator('#owgPathsResults button',{hasText:'Go to Check'}).click();
   await page.locator('#owgPathsStatus').filter({hasText:'9 cases (75%) reach this point, in a median'}).waitFor();
   const deeper=await rowsText();assert(deeper.startsWith('Approve666.7%')&&deeper.includes('Reject333.3%'),deeper);
   // The branches at this point download with the path that leads there.
   const [pathCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPathsReportCsv').click()]);
   const pathLines=(await fs.readFile(await pathCsv.path(),'utf8')).trim().split(/\r?\n/);
   assert.equal(pathLines[0],'path,anchor,next,cases,share,median_hours_from_previous,median_hours_to_case_end');
   assert.match(pathLines[1],/^Open → Check,start,Approve,6,0\.66\d*,/);assert.match(pathLines[2],/^Open → Check,start,Reject,3,0\.33\d*,/);
   const review=page.locator('#owgPathsCaseReview');
   await box.getByRole('button',{name:'Inspect cases continuing to Reject'}).click();
   await review.getByRole('status').filter({hasText:'3 cases in this branch'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c6','c7','c8']);
   await review.getByRole('button',{name:'Open case c6'}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 3 of 3 events'}).waitFor();
   await page.locator('#owgPathsBack').click();
   await page.locator('#owgPathsStatus').filter({hasText:'10 cases (83.3%) reach this point'}).waitFor();
   assert.equal(await review.locator('h4').count(),0);
   await box.getByRole('button',{name:'Go to Close'}).click();
   await box.getByRole('button',{name:'Inspect cases ending here'}).click();
   await review.getByRole('status').filter({hasText:'1 case in this branch'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c9']);
   await page.locator('#owgPathsBack').click();
   await page.locator('#owgPathsStatus').filter({hasText:'10 cases (83.3%) reach this point'}).waitFor();
   await page.locator('#owgPathsAnywhere').check();
   await page.locator('#owgPathsStatus').filter({hasText:'12 cases (100%) reach this point.'}).waitFor();
   assert.equal(await page.locator('#owgPathsTrail').textContent(),'Path: … → Open');
   assert((await rowsText()).startsWith('Check1191.7%'),await rowsText());
   const [anchored]=await Promise.all([
     page.waitForResponse(response=>{
       const url=new URL(response.url());
       return url.pathname.endsWith('/next-steps')&&url.searchParams.getAll('step').length===2;
     }),
     page.locator('#owgPathsResults button',{hasText:'Go to Check'}).click()
   ]);
   assert.equal(new URL(anchored.url()).searchParams.get('anchor_steps'),'1');
   assert.equal((await anchored.json()).anchor_steps,1);
   await page.locator('#owgPathsStatus').filter({hasText:'11 cases'}).waitFor();
   assert.equal(await page.locator('#owgPathsResults button',{hasText:'Go to'}).count(),2);
   await box.getByRole('button',{name:'Inspect cases continuing to Approve'}).click();
   await review.getByRole('status').filter({hasText:'8 cases in this branch'}).waitFor();
   assert.deepEqual(await review.locator('tbody tr td:first-child').allTextContents(),['c0','c1','c10','c11','c2','c3','c4','c5']);
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPaths'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Path explorer browser acceptance passed: start, branches and event history, case endings, back, anywhere anchor, plain-words summary, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
