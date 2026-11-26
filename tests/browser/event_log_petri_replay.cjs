const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {spawn}=require('node:child_process');
const {tmpdir}=require('node:os');
const path=require('node:path');
// Real-browser acceptance: replay a log on the discovered Petri net and on an uploaded PNML net.
(async()=>{
 const scratch=await fs.mkdtemp(path.join(tmpdir(),'owg-petri-'));
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
   const context=await browser.newContext({acceptDownloads:true});const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   const session=(await fs.readFile(path.join(scratch,'session.txt'),'utf8')).trim();
   await page.goto(base+'/#session='+session);await page.locator('[role=tab][data-tab=evidence]').click();
   // 30 orders: Register, Check, Pay, Close; 6 skip Check.
   const rows=['case,activity,time,who'];
   for(let i=0;i<30;i++){const steps=['Register',...(i%5===0?[]:['Check']),'Pay','Close'];
     steps.forEach((a,j)=>rows.push(`c${i},${a},2024-0${1+i%2}-01T${String(j).padStart(2,'0')}:${String(i).padStart(2,'0')}:00Z,ana`));}
   await page.locator('#owgEventLogFile').setInputFiles({name:'orders.csv',mimeType:'text/csv',buffer:Buffer.from(rows.join('\n'))});
   await page.waitForFunction(()=>document.querySelector('#owgEventLogCase').options.length===4);
   await page.locator('#owgEventLogResource').selectOption('who');
   await page.locator('#owgEventLogConsent').check();await page.locator('#owgEventLogImport').click();
   await page.locator('#owgEventLogStatus').filter({hasText:'Imported 114 events'}).waitFor();
   await page.locator('#owgEventLogAnalyze').click();
   const box=page.locator('#owgPetri');await box.waitFor();await box.scrollIntoViewIfNeeded();
   await page.locator('#owgPetriDiscovered').click();
   await page.locator('#owgPetriStatus').filter({hasText:'Fitness 100% on the discovered net (every path kept): 30 of 30 cases'}).waitFor();
   // A strict sequence net, as another tool might export it: Check cannot be skipped.
   const labels=['Register','Check','Pay','Close'];
   const places=labels.map((_,i)=>`<place id="p${i}">${i===0?'<initialMarking><text>1</text></initialMarking>':''}</place>`).join('')+`<place id="p${labels.length}"/>`;
   const transitions=labels.map((a,i)=>`<transition id="t${i}"><name><text>${a}</text></name></transition>`).join('');
   const arcs=labels.map((_,i)=>`<arc id="i${i}" source="p${i}" target="t${i}"/><arc id="o${i}" source="t${i}" target="p${i+1}"/>`).join('');
   const xml=`<?xml version="1.0"?><pnml><net id="n" type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel"><page id="g">${places}${transitions}${arcs}</page><finalmarkings><marking><place idref="p4"><text>1</text></place></marking></finalmarkings></net></pnml>`;
   await page.locator('#owgPetriFile').setInputFiles({name:'strict.pnml',mimeType:'application/xml',buffer:Buffer.from(xml)});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on strict.pnml: 24 of 30 cases'}).waitFor();
   assert((await page.locator('#owgPetriStatus').textContent()).includes('Precision 100%'));
   const out=page.locator('#owgPetriResults');
   const placesTable=await out.locator('table').first().textContent();
   assert(placesTable.includes('p2'),placesTable);assert(placesTable.includes('p1'),placesTable);
   assert((await out.locator('table').nth(1).textContent()).includes('Pay'));
   await out.locator('details summary').click();
   assert((await out.locator('details').textContent()).includes('Register → Pay → Close'));
   const evidenceStatus=page.locator('#owgEventLogCasesStatus'),evidenceList=page.locator('#owgEventLogCasesList');
   const variantButton='Inspect cases on Register → Pay → Close';
   await out.getByRole('button',{name:variantButton,exact:true}).click();
   await evidenceStatus.getByText('6 matching cases. Showing 6 on this page.',{exact:true}).waitFor();
   await evidenceList.getByRole('button',{name:'Open case c0',exact:true}).click();
   await page.locator('#owgEventLogCaseEvents').getByRole('status').filter({hasText:'Showing 3 of 3 events'}).waitFor();
   // Alignments name exactly which step each non-fitting case skipped.
   await page.locator('#owgPetriAlign').click();
   await page.locator('#owgPetriAlignStatus').filter({hasText:'Alignment fitness 97.4%: 24 of 30 cases follow the net exactly'}).waitFor();
   assert((await page.locator('#owgPetriAlignStatus').textContent()).includes('needs 0.2 extra or skipped steps'));
   const aligned=page.locator('#owgPetriAlignResults');
   const skippedTable=await aligned.locator('table').nth(1).textContent();
   assert(skippedTable.includes('Check')&&skippedTable.includes('6'),skippedTable);
   assert((await aligned.locator('details').textContent()).includes('Register → −Check (skipped) → Pay → Close'));
   await aligned.getByRole('button',{name:variantButton,exact:true}).click();
   await page.locator('#owgEventLogCasesPath').getByText('Step-by-step alignment on strict.pnml.',{exact:false}).waitFor();
   await evidenceStatus.getByText('6 matching cases. Showing 6 on this page.',{exact:true}).waitFor();
   // Cases i%5===0 skip Check; they alternate months, so each month has 3 non-fitting cases of 15.
   const months=await page.locator('#owgPetriAlignMonths').textContent();
   assert(months.includes('2024-011580%97.4%')&&months.includes('2024-021580%97.4%'),months);
   const [csvDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#owgPetriCasesCsv').click()]);
   const csvText=await fs.readFile(await csvDownload.path(),'utf8');
   assert.equal(csvText.trim().split('\n').length,7,csvText);assert(csvText.includes('Register > Pay > Close')&&csvText.includes(',Check'),csvText);
   // Tag the six non-fitting cases so the case filter can focus on them.
   assert.equal(await page.locator('#owgPetriTag').textContent(),'Tag the 6 non-fitting cases');
   await page.locator('#owgPetriTagName').fill('skips check');await page.locator('#owgPetriTag').click();
   await page.locator('#owgPetriTagMessage').filter({hasText:'Tagged 6 cases “skips check”.'}).waitFor();
   // Root causes of the deviation: the six cases without Check are the deviating ones.
   await page.locator('#owgPetriCauses').click();
   await page.locator('#owgPetriCausesResults').filter({hasText:'6 of 30 cases deviate'}).waitFor();
   const causes=await page.locator('#owgPetriCausesResults').textContent();
   assert(causes.includes('activity: Check')&&causes.includes('not causes'),causes);
   // Keep this net as a conformance signal: 6 of 30 cases (20%) do not fit, above the 10% limit.
   await page.locator('#owgPetriAlertShare').selectOption('0.1');await page.locator('#owgPetriAlertWindow').selectOption('');
   await page.locator('#owgPetriAlertSave').click();
   await page.locator('#owgPetriAlertMessage').filter({hasText:'Saved as a signal with a copy of this net.'}).waitFor();
   await page.locator('#owgSignalEvaluate').scrollIntoViewIfNeeded();await page.locator('#owgSignalEvaluate').click();
   const signalRow=page.locator('tr',{hasText:'Cases not fitting strict.pnml'}).filter({hasText:'20.0% of 30 cases'});
   await signalRow.first().waitFor();
   assert.match(await signalRow.first().textContent(),/yes/);
   await page.addScriptTag({content:axeSource});
   const alignedAudit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPetri'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(alignedAudit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   // Keep the strict net as a reference and check against it again from the saved list.
   await page.locator('#owgPetriSaveName').fill('Strict target');await page.locator('#owgPetriSave').click();
   await page.locator('#owgPetriSavedMessage').filter({hasText:'Saved “Strict target” with this log.'}).waitFor();
   await page.locator('#owgPetriSaved').selectOption({label:'Strict target (4 transitions)'});
   await page.locator('#owgPetriSavedRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on Strict target: 24 of 30 cases'}).waitFor();
   await page.locator('#owgPetriFile').setInputFiles({name:'bad.pnml',mimeType:'application/xml',buffer:Buffer.from('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><pnml/>')});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'DOCTYPE'}).waitFor();
   assert.equal(await page.locator('#owgPetriAlign').isDisabled(),true);
   assert.equal(await page.locator('#owgPetriAlignResults').textContent(),'');
   // A process tree from PM4Py or ProM (PTML) is converted to its workflow net and replayed the same way.
   const tasks=labels.map((a,i)=>`<manualTask id="t${i}" name="${a}"/><parentsNode id="e${i}" sourceId="s" targetId="t${i}"/>`).join('');
   const ptml=`<?xml version="1.0" encoding="UTF-8"?><ptml><processTree id="x" name="strict tree" root="s"><sequence id="s" name=""/>${tasks}</processTree></ptml>`;
   await page.locator('#owgPetriFile').setInputFiles({name:'strict.ptml',mimeType:'application/xml',buffer:Buffer.from(ptml)});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on strict.ptml (process tree): 24 of 30 cases'}).waitFor();
   await page.locator('#owgPetriFile').setInputFiles({name:'or.ptml',mimeType:'application/xml',buffer:Buffer.from('<ptml><processTree id="x" root="o"><or id="o" name=""/></processTree></ptml>')});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'<or> node has no children'}).waitFor();
   // The same tree pasted as PM4Py prints it.
   await page.locator('#owgPetriTreeText').fill("->( 'Register', 'Check', 'Pay', 'Close' )");
   await page.locator('#owgPetriTreeRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on the pasted process tree: 24 of 30 cases'}).waitFor();
   await page.locator('#owgPetriTreeText').fill("->( 'Register', O( 'Check', tau ), 'Pay', 'Close' )");
   await page.locator('#owgPetriTreeRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on the pasted process tree: 30 of 30 cases'}).waitFor();
   await page.locator('#owgPetriTreeText').fill("->( 'Register', <>( 'Check', 'Pay' ), 'Close' )");
   await page.locator('#owgPetriTreeRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on the pasted process tree: 24 of 30 cases'}).waitFor();
   await page.locator('#owgPetriTreeText').fill('O()');
   await page.locator('#owgPetriTreeRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'unexpected text'}).waitFor();
   // Direct BPMN replay: the same strict sequence, retaining conversion provenance.
   const bpmnNodes='<startEvent id="start"/>'+labels.map((name,i)=>'<task id="b'+i+'" name="'+name+'"/>').join('')+'<endEvent id="end"/>';
   const bpmnPath=['start',...labels.map((_,i)=>'b'+i),'end'];
   const bpmnFlows=bpmnPath.slice(1).map((target,i)=>'<sequenceFlow id="flow'+i+'" sourceRef="'+bpmnPath[i]+'" targetRef="'+target+'"/>').join('');
   const bpmn='<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="orders">'+bpmnNodes+bpmnFlows+'</process></definitions>';
   await page.locator('#owgPetriFile').setInputFiles({name:'orders.bpmn',mimeType:'application/xml',buffer:Buffer.from(bpmn)});
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on orders.bpmn (BPMN control flow): 24 of 30 cases'}).waitFor();
   await page.locator('#owgPetriBpmnConversion').getByText('BPMN conversion evidence',{exact:true}).waitFor();
   const [bpmnDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download BPMN conversion evidence (JSON)',exact:true}).click()]);
   const conversion=JSON.parse(await fs.readFile(await bpmnDownload.path(),'utf8'));
   assert.equal(conversion.process_id,'orders');
   assert.equal(Object.keys(conversion.node_transition_ids).length,6);
   assert.equal(Object.keys(conversion.flow_place_ids).length,5);
   const [pnmlDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download converted PNML',exact:true}).click()]);
   assert.equal(await fs.readFile(await pnmlDownload.path(),'utf8'),conversion.pnml);
   await page.locator('#owgPetriFile').setInputFiles({name:'unsupported.bpmn',mimeType:'application/xml',buffer:Buffer.from(bpmn.replace('<startEvent id="start"/>','<startEvent id="start"><timerEventDefinition/></startEvent>'))});
   assert.equal(await page.locator('#owgPetriBpmnConversion').textContent(),'');
   await page.locator('#owgPetriRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'unsupported task, gateway or event semantics'}).waitFor();
   // The most common path (Register, Check, Pay, Close; 24 of 30 cases) as the reference model.
   await page.locator('#owgPetriHappy').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on the most common path (followed by 80% of cases): 24 of 30 cases'}).waitFor();
   assert.equal(await page.locator('#owgPetriAlign').isDisabled(),false);
   await page.locator('#owgCaseFilterEndedBefore').fill('2024-02-01');
   await page.locator('#owgCaseFilterApply').click();
   await page.locator('#owgCaseFilterCount').filter({hasText:'Filtered: 15 of 30 cases'}).waitFor();
   await page.locator('#owgPetriSaved').selectOption({label:'Strict target (4 transitions)'});
   await page.locator('#owgPetriSavedRun').click();
   await page.locator('#owgPetriStatus').filter({hasText:'on Strict target: 12 of 15 cases'}).waitFor();
   await page.locator('#owgPetriAlign').click();
   await aligned.getByRole('button',{name:variantButton,exact:true}).click();
   await evidenceStatus.getByText('3 matching cases (case filter applied). Showing 3 on this page.',{exact:true}).waitFor();
   assert.deepEqual(await evidenceList.getByRole('button',{name:/Open case c\d+$/}).evaluateAll(buttons=>buttons.map(b=>b.getAttribute('aria-label'))),
     ['Open case c0','Open case c10','Open case c20']);
   const audit=await page.evaluate(async()=>window.axe.run(document.querySelector('#owgPetri'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
   assert.deepEqual(audit.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target).join(',')),[]);
   assert.deepEqual(errors,[]);
   console.log('Petri net replay browser acceptance passed: missing tokens and skipped steps, exact evidence and filtered saved-net review, deviation causes, unsafe XML refusal, PTML and pasted PM4Py process trees, conformance signals, WCAG audit.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exit(1);});
