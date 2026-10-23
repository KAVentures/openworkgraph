// Loads the generated n8n workflow with n8n's own Workflow class.
const assert=require('node:assert/strict');const fs=require('node:fs');
const {Workflow}=require('n8n-workflow');
const wf=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const desc=(name,inputs,outputs)=>({description:{name,displayName:name,group:['transform'],version:1,defaults:{},inputs,outputs,properties:[]}});
const types={'n8n-nodes-base.manualTrigger':desc('manualTrigger',[],['main']),'n8n-nodes-base.noOp':desc('noOp',['main'],['main']),'n8n-nodes-base.stickyNote':desc('stickyNote',[],[])};
const nodeTypes={getByName:n=>types[n],getByNameAndVersion:n=>types[n],getKnownTypes:()=>({})};
const workflow=new Workflow({nodes:wf.nodes,connections:wf.connections,active:wf.active,nodeTypes,settings:wf.settings});
assert.equal(wf.active,false);
assert.equal(workflow.getChildNodes('Manual start').length,5);
assert(!JSON.stringify(wf).includes('credentials'));
console.log('n8n starter check passed: inactive workflow loads with five chained placeholder steps.');
