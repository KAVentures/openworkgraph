// OpenWorkGraph structural agent telemetry for Node.js / TypeScript projects.
//
// This module is deliberately content-blind. It never accepts or serializes
// prompts, responses, tool arguments/results, reasoning, returned values, or
// exception messages. Delivery is best-effort and fail-open by default.

const OPERATIONS = new Set([
  'run_started','run_finished','model_call','tool_call','handoff',
  'human_approval_requested','human_approval_received','error'
]);
const LEVELS = new Set(['native_trace','instrumented_tools','mcp_only','os_observed','outcome_only']);
const CATEGORIES = new Set([
  'filesystem','shell','browser','code','search','network','database',
  'messaging','issue_tracker','deployment','mcp','other','none'
]);

function clean(value, limit=240){return String(value??'').replace(/\s+/g,' ').trim().slice(0,limit);}
function identifier(prefix){
  const c=globalThis.crypto;
  if(c?.randomUUID)return `${prefix}-${c.randomUUID().replaceAll('-','')}`;
  return `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}
function now(){return new Date().toISOString();}
function usage(value={}){
  const allowed=new Set(['input_tokens','output_tokens','cached_input_tokens','total_tokens']);
  const out={};
  for(const [key,raw] of Object.entries(value||{})){
    if(!allowed.has(key))throw new TypeError(`unsupported usage field: ${key}`);
    const amount=Number(raw);
    if(!Number.isSafeInteger(amount)||amount<0)throw new TypeError(`${key} must be a non-negative integer`);
    out[key]=amount;
  }
  return out;
}

export class AgentObserver {
  constructor(agentName, options={}){
    this.agentName=clean(agentName,160);
    if(!this.agentName)throw new TypeError('agentName is required');
    this.provider=clean(options.provider,160);
    this.framework=clean(options.framework||'custom',160);
    this.observationLevel=clean(options.observationLevel||'instrumented_tools',80);
    if(!LEVELS.has(this.observationLevel))throw new TypeError(`unsupported observation level: ${this.observationLevel}`);
    this.endpoint=clean(options.endpoint||process.env.OWG_AGENT_INGEST_URL||'http://127.0.0.1:8787/agent-ingest/v1/events',1000);
    this.token=clean(options.token||process.env.OWG_AGENT_INGEST_TOKEN||'',4000);
    this.deviceId=clean(options.deviceId||'agent-local');
    this.sensorId=clean(options.sensorId||'agent:typescript-sdk');
    this.maxQueue=Math.max(1,Math.min(Number(options.maxQueue||512),10000));
    this.batchSize=Math.max(1,Math.min(Number(options.batchSize||32),256));
    this.flushMs=Math.max(20,Math.min(Number(options.flushMs||200),2000));
    this.queue=[];this.timer=null;this.flushPromise=null;this.closed=false;
    this.counts={accepted:0,dropped:0,sendFailures:0,batchesSent:0};
  }

  startRun(options={}){
    const run=new AgentRun(this,options);
    run.start();
    return run;
  }

  async withRun(fn, options={}){
    const run=this.startRun(options);
    try{
      return await fn(run);
    }catch(error){
      run.recordError();
      run.finish('error');
      throw error;
    }finally{
      if(!run.closed)run.finish('success');
    }
  }

  _enqueue(operation, fields={}){
    if(this.closed){this.counts.dropped++;return false;}
    if(!OPERATIONS.has(operation)){this.counts.dropped++;return false;}
    if(this.queue.length>=this.maxQueue){this.counts.dropped++;return false;}
    const event={
      event_id:identifier('evt'), observed_at:now(), agent_name:this.agentName,
      provider:this.provider, framework:this.framework, operation,
      status:fields.status||'unknown', observation_level:this.observationLevel,
      device_id:this.deviceId, sensor_id:this.sensorId,
      run_id:clean(fields.run_id), trace_id:clean(fields.trace_id),
      workflow_id:clean(fields.workflow_id), trigger_event_id:clean(fields.trigger_event_id),
      span_id:clean(fields.span_id), parent_span_id:clean(fields.parent_span_id),
      tool_name:clean(fields.tool_name,200), tool_category:fields.tool_category||'none',
      model:clean(fields.model,200), duration_seconds:Math.max(0,Number(fields.duration_seconds||0)),
      usage:usage(fields.usage||{})
    };
    if(!CATEGORIES.has(event.tool_category)){this.counts.dropped++;return false;}
    this.queue.push(event);this.counts.accepted++;this._schedule();return true;
  }

  _schedule(){
    if(this.timer||this.flushPromise||this.closed)return;
    this.timer=setTimeout(()=>{this.timer=null;void this.flush();},this.flushMs);
    this.timer.unref?.();
  }

  async flush(){
    if(this.flushPromise)return this.flushPromise;
    if(!this.queue.length)return;
    const drain=async()=>{
      while(this.queue.length){
        const batch=this.queue.splice(0,this.batchSize);
        if(!this.token){this.counts.sendFailures++;continue;}
        const controller=new AbortController();
        const timeout=setTimeout(()=>controller.abort(),1500);timeout.unref?.();
        try{
          const response=await fetch(this.endpoint,{
            method:'POST',headers:{'content-type':'application/json','authorization':`Bearer ${this.token}`},
            body:JSON.stringify({events:batch}),signal:controller.signal
          });
          if(!response.ok)this.counts.sendFailures++;else this.counts.batchesSent++;
        }catch(_){this.counts.sendFailures++;}
        finally{clearTimeout(timeout);}
      }
    };
    this.flushPromise=drain();
    try{await this.flushPromise;}
    finally{
      this.flushPromise=null;
      if(this.queue.length&&!this.closed)this._schedule();
    }
  }

  async shutdown(){
    // Stop admission first, then await an already-running or newly-started
    // drain. This prevents shutdown from returning while a fetch is in flight.
    this.closed=true;
    if(this.timer){clearTimeout(this.timer);this.timer=null;}
    await this.flush();
  }

  stats(){return {...this.counts,queued:this.queue.length};}
}

export class AgentRun {
  constructor(observer, options={}){
    this.observer=observer;
    this.runId=clean(options.runId)||identifier('run');
    this.traceId=clean(options.traceId)||identifier('trace');
    this.workflowId=clean(options.workflowId);
    this.triggerEventId=clean(options.triggerEventId);
    this.startedAt=0;this.closed=false;
  }
  _fields(extra={}){return {run_id:this.runId,trace_id:this.traceId,workflow_id:this.workflowId,trigger_event_id:this.triggerEventId,...extra};}
  start(){if(!this.startedAt){this.startedAt=performance.now();this.observer._enqueue('run_started',this._fields({status:'running'}));}return this;}
  finish(status='success'){
    if(this.closed)return false;this.closed=true;
    return this.observer._enqueue('run_finished',this._fields({status,duration_seconds:Math.max(0,(performance.now()-this.startedAt)/1000)}));
  }
  async tool(name, options={}, fn){
    if(typeof options==='function'){fn=options;options={};}
    if(typeof fn!=='function')throw new TypeError('tool requires a function');
    const category=options.category||'other';if(!CATEGORIES.has(category))throw new TypeError(`unsupported tool category: ${category}`);
    const started=performance.now();let failed=false;
    try{return await fn();}
    catch(error){failed=true;throw error;}
    finally{
      this.observer._enqueue('tool_call',this._fields({status:failed?'error':'success',tool_name:clean(name,200),tool_category:category,duration_seconds:(performance.now()-started)/1000}));
    }
  }
  async model(options={}, fn){
    if(typeof options==='function'){fn=options;options={};}
    if(typeof fn!=='function')throw new TypeError('model requires a function');
    const started=performance.now();let failed=false;
    try{return await fn();}catch(error){failed=true;throw error;}
    finally{this.observer._enqueue('model_call',this._fields({status:failed?'error':'success',model:clean(options.model,200),usage:usage(options.usage||{}),duration_seconds:(performance.now()-started)/1000}));}
  }
  handoff(){return this.observer._enqueue('handoff',this._fields({status:'success',span_id:identifier('span')}));}
  approvalRequested(){return this.observer._enqueue('human_approval_requested',this._fields({status:'running'}));}
  approvalReceived(approved=true){return this.observer._enqueue('human_approval_received',this._fields({status:approved?'success':'denied'}));}
  recordError(){return this.observer._enqueue('error',this._fields({status:'error'}));}
}
