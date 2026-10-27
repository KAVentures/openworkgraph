/* Exact, human-declared process dependencies. No persistent browser state or grants. */
(() => {
  'use strict';
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  const label=(text,node)=>{const n=el('label',text+' ');n.append(node);return n;};
  const button=(text,id,fn)=>{const n=el('button',text);n.type='button';if(id)n.id=id;n.onclick=fn;return n;};
  const options=(node,rows)=>node.replaceChildren(...rows.map(([id,text])=>{const n=el('option',text);n.value=id;return n;}));
  const ref=p=>({workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
  const same=(a,b)=>a.workflow_id===b.workflow_id&&a.revision===b.revision&&a.content_sha256===b.content_sha256;
  const saveFile=(value,name)=>{const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url);};
  function create({inventoryHost,detailHost,call,open,onDenied}){
    let epoch=0,busy=false,selected=null,view=null,draft=null,dirty=false,choices=[],after='',graph=null,focus='';
    const company=el('fieldset'),output=el('div'),notice=el('p'),focusPicker=el('select');company.id='dependencyWorkspace';output.id='dependencyGraphResult';notice.id='dependencyGraphStatus';notice.setAttribute('role','status');focusPicker.id='dependencyFocus';
    const downloadGraph=button('Download current dependency report','dependencyGraphExport',()=>work(async ok=>{if(!graph)return;const r=await call('/v1/human/process-dependency-graph/export?'+new URLSearchParams({fingerprint:graph.fingerprint,...(focus?{focus}:{})}));if(ok())saveFile(r,'owg-process-dependency-graph.json');}));downloadGraph.disabled=true;
    const focusButton=button('Inspect declared change impact','dependencyInspect',()=>work(ok=>loadGraph(ok,focusPicker.value)));focusButton.disabled=true;
    company.append(el('legend','Process dependencies and change impact'),el('p','Arrows point from a consumer to a prerequisite. These are saved human declarations, separate from repository groupings, executable calls, permissions and benefit totals. Impact lists potential review candidates, not verified causal effects.'),
      button('Load current dependency graph','dependencyGraphLoad',()=>work(ok=>loadGraph(ok))),label('Process for dependency impact',focusPicker),focusButton,downloadGraph,notice,output);inventoryHost.append(company);
    const host=el('div');detailHost.append(host);let form=null,status=null,confirm=null,save=null,download=null,picker=null,pickerFilter=null,choiceNote=null,next=null,list=null;
    const note=text=>{if(status)status.textContent=text;notice.textContent=text;};
    function clearGraph(){graph=null;focus='';output.replaceChildren();options(focusPicker,[]);focusButton.disabled=downloadGraph.disabled=true;notice.textContent='';}
    function clear(){epoch++;busy=false;selected=view=draft=null;dirty=false;choices=[];after='';host.replaceChildren();form=status=null;company.disabled=false;clearGraph();}
    async function work(fn){if(busy)return;const e=epoch;busy=true;company.disabled=true;if(form)form.disabled=true;
      try{await fn(()=>e===epoch);}catch(error){if(e!==epoch)return;clearGraph();if([401,403].includes(error.status)){clear();onDenied();return;}note(error.message||'Dependency request failed.');}
      finally{if(e===epoch){busy=false;company.disabled=false;if(form)form.disabled=false;}}
    }
    function select(data){epoch++;busy=false;selected=data;view=draft=null;dirty=false;choices=[];after='';host.replaceChildren();form=null;company.disabled=false;clearGraph();
      status=el('p');status.id='dependencyStatus';status.setAttribute('role','status');host.append(el('h3','Dependencies of this process'),el('p','Pin prerequisite designs and explain each relationship. Empty saved links explicitly record that no prerequisites were declared; absent declarations remain unassessed. New process versions require explicit reference refresh and reassessment. Normal model review, benefits and implementation exports remain independent.'),button('Load process dependencies','dependencyLoad',()=>work(load)),status);
    }
    function writable(){return view?.can_write&&selected.permissions.edit&&!selected.viewing_historical_revision&&(!view.artifact||view.artifact.revision===view.latest_revision);}
    function changed(){dirty=true;if(confirm)confirm.checked=false;clearGraph();update();note('Unsaved dependency changes. Review and confirm sharing before saving.');}
    function update(){if(save)save.disabled=!writable()||!confirm.checked;if(download)download.disabled=dirty||!view?.artifact||!view.integrity_ok;}
    function initial(){return {format:'openworkgraph.process-dependencies.v1',organization_id:selected.organization_id,root_process:ref(selected.process),links:[],relationships_independently_verified:false,executable:false,propagates_process_review:false,execution_authorized:false,benefit_rollup_included:false};}
    async function load(ok,revision){const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/dependencies'+(revision?'?revision='+revision:''));if(!ok())return;view=r;draft=structuredClone(r.artifact?.dependencies||initial());dirty=false;render();
      note(!r.integrity_ok?'Dependency integrity failed. An owner can erase all history; content is withheld.':r.artifact?'Opened dependency revision '+r.artifact.revision+'. '+(r.all_references_current?'All saved references are current.':'References need reassessment; inspect source changes.'):'No dependency declaration saved. Review explicit links or an empty declaration before sharing.');}
    function choiceOptions(){const q=pickerFilter.value.toLocaleLowerCase();options(picker,choices.filter(p=>p.workflow_id!==selected.process.workflow_id&&(p.title+' '+p.workflow_id).toLocaleLowerCase().includes(q)).map(p=>[p.workflow_id,p.title+' · r'+p.revision]));}
    async function loadChoices(ok,cursor=''){const r=await call('/v1/human/processes?limit=100&after='+encodeURIComponent(cursor));if(!ok())return;choices=r.processes;after=r.next_after||'';choiceOptions();next.disabled=!after;choiceNote.textContent='Showing '+choices.length+' source processes in this page; filter applies only to this page.';}
    function description(reference,identifier){const info=view.resolutions.find(r=>r.link_id===identifier&&same(r,reference));return (info?.available?info.title:reference.workflow_id)+' · pinned r'+reference.revision+' · '+(info?info.state+(info.available?' · model review '+info.independent_model_review:''):'edited reference; save or reload to check');}
    async function refresh(ok,reference){const r=await call('/v1/human/processes/'+encodeURIComponent(reference.workflow_id));if(!ok())return;if(r.archived)throw new Error('The referenced process is archived.');Object.assign(reference,ref(r.process));changed();draw();note('Reference refreshed in the draft; review and save the exact new basis.');}
    function draw(){list.replaceChildren(el('p','Consumer root: '+description(draft.root_process,'root')));
      if(writable())list.append(button('Refresh dependency root','dependencyRootRefresh',()=>work(ok=>refresh(ok,draft.root_process))));
      if(!draft.links.length)list.append(el('p','No prerequisite links in this declaration.'));
      for(const link of draft.links){const row=el('fieldset');row.append(el('legend',link.id),el('p',description(link.prerequisite,link.id)),button('Open pinned prerequisite '+link.id,null,()=>work(()=>open(link.prerequisite.workflow_id,link.prerequisite.revision))));
        const kind=el('select');options(kind,[['uses_output','Uses output'],['uses_policy','Uses policy']]);kind.value=link.relationship;kind.disabled=!writable();kind.onchange=()=>{link.relationship=kind.value;changed();};
        const reason=el('input');reason.maxLength=500;reason.value=link.reason;reason.disabled=!writable();reason.oninput=()=>{link.reason=reason.value;changed();};
        row.append(label('Relationship '+link.id,kind),label('Reason '+link.id,reason));
        if(writable())row.append(button('Refresh prerequisite '+link.id,null,()=>work(ok=>refresh(ok,link.prerequisite))),button('Remove dependency '+link.id,null,()=>{draft.links=draft.links.filter(r=>r.id!==link.id);changed();draw();}));list.append(row);
      }update();
    }
    function render(){if(form)form.remove();form=el('fieldset');form.id='dependencyEditor';form.append(el('legend','Saved dependency declaration'));
      if(view.artifact)form.append(el('p','Saved by '+view.artifact.author_actor_id+' at '+view.artifact.saved_at+'. Relationships are not independently verified.'));
      list=el('div');list.id='dependencyLinks';form.append(list);
      if(writable()){
        picker=el('select');picker.id='dependencyChoice';pickerFilter=el('input');pickerFilter.id='dependencyChoiceFilter';pickerFilter.maxLength=120;pickerFilter.oninput=choiceOptions;choiceNote=el('p');next=button('Next dependency choice page','dependencyChoiceNext',()=>work(ok=>loadChoices(ok,after)));next.disabled=true;
        form.append(button('Load prerequisite choices','dependencyChoices',()=>work(ok=>loadChoices(ok))),label('Filter loaded prerequisite choices',pickerFilter),choiceNote,next,label('Prerequisite process',picker),button('Add prerequisite process','dependencyAdd',()=>{
          const p=choices.find(p=>p.workflow_id===picker.value);if(!p){note('Load and choose a prerequisite.');return;}
          if(draft.links.length>=20||draft.links.some(link=>link.prerequisite.workflow_id===p.workflow_id)){note('Use at most twenty distinct prerequisites.');return;}
          let id=1;while(draft.links.some(link=>link.id==='dependency_'+id))id++;
          draft.links.push({id:'dependency_'+id,prerequisite:ref(p),relationship:'uses_output',reason:'Uses the declared output of this process'});changed();draw();note('Prerequisite added to draft. Review its exact relationship before sharing.');}));
      }
      confirm=el('input');confirm.type='checkbox';confirm.id='dependencyConfirm';confirm.disabled=!writable();confirm.onchange=update;
      save=button('Save reviewed dependencies','dependencySave',()=>work(async ok=>{await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/dependencies',{dependencies:structuredClone(draft),expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:confirm.checked,organization_sharing_confirmed:confirm.checked});if(!ok())return;clearGraph();await load(ok);if(ok())note('Dependencies saved. No model approval, execution grant or benefit total was changed.');}));
      download=button('Download saved dependency manifest','dependencyExport',()=>work(async ok=>{if(dirty||!view?.artifact)return;const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/dependencies/export?revision='+view.artifact.revision);if(ok())saveFile(r,'owg-process-dependencies.json');}));
      const history=el('select');history.id='dependencyRevision';options(history,Array.from({length:Math.min(20,view.latest_revision)},(_,i)=>[String(i+1),'Dependency revision '+(i+1)]));if(view.artifact)history.value=String(view.artifact.revision);
      const erase=button('Erase all dependency history','dependencyDelete',()=>work(async ok=>{if(!window.confirm('Erase all dependency declarations for this root? Other process records remain.'))return;await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/dependencies/delete',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(!ok())return;clearGraph();await load(ok);if(ok())note('Dependency history erased. Other declarations may still reference this process.');}));erase.disabled=!view.can_delete||!view.latest_revision;
      form.append(label('I reviewed these exact process dependencies and confirm sharing their reasons and references with organization process readers',confirm),save,download,label('Saved dependency history',history),button('Open dependency revision','dependencyHistory',()=>work(ok=>load(ok,Number(history.value)))),erase);host.append(form);draw();if(busy)form.disabled=true;
    }
    async function loadGraph(ok,selectedFocus=''){clearGraph();const r=await call('/v1/human/process-dependency-graph'+(selectedFocus?'?focus='+encodeURIComponent(selectedFocus):''));if(!ok())return;graph=r;focus=selectedFocus;paintGraph();}
    function paintGraph(){output.replaceChildren();notice.textContent='As of '+graph.checked_at+' · '+graph.status.replaceAll('_',' ')+'. This is a refreshable snapshot of saved declarations, not live or complete business dependency discovery.';downloadGraph.disabled=false;
      for(const b of graph.blockers)output.append(el('p','Impact withheld: '+b.code.replaceAll('_',' ')));
      if(!graph.graph){focusButton.disabled=true;return;}
      const data=graph.graph;options(focusPicker,data.nodes.map(n=>[n.workflow_id,(n.available?n.title:n.workflow_id)+' · '+(n.dependencies_assessed?'assessed':'unassessed')]));if(focus)focusPicker.value=focus;focusButton.disabled=!data.nodes.length;
      output.append(el('p',data.nodes.length+' declared processes · '+data.edges.length+' saved relationships. '+(graph.current_basis?'Every pin matches an active reviewed model and every node has a declaration.':'Source pins/reviews or unassessed dependencies need review. Relationships remain human assertions.')));
      if(graph.impact){const groups=[['Prerequisites',graph.impact.prerequisites],['Potential consumers to review after a change',graph.impact.potential_consumers]];for(const [title,rows] of groups){const section=el('section');section.append(el('h4',title));const ul=el('ul');for(const row of rows){const n=data.nodes.find(n=>n.workflow_id===row.workflow_id);ul.append(el('li',(n?.available?n.title:row.workflow_id)+' · distance '+row.distance));}if(!rows.length)ul.append(el('li','None found in saved declarations. This does not establish absence in the business.'));section.append(ul);output.append(section);}}
      drawMap(data);
      const details=el('details');details.append(el('summary','Inspect every saved process and relationship'));const list=el('ul');
      for(const node of data.nodes){const row=el('li',(node.available?node.title:node.workflow_id)+' · '+node.state+' · '+(node.all_pins_match_latest?'pins match latest':'pins need reassessment')+' · '+(node.dependencies_assessed?'declaration r'+node.declaration_revision:'dependencies unassessed')+' · model review '+(node.independent_model_review||'unavailable'));
        row.append(button('Inspect impact '+node.workflow_id,null,()=>work(ok=>loadGraph(ok,node.workflow_id))));if(node.available)row.append(button('Open latest process '+node.workflow_id,null,()=>work(()=>open(node.workflow_id))));list.append(row);}
      for(const edge of data.edges)list.append(el('li',edge.consumer+' → '+edge.prerequisite+' · '+edge.relationship+' · declaration r'+edge.declaration_revision+' · target model r'+edge.prerequisite_process.revision));details.append(list);output.append(details);
    }
    function drawMap(data){
      const NS='http://www.w3.org/2000/svg';const svgEl=(tag,attrs={})=>{const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
      const ordered=[...data.process_ids].sort((a,b)=>(a===b?0:a===focus?-1:b===focus?1:a.localeCompare(b)));const ids=ordered.slice(0,50),set=new Set(ids),edges=data.edges.filter(e=>set.has(e.consumer)&&set.has(e.prerequisite));
      const frame=el('div');frame.className='dependency-map';frame.tabIndex=0;frame.setAttribute('role','region');frame.setAttribute('aria-label','Scrollable declared dependency map');
      const columns=Math.min(5,Math.max(1,ids.length)),width=columns*250,height=Math.max(1,Math.ceil(ids.length/columns))*95;
      const svg=svgEl('svg',{width,height,viewBox:'0 0 '+width+' '+height,role:'group','aria-label':'Declared dependencies. Arrows point from consumer to prerequisite.'});
      const defs=svgEl('defs'),marker=svgEl('marker',{id:'dependencyArrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:7,markerHeight:7,orient:'auto-start-reverse'});marker.append(svgEl('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#61728a'}));defs.append(marker);svg.append(defs);
      const position=new Map(ids.map((id,i)=>[id,{x:(i%columns)*250+12,y:Math.floor(i/columns)*95+15}]));
      for(const edge of edges){const a=position.get(edge.consumer),b=position.get(edge.prerequisite),dx=b.x-a.x,dy=b.y-a.y,t=Math.min(112/Math.abs(dx),32/Math.abs(dy));const path=svgEl('path',{d:'M '+(a.x+110+dx*t)+' '+(a.y+30+dy*t)+' L '+(b.x+110-dx*t)+' '+(b.y+30-dy*t),stroke:'#61728a','stroke-width':1.5,fill:'none','marker-end':'url(#dependencyArrow)'});const title=svgEl('title');title.textContent=edge.consumer+' requires '+edge.prerequisite;path.append(title);svg.append(path);}
      for(const id of ids){const node=data.nodes.find(n=>n.workflow_id===id),p=position.get(id),g=svgEl('g',{tabindex:0,role:'button','aria-label':'Inspect impact of '+(node?.available?node.title:id),transform:'translate('+p.x+' '+p.y+')'});g.style.cursor='pointer';
        g.append(svgEl('rect',{width:220,height:60,rx:8,fill:id===focus?'#dceafa':'#ffffff',stroke:node?.current_declared_basis?'#405b46':'#9b691e','stroke-width':2}));
        const title=svgEl('text',{x:10,y:22,fill:'#1c2b3d','font-size':13});title.textContent=Array.from(node?.available?node.title:id).slice(0,27).join('');g.append(title);
        const state=svgEl('text',{x:10,y:43,fill:'#34485d','font-size':11});state.textContent=node?.dependencies_assessed?'declaration r'+node.declaration_revision:'dependencies unassessed';g.append(state);
        const inspect=()=>work(ok=>loadGraph(ok,id));g.onclick=inspect;g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inspect();}};svg.append(g);}
      frame.append(svg);output.append(el('p','Map shows '+ids.length+' of '+data.process_ids.length+' processes and '+edges.length+' of '+data.edges.length+' relationships. The impact lists above use the entire saved graph. Select a node or inspect the full list.'),frame);
    }
    return {clear,select};
  }
  window.owgProcessDependencies={create};
})();
