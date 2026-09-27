import test from 'node:test';
import assert from 'node:assert/strict';
import {AgentObserver} from '../../sdk/typescript/index.mjs';

function installFetchCapture(){
  const calls=[];
  const previous=globalThis.fetch;
  globalThis.fetch=async (url,options)=>{
    calls.push({url:String(url),options});
    return {ok:true,status:200};
  };
  return {calls,restore(){globalThis.fetch=previous;}};
}

test('TypeScript SDK emits structural run/model/tool events without returned content', async ()=>{
  const capture=installFetchCapture();
  try{
    const observer=new AgentObserver('Hermes-style harness',{
      provider:'local',framework:'custom-harness',token:'write-only-token',flushMs:20
    });
    const result=await observer.withRun(async run=>{
      await run.model({model:'example-model',usage:{input_tokens:3,output_tokens:2}},async()=> 'MODEL_SECRET_RETURN');
      return run.tool('repository_search',{category:'search'},async()=> 'TOOL_SECRET_RETURN');
    },{runId:'run-ts-1',workflowId:'workflow-ts-1'});
    assert.equal(result,'TOOL_SECRET_RETURN');
    await observer.shutdown();

    assert.ok(capture.calls.length>=1);
    const bodies=capture.calls.flatMap(call=>JSON.parse(call.options.body).events);
    assert.deepEqual(bodies.map(x=>x.operation),['run_started','model_call','tool_call','run_finished']);
    assert.ok(bodies.every(x=>x.run_id==='run-ts-1'));
    assert.ok(bodies.every(x=>x.workflow_id==='workflow-ts-1'));
    const serialized=JSON.stringify(bodies);
    assert.equal(serialized.includes('MODEL_SECRET_RETURN'),false);
    assert.equal(serialized.includes('TOOL_SECRET_RETURN'),false);
    assert.equal(serialized.toLowerCase().includes('prompt'),false);
    assert.equal(serialized.toLowerCase().includes('response'),false);
    assert.equal(capture.calls[0].options.headers.authorization,'Bearer write-only-token');
  }finally{capture.restore();}
});

test('TypeScript SDK records failure structurally but never exception text', async ()=>{
  const capture=installFetchCapture();
  try{
    const observer=new AgentObserver('Custom harness',{token:'t',flushMs:20});
    await assert.rejects(
      observer.withRun(async run=>{
        await run.tool('shell',{category:'shell'},async()=>{throw new Error('TOP_SECRET_EXCEPTION');});
      },{runId:'run-error'}),
      /TOP_SECRET_EXCEPTION/
    );
    await observer.shutdown();
    const bodies=capture.calls.flatMap(call=>JSON.parse(call.options.body).events);
    assert.equal(JSON.stringify(bodies).includes('TOP_SECRET_EXCEPTION'),false);
    assert.ok(bodies.some(x=>x.operation==='tool_call'&&x.status==='error'));
    assert.ok(bodies.some(x=>x.operation==='error'));
    assert.ok(bodies.some(x=>x.operation==='run_finished'&&x.status==='error'));
  }finally{capture.restore();}
});

test('TypeScript SDK remains fail-open when OWG is unavailable', async ()=>{
  const previous=globalThis.fetch;
  globalThis.fetch=async()=>{throw new Error('observer unavailable');};
  try{
    const observer=new AgentObserver('Custom harness',{token:'t',flushMs:20});
    await observer.withRun(async run=>run.tool('search',{category:'search'},async()=>42));
    await observer.shutdown();
    assert.ok(observer.stats().sendFailures>=1);
  }finally{globalThis.fetch=previous;}
});
