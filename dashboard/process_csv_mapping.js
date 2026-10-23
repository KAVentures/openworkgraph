/* Explicit browser-only CSV minimization. No network, storage or inference. */
(() => {
  'use strict';
  const MAX_BYTES=1024*1024,MAX_ROWS=5000,MAX_COLUMNS=100;
  const fail=message=>{throw new Error(message);};
  function inspectText(text,delimiter=','){
    if(![',',';','\t'].includes(delimiter))fail('Choose comma, semicolon or tab as the delimiter.');
    if(typeof text!=='string'||!text||new TextEncoder().encode(text).byteLength>MAX_BYTES)
      fail('Choose a nonempty UTF-8 file of at most 1 MiB.');
    if(text.includes('\0'))fail('The file contains an invalid control byte.');
    if(text.charCodeAt(0)===0xfeff)text=text.slice(1);
    const records=[];
    let row=[],field='',quoted=false,closed=false,started=false;
    function cell(){
      row.push(field);field='';closed=false;
      if(row.length>MAX_COLUMNS)fail('The file exceeds the 100-column limit.');
    }
    function record(){
      if(started||row.length||field.length){cell();records.push(row);}
      row=[];field='';closed=false;started=false;
      if(records.length>MAX_ROWS+1)fail('The file exceeds the 5,000-event limit.');
    }
    for(let i=0;i<text.length;i++){
      const char=text[i];
      if(quoted){
        if(char==='"'){
          if(text[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}
        }else field+=char;
        continue;
      }
      if(char===delimiter){started=true;cell();continue;}
      if(char==='\r'||char==='\n'){
        record();if(char==='\r'&&text[i+1]==='\n')i++;continue;
      }
      if(closed)fail('The file has characters after a closing CSV quote.');
      if(char==='"'){
        if(field.length)fail('CSV quotes must start at the beginning of a field.');
        quoted=true;started=true;
      }else{field+=char;started=true;}
    }
    if(quoted)fail('The file has an unclosed CSV quote.');
    record();
    if(records.length<2)fail('The file needs a header and at least one event row.');
    const headers=records[0];
    if(!headers.length||headers.length>MAX_COLUMNS)fail('The file needs 1 to 100 columns.');
    if(headers.some(header=>header.length>120))fail('Column headers must be at most 120 characters.');
    if(records.slice(1).some(record=>record.length!==headers.length))fail('All event rows must have the same number of columns as the header.');
    const quote=value=>'"'+value.replaceAll('"','""')+'"';
    return Object.freeze({headers:Object.freeze([...headers]),row_count:records.length-1,
      createMappedFile(mapping,participantLabelsAuthorized=false){
        const required=['case_id','activity','timestamp'],optional=['participant','team'];
        if(!mapping||required.some(key=>!Object.hasOwn(mapping,key))||Object.keys(mapping).some(key=>![...required,...optional].includes(key)))
          fail('Select the case, activity and timestamp columns and only supported optional columns.');
        const dimensions=optional.filter(key=>Object.hasOwn(mapping,key));
        if(dimensions.length&&participantLabelsAuthorized!==true)fail('Explicitly authorize the selected participant/team columns.');
        const keys=[...required,...dimensions],indices=keys.map(key=>mapping[key]);
        if(new Set(indices).size!==keys.length||indices.some(index=>!Number.isInteger(index)||index<0||index>=headers.length))
          fail('Selected columns must use different existing columns.');
        const labels=Object.fromEntries(dimensions.map(key=>[key,new Map()]));
        const body=keys.join(',')+'\n'+records.slice(1).map(row=>keys.map((key,i)=>{
          let value=row[indices[i]].trim();
          if(dimensions.includes(key)&&value){
            if(value.length>120)fail('Participant/team identifiers must be at most 120 characters.');
            const aliases=labels[key];if(!aliases.has(value)){
              if(aliases.size>=1000)fail('Use at most 1,000 distinct participant/team labels per column.');
              aliases.set(value,key+'_'+(aliases.size+1));
            }value=aliases.get(value);
          }return quote(value);
        }).join(',')).join('\n')+'\n';
        const bytes=new TextEncoder().encode(body);
        if(bytes.byteLength>MAX_BYTES)fail('The mapped event log exceeds the 1 MiB limit.');
        const file=new Blob([bytes],{type:'text/csv'});
        if(dimensions.length)Object.defineProperty(file,'localParticipantLookup',{value:Object.freeze(
          Object.fromEntries(dimensions.map(key=>[key,Object.freeze([...labels[key]].map(([source,label])=>Object.freeze({source,label})))])))});
        return file;
      }});
  }
  async function inspectFile(file,delimiter=','){
    if(!file||!Number.isSafeInteger(file.size)||file.size<1||file.size>MAX_BYTES)
      fail('Choose a nonempty UTF-8 file of at most 1 MiB.');
    let text;
    try{
      const buffer=await file.arrayBuffer();
      if(buffer.byteLength>MAX_BYTES)fail('File exceeds the 1 MiB limit.');
      text=new TextDecoder('utf-8',{fatal:true}).decode(buffer);
    }catch{fail('The selected file must be readable UTF-8 of at most 1 MiB.');}
    return inspectText(text,delimiter);
  }
  window.owgProcessCSVMapping=Object.freeze({inspectText,inspectFile});
})();
