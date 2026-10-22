"""Minimized status-history projection from exact selected Zendesk audits."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from io import StringIO

from .process_mining_csv import mine_csv_events

STATUSES={'new','open','pending','hold','solved','closed'}


def _stamp(value):
    if not isinstance(value,str) or not 1<=len(value)<=64:return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError,OverflowError):return None


def minimize_ticket_audits(rows,ticket_id):
    """Drop provider content before accumulating pages in memory."""
    minimal=[]
    for audit in rows:
        if (not isinstance(audit,dict) or type(audit.get('ticket_id')) is not int
                or audit['ticket_id']!=ticket_id):return None
        events=audit.get('events');selected=[]
        if isinstance(events,list) and len(events)<=2000:
            for event in events:
                if not isinstance(event,dict):continue
                kind=event.get('type')
                if not isinstance(kind,str) or kind not in {'Create','Change'} or event.get('field_name')!='status':continue
                value=event.get('value')
                selected.append({'type':kind,'field_name':'status',
                                 'value':value if isinstance(value,str) and value in STATUSES else None})
        else:selected=None
        identifier=audit.get('id')
        minimal.append({'id':identifier if type(identifier) is int and 1<=identifier<10**18 else None,
            'ticket_id':ticket_id,'created_at':_stamp(audit.get('created_at')),'events':selected})
    return minimal


def project_ticket_audits(audits, ticket_id):
    """Every audit must name the selected ticket; no timestamp-based joining.

    Native IDs stay inside this function. Only a single unambiguous standard
    status Create/Change event per immutable audit is projected. All comment,
    author, IP, custom-field and other content is ignored.
    """
    if not isinstance(audits,list) or len(audits)>300:
        return {'availability':'invalid_audit_response','events':[]}
    variants={};conflicts=set();invalid=ambiguous=duplicates=0
    for audit in audits:
        if (not isinstance(audit,dict) or type(audit.get('ticket_id')) is not int
                or audit['ticket_id']!=ticket_id):
            return {'availability':'ticket_identity_mismatch','events':[]}
        identifier=audit.get('id');stamp=_stamp(audit.get('created_at'));entries=audit.get('events')
        if type(identifier) is not int or not 1<=identifier<10**18 or stamp is None or not isinstance(entries,list) or len(entries)>2000:
            invalid+=1;continue
        statuses=[]
        for event in entries:
            if not isinstance(event,dict):continue
            kind=event.get('type')
            if not isinstance(kind,str) or kind not in {'Create','Change'} or event.get('field_name')!='status':continue
            value=event.get('value')
            if isinstance(value,str) and value in STATUSES:statuses.append((event['type'],value))
            else:statuses.append(('invalid','invalid'))
        signature=(stamp,tuple(statuses))
        if identifier in variants:
            duplicates+=1
            if variants[identifier]!=signature:conflicts.add(identifier)
        else:variants[identifier]=signature
    events=[]
    for identifier,(stamp,statuses) in variants.items():
        if identifier in conflicts:continue
        if len(statuses)>1:ambiguous+=1;continue
        if not statuses:continue
        kind,value=statuses[0]
        if kind=='invalid':invalid+=1;continue
        label=('Created ' if kind=='Create' else 'Status ')+value.capitalize()
        events.append({'activity':label,'observed_at':stamp})
    return {'availability':'source_audit','events':events,
        'invalid_audit_count':invalid,'ambiguous_status_audit_count':ambiguous,
        'duplicate_audit_count':duplicates,'conflicting_audit_count':len(conflicts)}


def zendesk_selected_cases_process(receipts):
    if not isinstance(receipts,list) or not 1<=len(receipts)<=8:
        raise ValueError('Select 1 to 8 exact Zendesk tickets')
    stream=StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(('case_id','activity','timestamp'))
    selections=[];case_order=[];diagnostics=Counter();available=0
    for ordinal,receipt in enumerate(receipts,1):
        projected=receipt.get('projection') or {}
        events=projected.get('events') or []
        summary={'selection_number':ordinal,'availability':receipt['availability'],
                 'projected_event_count':len(events),
                 'provider_pagination_complete':receipt.get('provider_pagination_complete') is True,
                 'case_label':None}
        selections.append(summary)
        for key in ('invalid_audit_count','ambiguous_status_audit_count','duplicate_audit_count','conflicting_audit_count'):
            diagnostics[key]+=projected.get(key,0)
        if events:
            available+=1
            case_key=f'Selection {ordinal:02d}'
            for event in events:writer.writerow((case_key,event['activity'],event['observed_at']))
            case_order.append((min(event['observed_at'] for event in events),case_key,ordinal))
    for number,(_,_,selection) in enumerate(sorted(case_order),1):selections[selection-1]['case_label']='Case '+str(number)
    process=mine_csv_events(stream.getvalue().encode()) if available else None
    if process:
        process.update(source='explicit_selected_zendesk_ticket_status_audits',source_preview=True,
            source_event_window_may_be_partial=True,complete_business_case_history=False,
            source_case_identity_basis='selected_ticket_id_verified_on_every_audit',
            source_case_ids_included=False,source_user_ids_included=False,
            cross_case_links_inferred=False,business_outcomes_verified=False)
    return {'format':'openworkgraph.zendesk-selected-cases-process.v1',
        'availability':'source_audit' if process else 'no_projectable_audits',
        'requested_ticket_count':len(receipts),'projected_ticket_count':available,
        'selections':selections,'projection_diagnostics':dict(diagnostics),'process':process,
        'source_case_ids_included':False,'source_audit_event_ids_included':False,'provider_actor_ids_included':False,
        'status_history_is_complete_business_workflow':False,'business_outcomes_verified':False,
        'tenant_inventory_scanned':False,'cross_case_identity_inferred':False,
        'business_writes_performed':False,'result_persisted':False,'execution_permissions_granted':False}
