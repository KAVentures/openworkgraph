const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn,spawnSync}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-grouping-browser-'));
 const python=process.env.OWG_BROWSER_PYTHON||'python',base='http://127.0.0.1:8787';
 const server=spawn(python,['-m','uvicorn','workflow_groupings_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8787'],{
   env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',error=>{spawnError=error;});for(const pipe of [server.stdout,server.stderr])pipe.on('data',chunk=>{logs=(logs+chunk).slice(-8000);});
 try{
   let ready=false;for(let i=0;i<100;i++){
     if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Synthetic grouping server exited: '+logs);
     try{await fs.access(path.join(scratch,'original-events.json'));if((await fetch(base+'/')).ok){ready=true;break;}}catch{}
     await new Promise(resolve=>setTimeout(resolve,200));
   }if(!ready)throw new Error('Synthetic grouping server not ready: '+logs);
   browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
   const page=await browser.newPage(),errors=[];let writes=0;
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname.startsWith('/v1/workflow-groupings'))writes++;});
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=overview]').click();
   await page.getByRole('button',{name:'Explore workflows',exact:true}).click();
   await page.locator('#owgExplorerStatus').filter({hasText:'candidate groups shown'}).waitFor();
   await page.locator('#owgExplorerCandidates button').first().click();
   await page.getByRole('button',{name:'Name or regroup selected examples',exact:true}).click();
   await page.getByLabel('Reviewed workflow group name').fill('Invoice review');
   assert.equal(writes,0);
   await page.locator('#owgGroupConfirmed').check();await page.getByRole('button',{name:'Save reviewed group',exact:true}).click();
   await page.locator('#owgGroupStatus').filter({hasText:'Saved reviewed group revision 1.'}).waitFor();
   assert.equal(writes,1);assert.equal(await page.locator('#owgGroupMembers input').count(),3);
   assert.match(await page.locator('#owgExplorerDetail h3').textContent(),/Reviewed group · Invoice review/);
   await page.reload();await page.locator('[role=tab][data-tab=overview]').click();
   await page.getByRole('button',{name:'Load reviewed workflow groups',exact:true}).click();
   await page.getByRole('button',{name:'Invoice review · r1 · 3 executions',exact:true}).click();
   await page.getByLabel('Include grouping execution 2',{exact:true}).uncheck();
   await page.getByLabel('Include grouping execution 3',{exact:true}).uncheck();
   await page.getByLabel('Split workflow group name').fill('Escalations');await page.locator('#owgGroupConfirmed').check();
   await page.getByRole('button',{name:'Split checked executions into a new group',exact:true}).click();
   await page.locator('#owgGroupStatus').filter({hasText:'Moved only the checked classifications.'}).waitFor();
   assert.equal(await page.locator('#owgGroupMembers input').count(),1);
   await page.getByLabel('Target reviewed workflow group').selectOption({label:'Invoice review · r2 · 2 executions'});
   await page.locator('#owgGroupConfirmed').check();await page.getByRole('button',{name:'Move checked executions to target group',exact:true}).click();
   await page.locator('#owgGroupStatus').filter({hasText:'Moved only the checked classifications.'}).waitFor();
   await page.getByLabel('Reviewed workflow group name').fill('Accounts review');await page.locator('#owgGroupConfirmed').check();
   await page.getByRole('button',{name:'Save reviewed group',exact:true}).click();
   await page.locator('#owgGroupStatus').filter({hasText:'Saved reviewed group revision 4.'}).waitFor();
   await page.getByRole('button',{name:'Inspect group revision history',exact:true}).click();
   await page.locator('#owgGroupHistory').filter({hasText:'Revision 1 · Invoice review'}).waitFor();
   assert.match(await page.locator('#owgExplorerDetail h3').textContent(),/Reviewed group · Accounts review/);
   await page.evaluate(()=>{window.copyText=text=>{window.groupingHandoffForTest=text;};});
   await page.getByRole('button',{name:'Copy AI handoff request',exact:true}).click();
   const handoff=await page.evaluate(()=>window.groupingHandoffForTest);
   assert.match(handoff,/get_workflow_knowledge/);assert.match(handoff,/require revision 4/);
   await page.setViewportSize({width:390,height:844});
   assert.equal(await page.evaluate(()=>document.body.scrollWidth>window.innerWidth),false);
   await page.locator('#owgGroupConfirmed').check();page.once('dialog',dialog=>dialog.accept());
   await page.getByRole('button',{name:'Delete this reviewed group',exact:true}).click();
   await page.locator('#owgGroupStatus').filter({hasText:'Deleted the reviewed classification and its versions.'}).waitFor();
   const verified=spawnSync(python,['-c',`import json,sqlite3,sys
c=sqlite3.connect(sys.argv[1]);original=json.load(open(sys.argv[2]))
assert [list(row) for row in c.execute('SELECT * FROM events ORDER BY id')]==original
rows=c.execute('SELECT payload FROM workflow_knowledge').fetchall()
assert all('SUPERSECRET' not in row[0] and 'patient@example.com' not in row[0] for row in rows)
`,path.join(scratch,'data','workflow_observer.db'),path.join(scratch,'original-events.json')],{encoding:'utf8'});
   assert.equal(verified.status,0,verified.stderr);assert.deepEqual(errors,[]);
   console.log('Reviewed grouping browser acceptance passed: exact retained selection, explicit save, reload, split, merge, rename, immutable history, corrected map, mobile and byte-for-byte canonical row preservation.');
 }finally{
   if(browser)await browser.close();server.kill('SIGTERM');
   await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
   if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
