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
  const limits=el('p','Supported: work-email userName, display name, active state and provider external ID. Bulk supports up to 50 Users/Groups operations per request (64 KiB). Operations commit separately; stop-on-error limits do not undo earlier successes. Every update/delete needs its exact current resource version. Direct User group membership is available through SCIM. Groups do not grant OWG permissions. User-name renames, nested groups, password changes and complex attributes are not supported. An exact current If-Match version is required for changes. Provider compatibility must be tested before rollout.');limits.className='muted';
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
    row.append(controls);list.append(row);
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
