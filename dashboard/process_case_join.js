/* Explicit local preparation of source-of-truth case-key imports; no I/O except selected file reads. */
(() => {
  'use strict';
  let section,picker,sourceHost,status,review,prepare,use,analyze,lookupButton,lookupHost;
  let sources=[],prepared=null,generation=0,lookupVisible=false;
  const plural=(count,noun)=>count+' '+noun+(count===1?'':'s');
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);return el;};
  const button=(text,action)=>{const el=make('button',text);el.type='button';el.className='secondary';el.addEventListener('click',action);return el;};
  function clearLookup(){lookupVisible=false;lookupHost?.replaceChildren();if(lookupButton)lookupButton.textContent='Show local shared-case lookup';}
  function invalidate(keepPreparation=false,notify=true){
    generation++;if(!keepPreparation)prepared=null;
    clearLookup();if(review)review.checked=false;if(use)use.disabled=true;if(analyze)analyze.disabled=true;
    if(lookupButton)lookupButton.disabled=!prepared;
    if(notify)window.owgProcessMining?.withdrawSharedCaseImport?.();
  }
  function reset(){
    invalidate(false,false);sources=[];if(picker)picker.value='';sourceHost?.replaceChildren();
    if(prepare)prepare.disabled=true;
    if(status)status.textContent='No shared-key import prepared. Files and native case keys stay on this device until explicit analysis.';
  }
  function choices(source,key,title,index){
    const label=make('label',title+' · Source '+(index+1)),select=make('select');
    select.setAttribute('aria-label',title+' · Source '+(index+1));
    const empty=make('option','Choose a column');empty.value='';select.appendChild(empty);
    source.inspection.headers.forEach((header,n)=>{const option=make('option',(n+1)+': '+(header||'(blank header)'));option.value=String(n);select.appendChild(option);});
    // Exact standard matches only; no guessed business meaning or automatic mapping application.
    const exact=source.inspection.headers.map((header,n)=>header.trim().toLowerCase()===key?n:-1).filter(n=>n>=0);
    select.value=exact.length===1?String(exact[0]):'';source.mapping[key]=select.value===''?null:Number(select.value);
    select.addEventListener('change',()=>{source.mapping[key]=select.value===''?null:Number(select.value);invalidate();status.textContent='Mappings changed. Prepare and review this shared-key import again.';});
    label.appendChild(select);return label;
  }
  function renderSources(){
    sourceHost.replaceChildren();
    sources.forEach((source,index)=>{
      const box=make('fieldset');box.appendChild(make('legend','Source '+(index+1)+' · '+source.file.name));
      const label=make('label','Delimiter · Source '+(index+1)),delimiter=make('select');delimiter.setAttribute('aria-label','Delimiter · Source '+(index+1));
      for(const [value,text] of [[',','Comma'],[';','Semicolon'],['\t','Tab']]){const option=make('option',text);option.value=value;delimiter.appendChild(option);}delimiter.value=source.delimiter;
      delimiter.addEventListener('change',()=>{source.delimiter=delimiter.value;sources.forEach(s=>{s.inspection=null;s.mapping={};});invalidate();renderSources();status.textContent='Delimiter changed. Inspect the selected exports again.';});label.appendChild(delimiter);box.appendChild(label);
      if(source.inspection){
        box.appendChild(make('p',source.inspection.row_count+' event rows inspected locally. Unselected columns are excluded.'));
        const controls=make('div');controls.className='owg-case-join-fields';
        for(const [key,title] of [['case_id','Shared authoritative case key'],['activity','Activity column'],['timestamp','Timestamp column']])controls.appendChild(choices(source,key,title,index));
        box.appendChild(controls);
      }sourceHost.appendChild(box);
    });
  }
  async function inspect(){
    if(sources.length<2||sources.length>4)return;
    invalidate();const attempt=generation,selected=[...sources];
    status.textContent='Inspecting selected export headers and structure on this device…';
    try{
      const results=await Promise.all(selected.map(source=>window.owgProcessCSVMapping.inspectFile(source.file,source.delimiter)));
      if(attempt!==generation)return;
      results.forEach((inspection,index)=>{selected[index].inspection=inspection;selected[index].mapping={};});renderSources();
      prepare.disabled=false;status.textContent='Choose each export’s authoritative shared case key, activity and timestamp. Keys match literally and case-sensitively, without whitespace or Unicode normalization. Nothing has been uploaded.';
    }catch(error){if(attempt===generation){prepare.disabled=true;status.textContent='Unable to inspect exports: '+error.message;}}
  }
  function prepareJoin(){
    invalidate();
    try{
      prepared=window.owgProcessCSVMapping.prepareSharedCaseImport(sources.map(source=>({inspection:source.inspection,mapping:source.mapping})));
      lookupButton.disabled=false;
      status.textContent=plural(prepared.event_count,'event')+' across '+plural(prepared.case_count,'case')+' from '+plural(prepared.source_count,'export')+'; '+plural(prepared.multi_source_case_count,'case')+(prepared.multi_source_case_count===1?' shares':' share')+' keys across exports. Review the same authoritative case namespace across sources. Equal-looking ticket IDs from unrelated systems are not equivalent case keys. No timing or activity similarity is used.';
    }catch(error){status.textContent='Unable to prepare shared-key import: '+error.message;}
  }
  function install(parent){
    if(section)return;
    section=make('details');section.id='owgSharedCaseImport';section.appendChild(make('summary','Combine authorized exports using a shared case key'));
    section.appendChild(make('p','Choose two to four logs whose selected keys identify the same authoritative business cases. Map each export independently. This combines only literal reviewed keys within this import, preserves unmatched cases, and never infers associations from time or names. Case keys, filenames and unused columns stay in browser memory; only structural events and case/source aliases are sent after explicit analysis.'));
    picker=make('input');picker.type='file';picker.accept='.csv,text/csv';picker.multiple=true;picker.setAttribute('aria-label','Choose two to four event-log exports');
    sourceHost=make('div');sourceHost.id='owgSharedCaseSources';
    status=make('p','No shared-key import prepared.');status.id='owgSharedCaseStatus';status.setAttribute('role','status');
    picker.addEventListener('change',()=>{
      invalidate();const files=Array.from(picker.files||[]);prepare.disabled=true;
      if(files.length<2||files.length>4){sources=[];sourceHost.replaceChildren();status.textContent='Choose two to four authorized exports.';return;}
      sources=files.map(file=>({file,delimiter:',',inspection:null,mapping:{}}));renderSources();
      status.textContent=sources.length<2||sources.length>4?'Choose two to four authorized exports.':'Exports selected locally. Inspect their columns before preparing a shared-key import.';
    });
    const inspectButton=button('Inspect selected export columns',inspect);
    prepare=button('Prepare shared-key import for review',prepareJoin);prepare.disabled=true;
    const label=make('label');review=make('input');review.type='checkbox';review.id='owgSharedCaseReview';
    label.append(review,make('span',' I reviewed that these selected keys identify the same authoritative cases across all chosen sources; exclude all other columns.'));
    review.addEventListener('change',()=>{
      if(!review.checked){invalidate(true);status.textContent='Shared-key review withdrawn. Future transfers are canceled; the selected local files remain available for review. Already saved snapshots use their separate Delete action.';}
      use.disabled=!review.checked||!prepared;
    });
    use=button('Use reviewed shared-key import for next analysis',()=>{
      if(!prepared||!review.checked)return;
      const file=prepared.createMappedFile(true);
      if(window.owgProcessMining?.useSharedCaseImport?.(file)){analyze.disabled=false;status.textContent='Reviewed combined log ready for explicit analysis. Nothing has been uploaded or retained.';}
    });use.disabled=true;
    analyze=button('Analyze reviewed combined log',()=>window.owgProcessMining?.analyzeSharedCaseImport?.());analyze.disabled=true;
    lookupHost=make('div');lookupHost.id='owgSharedCaseLookup';
    lookupButton=button('Show local shared-case lookup',()=>{
      if(!prepared)return;
      if(lookupVisible){clearLookup();return;}
      const list=make('ul');for(const row of prepared.inspectLocalCaseKeys())list.appendChild(make('li',row.label+' · '+JSON.stringify(row.source)+' · '+row.origins.map(origin=>origin.replace(/^source_/,'Source ')).join(', ')));
      lookupHost.replaceChildren(list);lookupVisible=true;lookupButton.textContent='Hide local shared-case lookup';
    });lookupButton.disabled=true;
    const style=make('style');style.textContent='#owgSharedCaseImport fieldset{min-width:0;max-width:100%;margin:12px 0;overflow-wrap:anywhere}#owgSharedCaseImport .owg-case-join-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}#owgSharedCaseImport legend{max-width:100%;white-space:normal;overflow-wrap:anywhere}#owgSharedCaseImport label{display:block;margin:8px 0}#owgSharedCaseImport select{max-width:100%;width:100%;min-width:0}#owgSharedCaseImport input[type=file]{max-width:100%}#owgSharedCaseLookup{overflow-wrap:anywhere}';
    section.append(style,picker,inspectButton,sourceHost,prepare,status,lookupButton,lookupHost,label,use,analyze);parent.appendChild(section);
  }
  window.owgProcessCaseJoin=Object.freeze({install,reset});
})();
