"""Explicit partial lifecycle projection from existing exact-invoice receipts."""
from __future__ import annotations

import csv
from io import StringIO
import re

from .process_mining_csv import mine_csv_events

LABELS={'invoice_created':'Invoice created','invoice_finalized':'Invoice finalized',
        'invoice_marked_uncollectible':'Marked uncollectible','invoice_paid':'Invoice paid','invoice_voided':'Invoice voided'}


def stripe_selected_invoices_process(receipts):
    if not isinstance(receipts,list) or not 1<=len(receipts)<=8:raise ValueError('Select 1 to 8 exact observed invoices')
    stream=StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(('case_id','activity','timestamp'))
    available=invalid=conflicts=0
    selections=[];case_order=[]
    for number,receipt in enumerate(receipts,1):
        selection={'selection_number':number,'case_label':None,'projected_event_count':0}
        selections.append(selection)
        if (not isinstance(receipt,dict) or receipt.get('provider')!='stripe' or receipt.get('object_type')!='invoice'
                or receipt.get('availability')!='source_reported' or not isinstance(receipt.get('process_audit_events'),list)):
            continue
        quality=receipt.get('lifecycle_projection_diagnostics')
        quality=quality if isinstance(quality,dict) else {}
        count=quality.get('invalid_timestamp_field_count',0)
        invalid+=count if type(count) is int and 0<=count<=5 else 0
        conflicts+=int(quality.get('chronology_conflict') is True)
        seen=set();rows=[]
        for event in receipt['process_audit_events'][:5]:
            if not isinstance(event,dict):continue
            action,stamp,ref=event.get('action'),event.get('observed_at'),event.get('source_event_ref')
            if (not isinstance(action,str) or action not in LABELS or not isinstance(stamp,str)
                    or not isinstance(ref,str) or not re.fullmatch(r'source_event:[0-9a-f]{32}',ref) or ref in seen):continue
            seen.add(ref);rows.append((LABELS[action],stamp))
        if rows:
            available+=1
            selection['projected_event_count']=len(rows)
            case_order.append((min(stamp for _,stamp in rows),f'Selection {number:02d}',number))
            for label,stamp in rows:writer.writerow((f'Selection {number:02d}',label,stamp))
    for case,(_,_,number) in enumerate(sorted(case_order),1):selections[number-1]['case_label']='Case '+str(case)
    process=mine_csv_events(stream.getvalue().encode()) if available else None
    if process:process.update(source='explicit_selected_stripe_invoice_lifecycle_projection',source_preview=True,
        source_event_window_may_be_partial=True,complete_business_case_history=False,
        source_case_ids_included=False,source_user_ids_included=False,cross_case_links_inferred=False,
        business_outcomes_verified=False,source_case_identity_basis='existing_account_and_exact_invoice_identity_checks')
    return {'format':'openworkgraph.stripe-selected-invoices-process.v1',
        'availability':'source_lifecycle' if process else 'no_eligible_source_lifecycle',
        'requested_record_count':len(receipts),'projected_record_count':available,
        'selections':selections,
        'invalid_timestamp_field_count':invalid,'chronology_conflict_record_count':conflicts,
        'process':process,'source_case_ids_included':False,'source_event_refs_included':False,
        'source_audit_history_complete':False,'business_outcomes_verified':False,
        'agent_causality_verified':False,'customer_bank_settlement_verified':False,
        'tenant_inventory_scanned':False,'result_persisted':False,'execution_permissions_granted':False}
