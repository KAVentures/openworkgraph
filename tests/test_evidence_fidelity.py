from gateway.plugin_redaction import project


def test_reference_arrays_and_clipboard_linkage_survive_both_hosted_modes():
    identity='c2183919-6004-4123-9852-202610101832'
    raw={'event_ids':[identity],'rows':[{'event_id':identity,'observed_at':'2026-10-11T12:00:00Z',
        'event_type':'browser_paste','metadata':{'clipboard_transfer_id':'transfer-123456789012',
        'linked_copy_event_id':identity,'clipboard_source_tab_context_id':'tab-123456789012',
        'clipboard_source_observed':True,'clipboard_contents_captured':False,
        'clipboard_link_age_seconds':4,'clipboard_text':'DO_NOT_LEAK'}}]}
    for detail in ('compact','rich'):
        result=project(raw,secret='isolated',principal='alice',detail=detail)
        assert result['event_ids']==[identity]
        row=result['rows'][0]
        assert row['linked_copy_event_id']==identity
        assert row['clipboard_transfer_id']=='transfer-123456789012'
        assert row['clipboard_contents_captured'] is False
        assert 'DO_NOT_LEAK' not in str(result)


def test_visible_match_scan_can_page_after_a_bounded_false_positive_tail(tmp_path):
    from gateway.db import GatewayDB
    from gateway.auth import Principal
    from gateway.query import workflow_trace
    from datetime import datetime,timedelta,timezone
    db=GatewayDB('sqlite:///'+str(tmp_path/'scan.db'));db.init()
    p=Principal('device','device','org','actor','device',frozenset())
    now=datetime.now(timezone.utc)-timedelta(days=1)
    db.insert_events(p,[{'event_id':str(i),'observed_at':(now+timedelta(seconds=i)).isoformat(),
        'event_type':'screen_click','app':'Chrome','window_title':'PR 183' if i==2001 else 'PR 180',
        'metadata':{'internal':'183'}} for i in range(2002)])
    matcher=lambda row:'183' in row['window_title']
    page=workflow_trace(db,organization_id='org',actor_id='actor',query='183',limit=20,row_matcher=matcher)
    assert page['rows']==[] and page['has_more'] and page['next_cursor']
    assert page['search_coverage']['scan_limit_reached']
    following=workflow_trace(db,organization_id='org',actor_id='actor',cursor=page['next_cursor'],limit=20,row_matcher=matcher)
    assert [row['event_id'] for row in following['rows']]==['2001']
    assert not following['has_more']
