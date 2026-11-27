/* Communications mining: analyse an explicitly selected email/ticket export on this computer; keep only aggregates. */
(() => {
  'use strict';
  const FORMAT='openworkgraph.communications-taxonomy.v1';
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined&&text!==null)node.textContent=String(text);if(cls)node.className=cls;return node;};
  const input=(id,type,placeholder)=>{const node=document.createElement('input');node.id=id;node.type=type||'text';if(placeholder)node.placeholder=placeholder;return node;};
  const field=(labelText,control)=>{const wrap=el('div');const label=el('label',labelText);label.htmlFor=control.id;wrap.append(label,control);return wrap;};
  const setOptions=(node,options)=>node.replaceChildren(...options.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));
  const select=(id,options)=>{const node=document.createElement('select');node.id=id;setOptions(node,options);return node;};
  const button=(text,cls)=>{const node=el('button',text,cls);node.type='button';return node;};
  const show=(node,visible)=>{node.hidden=!visible;node.style.display=visible?'':'none';};
  const pct=v=>v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%';
  const duration=s=>{if(s===null||s===undefined)return '—';s=Number(s);return s<7200?Math.round(s/60)+' min':s<172800?(s/3600).toFixed(1)+' h':(s/86400).toFixed(1)+' d';};
  function table(headers,rows){
    const wrap=el('div',null,'table-wrap');const t=el('table');const head=el('thead');const hr=el('tr');
    headers.forEach(h=>hr.appendChild(el('th',h)));head.appendChild(hr);
    const body=el('tbody');rows.forEach(r=>{const tr=el('tr');r.forEach(v=>tr.appendChild(el('td',v)));body.appendChild(tr);});
    t.append(head,body);wrap.appendChild(t);return wrap;
  }

  // Pure helpers, exercised by tests.
  function detectFormat(name){const n=String(name||'').toLowerCase();return n.endsWith('.eml')?'eml':(n.endsWith('.csv')||n.endsWith('.tsv'))?'csv':'mbox';}
  // "Intent name: term, term" per line.
  function parseTaxonomy(text){
    const intents=[];
    for(const line of String(text||'').split(/\r?\n/)){
      if(!line.trim())continue;
      const at=line.indexOf(':');
      if(at<1)throw new Error('Write each intent as "Name: term, term".');
      const name=line.slice(0,at).trim(),terms=line.slice(at+1).split(',').map(t=>t.trim()).filter(Boolean);
      if(!terms.length)throw new Error('Intent "'+name+'" needs at least one term.');
      intents.push({name,terms});
    }
    if(!intents.length)throw new Error('Enter at least one intent.');
    return {format:FORMAT,intents};
  }
  const formatTaxonomy=t=>(t.intents||[]).map(i=>i.name+': '+i.terms.join(', ')).join('\n');
  function csvHeader(text,delimiter){
    const line=String(text||'').split(/\r?\n/,1)[0].replace(/^﻿/,'');const out=[];let cell='',quoted=false;
    for(let i=0;i<line.length;i++){const c=line[i];
      if(quoted){if(c==='"'&&line[i+1]==='"'){cell+='"';i++;}else if(c==='"')quoted=false;else cell+=c;}
      else if(c==='"')quoted=true;else if(c===delimiter){out.push(cell.trim());cell='';}else cell+=c;}
    out.push(cell.trim());return out.filter(Boolean).slice(0,200);
  }

  async function api(path,init){
    await window.__owgAuthReady;
    const response=await fetch(path,{cache:'no-store',...(init||{})});
    if(!response.ok){let detail='';try{detail=(await response.json()).detail;}catch(_){}throw new Error(typeof detail==='string'&&detail?detail:'Communications request failed ('+response.status+').');}
    return response.json();
  }

  function build(){
    const panel=document.getElementById('panel-evidence');
    if(!panel||document.getElementById('owgCommsCard'))return;
    const card=el('div',null,'card');card.id='owgCommsCard';
    card.appendChild(el('h2','Communications'));
    card.appendChild(el('div','Analyse an email (mbox or .eml) or ticket (CSV) export you choose. It is read on this computer, sensitive identifiers are redacted, and only counts are kept: intents, entity types, volumes and response times. No message text or entity value is stored or returned. Intents are keyword matches from the taxonomy below, not verified reasons for contact.','muted'));
    const file=input('owgCommsFile','file');file.accept='.mbox,.eml,.csv,.tsv,.txt';
    const format=select('owgCommsFormat',[['mbox','Mailbox (mbox)'],['eml','Single email (.eml)'],['csv','Ticket export (CSV)']]);
    const domains=input('owgCommsDomains','text','example.com');
    const row=el('div',null,'row');row.style.alignItems='end';
    const domainField=field('Your own email domains (marks replies)',domains);
    row.append(field('File',file),field('Format',format),domainField);
    const mapping=el('div');mapping.id='owgCommsMapping';
    const delimiter=select('owgCommsDelimiter',[[',','Comma'],[';','Semicolon'],['t','Tab'],['|','Pipe']]);
    const textCols=el('fieldset');textCols.id='owgCommsTextColumns';textCols.appendChild(el('legend','Text columns'));
    const cols={time:select('owgCommsTime',[]),thread:select('owgCommsThread',[]),direction:select('owgCommsDirection',[]),channel:select('owgCommsChannel',[])};
    const mapRow=el('div',null,'row');mapRow.style.alignItems='end';
    mapRow.append(field('Delimiter',delimiter),field('Time (with timezone)',cols.time),field('Thread or ticket ID',cols.thread),field('Direction (inbound/outbound)',cols.direction),field('Channel',cols.channel));
    mapping.append(textCols,mapRow);
    const consent=input('owgCommsConsent','checkbox');const consentLabel=el('label');consentLabel.style.cssText='display:flex;gap:6px;align-items:center;margin-top:8px';
    consentLabel.append(consent,document.createTextNode(' Analyse this export on this computer (nothing is uploaded or kept)'));
    const save=input('owgCommsSave','checkbox');const saveLabel=el('label');saveLabel.style.cssText='display:flex;gap:6px;align-items:center';
    saveLabel.append(save,document.createTextNode(' Also store an intent-only event log (thread, intent, time, direction) on this computer'));
    const classifier=select('owgCommsClassifier',[['keyword','Keyword taxonomy'],['learned','Learned model (from reviewed examples)']]);
    const qualityGate=input('owgCommsQualityGate','checkbox');
    const declaredLanguage=input('owgCommsGateLang','text','en, sv, de, pt-BR');declaredLanguage.maxLength=12;
    const qualityLabel=el('label');qualityLabel.style.cssText='display:flex;gap:6px;align-items:center';
    qualityLabel.append(qualityGate,document.createTextNode(' Require a strong reviewer-labelled holdout for this declared language (optional; may refuse analysis)'));
    const qualityControls=el('div');qualityControls.id='owgCommsQualityControls';
    const languageField=field('Declared language of the entire export (OWG does not detect it)',declaredLanguage);
    qualityControls.append(qualityLabel,languageField);
    const updateQuality=()=>{show(qualityControls,classifier.value==='learned');show(languageField,qualityGate.checked);};
    classifier.onchange=updateQuality;qualityGate.onchange=updateQuality;updateQuality();
    const sample=input('owgCommsSample','checkbox');const sampleCount=input('owgCommsSampleCount','number');sampleCount.min='1';sampleCount.max='200';sampleCount.value='25';
    const sampleLabel=el('label');sampleLabel.style.cssText='display:flex;gap:6px;align-items:center';
    sampleLabel.append(sample,document.createTextNode(' Keep the least certain inbound messages, redacted, as review examples (needs review examples turned on below)'));
    const run=button('Analyse communications');run.id='owgCommsRun';run.disabled=true;
    const status=el('div',null,'muted');status.id='owgCommsStatus';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const results=el('div');results.id='owgCommsResults';
    const taxonomyBox=el('details');const summary=el('summary','Intent taxonomy');taxonomyBox.appendChild(summary);
    const taxonomy=document.createElement('textarea');taxonomy.id='owgCommsTaxonomy';taxonomy.rows=8;taxonomy.style.width='100%';
    const saveTaxonomy=button('Save taxonomy','secondary');saveTaxonomy.id='owgCommsTaxonomySave';
    const resetTaxonomy=button('Restore default','secondary');resetTaxonomy.id='owgCommsTaxonomyReset';
    const taxStatus=el('div',null,'muted');taxStatus.id='owgCommsTaxonomyStatus';taxStatus.setAttribute('role','status');
    const previewTaxonomy=button('Preview changes on reviewed training messages','secondary');
    previewTaxonomy.id='owgCommsTaxonomyPreview';
    const applyPreview=button('Apply reviewed taxonomy','secondary');applyPreview.id='owgCommsTaxonomyApply';applyPreview.disabled=true;
    const taxPreview=el('div');taxPreview.id='owgCommsTaxonomyPreviewResult';taxPreview.setAttribute('aria-live','polite');
    let previewGeneration=0, pinnedPreview=null;
    function invalidateTaxPreview(){
      previewGeneration++;pinnedPreview=null;applyPreview.disabled=true;previewTaxonomy.disabled=false;taxPreview.replaceChildren();
    }
    taxonomy.addEventListener('input',invalidateTaxPreview);
    const taxRow=el('div',null,'row');taxRow.append(saveTaxonomy,resetTaxonomy,previewTaxonomy,applyPreview);
    taxonomyBox.append(el('div','One intent per line: "Name: term, term". A message gets the intent whose terms match best; ties are shown as ambiguous.','muted'),field('Intents and terms',taxonomy),taxRow,taxStatus,taxPreview);
    const review=el('details');review.id='owgCommsReview';review.appendChild(el('summary','Review examples and learned model'));
    card.append(row,mapping,field('Intents from',classifier),qualityControls,consentLabel,saveLabel,sampleLabel,field('How many review examples',sampleCount),run,status,results,review,taxonomyBox);
    const anchor=document.getElementById('owgEventLogCard')||document.getElementById('owgProcessCSV');
    if(anchor)anchor.insertAdjacentElement('afterend',card);else panel.appendChild(card);

    const refresh=()=>{run.disabled=!(file.files&&file.files[0]&&consent.checked);show(mapping,format.value==='csv');show(domainField,format.value!=='csv');};
    async function readColumns(){
      const chosen=file.files&&file.files[0];if(!chosen||format.value!=='csv')return;
      const columns=csvHeader(await chosen.slice(0,65536).text(),delimiter.value==='t'?'\t':delimiter.value);
      textCols.replaceChildren(el('legend','Text columns'));
      columns.forEach((c,i)=>{const box=input('owgCommsText'+i,'checkbox');box.value=c;box.checked=/subject|body|message|text|description|comment/i.test(c);
        const l=el('label');l.style.cssText='display:inline-flex;gap:4px;margin-right:12px';l.append(box,document.createTextNode(' '+c));textCols.appendChild(l);});
      const options=[['','(none)'],...columns.map(c=>[c,c])];
      for(const [key,words] of [['time',/time|date|created/i],['thread',/thread|ticket|case|conversation/i],['direction',/direction|from_type|author_type|sender_type/i],['channel',/channel|source|via/i]]){
        setOptions(cols[key],options);cols[key].value=columns.find(c=>words.test(c))||'';}
    }
    file.onchange=()=>{const chosen=file.files&&file.files[0];if(chosen){format.value=detectFormat(chosen.name);if(chosen.name.toLowerCase().endsWith('.tsv'))delimiter.value='t';}refresh();readColumns().catch(()=>{status.textContent='Could not read the column names.';});};
    format.onchange=()=>{refresh();readColumns().catch(()=>{});};delimiter.onchange=()=>readColumns().catch(()=>{});consent.onchange=refresh;
    run.onclick=async()=>{
      const chosen=file.files&&file.files[0];if(!chosen||!consent.checked)return;
      const q=new URLSearchParams({format:format.value});
      if(format.value==='csv'){
        const picked=[...textCols.querySelectorAll('input:checked')].map(b=>b.value);
        if(!picked.length){status.textContent='Choose at least one text column.';return;}
        q.set('text_columns',picked.join(','));q.set('delimiter',delimiter.value);
        for(const key of ['time','thread','direction','channel'])if(cols[key].value)q.set(key+'_column',cols[key].value);
      }else if(domains.value.trim())q.set('own_domains',domains.value);
      const headers={'Content-Type':'application/octet-stream','X-OWG-Local-Processing-Confirmed':'true'};
      q.set('classifier',classifier.value);
      if(classifier.value==='learned'&&qualityGate.checked){
        const code=declaredLanguage.value.trim();
        if(!/^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$/i.test(code)){
          status.textContent='Declare one language for the whole export (for example en or sv), or turn off the optional holdout gate.';
          return;
        }
        q.set('quality_gate_language',code.toLowerCase());
      }
      if(sample.checked){q.set('review_sample',String(Math.max(1,Math.min(200,Number(sampleCount.value)||25))));headers['X-OWG-Review-Examples-Confirmed']='true';}
      if(save.checked){q.set('save_event_log','true');q.set('name',chosen.name.replace(/\.[^.]+$/,'').slice(0,100)+' intents');headers['X-OWG-Local-Storage-Confirmed']='true';}
      run.disabled=true;status.textContent='Analysing '+chosen.name+'…';results.replaceChildren();
      try{render(await api('/v1/communications/analyze?'+q,{method:'POST',body:chosen,headers}));status.textContent='';consent.checked=false;save.checked=false;if(sample.checked){sample.checked=false;loadReview();}}
      catch(error){status.textContent=error.message;}finally{refresh();}
    };
    const loadTaxonomy=async()=>{invalidateTaxPreview();try{const t=await api('/v1/communications/taxonomy');taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent=t.is_default?'Default taxonomy.':'Your saved taxonomy.';}catch(error){taxStatus.textContent=error.message;}};
    saveTaxonomy.onclick=async()=>{invalidateTaxPreview();try{const t=await api('/v1/communications/taxonomy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(parseTaxonomy(taxonomy.value))});taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent='Taxonomy saved on this computer.';renderModel(await api('/v1/communications/review/model'));}catch(error){taxStatus.textContent=error.message;}};
    resetTaxonomy.onclick=async()=>{invalidateTaxPreview();try{const t=await api('/v1/communications/taxonomy/reset',{method:'POST'});taxonomy.value=formatTaxonomy(t.taxonomy);taxStatus.textContent='Default taxonomy restored.';renderModel(await api('/v1/communications/review/model'));}catch(error){taxStatus.textContent=error.message;}};
    previewTaxonomy.onclick=async()=>{
      invalidateTaxPreview();
      const generation=previewGeneration;
      const original=taxonomy.value;
      previewTaxonomy.disabled=true;
      taxPreview.replaceChildren(el('p','Comparing existing labelled training examples…','muted'));
      try{
        const candidate=parseTaxonomy(original);
        const data=await api('/v1/communications/taxonomy/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(candidate)});
        if(generation!==previewGeneration||taxonomy.value!==original)return;
        const lang=select('owgCommsTaxonomyPreviewLanguage',[['','All reviewed languages'],...data.by_language.map(g=>[g.language,g.language])]);
        const body=el('div');
        const draw=()=>{
          const m=lang.value?data.by_language.find(g=>g.language===lang.value):data.overall;
          if(!m)return;
          body.replaceChildren(el('p',m.messages+' labelled training messages · '+m.changed_decisions+' changed decisions · '+m.newly_correct+' newly correct · '+m.newly_not_correct+' no longer correct.'),
            el('p','Strict complete-message accuracy: '+pct(m.baseline_strict_accuracy)+' → '+pct(m.candidate_strict_accuracy)+
              '. Ambiguous '+m.baseline_ambiguous+' → '+m.candidate_ambiguous+'; unclassified '+m.baseline_unclassified+' → '+m.candidate_unclassified+'.'),
            table(['Intent','Positive support','Current F1','Proposed F1','Current recall','Proposed recall','Current coverage','Proposed coverage'],
              m.per_intent.map(x=>[x.intent,x.baseline.positive_support,pct(x.baseline.f1),pct(x.candidate.f1),pct(x.baseline.recall),pct(x.candidate.recall),pct(x.baseline.decision_coverage),pct(x.candidate.decision_coverage)])),
            table(['Current primary result','Proposed primary result','Messages'],m.primary_transitions.map(x=>[x.baseline,x.candidate,x.messages])),
            table(['Current correctness','Proposed correctness','Messages'],m.correctness_transitions.map(x=>[x.baseline,x.candidate,x.messages])));
        };
        lang.onchange=draw;draw();
        taxPreview.replaceChildren(el('h3','Training-only change review'),el('p',data.interpretation,'note'),
          el('p','Language groups shown: '+data.language_groups_displayed+' of '+data.language_groups_total+'. No message text or example IDs in this report.','muted'),
          table(['Intent','Added normalized terms','Removed normalized terms'],data.term_changes.map(x=>[x.intent,x.added_normalized_terms,x.removed_normalized_terms])),
          field('Review language',lang),body);
        pinnedPreview={candidate,original,digest:data.preview_sha256};
        applyPreview.disabled=false;
      }catch(error){if(generation===previewGeneration)taxPreview.replaceChildren(el('p',error.message,'note'));}
      finally{if(generation===previewGeneration)previewTaxonomy.disabled=false;}
    };
    applyPreview.onclick=async()=>{
      const pin=pinnedPreview;
      if(!pin||taxonomy.value!==pin.original)return;
      invalidateTaxPreview();const generation=previewGeneration;
      taxStatus.textContent='Saving the exact reviewed taxonomy…';
      try{
        const data=await api('/v1/communications/taxonomy/apply-preview',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({taxonomy:pin.candidate,preview_sha256:pin.digest,confirm:true})});
        if(generation!==previewGeneration)return;
        taxonomy.value=formatTaxonomy(data.taxonomy);taxStatus.textContent='Reviewed taxonomy saved on this computer.';
        renderModel(await api('/v1/communications/review/model'));
      }catch(error){if(generation===previewGeneration)taxStatus.textContent=error.message;}
    };
    refresh();loadTaxonomy();

    // Review examples: separately consented, retention-bounded, redacted texts the user labels.
    const reviewStatus=el('div',null,'muted');reviewStatus.id='owgCommsReviewStatus';reviewStatus.setAttribute('role','status');
    const reviewOn=input('owgCommsReviewOn','checkbox');const retention=input('owgCommsReviewRetention','number');retention.min='1';retention.max='90';retention.value='30';
    const ack=el('label');ack.style.cssText='display:flex;gap:6px;align-items:center';const ackText=el('span');ack.append(reviewOn,ackText);
    const saveReview=button('Save review settings','secondary');saveReview.id='owgCommsReviewSave';
    const examples=el('div');examples.id='owgCommsReviewExamples';
    const prioritize=button('Prioritize human review','secondary');prioritize.id='owgCommsReviewTriage';
    const newest=button('Show unlabelled examples','secondary');newest.id='owgCommsReviewNormal';
    const diverse=button('Review diverse training examples','secondary');diverse.id='owgCommsReviewDiverse';
    const previousReview=button('Previous review page','secondary'),nextReview=button('Next review page','secondary');
    previousReview.id='owgCommsReviewPrevious';nextReview.id='owgCommsReviewNext';
    previousReview.disabled=nextReview.disabled=true;
    const reviewOrder=el('div',null,'row');reviewOrder.append(prioritize,diverse,newest,previousReview,nextReview);
    let triageMode=false,triageOrder='priority',reviewOffset=0,reviewGeneration=0,queuePin=null;
    const facetControls=el('div',null,'row');facetControls.hidden=true;
    const facetFields={};
    for(const [key,label] of [['language','Declared language'],['keyword_intent','Keyword intent'],['priority','Review priority']]){
      const picker=el('select');picker.setAttribute('aria-label',label);
      const option=el('option','All');option.value='';picker.append(option);
      facetFields[key]=picker;facetControls.append(field(label,picker));
      picker.onchange=()=>{resetQueue();loadReview();};
    }
    const resetQueue=()=>{reviewOffset=0;queuePin=null;reviewGeneration++;previousReview.disabled=nextReview.disabled=true;examples.replaceChildren();};
    const refreshQueue=button('Refresh review queue','secondary');
    refreshQueue.onclick=()=>{resetQueue();loadReview();};
    facetControls.append(refreshQueue);facetControls.style.display='none';
    function drawFacets(page){
      for(const [key,picker] of Object.entries(facetFields)){
        const selected=picker.value;picker.replaceChildren();
        const all=el('option','All');all.value='';picker.append(all);
        for(const [value,n] of Object.entries(page.eligible_facets?.[key]||{})){
          const option=el('option',value+' ('+n+')');option.value=value;picker.append(option);}
        if(selected&&![...picker.options].some(o=>o.value===selected)){
          const option=el('option',selected+' (0)');option.value=selected;picker.append(option);}
        picker.value=selected;
      }
    }
    const train=button('Train intent model','secondary');train.id='owgCommsTrain';
    const dropModel=button('Delete model','secondary');dropModel.id='owgCommsModelDelete';
    const model=el('div');model.id='owgCommsModel';
    let ackVersion='',intentNames=[];
    review.append(el('div','Off by default. When on, only the messages you choose are kept, with identifiers, contact details and card numbers redacted, for the retention you set (1–90 days). You label them to train and test a local model. One in five examples is held out and only used to measure quality. Message text is treated as data, never as instructions. Turning this off deletes every example and the model.','muted'),
      ack,field('Keep examples for (days)',retention),saveReview,reviewStatus,reviewOrder,facetControls,examples,el('div',null,'row'),train,dropModel,model);
    const names=async()=>{const t=await api('/v1/communications/taxonomy');intentNames=t.taxonomy.intents.map(i=>i.name);};
    function renderModel(m){
      if(!m.trained){model.replaceChildren(el('div','No intent model is trained yet.','muted'));return;}
      if(m.stale||m.evaluation_current===false){
        model.replaceChildren(el('h3','Learned model needs retraining'),
          el('div','The reviewed labels, held-out examples, retention, or intent names have changed since this model was trained. Its previous quality scores are historical and cannot be used to justify new classifications. Train again before choosing Learned model.','note'),
          el('div','Previous model trained '+m.trained_at+'. New learned classifications are blocked until retraining.','muted'));
        return;
      }
      const e=m.evaluation;const f=v=>v===null||v===undefined?'—':Number(v).toFixed(2);
      const gateLines=Object.entries(m.holdout_language_gates||{}).map(([code,g])=>code+': '+(g.ready?'descriptive gate passes':'blocked for '+g.rejected_intents_total+' intent(s)'));
      const overlap=e.lexical_overlap, screened=e.without_detected_overlap;
      const overlapView=overlap&&overlap.complete&&screened?[
        el('h3','Training / holdout overlap check'),
        el('p',overlap.normalized_exact_overlap+' normalized matches and '+overlap.near_lexical_overlap+
          ' near lexical matches excluded from the screened score. '+overlap.no_detected_overlap+
          ' of '+overlap.holdout_examples+' held-out examples remain; macro F1 '+f(screened.macro_f1_supported_intents)+
          '. The optional language gate now uses this subset. No detected lexical overlap is not proof of independent messages or threads.','muted'),
        table(['Language','Reviewed','Normalized matches','Near matches','Remaining'],
          Object.entries(overlap.by_language).map(([l,v])=>[l,String(v.holdout_examples),String(v.normalized_exact_overlap),String(v.near_lexical_overlap),String(v.no_detected_overlap)])),
        table(['Screened intent','F1','Precision','Recall','Support','By language (F1)'],
          screened.intents.map(i=>[i.intent,f(i.f1),f(i.precision),f(i.recall),i.sufficient_support?String(i.positive_examples):i.positive_examples+' (too few)',Object.entries(i.by_language).map(([l,v])=>l+' '+f(v.f1)).join(', ')]))
      ]:[el('p',overlap?'Overlap audit could not finish within its work budget. Screened scores are unavailable and the optional language gate is blocked.':'This older model has not been screened for lexical overlap. Retrain to check repeated templates; the legacy language gate uses unscreened scores.','note')];
      model.replaceChildren(el('h3','Learned model'),el('div','Trained '+m.trained_at+' from '+m.training_example_count+' examples; expires '+m.expires_at+'. Holdout: '+e.holdout_examples+' reviewed examples in '+(e.languages.join(', ')||'no language')+'. Macro F1 '+f(e.macro_f1_supported_intents)+'. Estimates, not verified reasons for contact.','muted'),
        table(['Intent','F1','Precision','Recall','Calibration error','Uncertain','Support','By language (F1)'],e.intents.map(i=>[i.intent,f(i.f1),f(i.precision),f(i.recall),f(i.expected_calibration_error),pct(i.uncertain_rate),i.sufficient_support?String(i.positive_examples):i.positive_examples+' (too few)',Object.entries(i.by_language).map(([l,v])=>l+' '+f(v.f1)).join(', ')])),
        ...overlapView,
        el('p','Optional reviewer-labelled language gate: '+(gateLines.join('; ')||'no supported language reported')+
          '. Each modelled intent needs at least five held-out positives and negatives, F1 at least 0.80, and at most 35% uncertain decisions. Passing is NOT external validation. OWG does not detect the language of an uploaded export.','muted'),
        ...(m.skipped_intents.length?[el('div','Not trained (fewer than 5 positive or negative examples): '+m.skipped_intents.map(x=>x.intent).join(', ')+'.','muted')]:[]));
      const pairedButton=button('Compare learned and keyword classifiers','secondary');
      const pairedHost=el('div');pairedHost.setAttribute('aria-live','polite');
      pairedButton.id='owgCommsPairedEvaluation';pairedHost.id='owgCommsPairedEvaluationResult';
      let generation=0;
      pairedButton.onclick=async()=>{
        const n=++generation;pairedButton.disabled=true;
        const active=()=>n===generation&&pairedHost.isConnected&&reviewOn.checked;
        pairedHost.replaceChildren(el('p','Evaluating the same reviewed holdout with both methods…','muted'));
        try{
          const data=await api('/v1/communications/review/paired-evaluation');
          if(!active())return;
          if(data.model_sha256!==m.model_sha256)throw Error('Model changed; refresh model status before comparing.');
          const select=el('select');select.setAttribute('aria-label','Holdout language');
          for(const [value,label] of [['','All languages'],...data.by_language.map(g=>[g.language,g.language])]){
            const option=el('option',label);option.value=value;select.appendChild(option);}
          const subset=el('select');subset.setAttribute('aria-label','Holdout subset');
          for(const [value,label] of [['all_holdout','All reviewed holdout'],['without_detected_lexical_overlap','Without detected lexical overlap']]){
            const option=el('option',label);option.value=value;subset.appendChild(option);}
          subset.value=data.without_detected_lexical_overlap?'without_detected_lexical_overlap':'all_holdout';
          const body=el('div');
          function draw(){
            const selected=select.value?data.by_language.find(g=>g.language===select.value):data;
            const report=selected?.[subset.value];
            if(!report){body.replaceChildren(el('p','Screened results unavailable within the overlap work budget.','note'));return;}
            const pair=report.strict_all_intent_message_comparison;
            const rows=report.per_intent.map(i=>[i.intent,
              pct(i.keyword.accuracy_all),pct(i.learned.accuracy_all),pct(i.keyword.coverage),pct(i.learned.coverage),
              pct(i.keyword.recall_all_positives),pct(i.learned.recall_all_positives),i.paired.learned_only_correct,i.paired.keyword_only_correct]);
            const reliability=report.probability_reliability;
            const reliabilityView=[];
            if(reliability){
              const metrics=reliability.per_intent;
              const fmt=v=>v===null?'—':Number(v).toFixed(3);
              const chooser=el('select');chooser.setAttribute('aria-label','Probability reliability intent');
              metrics.filter(x=>x.trained).forEach(x=>{const o=el('option',x.intent);o.value=x.intent;chooser.appendChild(o);});
              const bins=el('div');
              function drawBins(){
                const m=metrics.find(x=>x.intent===chooser.value)?.metrics;
                bins.replaceChildren(...(m?[table(['Probability interval','Scored','Positive labels','Mean probability','Observed rate'],
                  m.bins.map(b=>[b.lower.toFixed(1)+'–'+b.upper.toFixed(1),b.count,b.positives,fmt(b.mean_probability),fmt(b.observed_positive_rate)]))]:[]));
              }
              chooser.onchange=drawBins;drawBins();
              reliabilityView.push(el('h4','Learned probability reliability'),
                el('p','Fixed holdout only. Macro support: '+reliability.macro_supported_intents+
                  ' trained intents with at least five positive and five negative scores and no missing predictions. No threshold was fitted.','muted'),
                table(['Supported-intent macro metric','Value','Intents included'],[
                  ['Brier score','brier_score'],['Brier skill','brier_skill_against_training_prior'],
                  ['Clipped log loss','log_loss_clipped'],['Calibration error','ece_fixed_10_bins'],
                  ['AUROC','auroc'],['Average precision','average_precision']
                ].map(([label,key])=>[label,fmt(reliability.macro[key]),reliability.macro_metric_support[key]])),
                table(['Intent','Scored / total','Brier','Training prior Brier','Brier skill','Log loss','ECE','AUROC','Average precision','Support'],
                  metrics.map(x=>{const m=x.metrics;return m?[x.intent,m.scored_examples+' / '+m.holdout_examples,
                    fmt(m.brier_score),fmt(m.training_prior_brier_score),fmt(m.brier_skill_against_training_prior),fmt(m.log_loss_clipped),
                    fmt(m.ece_fixed_10_bins),fmt(m.auroc),fmt(m.average_precision),m.sufficient_scored_support&&!m.missing_probabilities?'Supported':'Limited']:
                    [x.intent,'Not trained','—','—','—','—','—','—','—','—'];})),
                el('p','Brier and log loss: lower is better. Negative Brier skill means worse than the training prevalence baseline. AUROC/AP are withheld for single-class samples. ECE uses ten fixed bins. Bin means are hidden below five observations. These are descriptive estimates, not deployment approval.','muted'),
                field('Reliability bins for intent',chooser),bins);
            }
            body.replaceChildren(el('p',report.message_count+' reviewed messages; '+report.declared_intents+' declared intents.'),
              el('p','Strict complete-message correctness: learned only '+pair.learned_only_correct+' · keyword only '+pair.keyword_only_correct+
                ' · difference '+pct(pair.paired_correctness_difference)+' · exact paired p '+(pair.unadjusted_exact_mcnemar_p===null?'—':pair.unadjusted_exact_mcnemar_p.toPrecision(3))),
              table(['Intent','Keyword correct','Learned correct','Keyword coverage','Learned coverage','Keyword recall','Learned recall','Learned only correct','Keyword only correct'],rows),
              table(['Keyword result','Learned result','Messages'],pair.transitions.map(t=>[t.keyword,t.learned,t.count])),
              el('p','Accuracy uses all cases, with abstentions not counted as correct. Recall counts positive abstentions in the denominator. Untrained intents are abstentions.','muted'),...reliabilityView);
          }
          select.onchange=draw;subset.onchange=draw;draw();
          const download=button('Download aggregate comparison JSON','secondary');
          download.onclick=()=>{if(!active())return;const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-communications-paired-evaluation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
          pairedHost.replaceChildren(el('h3','Paired classifier evaluation'),el('p',data.interpretation,'muted'),
            el('p','Language groups displayed '+data.language_groups_displayed+' of '+data.language_groups_total+
              '. Untrained intents: '+(data.untrained_intents.join(', ')||'none')+'.'),
            field('Holdout subset',subset),field('Language',select),body,download);
        }catch(error){if(active())pairedHost.replaceChildren(el('p',error.message,'note'));}
        finally{pairedButton.disabled=false;}
      };
      model.append(pairedButton,pairedHost);
    }
    function renderExamples(page){
      if(!reviewOn.checked){examples.replaceChildren();return;}
      const items=page.examples;
      const list=el('div');
      items.forEach(x=>{
        const box=el('fieldset');box.appendChild(el('legend','Example '+x.example_id+(x.split==='holdout'?' (holdout)':'')));
        box.appendChild(el('div',x.text));
        if(x.priority)box.appendChild(el('div','Review priority: '+x.priority.replaceAll('_',' ')+
          '. Keyword suggestion: '+x.keyword_intent+
          (x.learned_primary_intent?'; learned-model suggestion: '+x.learned_primary_intent:'')+
          '. Reasons: '+(x.reasons||[]).join(', ')+
          '. These are suggestions, never confirmed labels.','muted'));
        const picks=intentNames.map((n,i)=>{const c=input('owgCommsLabel_'+x.example_id+'_'+i,'checkbox');c.value=n;c.checked=(x.labels||[]).includes(n);const l=el('label');l.style.cssText='display:inline-flex;gap:4px;margin-right:10px';l.append(c,document.createTextNode(' '+n));box.appendChild(l);return c;});
        const lang=input('owgCommsLang_'+x.example_id,'text','en');lang.value=x.language==='und'?'':x.language;lang.maxLength=12;
        box.appendChild(field('Language',lang));
        const saveLabel=button(x.labels===null?'Save labels (none ticked means no intent)':'Update labels','secondary');
        saveLabel.onclick=async()=>{invalidateTaxPreview();try{await api('/v1/communications/review/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({example_id:x.example_id,labels:picks.filter(c=>c.checked).map(c=>c.value),language:lang.value.trim()})});reviewOffset=0;await loadReview();reviewStatus.textContent='Labels saved. '+reviewStatus.textContent;}catch(error){reviewStatus.textContent=error.message;}};
        const remove=button('Delete example','secondary');remove.setAttribute('aria-label','Delete example '+x.example_id);
        remove.onclick=async()=>{invalidateTaxPreview();try{await api('/v1/communications/review/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({example_ids:[x.example_id]})});reviewOffset=0;await loadReview();}catch(error){reviewStatus.textContent=error.message;}};
        box.append(saveLabel,remove);list.appendChild(box);
      });
      const intro=triageMode?
        page.total+' matching unlabelled training examples of '+page.total_eligible+' eligible; '+items.length+' prioritized suggestions shown. '+
        page.unlabelled_holdout_excluded+' held-out examples excluded. '+
        (page.learned_predictions_used?'Current learned-model suggestions included.':
          'Keyword-only order; learned model '+page.model_status+'.'):
        page.total+' unlabelled examples; showing '+items.length+'.';
      const details=triageMode&&page.review_order==='diverse'?
        el('p','Diverse order: rotate through declared language and keyword groups. This page: '+
          Object.entries(page.page_by_declared_language||{}).map(([lang,n])=>lang+': '+n).join(', ')+
          '. Language is not inferred; these are review suggestions, not a representative sample.','muted'):el('span');
      examples.replaceChildren(el('h3',triageMode?'Human review suggestions':'Examples to label'),
        el('div',intro,'muted'),details,list);
      previousReview.disabled=!triageMode||reviewOffset===0;
      nextReview.disabled=!triageMode||reviewOffset+items.length>=page.total||!items.length;
    }
    async function loadReview(){
      const generation=++reviewGeneration;
      const current=()=>generation===reviewGeneration;
      previousReview.disabled=nextReview.disabled=true;
      examples.replaceChildren();
      try{
        const st=await api('/v1/communications/review/settings');if(!current())return;
        ackVersion=st.acknowledgement_version;ackText.textContent=' '+st.acknowledgement;
        reviewOn.checked=st.enabled;retention.value=String(st.retention_days||30);
        reviewStatus.textContent=st.enabled?st.example_count+' examples kept ('+st.labelled_count+' labelled, '+st.holdout_count+' held out).':'Review examples are off.';
        const t=await api('/v1/communications/taxonomy');if(!current())return;
        intentNames=t.taxonomy.intents.map(i=>i.name);
        prioritize.disabled=diverse.disabled=newest.disabled=!st.enabled;
        facetControls.hidden=!st.enabled||!triageMode;facetControls.style.display=facetControls.hidden?'none':'';
        if(!st.enabled){examples.replaceChildren();reviewOffset=0;queuePin=null;}
        const params=new URLSearchParams({limit:'20',offset:String(reviewOffset),order:triageOrder});
        for(const [key,picker] of Object.entries(facetFields))if(picker.value)params.set(key,picker.value);
        if(reviewOffset&&queuePin)params.set('expected_queue_sha256',queuePin);
        const endpoint=triageMode?'/v1/communications/review/triage?'+params:
          '/v1/communications/review/examples?status=unlabelled&limit=20';
        const data=st.enabled?await api(endpoint):{examples:[],total:0,unlabelled_holdout_excluded:0,model_status:'absent'};
        if(!current())return;
        if(triageMode&&reviewOffset&&reviewOffset>=data.total){reviewOffset=0;return loadReview();}
        if(triageMode&&st.enabled){queuePin=data.queue_sha256;drawFacets(data);}
        renderExamples(data);
        const fresh=await api('/v1/communications/review/model');if(current()){renderModel(fresh);return fresh;}
      }catch(error){if(current()){examples.replaceChildren();queuePin=null;reviewOffset=0;reviewStatus.textContent=error.message;}}
    }
    prioritize.onclick=()=>{triageMode=true;triageOrder='priority';reviewOffset=0;queuePin=null;loadReview();};
    diverse.onclick=()=>{triageMode=true;triageOrder='diverse';reviewOffset=0;queuePin=null;loadReview();};
    newest.onclick=()=>{triageMode=false;reviewOffset=0;queuePin=null;loadReview();};
    previousReview.onclick=()=>{reviewOffset=Math.max(0,reviewOffset-20);loadReview();};
    nextReview.onclick=()=>{reviewOffset+=20;loadReview();};
    saveReview.onclick=async()=>{invalidateTaxPreview();
      if(!reviewOn.checked&&!window.confirm('Turn off review examples and delete every kept example and the model?'))return;
      ++reviewGeneration;examples.replaceChildren();reviewOffset=0;
      try{await api('/v1/communications/review/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(reviewOn.checked?{enabled:true,acknowledgement_version:ackVersion,retention_days:Number(retention.value)}:{enabled:false})});await loadReview();}
      catch(error){reviewStatus.textContent=error.message;}
    };
    train.onclick=async()=>{resetQueue();train.disabled=true;reviewStatus.textContent='Training on this computer…';try{await api('/v1/communications/review/train',{method:'POST'});resetQueue();const fresh=await loadReview();if(fresh?.trained)reviewStatus.textContent='Model trained. Choose "Learned model" above to use it.';}catch(error){resetQueue();reviewStatus.textContent=error.message;}finally{train.disabled=false;}};
    dropModel.onclick=async()=>{resetQueue();dropModel.disabled=true;try{await api('/v1/communications/review/model/delete',{method:'POST'});resetQueue();await loadReview();}catch(error){resetQueue();reviewStatus.textContent=error.message;}finally{dropModel.disabled=false;}};
    review.addEventListener('toggle',()=>{if(review.open)loadReview();});

    function render(r){
      const parts=[el('h3','Intents'),el('div',Number(r.messages).toLocaleString()+' messages; '+Number(r.classified_messages).toLocaleString()+' classified (outbound replies are counted, not classified).','muted'),
        table(['Intent','Messages','Share'],r.intents.map(i=>[i.intent,Number(i.messages).toLocaleString(),pct(i.share)]))];
      if(r.learned_intents){parts.push(el('h3','Learned intents (a message can have several or none)'),
        table(['Intent','Present','Uncertain','Share present'],r.learned_intents.map(i=>[i.intent,Number(i.present_messages).toLocaleString(),Number(i.uncertain_messages).toLocaleString(),pct(i.present_share)])),
        el('div','Model estimates from your reviewed examples, not verified reasons for contact.','muted'));}
      if(r.review_examples)parts.push(el('div','Kept '+r.review_examples.added+' redacted review examples ('+r.review_examples.skipped_duplicates_or_empty+' skipped as duplicates or empty).','note'));
      if(r.ambiguous_between.length)parts.push(el('div','Ambiguous messages matched: '+r.ambiguous_between.map(a=>a.intent+' ('+a.messages+')').join(', ')+'.','muted'));
      parts.push(el('h3','Entities mentioned'),table(['Entity type','Messages','Mentions'],r.entities.map(e=>[e.type.replace(/_/g,' '),Number(e.messages).toLocaleString(),Number(e.mentions).toLocaleString()])),
        el('div','Only entity types are counted; values are never kept or shown.','muted'));
      const t=r.threads;
      parts.push(el('h3','Threads'),table(['Threads','Median messages','Threads with repeat contact','Median first response'],[[Number(t.threads).toLocaleString(),t.median_messages_per_thread===null?'—':t.median_messages_per_thread,Number(t.threads_with_repeat_inbound).toLocaleString(),t.first_response_seconds?duration(t.first_response_seconds.median)+' ('+t.first_response_seconds.count+' threads)':'—']]));
      if(r.weekly_volume.length)parts.push(el('h3','Weekly volume'),table(['Week','Messages','Most common intent'],r.weekly_volume.slice(-26).map(w=>{const top=Object.entries(w.by_intent).filter(([k])=>k!=='outbound reply').sort((a,b)=>b[1]-a[1])[0];return [w.week,Number(w.messages).toLocaleString(),top?top[0]+' ('+top[1]+')':'—'];})));
      if(r.unclassified_themes.length)parts.push(el('h3','Frequent phrases in unclassified messages'),el('div','Phrases in at least 5 messages that matched no intent; consider adding them to the taxonomy.','muted'),
        table(['Phrase','Messages'],r.unclassified_themes.map(x=>[x.terms,x.messages])));
      const doms=Object.entries(r.sender_domains);
      if(doms.length)parts.push(el('h3','Sender domains'),table(['Domain','Messages'],doms.map(([d,n])=>[d,n])),el('div','Domains with fewer than 5 messages are not listed ('+r.sender_domains_below_threshold+' messages).','muted'));
      if(r.event_log)parts.push(el('div','Saved the intent event log "'+r.event_log.name+'" with '+Number(r.event_log.events).toLocaleString()+' events. Open it under Large event logs to analyse it like any process.','note'));
      results.replaceChildren(...parts);
      if(r.event_log&&typeof window.owgEventLogRefresh==='function')window.owgEventLogRefresh(r.event_log.log_id);
    }
  }

  window.__owgCommsModel={detectFormat,parseTaxonomy,formatTaxonomy,csvHeader};
  if(typeof document!=='undefined'&&document.addEventListener)document.addEventListener('DOMContentLoaded',build);
})();
