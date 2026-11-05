const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {spawn}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
// Real-browser acceptance: device sync reports -> production roster query -> admin console badges.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-fleet-sync-browser-'));
 const base='http://127.0.0.1:8792',token='admin-'+'x'.repeat(40);
 const python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','fleet_status_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8792'],{
  env:{...process.env,OWG_BROWSER_TEST_DIR:scratch},stdio:['ignore','pipe','pipe']});
 let logs='',browser,spawnError;
 server.on('error',e=>spawnError=e);for(const stream of [server.stdout,server.stderr])stream.on('data',chunk=>logs=(logs+chunk).slice(-8000));
 const api=async(method,url,body,credential=token)=>{
  const r=await fetch(base+url,{method,headers:{Authorization:'Bearer '+credential,'Content-Type':'application/json'},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
  const value=await r.json();assert(r.ok,JSON.stringify({status:r.status,value}));return value;
 };
 try{
  let ready=false;for(let i=0;i<100;i++){
   if(spawnError)throw spawnError;if(server.exitCode!==null)throw new Error('Owned Gateway exited: '+logs);
   try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}
   await new Promise(resolve=>setTimeout(resolve,200));
  }assert(ready,'Owned Gateway not ready: '+logs);
  await api('POST','/v1/admin/auth/bootstrap',{bootstrap_token:token,email:'owner@acme.se',display_name:'Owned admin'});
  const employee=await api('POST','/v1/admin/employees/acme',{email:'operator@acme.se',display_name:'=SUM(1,2)',teams:['support']});
  const invitation=await api('POST','/v1/admin/employees/acme/'+employee.employee_id+'/invites',{
   organization_name:'Owned Company',gateway_url:'https://gw.acme.test',max_devices:5});
  const reports={
   current:{sync_state:'connected',unprocessed_local_events:2,quarantined_events:0,last_success_age_seconds:4,sync_error_present:false},
   backlog:{sync_state:'syncing',unprocessed_local_events:700,quarantined_events:0,last_success_age_seconds:4,sync_error_present:false},
   errors:{sync_state:'error',unprocessed_local_events:9,quarantined_events:1,last_success_age_seconds:1200,sync_error_present:true},
   missing:null,
  };
  for(const [name,body] of Object.entries(reports)){
   const device=await api('POST','/v1/devices/enroll',{organization_id:'acme',device_id:'owned-'+name},invitation.token);
   if(body)assert.equal((await api('POST','/v1/device/sync-health',body,device.token)).contents_received,false);
  }
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.clock.install();
  await page.goto(base+'/admin');
  await page.locator('#orgIn').fill('acme');await page.locator('#tokenBox summary').click();await page.locator('#tokenIn').fill(token);
  await page.locator('#tokenSignIn').click();
  const stat=page.locator('#stats .stat').filter({hasText:'Sync problems reported'});
  await stat.waitFor();assert.equal((await stat.locator('b').textContent()).trim(),'2');
  await page.locator('nav button[data-tab=people]').click();
  for(const label of ['Sync reported current','Sync backlog reported','Sync errors reported','Sync status unknown']){
   await page.locator('#ppl .pill').filter({hasText:new RegExp('^'+label+'$')}).waitFor();
  }
  assert.equal(await page.locator('#fleetRows tr').count(),4);
  await page.locator('#fleetFilter').selectOption('problems');
  assert.equal(await page.locator('#fleetRows tr').count(),2);
  assert.match(await page.locator('#fleetSummary').textContent(),/^2 of 4/);
  const [fleetCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#fleetDownload').click()]);
  const fleetCsvText=await fs.readFile(await fleetCsv.path(),'utf8');
  assert.equal(fleetCsv.suggestedFilename(),'owg-fleet-inventory.csv');
  assert.equal(fleetCsvText.trim().split('\r\n').length,3,'only the two shown computers export');
  assert.match(fleetCsvText,/"problems","2","4"/);
  assert.ok(fleetCsvText.includes('"\'=SUM(1,2)"'),'formula prefix neutralized before CSV quoting');
  assert.match(fleetCsvText,/"owned-backlog"/);assert.match(fleetCsvText,/"700","0"/);
  assert.doesNotMatch(fleetCsvText,/"owned-current"/);
  await page.locator('#fleetSearch').fill('owned-errors');
  assert.equal(await page.locator('#fleetRows tr').count(),1);
  assert.match(await page.locator('#fleetRows').textContent(),/Sync errors reported/);
  await page.locator('#fleetSearch').fill('no such device');
  assert.match(await page.locator('#fleetRows').textContent(),/No computers match/);
  assert.equal(await page.locator('#fleetDownload').isDisabled(),true);
  await page.locator('#fleetSearch').fill('support');
  await page.locator('#fleetFilter').selectOption('unknown');
  assert.match(await page.locator('#fleetSummary').textContent(),/^4 of 4/);
  await page.locator('#fleetFilter').selectOption('unlinked');
  assert.match(await page.locator('#fleetSummary').textContent(),/^0 of 4/);
  await page.locator('#fleetSearch').fill('');
  await page.locator('#fleetFilter').selectOption('all');
  const refreshed=page.waitForResponse(r=>r.url().endsWith('/v1/admin/people/acme'));
  await page.locator('#fleetRefresh').click();await refreshed;
  const text=await page.locator('#ppl').textContent();
  assert.match(text,/700 waiting, 0 rejected/);assert.match(text,/9 waiting, 1 rejected/);
  // Recorder badges are unaffected: these devices sent no recorder heartbeat.
  assert.equal(await page.locator('#ppl .pill').filter({hasText:/^Recorder status unknown$/}).count(),4);
  const beforeExpiry=await page.evaluate(()=>Date.now());
  await page.clock.fastForward(121000);
  assert.equal(await page.locator('#ppl .pill').filter({hasText:'Sync status needs refresh'}).count(),3);
  assert.equal(await page.locator('#ppl .pill').filter({hasText:'Recorder status needs refresh'}).count(),0);
  await page.locator('#fleetFilter').selectOption('problems');
  assert.match(await page.locator('#fleetSummary').textContent(),/^0 of 4/,'expired problems leave the reported-problems filter');
  await page.clock.setSystemTime(beforeExpiry);
  await page.locator('#fleetSearch').fill('owned-');
  assert.match(await page.locator('#fleetSummary').textContent(),/^0 of 4/,'clock rollback cannot revive expired problem claims');
  await page.locator('#fleetFilter').selectOption('all');
  assert.equal(await page.locator('#fleetRows .pill').filter({hasText:/^Sync reported current$/}).count(),0);
  assert.equal(await page.locator('#fleetRows .pill').filter({hasText:/^Sync errors reported$/}).count(),0);
  const [expiredCsv]=await Promise.all([page.waitForEvent('download'),page.locator('#fleetDownload').click()]);
  const expiredCsvText=await fs.readFile(await expiredCsv.path(),'utf8');
  assert.doesNotMatch(expiredCsvText,/"700"|"sync_error_reported"|"in_sync_reported"/,'expired claims and their counts do not export as fresh after rollback');

  // A new authenticated fetch can establish fresh claims again.
  const freshAgain=page.waitForResponse(r=>r.url().endsWith('/v1/admin/people/acme'));
  await page.locator('#fleetRefresh').click();await freshAgain;
  await page.locator('#fleetFilter').selectOption('problems');
  await page.waitForFunction(()=>document.querySelector('#fleetSummary').textContent.startsWith('2 of 4'));
  const after=await api('GET','/v1/admin/people/acme');
  assert(after.employees[0].devices.every(d=>d.events===0));
  // A response started by an old session cannot restore its roster after logout.
  let releaseRoster, requestStarted;
  const rosterGate=new Promise(resolve=>{releaseRoster=resolve});
  const rosterStarted=new Promise(resolve=>{requestStarted=resolve});
  await page.route('**/v1/admin/people/acme',async route=>{
   requestStarted();await rosterGate;
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(after)});
  });
  await page.locator('#fleetRefresh').click();await rosterStarted;
  await page.locator('#signOut').click();await page.locator('#login').waitFor({state:'visible'});
  assert.equal(await page.locator('#fleetDownload').isDisabled(),true);
  assert.equal(await page.locator('#fleetRows tr').count(),0);
  const lateResponse=page.waitForResponse(r=>r.url().endsWith('/v1/admin/people/acme'));
  releaseRoster();await lateResponse;
  await new Promise(resolve=>setTimeout(resolve,100));
  assert.equal(await page.locator('#fleetDownload').isDisabled(),true);
  assert.equal(await page.locator('#fleetRows tr').count(),0,'old roster must not return after sign out');
  for(const id of ['ppl','unl','pinv'])assert.equal(await page.locator('#'+id+' tr').count(),0,'roster tables must be cleared at sign out');
  assert.deepEqual(errors,[]);
  console.log('Fleet sync health browser acceptance passed: device sync reports → production roster → admin badges, counts, overview total and independent expiry, no evidence sent.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
