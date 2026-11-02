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
  const REVIEW_STATUS = {recorded_issue:'Recorded issue — inspect',incomplete_evidence:'Incomplete evidence',
    missing_report:'Missing quality report',no_recorded_issue:'No recorded issue (not source-complete)'};
  const REVIEW_FIELDS = Object.fromEntries(METRICS);
  Object.assign(REVIEW_FIELDS, {transform_unverified:'Transform unknown',
    comparison_missing_intermediate_report:'Missing intermediate report',
    comparison_unknown_transform:'Comparison transform unknown',
    comparison_transform_changed:'Comparison mapping changed',
    quality_report:'Quality report missing'});
  function reviewRows(trends) {
    if (!trends || !Array.isArray(trends.review_queue)) return [];
    return trends.review_queue.slice(-MAX_VISIBLE).filter(r=>r && Number.isSafeInteger(r.batch) && r.batch>=0)
      .map(r=>({
        batchId:r.batch,batch:label(r.batch),
        status:REVIEW_STATUS[r.status] || 'Unknown review status',
        flags:Array.isArray(r.flags) ? r.flags.filter(k=>Object.hasOwn(REVIEW_FIELDS,k))
          .map(k=>REVIEW_FIELDS[k]).join(', ') || 'None recorded' : 'Unknown',
        unknown:Array.isArray(r.unknown_fields) ? r.unknown_fields
          .filter(k=>Object.hasOwn(REVIEW_FIELDS,k)).map(k=>REVIEW_FIELDS[k]).join(', ') || 'None' : 'Unknown'
      }));
  }
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
  const MAX_REASONS = 12;
  function detail(reports, batch) {
    if (!Number.isSafeInteger(batch) || batch < 0 || !Array.isArray(reports)) return null;
    const r = reports.find(x => x && x.batch === batch);
    if (!r) return null;
    const data = r.rows && typeof r.rows === 'object' ? r.rows : {};
    const reasons = data.rejected_by_reason && typeof data.rejected_by_reason === 'object' &&
      !Array.isArray(data.rejected_by_reason) ? data.rejected_by_reason : null;
    const all = reasons ? Object.entries(reasons)
      .filter(([name]) => typeof name === 'string')
      .map(([name,n]) => ({name:name.slice(0,160),count:count(n)}))
      .sort((a,b) => (Number(b.count === 'Unknown' ? -1 : b.count) -
                      Number(a.count === 'Unknown' ? -1 : a.count)) ||
                      (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) : [];
    const digest = r.transform && r.transform.transform_sha256;
    return {batch:label(batch),created_at:typeof r.created_at === 'string' &&
      /^\d{4}-\d\d-\d\dT/.test(r.created_at) ? r.created_at.slice(0,32) : 'Unknown',
      transform:typeof digest === 'string' && /^[a-f0-9]{64}$/.test(digest) ?
        digest.slice(0,12) : 'Unknown',
      accepted:count(data.accepted),rejected:count(data.rejected),
      skipped:batch === 0 ? '0' : count(data.duplicates_skipped_on_append),
      reasons:all.slice(0,MAX_REASONS),omitted:Math.max(0,all.length-MAX_REASONS),
      reason_status:reasons ? (data.reason_counts_can_overlap ?
        'Reason counts may overlap; do not sum them as unique rejected rows.' :
        'Reason counts describe recorded categories, not raw source rows.') :
        'Rejection reasons were not recorded for this batch.'};
  }

  const SHA256 = /^[a-f0-9]{64}$/;
  // Compare two user-selected *stored* batches without reading source events.
  // Missing intermediate reports or a changed mapping make the whole comparison
  // ineligible; unknown denominators suppress only the affected metric delta.
  function comparePair(trends, baseline, target) {
    const refuse = reason => ({status:reason, rows:[]});
    if (!trends || !Array.isArray(trends.batches)) return refuse('No saved batch reports.');
    if (!Number.isSafeInteger(baseline) || !Number.isSafeInteger(target) ||
        baseline < 0 || target < 0) return refuse('Select two valid stored batches.');
    if (baseline === target) return refuse('Select two different batches.');
    if (baseline > target) return refuse('Choose an earlier baseline and a later target.');
    if (target - baseline >= MAX_VISIBLE)
      return refuse('Comparison exceeds the latest '+MAX_VISIBLE+' displayed batches.');
    const byId = new Map();
    for (const item of trends.batches.slice(-MAX_VISIBLE)) {
      if (!item || !Number.isSafeInteger(item.batch) || item.batch < 0) continue;
      if (byId.has(item.batch)) return refuse('Duplicate batch identifiers; comparison unavailable.');
      byId.set(item.batch,item);
    }
    if (!byId.has(baseline) || !byId.has(target))
      return refuse('Selected report is outside the displayed batch window.');
    const expected = byId.get(baseline).transform_sha256;
    if (typeof expected !== 'string' || !SHA256.test(expected))
      return refuse('Transform digest unknown; comparison unavailable.');
    for (let id=baseline; id<=target; id++) {
      const item=byId.get(id);
      if (!item) return refuse('Missing intermediate quality report; comparison unavailable.');
      const digest=item.transform_sha256;
      if (typeof digest !== 'string' || !SHA256.test(digest))
        return refuse('Transform digest unknown; comparison unavailable.');
      if (digest !== expected)
        return refuse('Mapping changed between selected batches; comparison unavailable.');
    }
    function metric(item,key) {
      const m=item.metrics && item.metrics[key] || {};
      const n=m.count, d=m.denominator;
      const valid=Number.isSafeInteger(n) && n>=0 && Number.isSafeInteger(d) && d>0 && n<=d;
      return {count:count(n),denominator:count(d),
        rate:valid ? rate(n/d) : 'Unknown',numeric:valid ? n/d : null};
    }
    const first=byId.get(baseline),last=byId.get(target);
    const compared=METRICS.map(([key,title])=>{
      const before=metric(first,key),after=metric(last,key);
      const future=key==='future_dated_events';
      const valid=!future && before.numeric!==null && after.numeric!==null;
      return {metric:title,baseline_count:before.count,baseline_denominator:before.denominator,
        baseline_rate:before.rate,target_count:after.count,
        target_denominator:after.denominator,target_rate:after.rate,
        change:valid ? change((after.numeric-before.numeric)*100) : 'Not comparable',
        reason:future ? 'Different report reference times' :
          valid ? 'Descriptive difference only' : 'Missing or invalid count/denominator'};
    });
    return {status:'Same verified transform across '+(target-baseline+1)+
      ' contiguous stored reports; descriptive comparison only.',
      baseline:label(baseline),target:label(target),rows:compared};
  }
  if (typeof module !== 'undefined' && module.exports)
    module.exports = {rows,csv,csvCell,count,rate,change,status,detail,reviewRows,comparePair,MAX_VISIBLE,MAX_REASONS};
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
    const pairPanel=element('section');pairPanel.id='owgQualityPairCompare';
    const baselineLabel=element('label','Baseline batch: ');
    baselineLabel.htmlFor='owgQualityPairBaseline';
    const pairBaseline=element('select');pairBaseline.id='owgQualityPairBaseline';pairBaseline.disabled=true;
    baselineLabel.appendChild(pairBaseline);
    const targetLabel=element('label','Target batch: ');
    targetLabel.htmlFor='owgQualityPairTarget';
    const pairTarget=element('select');pairTarget.id='owgQualityPairTarget';pairTarget.disabled=true;
    targetLabel.appendChild(pairTarget);
    const pairOutput=element('div');pairOutput.id='owgQualityPairRows';
    pairPanel.append(element('h4','Compare two selected batches'),
      element('p','Compare stored counts only. Both reports and every intervening batch must have the same verified mapping.'),
      baselineLabel,targetLabel,pairOutput);
    const reviewPanel=element('section');reviewPanel.id='owgQualityReviewQueue';
    const reviewOutput=element('div');reviewOutput.id='owgQualityReviewRows';
    reviewPanel.append(element('h4','Batch quality review queue'),
      element('p','Recorded problems and missing evidence are review prompts, not quarantine decisions or proof of upstream failure.'),reviewOutput);
    const drill=element('section');drill.id='owgQualityBatchDrilldown';
    const drillLabel=element('label','Inspect rejection reasons for batch: ');
    drillLabel.htmlFor='owgQualityBatchSelect';
    const drillSelect=element('select');drillSelect.id='owgQualityBatchSelect';drillSelect.disabled=true;
    drillLabel.appendChild(drillSelect);
    const drillOutput=element('div');drillOutput.id='owgQualityBatchDetails';
    drill.append(element('h4','Batch rejection diagnostics'),drillLabel,drillOutput);
    panel.append(element('h4','Import/append quality trends'),intro,run,download,message,pairPanel,reviewPanel,output,drill);
    quality.appendChild(panel);
    const selected=logId();let ticket=0,loaded=null,loadedReports=[];
    const current=(id,n)=>panel.isConnected && logId()===id && ticket===n;
    function showPair() {
      pairOutput.replaceChildren();
      if (!loaded || logId()!==selected) return;
      const result=comparePair(loaded,Number(pairBaseline.value),Number(pairTarget.value));
      pairOutput.appendChild(element('p',result.status));
      if (!result.rows.length) return;
      const table=element('table');table.id='owgQualityPairTable';
      const head=element('thead'),header=element('tr');
      for (const title of ['Metric','Baseline count','Baseline denominator','Baseline rate',
        'Target count','Target denominator','Target rate','Change','Interpretation']) {
        const th=element('th',title);th.scope='col';header.appendChild(th);
      }
      head.appendChild(header);
      const body=element('tbody');
      for (const item of result.rows) {
        const tr=element('tr');
        for (const key of ['metric','baseline_count','baseline_denominator','baseline_rate',
          'target_count','target_denominator','target_rate','change','reason'])
          tr.appendChild(element('td',item[key]));
        body.appendChild(tr);
      }
      table.append(head,body);
      const wrap=element('div');wrap.className='table-wrap';wrap.appendChild(table);
      pairOutput.appendChild(wrap);
    }
    pairBaseline.addEventListener('change',showPair);
    pairTarget.addEventListener('change',showPair);
    function showDetail() {
      drillOutput.replaceChildren();
      if (!loaded || logId()!==selected) return;
      const d=detail(loadedReports,Number(drillSelect.value));
      if (!d) {drillOutput.appendChild(element('p','No saved rejection diagnostics for this batch.'));return;}
      const summary=element('p','Batch '+d.batch+' · Reported '+d.created_at+
        ' · Transform SHA-256 prefix '+d.transform+' · Accepted '+d.accepted+
        ' · Rejected '+d.rejected+' · Skipped duplicates '+d.skipped+'.');
      const note=element('p',d.reason_status);note.className='muted';
      drillOutput.append(summary,note);
      if (!d.reasons.length) return;
      const table=element('table'),head=element('thead'),tr=element('tr');
      for(const heading of ['Rejection category','Count']){const th=element('th',heading);th.scope='col';tr.appendChild(th);}
      head.appendChild(tr);const body=element('tbody');
      for(const reason of d.reasons){const row=element('tr');row.append(element('td',reason.name),element('td',reason.count));body.appendChild(row);}
      table.append(head,body);const wrap=element('div');wrap.className='table-wrap';wrap.appendChild(table);
      drillOutput.appendChild(wrap);
      if(d.omitted)drillOutput.appendChild(element('p',d.omitted+' additional categories omitted (latest '+MAX_REASONS+' shown by count).'));
    }
    drillSelect.addEventListener('change',showDetail);
    function clearState() {
      ++ticket;loaded=null;loadedReports=[];download.disabled=true;run.disabled=false;
      output.replaceChildren();reviewOutput.replaceChildren();pairOutput.replaceChildren();
      pairBaseline.disabled=true;pairTarget.disabled=true;
      pairBaseline.replaceChildren();pairTarget.replaceChildren();
      drillSelect.disabled=true;drillSelect.replaceChildren();
      drillOutput.replaceChildren();
    }
    const picker=document.getElementById('owgEventLogPick');
    if(picker)picker.addEventListener('change',()=>{
      if(logId()!==selected){clearState();message.textContent='Select and analyse a log again.';}
    });
    run.addEventListener('click',async()=>{
      const id=logId();
      if(!id || id!==selected){clearState();message.textContent='Select and analyse a log again.';return;}
      clearState();const n=ticket;run.disabled=true;
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
        loadedReports=Array.isArray(data.reports)?data.reports:[];
        const available=trends.batches.slice(-MAX_VISIBLE).filter(b=>Number.isSafeInteger(b.batch)&&b.batch>=0);
        for(const batch of available){const option=element('option',label(batch.batch));option.value=String(batch.batch);drillSelect.appendChild(option);}
        drillSelect.disabled=!available.length;
        if(available.length){drillSelect.value=String(available[available.length-1].batch);showDetail();}
        for(const batch of available) {
          for(const select of [pairBaseline,pairTarget]) {
            const option=element('option',label(batch.batch));option.value=String(batch.batch);
            select.appendChild(option);
          }
        }
        pairBaseline.disabled=available.length<2;pairTarget.disabled=available.length<2;
        if(available.length>=2) {
          pairBaseline.value=String(available[0].batch);
          pairTarget.value=String(available[available.length-1].batch);
          showPair();
        } else pairOutput.appendChild(element('p','At least two stored reports are required.'));
        const queue=reviewRows(trends);
        if (queue.length) {
          const table=element('table');table.id='owgQualityReviewTable';
          const head=element('thead'),hr=element('tr');
          for(const heading of ['Batch','Review status','Recorded diagnostics','Unknown / incomparable','Inspect']){
            const th=element('th',heading);th.scope='col';hr.appendChild(th);
          }
          head.appendChild(hr);
          const body=element('tbody');
          for(const item of queue) {
            const tr=element('tr');
            for(const value of [item.batch,item.status,item.flags,item.unknown])tr.appendChild(element('td',value));
            const cell=element('td'),inspect=element('button','Inspect batch');
            inspect.type='button';inspect.className='secondary';
            inspect.disabled=!available.some(b=>b.batch===item.batchId);
            inspect.setAttribute('aria-label','Inspect quality report for '+item.batch);
            inspect.addEventListener('click',()=>{
              if(!current(id,n) || inspect.disabled)return;
              drillSelect.value=String(item.batchId);showDetail();drillSelect.focus();
            });
            cell.appendChild(inspect);tr.appendChild(cell);body.appendChild(tr);
          }
          table.append(head,body);const wrap=element('div');wrap.className='table-wrap';
          wrap.appendChild(table);reviewOutput.appendChild(wrap);
          if(trends.review_queue.length>MAX_VISIBLE)
            reviewOutput.appendChild(element('p','Latest '+MAX_VISIBLE+' review entries shown.'));
        } else reviewOutput.appendChild(element('p','No saved quality reports to review.'));
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
      finally{if(panel.isConnected && ticket===n)run.disabled=false;}
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
