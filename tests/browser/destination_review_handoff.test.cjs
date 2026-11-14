/* Executable destination handoff guard tests using the production event handler. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.resolve(__dirname,'../../dashboard/automation_pipeline.js'),'utf8');
const start=source.indexOf('    let handoffGeneration=0;');
const end=source.indexOf('    add.onclick=async()=>',start);
assert.ok(start>0&&end>start,'production destination event handler must exist');
const handler=source.slice(start,end);
const hash=x=>x.repeat(64);
const basis={project_id:'p1',idea_revision:3,idea_state:'approved',spec_sha256:hash('a'),
 archive_sha256:hash('b'),project_set_sha256:hash('d'),blockers:[]};
const handoff={idea_id:'idea-1',project_id:'p1',idea_revision:3,idea_state:'approved',
 spec_sha256:basis.spec_sha256,archive_sha256:basis.archive_sha256,review_revision:2,review_basis:basis};
const journey={idea:{revision:3,state:'approved'},projects_truncated:false,
 projects:[{project_id:'p1',idea_revision:3,spec_sha256:basis.spec_sha256,archive_sha256:basis.archive_sha256}]};
const preflight={format:'openworkgraph.review-preflight.v1',idea_id:'idea-1',idea_revision:3,project_id:'p1',
 project_set_sha256:basis.project_set_sha256,spec_sha256:basis.spec_sha256,archive_sha256:basis.archive_sha256,
 execution_authorized:false,review_required:true,source_read_authorized:false,
 verified_business_outcomes:false,blockers:[]};
const approved={format:'openworkgraph.reviewed-handoff-preflight.v1',idea_id:'idea-1',idea_revision:3,
 project_id:'p1',project_set_sha256:basis.project_set_sha256,spec_sha256:basis.spec_sha256,
 archive_sha256:basis.archive_sha256,review_revision:2,human_review_approved:true,
 execution_authorized:false,source_read_authorized:false};
const reply=(data,status=200)=>({ok:status===200,status,json:async()=>data});
async function scenario(name,options={}){
 let listener,shown=0,journeyOpened=0,calls=[],status={textContent:''},children=[];
 const detail={replaceChildren(...x){children=x;}};
 const button={onclick:async()=>{journeyOpened++;}};
 const document={getElementById:id=>id==='owgPipelineJourney'?button:null};
 const window={addEventListener:(name,fn)=>{assert.equal(name,'owg-pipeline-open');listener=fn;}};
 const api=async(url,request)=>{
   calls.push({url,request});
   if(url.endsWith('/journey'))return reply(journey);
   if(url.endsWith('/preflight'))return reply(preflight);
   if(url.endsWith('/reviewed-handoff-preflight'))return options.denied?reply({},403):reply({...approved,...options.approved});
   throw new Error('Unexpected endpoint '+url);
 };
 const show=async()=>{shown++;};
 const refresh=async()=>true;
 vm.runInNewContext(handler,{window,document,api,show,refresh,detail,status});
 assert.ok(listener);
 listener({detail:{...handoff,...options.handoff}});
 for(let i=0;i<30;i++)await Promise.resolve();
 if(options.denied||options.approved||options.handoff){
   assert.equal(shown,0,name+' must not show a decision surface');
   assert.equal(journeyOpened,0,name+' must not open a journey');
   assert.match(status.textContent,/Unable to open reviewed evidence|Inspect again|refused/);
 }else{
   assert.equal(shown,1,'valid reviewed destination must display');
   assert.equal(journeyOpened,1,'valid reviewed destination must open journey');
   assert.equal(calls.length,3,'journey, basis and approval must each be authenticated');
   assert.match(calls[2].url,/reviewed-handoff-preflight$/);
   assert.equal(JSON.parse(calls[2].request.body).project_set_sha256,basis.project_set_sha256);
 }
 console.log('PASS '+name);
}
(async()=>{
 await scenario('approved destination rechecks authenticated saved review');
 await scenario('revoked approval denied at destination',{denied:true});
 await scenario('stale saved revision denied',{approved:{review_revision:3}});
 await scenario('wrong project denied',{approved:{project_id:'other'}});
 await scenario('execution authority cannot be smuggled in',{approved:{execution_authorized:true}});
 await scenario('missing saved approval revision denied',{handoff:{review_revision:undefined}});
})().catch(e=>{console.error(e);process.exitCode=1;});
