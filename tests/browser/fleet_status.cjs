const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises');
const {spawn}=require('node:child_process'),{tmpdir}=require('node:os'),path=require('node:path');
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-fleet-browser-'));
 const base='http://127.0.0.1:8791',token='admin-'+'x'.repeat(40);
 const python=process.env.OWG_BROWSER_PYTHON||'python';
 const server=spawn(python,['-m','uvicorn','fleet_status_app:app','--app-dir','tests/browser','--host','127.0.0.1','--port','8791'],{
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
  const employee=await api('POST','/v1/admin/employees/acme',{email:'operator@acme.se',display_name:'Owned operator',teams:['support']});
  const invitation=await api('POST','/v1/admin/employees/acme/'+employee.employee_id+'/invites',{
   organization_name:'Owned Company',gateway_url:'https://gw.acme.test',max_devices:5});
  for(const state of ['recording','paused','stopped','no_recent_collector_heartbeat','missing']){
   const device=await api('POST','/v1/devices/enroll',{organization_id:'acme',device_id:'owned-'+state},invitation.token);
   if(state!=='missing')await api('POST','/v1/device/recorder-heartbeat',{
    collector_state:state,local_capture_policy:state==='recording'?'recording':'paused',
    collector_heartbeat_age_seconds:state==='recording'?0:null},device.token);
  }
  const before=await api('GET','/v1/admin/people/acme');
  assert.equal(before.employees[0].devices.length,5);
  assert.equal(before.employees[0].devices.filter(d=>d.recorder_status.fresh_authenticated_device_report).length,4);
  assert(before.employees[0].devices.every(d=>d.events===0));
  browser=await chromium.launch({...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{}),headless:true,args:['--no-sandbox']});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.clock.install();
  await page.goto(base+'/admin');
  await page.locator('#orgIn').fill('acme');await page.locator('#tokenBox summary').click();await page.locator('#tokenIn').fill(token);
  await page.locator('#tokenSignIn').click();
  await page.locator('#stats').filter({hasText:'Fresh recorder reports at refresh'}).waitFor();
  assert(!(await page.locator('#stats').textContent()).includes('Silent >'));
  await page.locator('nav button[data-tab=people]').click();
  for(const label of ['Recording reported','Paused reported','Stopped reported','No recent collector signal','Recorder status unknown']){
   await page.locator('#ppl .pill').filter({hasText:new RegExp('^'+label+'$')}).waitFor();
  }
  assert.equal(await page.locator('#ppl .dev').count(),5);
  await page.clock.fastForward(61000);
  assert.equal(await page.locator('#ppl .pill.ok').filter({hasText:'Recording reported'}).count(),0);
  assert.equal(await page.locator('#ppl .pill').filter({hasText:'Recorder status needs refresh'}).count(),4);
  const after=await api('GET','/v1/admin/people/acme');
  assert(after.employees[0].devices.every(d=>d.events===0));
  assert.deepEqual(errors,[]);
  console.log('Fleet browser acceptance passed: enrolled device reports → production roster query → authenticated admin UI, five states, expiry without remote recording controls or evidence.');
 }finally{
  if(browser)await browser.close();server.kill('SIGTERM');
  await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);
  if(server.exitCode===null)server.kill('SIGKILL');await fs.rm(scratch,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1);});
