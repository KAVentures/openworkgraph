const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: the Privacy card replays one recorded visit locally (#634).
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-playback-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',port=8794,base='http://127.0.0.1:'+port;
 const axeSource=await fs.readFile(require.resolve('axe-core/axe.min.js'),'utf8');
 const server=spawn(python,['-m','uvicorn','interaction_playback_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port',String(port)],{env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;server.on('error',e=>{spawnError=e;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',c=>{logs=(logs+c).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<150;i++){if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Server exited: '+logs);
     try{await fs.access(path.join(scratch,'session.txt'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,200));}
   if(!ready)throw new Error('Server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('#tab-privacy').click();
   const card=page.locator('#pvCaptureDepth');await card.waitFor();
   await page.locator('#pvDepthPlayback').click();
   const steps=page.locator('#pvDepthPlaybackSteps li');await steps.first().waitFor();
   assert.deepEqual(await steps.allTextContents(),['4.0 s · Rested on a menuitem for 0.9 s','5.0 s · Clicked a menuitem']);
   assert.match(await page.locator('#pvDepthPlaybackVisit').textContent(),/\(4 events\)/);
   const line=page.locator('#pvDepthPlaybackView polyline');
   assert.equal((await line.getAttribute('points')).split(' ').length,11,'the whole path is drawn at the end of the visit');
   assert.match(await page.locator('#pvDepthPlaybackNow').textContent(),/2 of 2 interactions/);
   // Scrub to the start: only the first point is shown and no interaction has happened yet.
   await page.locator('#pvDepthPlaybackTime').fill('0');
   assert.equal((await line.getAttribute('points')).split(' ').length,1);
   assert.match(await page.locator('#pvDepthPlaybackNow').textContent(),/^0\.0 s .* 0 of 2 interactions/);
   await page.locator('#pvDepthPlaybackPlay').click();
   await page.locator('#pvDepthPlaybackNow').filter({hasText:/2 of 2 interactions/}).waitFor({timeout:15000});
   await page.locator('#pvDepthPlaybackPlay').filter({hasText:'Play'}).waitFor();
   const text=await card.textContent();
   assert.doesNotMatch(text,/Ada|Refund/,'labels are never replayed');
   await page.addScriptTag({content:axeSource});
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#pvCaptureDepth'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id),[]);
   await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
   assert.deepEqual(errors,[]);
   console.log('Interaction replay browser acceptance passed: visit list, ordered steps with control kinds only, pointer path drawn up to the scrubbed time, play to the end, no labels, WCAG audit, mobile width.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
