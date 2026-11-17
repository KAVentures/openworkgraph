const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-coverage-'));
 const base='http://127.0.0.1:8787';
 const server=spawn(process.env.OWG_BROWSER_PYTHON||'python',['-m','uvicorn','quality_coverage_pages_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let browser,logs='';for(const stream of [server.stdout,server.stderr])stream.on('data',c=>logs=(logs+c).slice(-10000));
 try{
  let ready=false;for(let i=0;i<100;i++){if(server.exitCode!==null)throw Error(logs);try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}assert.ok(ready,logs);
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#session='+(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim());
  await page.locator('[role=tab][data-tab=evidence]').click();await page.locator('#owgEventLogCard').waitFor();
  const option=page.locator('#owgEventLogPick option').filter({hasText:'Coverage pages'});await option.waitFor({state:'attached'});await page.locator('#owgEventLogPick').selectOption(await option.getAttribute('value'));await page.locator('#owgEventLogAnalyze').click();
  await page.locator('#owgQuality summary').click();await page.locator('#owgQualityCoverageBrowse').click();
  const result=page.locator('#owgQualityCoveragePages');await result.filter({hasText:'Showing 1–32 of 40'}).waitFor();
  assert.equal(await page.locator('#owgQualityCoveragePrevious').isDisabled(),true);
  await page.locator('#owgQualityCoverageNext').click();await result.filter({hasText:'Showing 33–40 of 40'}).waitFor();
  assert.match(await result.textContent(),/key39/);assert.doesNotMatch(await result.textContent(),/private-value/);
  await page.locator('#owgQualityCoveragePrevious').click();await result.filter({hasText:'Showing 1–32 of 40'}).waitFor();
  await page.addScriptTag({content:await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8')});
  const axe=await page.evaluate(()=>axe.run(document.querySelector('#owgEventLogCard'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
  assert.deepEqual(axe.violations.map(v=>v.id),[]);await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  console.log('Quality coverage browser passed: 40 keys, navigation, counts-only, accessibility and mobile.');
 }finally{if(browser)await browser.close();server.kill('SIGTERM');await Promise.race([new Promise(r=>server.once('exit',r)),new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exit(1);});
