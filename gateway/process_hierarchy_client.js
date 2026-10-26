/* Repository maps are descriptive versioned artifacts. No storage, execution or inherited approval. */
(() => {
  'use strict';
  const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(tag==='select'||tag==='button')n.style.maxWidth='100%';return n;};
  const input=(id)=>{const n=el('input');n.id=id;n.maxLength=120;return n;};
  const label=(text,node)=>{const n=el('label',text);n.append(node);return n;};
  const button=(text,fn,id)=>{const n=el('button',text);n.type='button';if(id)n.id=id;n.onclick=fn;return n;};
  const options=(node,items)=>{node.replaceChildren(...items.map(([value,text])=>{const n=el('option',text);n.value=value;return n;}));};
  const ref=p=>({workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256});
  const key=r=>r.workflow_id+':'+r.revision+':'+r.content_sha256;
  function create({inventoryHost,detailHost,call,open,onDenied}){
    let epoch=0,selected=null,view=null,draft=null,dirty=false,busy=false,choices=[],choiceAfter='',mapPage=[],mapAfter='',names=new Map();
    const inventory=el('div'),filter=input('hierarchyFilter'),mapList=el('div'),mapsNext=button('Next map page',()=>work(loadMaps)),mapNote=el('p');
    inventory.append(el('h2','Process repository maps'),el('p','Versioned organization groupings of existing designs, with pinned references and visible changes. Maps do not add executable subprocess calls or combine process benefits.'),
      button('Load repository maps',()=>{mapAfter='';work(loadMaps);},'hierarchyInventory'),label('Filter this loaded page by title, category or capability',filter),mapNote,mapList,mapsNext);
    inventoryHost.append(inventory);mapsNext.disabled=true;
    const host=el('div');detailHost.append(host);
    let form=null,status=null,title=null,category=null,capability=null,confirm=null,tree=null,parent=null,picker=null,pickFilter=null,history=null,save=null,download=null,remove=null;
    const message=text=>{if(status)status.textContent=text;};
    const current=e=>e===epoch;
    async function work(fn){
      if(busy)return;const e=epoch;busy=true;if(form)form.disabled=true;
      try{await fn(()=>current(e));}
      catch(error){if(!current(e))return;if([401,403].includes(error.status)){clear();onDenied();return;}message(error.message||'Repository request failed.');mapNote.textContent=error.message||'Repository request failed.';}
      finally{if(current(e)){busy=false;if(form)form.disabled=false;}}
    }
    function clear(){epoch++;selected=view=draft=null;dirty=busy=false;choices=[];choiceAfter=mapAfter='';mapPage=[];names.clear();host.replaceChildren();mapList.replaceChildren();mapNote.textContent='';filter.value='';mapsNext.disabled=true;form=status=null;}
    function select(data){epoch++;busy=false;selected=data;view=draft=null;dirty=false;choices=[];choiceAfter='';names.clear();host.replaceChildren();form=status=null;
      const h=el('h3','Repository hierarchy for this root process');status=el('p');status.id='hierarchyStatus';status.setAttribute('role','status');
      host.append(h,el('p','Repository grouping is separate from the selected activity graph. Existing BPMN, PDD and agent-project exports include that flat design only. Use the separate map manifest to retain hierarchy references; it grants no child review or execution authority.'),
        button('Load root repository map',()=>work(load),'hierarchyLoad'),status);
    }
    function initial(){return {format:'openworkgraph.process-repository-hierarchy.v1',organization_id:selected.organization_id,title:Array.from(selected.process.title.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g,' ').trim()||'Repository map').slice(0,120).join(''),category:'',capability:'',
      nodes:[{id:'root',parent_id:null,process:ref(selected.process)}],executable:false,propagates_process_review:false,execution_authorized:false,benefit_rollup_included:false};}
    async function load(ok,revision){
      const id=selected.process.workflow_id;const result=await call('/v1/human/processes/'+encodeURIComponent(id)+'/hierarchy'+(revision?'?revision='+revision:''));
      if(!ok())return;view=result;draft=structuredClone(result.artifact?.hierarchy||initial());dirty=false;names.clear();
      for(const n of result.resolutions)if(n.available)names.set(key(n),n.title);
      if(!result.artifact&&result.integrity_ok)names.set(key(ref(selected.process)),selected.process.title);render();
      message(!result.integrity_ok?'Map integrity failed. An owner can erase its history; no content is displayed.':result.artifact?'Opened map revision '+result.artifact.revision+'. '+(result.all_references_current?'All pinned references are current.':'Some references have changed, are archived or are unavailable.'): 'No map saved yet. Build and explicitly share this grouping.');
    }
    function changed(){dirty=true;if(confirm)confirm.checked=false;update();}
    function writable(){return view?.can_write&&selected.permissions.edit&&(!view.artifact||view.artifact.revision===view.latest_revision);}
    function update(){if(save)save.disabled=!writable()||!confirm.checked;if(download)download.disabled=dirty||!view?.artifact||!view.integrity_ok;}
    function valid(nodes){
      if(nodes.length>100)throw new Error('Use at most 100 nodes.');const index=new Map(nodes.map(n=>[n.id,n]));const siblings=new Set();
      for(const n of nodes){const s=n.parent_id+':'+n.process.workflow_id;if(siblings.has(s))throw new Error('This process is already under that parent.');siblings.add(s);
        let at=n,seen=new Set(),processes=new Set(),level=0;
        while(at){if(seen.has(at.id)||processes.has(at.process.workflow_id))throw new Error('A process cannot appear within its own ancestry.');seen.add(at.id);processes.add(at.process.workflow_id);if(++level>4)throw new Error('Use at most four total levels, including root.');at=at.parent_id===null?null:index.get(at.parent_id);}
      }
    }
    function pickerOptions(){const needle=pickFilter.value.toLocaleLowerCase();options(picker,choices.filter(p=>(p.title+' '+p.workflow_id).toLocaleLowerCase().includes(needle)).map(p=>[p.workflow_id,p.title+' · r'+p.revision]));}
    async function loadChoices(ok,after=''){
      const data=await call('/v1/human/processes?limit=100&after='+encodeURIComponent(after));if(!ok())return;
      choices=data.processes;choiceAfter=data.next_after||'';pickerOptions();
      choiceNote.textContent='Showing '+choices.length+' processes in this page. '+(data.has_more?'More pages available.':'End of process choices.');choiceNext.disabled=!choiceAfter;
    }
    let choiceNote,choiceNext;
    function draw(){
      tree.replaceChildren();options(parent,draft.nodes.map(n=>[n.id,n.id+' · '+(names.get(key(n.process))||n.process.workflow_id)]));
      const byParent=new Map();for(const n of draft.nodes){const list=byParent.get(n.parent_id)||[];list.push(n);byParent.set(n.parent_id,list);}
      function branch(id){const list=el('ul');for(const node of byParent.get(id)||[]){const li=el('li');li.dataset.nodeId=node.id;const info=view.resolutions.find(r=>r.node_id===node.id&&key(r)===key(node.process));
        li.append(el('span',node.id+' · '+(names.get(key(node.process))||node.process.workflow_id)+' · pinned r'+node.process.revision+' · '+(info?info.state+(info.available?' · model review: '+info.independent_model_review:''):'edited reference; save/refresh to inspect')),
          button('Open pinned design',()=>work(async ok=>{await open(node.process.workflow_id,node.process.revision);}),undefined));
        if(writable()){
          const move=el('select');move.setAttribute('aria-label','New parent for '+node.id);options(move,draft.nodes.filter(n=>n.id!==node.id).map(n=>[n.id,n.id]));move.value=node.parent_id||'root';
          if(node.id!=='root')li.append(move,button('Move '+node.id,()=>{try{const next=structuredClone(draft.nodes);next.find(n=>n.id===node.id).parent_id=move.value;valid(next);draft.nodes=next;changed();draw();}catch(e){message(e.message);}}),
            button('Remove branch '+node.id,()=>{const gone=new Set([node.id]);let size;do{size=gone.size;draft.nodes.forEach(n=>{if(gone.has(n.parent_id))gone.add(n.id);});}while(size!==gone.size);draft.nodes=draft.nodes.filter(n=>!gone.has(n.id));changed();draw();}));
          li.append(button('Refresh reference '+node.id,()=>work(async ok=>{const latest=await call('/v1/human/processes/'+encodeURIComponent(node.process.workflow_id));if(!ok())return;if(latest.archived)throw new Error('This process is archived.');node.process=ref(latest.process);names.set(key(node.process),latest.process.title);changed();draw();message('Updated '+node.id+' to r'+node.process.revision+'; review and save the map.');})));
        }
        const child=branch(node.id);if(child.childElementCount)li.append(child);list.append(li);
      }return list;}
      tree.append(branch(null));update();
    }
    function render(){
      if(form)form.remove();form=el('fieldset');form.id='hierarchyEditor';form.append(el('legend','Repository map'));if(view.artifact)form.append(el('p','Saved by '+view.artifact.author_actor_id+' at '+view.artifact.saved_at+'. This confirms the grouping only.'));
      title=input('hierarchyTitle');category=input('hierarchyCategory');capability=input('hierarchyCapability');title.value=draft.title;category.value=draft.category;capability.value=draft.capability;
      for(const [name,node] of [['title',title],['category',category],['capability',capability]])node.oninput=()=>{draft[name]=node.value;changed();};
      form.append(label('Map title',title),label('Category',category),label('Capability',capability));
      for(const n of [title,category,capability])n.disabled=!writable();
      tree=el('div');tree.id='hierarchyTree';form.append(tree);
      if(writable()){
        parent=el('select');parent.id='hierarchyParent';picker=el('select');picker.id='hierarchyChoice';pickFilter=input('hierarchyChoiceFilter');pickFilter.oninput=pickerOptions;
        choiceNote=el('p');choiceNext=button('Next process page',()=>work(ok=>loadChoices(ok,choiceAfter)),'hierarchyChoiceNext');choiceNext.disabled=true;
        form.append(button('Load process choices',()=>work(ok=>loadChoices(ok)),'hierarchyChoices'),label('Filter loaded process choices',pickFilter),choiceNote,choiceNext,
          label('Parent node',parent),label('Process to add',picker),button('Add child process',()=>{try{const p=choices.find(p=>p.workflow_id===picker.value);if(!p)throw new Error('Load and choose a process.');
            let i=1;while(draft.nodes.some(n=>n.id==='n'+i))i++;const nodes=[...draft.nodes,{id:'n'+i,parent_id:parent.value,process:ref(p)}];valid(nodes);draft.nodes=nodes;names.set(key(ref(p)),p.title);changed();draw();message('Child added to the draft; not shared until saved.');}catch(e){message(e.message);}},'hierarchyAdd'));
      }else{parent=el('select');}
      confirm=el('input');confirm.type='checkbox';confirm.id='hierarchyConfirm';confirm.disabled=!writable();confirm.onchange=update;
      save=button('Save reviewed repository map',()=>work(async ok=>{const h=structuredClone(draft);if(!h.title.trim())throw new Error('Enter a map title.');
        await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/hierarchy',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,hierarchy:h,user_confirmed:confirm.checked,organization_sharing_confirmed:confirm.checked});
        if(!ok())return;await load(ok);if(ok())message('Repository map saved. Model review and execution permissions were not changed.');}),'hierarchySave');
      download=button('Download saved map manifest',()=>work(async ok=>{if(dirty||!view.artifact)return;const r=await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/hierarchy/export?revision='+view.artifact.revision);if(!ok())return;
        const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='owg-process-repository-map.json';a.click();URL.revokeObjectURL(url);}), 'hierarchyExport');
      form.append(label('I reviewed this grouping and confirm sharing its title, category, capability and exact process references with organization process readers',confirm),save,download);
      history=el('select');history.id='hierarchyRevision';options(history,Array.from({length:view.latest_revision},(_,i)=>[String(i+1),'Map revision '+(i+1)]));if(view.artifact)history.value=String(view.artifact.revision);
      form.append(label('Saved map history',history),button('Open map revision',()=>work(ok=>load(ok,Number(history.value))),'hierarchyHistory'));
      remove=button('Erase all map history',()=>work(async ok=>{if(!window.confirm('Erase every repository map revision for this root? Process designs will remain.'))return;
        await call('/v1/human/processes/'+encodeURIComponent(selected.process.workflow_id)+'/hierarchy/delete',{expected_revision:view.latest_revision,expected_access_revision:view.access_revision,user_confirmed:true});if(ok()){await load(ok);if(ok())message('Map history erased. Process designs remain.');}}),'hierarchyDelete');remove.disabled=!view.can_delete||!view.latest_revision;form.append(remove);
      host.append(form);draw();if(busy)form.disabled=true;
    }
    function filterMaps(){const q=filter.value.toLocaleLowerCase();mapList.replaceChildren(...mapPage.filter(m=>(m.title+' '+(m.category||'')+' '+(m.capability||'')).toLocaleLowerCase().includes(q)).map(m=>{
      const row=el('p',m.title+' · r'+m.revision+(m.integrity_ok?' · '+m.category+' / '+m.capability+' · '+m.declared_node_count+' declared nodes'+(m.root_archived?' · root archived':''):''));row.append(button('Open repository root',()=>work(()=>open(m.workflow_id))));return row;}));}
    async function loadMaps(ok){const r=await call('/v1/human/process-hierarchies?limit=100&after='+encodeURIComponent(mapAfter));if(!ok())return;mapPage=r.maps;mapAfter=r.next_after||'';mapsNext.disabled=!r.has_more;mapNote.textContent=r.maps.length+' repository maps in this page; filters apply only to this page.';filterMaps();}
    filter.oninput=filterMaps;
    return {clear,select};
  }
  window.owgProcessHierarchy={create};
})();
