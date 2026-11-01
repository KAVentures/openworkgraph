/* Provider configuration uses the existing named-admin authorization. */
(function(root){
 'use strict';
 let generation=0,host=null;
 const el=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
 function clear(){generation++;if(host)host.replaceChildren();host=null;}
 async function mount(container,options){
  clear();host=container;const current=++generation;
  const valid=()=>generation===current&&host===container&&options.isCurrent();
  const title=el('h2','Identity provisioning (SCIM Users and Groups)');
  const help=el('p','Connect one explicitly approved provisioning provider to this organization’s employee roster. New credentials are disabled until enabled. Deactivation revokes employee access; rehire keeps old browser/device credentials revoked. Provisioning does not start recording or share evidence.');help.className='help';
  const limits=el('p','Supported: work-email userName, display name, optional bounded core name fields and unverified contact emails, active state and provider external ID. Bulk supports up to 50 Users/Groups operations per request (64 KiB). Operations commit separately; stop-on-error limits do not undo earlier successes. Strict versions are the default; the separately approved client policy can allow missing versions on standalone User PATCH only. Direct User group membership is available through SCIM. Groups do not grant OWG permissions. User-name renames, nested groups, password changes, email-based login aliases and other complex attributes are not supported. Core names and contacts never change login identity, roster email, invitation routing or displayName. Supplied versions must always be current. Provider compatibility must be tested before rollout.');limits.className='muted';
  const form=el('div');form.className='row';
  const label=el('label','Provider label');label.htmlFor='scimLabel';
  const name=el('input');name.id='scimLabel';name.maxLength=120;name.style.maxWidth='100%';
  const create=el('button','Create disabled provider');create.id='scimCreate';create.disabled=!options.canWrite;
  const refresh=el('button','Refresh providers');refresh.id='scimRefresh';
  const inputGroup=el('div');inputGroup.append(label,name);form.append(inputGroup,create,refresh);
  const status=el('p');status.id='scimStatus';status.setAttribute('role','status');
  const secret=el('div');secret.id='scimCredential';secret.hidden=true;secret.style.overflowWrap='anywhere';
  const list=el('div');list.id='scimProviders';
  container.style.overflowWrap='anywhere';container.replaceChildren(title,help,limits,form,status,secret,list);
  const path='/v1/admin/scim-providers/'+encodeURIComponent(options.organization);
  function forget(){secret.replaceChildren();secret.hidden=true;}
  function say(value){if(valid())status.textContent=value;}
  function assignmentForm(row,item){
   const open=el('button','Assign existing employee');open.dataset.scimAssign=item.provider_id;open.disabled=!options.canWrite;
   const form=el('div');form.hidden=true;
   let preview=null,sequence=0;
   const prefix='scimAssign-'+item.provider_id;
   const people=el('input');people.type='email';people.maxLength=512;people.required=true;people.id=prefix+'-employee';people.style.maxWidth='100%';
   const label=el('label','Existing employee email');label.htmlFor=people.id;
   const external=el('input');external.id=prefix+'-external';external.maxLength=200;
   const externalLabel=el('label','Provider external ID (optional)');externalLabel.htmlFor=external.id;
   const review=el('button','Review assignment');review.disabled=true;
   const details=el('p');details.setAttribute('role','status');
   const confirm=el('input');confirm.type='checkbox';confirm.id=prefix+'-confirm';confirm.disabled=true;
   const confirmLabel=el('label','Allow this provider to deactivate and reactivate this employee');confirmLabel.htmlFor=confirm.id;
   const commit=el('button','Confirm assignment');commit.disabled=true;
   const cancel=el('button','Cancel assignment');
   const check=el('div');check.className='check';check.append(confirm,confirmLabel);
   form.append(label,people,externalLabel,external,review,details,check,commit,cancel);row.append(open,form);
   const active=()=>valid()&&row.isConnected&&!form.hidden;
   function reset(){sequence++;preview=null;confirm.checked=false;confirm.disabled=true;commit.disabled=true;review.disabled=!people.value;details.textContent='';}
   people.addEventListener('input',()=>{reset();review.disabled=!people.value;});
   external.addEventListener('input',reset);
   confirm.addEventListener('change',()=>{commit.disabled=!preview||!confirm.checked||!options.canWrite;});
   cancel.addEventListener('click',()=>{reset();form.hidden=true;open.disabled=!options.canWrite;});
   open.addEventListener('click',()=>{
    if(!valid()||!options.canWrite)return;
    reset();open.disabled=true;form.hidden=false;forget();people.focus();
   });
   review.addEventListener('click',async()=>{
    if(!active()||!people.value||!people.reportValidity()||!options.canWrite)return;
    reset();const own=sequence,employee=people.value;review.disabled=true;
    try{
     const value=await options.api('POST',path+'/'+encodeURIComponent(item.provider_id)+'/assignment-preview',{userName:employee});
     if(!active()||own!==sequence||employee!==people.value)return;
     preview=value;confirm.disabled=false;
     details.textContent='Assign '+value.employee.email+' ('+value.employee.status+') to '+value.provider_label+'. '+
      (value.provider_enabled?'The provider is enabled. ':'The provider is disabled; its lifecycle authority starts when enabled. ')+
      'The employee keeps their identity, SSO link, current status and manual teams. This permits future provider deactivation and rehire. It does not start recording or share evidence.';
    }catch(error){if(active()&&own===sequence)details.textContent=error.message;}
    finally{if(active()&&own===sequence)review.disabled=!people.value;}
   });
   commit.addEventListener('click',async()=>{
    if(!active()||!options.canWrite||!preview||!confirm.checked)return;
    const own=sequence,value=preview,employee=preview.employee.employee_id;commit.disabled=true;confirm.disabled=true;
    try{
     await options.api('POST',path+'/'+encodeURIComponent(item.provider_id)+'/assignments/'+encodeURIComponent(employee),
      {revision:value.provider_revision,employeeVersion:value.employee_version,externalId:external.value,confirmAssignment:true});
     if(!active()||own!==sequence)return;
     reset();form.hidden=true;open.disabled=!options.canWrite;say('Existing employee assigned. Identity and current status are unchanged; the selected provider now controls future provisioning lifecycle.');
    }catch(error){if(active()&&own===sequence){reset();details.textContent=error.message+' Review the current employee before retrying.';}}
   });
  }
  function clientPolicyForm(row,item){
   const open=el('button','Client update policy');open.dataset.scimPolicy=item.provider_id;open.disabled=!options.canWrite;
   const form=el('div');form.hidden=true;let state=null,sequence=0;
   const note=el('p','Optional compatibility applies only to standalone User PATCH without If-Match. It uses current stored state and has no client-baseline conflict protection: targeted fields can overwrite a concurrent human or provider change. Supplied versions, Bulk, Groups, PUT and DELETE stay strict. Test your IdP mappings before rollout.');
   const status=el('p');status.setAttribute('role','status');
   const consent=el('input');consent.type='checkbox';consent.id='scimPolicyConsent-'+item.provider_id;
   const label=el('label','Approve unversioned standalone User PATCH and its overwrite behavior');label.htmlFor=consent.id;
   const check=el('div');check.className='check';check.append(consent,label);
   const save=el('button','Enable compatibility');save.disabled=true;save.dataset.scimPolicySave=item.provider_id;
   const close=el('button','Close policy');form.append(note,status,check,save,close);row.append(open,form);
   const active=()=>valid()&&row.isConnected&&!form.hidden;
   consent.addEventListener('change',()=>{save.disabled=!options.canWrite||!state||(!state.unversioned_user_patch&&!consent.checked);});
   close.addEventListener('click',()=>{sequence++;state=null;consent.checked=false;form.hidden=true;open.disabled=!options.canWrite;});
   open.addEventListener('click',async()=>{
    if(!valid()||!options.canWrite)return;
    const own=++sequence;state=null;consent.checked=false;save.disabled=true;open.disabled=true;form.hidden=false;forget();
    status.textContent='Loading current client policy…';
    try{
     const value=await options.api('GET',path+'/'+encodeURIComponent(item.provider_id)+'/client-policy');
     if(!active()||own!==sequence)return;
     state=value;status.textContent=value.unversioned_user_patch?'Unversioned standalone User PATCH is enabled.':'Strict versions are required.';
     check.hidden=value.unversioned_user_patch;save.textContent=value.unversioned_user_patch?'Require strict versions':'Enable compatibility';save.disabled=!options.canWrite||!value.unversioned_user_patch;
    }catch(error){if(active()&&own===sequence)status.textContent=error.message;}
   });
   save.addEventListener('click',async()=>{
    if(!active()||!options.canWrite||!state||(!state.unversioned_user_patch&&!consent.checked))return;
    const own=sequence,value=state;save.disabled=true;
    try{
     await options.api('PATCH',path+'/'+encodeURIComponent(item.provider_id)+'/client-policy',
      {revision:value.revision,enabled:!value.unversioned_user_patch,confirmUnversioned:!value.unversioned_user_patch});
     if(!active()||own!==sequence)return;
     await load();say(value.unversioned_user_patch?'Strict client versions restored.':'Unversioned standalone User PATCH enabled for this provider. Other operations remain strict.');
    }catch(error){if(active()&&own===sequence){await load().catch(()=>{});say(error.message+' Review the current policy before retrying.');}}
   });
  }
  function groupNamePolicyForm(row,item){
   const open=el('button','Group naming policy');open.dataset.scimGroupNamePolicy=item.provider_id;open.disabled=!options.canWrite;
   const form=el('div');form.hidden=true;let state=null,sequence=0;
   const note=el('p','Optionally require unique active Group names within this provider for identity-provider matching. Stored whitespace normalization and case-insensitive comparison apply. Resolve existing collisions first. Groups do not grant OWG permissions. Upgrade all serving workers before enabling and test the actual IdP mappings.');
   const status=el('p');status.setAttribute('role','status');
   const check=el('input');check.type='checkbox';check.id='scimGroupNames-'+item.provider_id;
   const label=el('label','Require unique active Group names');label.htmlFor=check.id;
   const choice=el('div');choice.className='check';choice.append(check,label);
   const save=el('button','Save Group naming policy');save.dataset.scimGroupNameSave=item.provider_id;save.disabled=true;
   const close=el('button','Close Group naming policy');form.append(note,status,choice,save,close);row.append(open,form);
   const active=()=>valid()&&row.isConnected&&!form.hidden;
   check.addEventListener('change',()=>{save.disabled=!options.canWrite||!state||check.checked===state.enabled;});
   close.addEventListener('click',()=>{sequence++;state=null;form.hidden=true;open.disabled=!options.canWrite;});
   open.addEventListener('click',async()=>{
    if(!valid()||!options.canWrite)return;
    const own=++sequence;state=null;save.disabled=true;check.disabled=true;open.disabled=true;form.hidden=false;forget();
    status.textContent='Checking current Group names…';
    try{
     const value=await options.api('GET',path+'/'+encodeURIComponent(item.provider_id)+'/group-name-policy');
     if(!active()||own!==sequence)return;
     state=value;check.checked=value.enabled;check.disabled=false;
     status.textContent=(value.enabled?'Unique names required. ':'Unique-name constraint is off. ')+'Active Groups: '+Number(value.active_groups).toLocaleString()+'. Additional name collisions: '+Number(value.colliding_groups).toLocaleString()+'.';
    }catch(error){if(active()&&own===sequence)status.textContent=error.message;}
   });
   save.addEventListener('click',async()=>{
    if(!active()||!options.canWrite||!state||check.checked===state.enabled)return;
    const own=sequence,value=state,allow=check.checked;save.disabled=true;check.disabled=true;
    try{
     await options.api('PATCH',path+'/'+encodeURIComponent(item.provider_id)+'/group-name-policy',{revision:value.revision,enabled:allow});
     if(!active()||own!==sequence)return;
     await load();say(allow?'Unique active Group names are now required for this provider.':'Group naming constraint disabled for this provider.');
    }catch(error){if(active()&&own===sequence){await load().catch(()=>{});say(error.message+' Review the current Group naming policy before retrying.');}}
   });
  }
  async function load(){
   const response=await options.api('GET',path);if(!valid())return;
   list.replaceChildren();
   if(!response.items.length)list.append(el('p','No provisioning providers configured.'));
   for(const item of response.items){
    const row=el('article');row.className='out';row.dataset.provider=item.provider_id;
    row.append(el('h3',item.label),el('p','State: '+(item.revoked_at?'Permanently revoked':item.enabled?'Enabled':'Disabled')),
               el('p','Provider: '+item.provider_id),el('p','Base URL: '+item.base_url));
    const controls=el('div');controls.className='row';
    if(!item.revoked_at){
     const consent=el('input');consent.type='checkbox';consent.id='scimConsent-'+item.provider_id;
     const consentLabel=el('label','Allow this provider to create, deactivate and reactivate roster users');consentLabel.htmlFor=consent.id;
     const check=el('div');check.className='check';check.append(consent,consentLabel);
     const toggle=el('button',item.enabled?'Disable provider':'Enable provider');toggle.dataset.scimToggle=item.provider_id;
     toggle.disabled=!options.canWrite||!item.enabled;
     consent.addEventListener('change',()=>{toggle.disabled=!options.canWrite||(!item.enabled&&!consent.checked);});
     if(!item.enabled)row.append(check);
     toggle.addEventListener('click',async()=>{
      if(!valid()||!options.canWrite||(!item.enabled&&!consent.checked))return;
      toggle.disabled=true;forget();
      try{await options.api('PATCH',path+'/'+encodeURIComponent(item.provider_id),{revision:item.revision,enabled:!item.enabled});if(valid()){await load();say(item.enabled?'Provider disabled.':'Provider enabled. Test its supported mappings before rollout.');}}
      catch(error){say(error.message);if(valid())await load().catch(()=>{});}
     });
     const revoke=el('button','Revoke credential permanently');revoke.dataset.scimRevoke=item.provider_id;revoke.disabled=!options.canWrite;
     revoke.addEventListener('click',async()=>{
      if(!valid()||!options.canWrite||!root.confirm('Permanently revoke this provisioning credential? Existing users and evidence remain.'))return;
      revoke.disabled=true;forget();
      try{await options.api('POST',path+'/'+encodeURIComponent(item.provider_id)+'/revoke',{revision:item.revision});if(valid()){await load();say('Credential permanently revoked; existing users and evidence retained.');}}
      catch(error){say(error.message);if(valid())await load().catch(()=>{});}
     });controls.append(toggle,revoke);
    }
    row.append(controls);if(!item.revoked_at){assignmentForm(row,item);clientPolicyForm(row,item);groupNamePolicyForm(row,item);}list.append(row);
   }
  }
  create.addEventListener('click',async()=>{
   if(!valid()||!options.canWrite||!name.value.trim())return;
   create.disabled=true;forget();
   try{
    const value=await options.api('POST',path,{label:name.value.trim()});if(!valid())return;
    name.value='';secret.hidden=false;
    const heading=el('p','Copy this credential now. It is shown only in this view and is cleared when you navigate, refresh, change provider state or sign out.');
    const credential=el('code',value.token);credential.id='scimOneTimeToken';
    const erase=el('button','Clear credential');erase.id='scimForget';erase.addEventListener('click',forget);
    secret.replaceChildren(heading,credential,el('p','Base URL: '+value.provider.base_url),erase);
    await load();say('Disabled provider created. Store its credential securely, then explicitly enable provisioning.');
   }catch(error){say(error.message);}finally{if(valid())create.disabled=!options.canWrite;}
  });
  refresh.addEventListener('click',async()=>{if(valid()){forget();try{await load();say('Providers refreshed.');}catch(error){say(error.message);}}});
  await load();
 }
 root.OWGScimAdmin={mount,clear};
})(window);
