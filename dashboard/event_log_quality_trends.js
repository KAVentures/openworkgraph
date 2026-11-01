/* Read-only local import/append quality trend dashboard. */
(function () {
  'use strict';
  const METRICS = [
    ['rejected_input_rows','Rejected input'],['duplicates_skipped_on_append','Skipped duplicates'],
    ['events_without_resource','Missing resources'],['events_sharing_timestamp','Timestamp ties'],
    ['future_dated_events','Future-dated events']
  ];
  const STATUS = {first_report:'First report',missing_intermediate_report:'Missing intermediate report',
    unknown_transform:'Transform unknown',transform_changed:'Mapping changed',
    same_transform:'Same transform (descriptive)'};
  const MAX_VISIBLE = 100;
  const count = x => Number.isSafeInteger(x) && x >= 0 ? String(x) : 'Unknown';
  const rate = x => typeof x === 'number' && Number.isFinite(x) && x >= 0 && x <= 1
    ? (x * 100).toFixed(2) + '%' : 'Unknown';
  const change = x => typeof x === 'number' && Number.isFinite(x)
    ? (x > 0 ? '+' : '') + x.toFixed(2) + ' pp' : 'Not comparable';
  const label = x => Number.isSafeInteger(x) && x >= 0 ? (x === 0 ? 'Import' : 'Append ' + x) : 'Unknown batch';
  const status = x => Object.prototype.hasOwnProperty.call(STATUS,x) ? STATUS[x] : 'Unknown comparison';
  function rows(trends) {
    if (!trends || !Array.isArray(trends.batches)) return [];
    return trends.batches.slice(-MAX_VISIBLE).flatMap(b => METRICS.map(([key,title]) => {
      const m = b.metrics && b.metrics[key] || {};
      const c = b.comparison || {};
      return {batch:label(b.batch),metric:title,count:count(m.count),denominator:count(m.denominator),
        rate:rate(m.rate),change:key === 'future_dated_events' ? 'Not comparable'
          : change((c.delta_percentage_points || {})[key]),comparison:status(c.status)};
    }));
  }
  function csvCell(value) {
    let s = String(value === undefined || value === null ? '' : value);
    if (/^\s*[=+\-@]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g,'""') + '"';
  }
  function csv(trends) {
    const keys = ['batch','metric','count','denominator','rate','change','comparison'];
    return [keys.map(csvCell).join(','),...rows(trends).map(r => keys.map(k => csvCell(r[k])).join(','))].join('\r\n') + '\r\n';
  }
  if (typeof module !== 'undefined' && module.exports)
    module.exports = {rows,csv,csvCell,count,rate,change,status,MAX_VISIBLE};
  if (typeof document === 'undefined') return;
  const element = (tag,text) => {const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const logId = () => document.getElementById('owgEventLogPick')?.value || '';
  function mount() {
    const quality=document.getElementById('owgQuality');
    if(!quality || quality.querySelector('#owgQualityBatchTrends'))return;
    const panel=element('section');panel.id='owgQualityBatchTrends';panel.style.marginTop='12px';
    const intro=element('p','Compare supplied import and append batches. Missing reports, changed mappings and unknown denominators prevent numeric comparisons.');
    intro.className='muted';
    const run=element('button','Compare batches');run.type='button';run.className='secondary';run.id='owgQualityTrendsRun';
    const download=element('button','Download shown batches CSV');download.type='button';download.className='secondary';
    download.id='owgQualityTrendsCsv';download.disabled=true;download.style.marginLeft='8px';
    const message=element('div','Select Compare batches to inspect saved reports.');
    message.id='owgQualityTrendsStatus';message.className='muted';
    message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const output=element('div');output.id='owgQualityTrendsTable';
    panel.append(element('h4','Import/append quality trends'),intro,run,download,message,output);
    quality.appendChild(panel);
    const selected=logId();let ticket=0,loaded=null;
    const current=(id,n)=>panel.isConnected && logId()===id && ticket===n;
    run.addEventListener('click',async()=>{
      const id=logId();
      if(!id || id!==selected){message.textContent='Select and analyse a log again.';return;}
      const n=++ticket;loaded=null;download.disabled=true;run.disabled=true;output.replaceChildren();
      message.textContent='Reading local batch reports…';
      try{
        // Dashboard bootstrap injects the local session; no AI bearer or external URL.
        const response=await fetch('/v1/event-logs/'+encodeURIComponent(id)+'/quality',{cache:'no-store'});
        if(!response.ok)throw new Error('Quality report unavailable (HTTP '+response.status+').');
        const data=await response.json();
        if(!current(id,n))return;
        const trends=data && data.batch_trends;
        if(!trends || !Array.isArray(trends.batches))throw new Error('Server does not provide batch trends yet.');
        loaded=trends;
        const shown=rows(trends),missing=Array.isArray(trends.batches_without_report)?trends.batches_without_report.length:0;
        message.textContent=trends.batches.length+' reports; '+missing+' missing; '+
          (trends.batches.length>MAX_VISIBLE?'latest '+MAX_VISIBLE+' shown. ':'')+
          'Deltas require adjacent batches, identical transforms and known denominators.';
        if(!shown.length){output.appendChild(element('p','No batch reports available.'));return;}
        const table=element('table');table.id='owgQualityTrendsRows';
        const head=element('thead'),tr=element('tr');
        ['Batch','Metric','Count','Denominator','Rate','Change','Comparison'].forEach(x=>{
          const th=element('th',x);th.scope='col';tr.appendChild(th);});
        head.appendChild(tr);
        const body=element('tbody');
        for(const row of shown){const tr=element('tr');for(const key of ['batch','metric','count','denominator','rate','change','comparison'])tr.appendChild(element('td',row[key]));body.appendChild(tr);}
        table.append(head,body);
        const wrap=element('div');wrap.className='table-wrap';wrap.appendChild(table);
        output.append(wrap,element('p','Future-dated rates use different report times and are never compared. No provider completeness, causality or individual performance is inferred.'));
        download.disabled=false;
      }catch(error){if(current(id,n))message.textContent=error?.message||'Batch trends unavailable.';}
      finally{if(current(id,n))run.disabled=false;}
    });
    download.addEventListener('click',()=>{
      if(!loaded || !panel.isConnected || logId()!==selected)return;
      const url=URL.createObjectURL(new Blob([csv(loaded)],{type:'text/csv;charset=utf-8'}));
      const a=element('a');a.href=url;a.download='owg-batch-quality-trends.csv';a.style.display='none';
      panel.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  function start(){new MutationObserver(mount).observe(document.body,{childList:true,subtree:true});mount();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});
  else start();
})();
