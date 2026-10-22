"""Ephemeral process mining over separately authorized selected GitHub receipts."""
from __future__ import annotations

import csv
import re
from io import StringIO
from typing import Any

from .process_mining_csv import mine_csv_events

_LABELS={'review_approved':'Review approved','review_changes_requested':'Changes requested',
    'review_commented':'Review commented','pull_request_merged':'Pull request merged',
    'pull_request_closed_unmerged':'Closed without merge'}


def github_selected_cases_process(receipts: list[Any]) -> dict[str, Any]:
    if not isinstance(receipts,list) or not 1<=len(receipts)<=8:
        raise ValueError('Select 1 to 8 exact observed GitHub pull requests')
    stream=StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(('case_id','activity','timestamp'))
    available=event_count=0
    for ordinal,receipt in enumerate(receipts,1):
        if (not isinstance(receipt,dict) or receipt.get('provider')!='github'
                or receipt.get('availability')!='source_reported' or receipt.get('object_type')!='pull_request'
                or not isinstance(receipt.get('process_audit_events'),list)):
            continue
        seen=set();rows=[]
        for row in receipt['process_audit_events'][:41]:
            if not isinstance(row,dict):continue
            action,stamp,ref=row.get('action'),row.get('observed_at'),row.get('source_event_ref')
            if (not isinstance(action,str) or action not in _LABELS or not isinstance(stamp,str)
                    or not isinstance(ref,str) or not re.fullmatch(r'source_event:[0-9a-f]{32}',ref) or ref in seen):continue
            seen.add(ref);rows.append((_LABELS[action],stamp))
        if rows:
            available+=1;event_count+=len(rows)
            for label,stamp in rows:writer.writerow((f'Case {ordinal}',label,stamp))
    process=mine_csv_events(stream.getvalue().encode()) if event_count else None
    if process:
        process.update(source='explicit_selected_github_review_and_lifecycle_projection',
            source_event_window_may_be_partial=True,complete_business_case_history=False,
            source_case_ids_included=False,source_user_ids_included=False,
            source_identity_comparability_verified=False,
            cross_case_links_inferred=False,business_outcomes_verified=False)
    return {'format':'openworkgraph.github-selected-cases-process.v1',
        'availability':'source_audit' if process else 'no_eligible_source_audits',
        'requested_record_count':len(receipts),'audited_record_count':available,
        'unavailable_or_empty_record_count':len(receipts)-available,'partial_audit_record_count':available,
        'all_selected_source_audits_complete':False,'process':process,
        'source_case_ids_included':False,'source_audit_event_ids_included':False,'provider_actor_ids_included':False,
        'business_outcomes_verified':False,'tenant_inventory_scanned':False,'cross_case_identity_inferred':False,
        'source_reads_performed_by_projector':False,'result_persisted':False}
