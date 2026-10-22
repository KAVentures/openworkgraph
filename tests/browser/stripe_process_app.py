"""Owned invoice reader and canonical proof through production Stripe code."""
from datetime import datetime,timezone
import json
from process_logs_app import app,scratch
import server.workflow_evidence_dashboard
from server import db,record_graph,stripe_source_evidence
from shared.lifespan import extend_lifespan

REF='owg:r:'+'a'*32
ID='in_1234567890ABCDEF';ACCOUNT='acct_1234567890ABCDEF'
REF_B='owg:r:'+'b'*32;ID_B='in_1234567890ABCDEG'
ENV={'OWG_STRIPE_SOURCE_READ_ENABLED':'true','OWG_STRIPE_ACCOUNT_ID':ACCOUNT,
     'OWG_STRIPE_RESTRICTED_READ_KEY':'rk_test_synthetic_sample_only'}
def resolve(ref):
    return {'provider':'stripe','resource_kind':'invoice','host':'dashboard.stripe.com',
            'resolver_locator':'invoice:'+(ID if ref==REF else ID_B)} if ref in {REF,REF_B} else None
record_graph.resolve_resource_reference=resolve
calls=[]
def reader(url,key):
    assert key==ENV['OWG_STRIPE_RESTRICTED_READ_KEY']
    assert url in {'https://api.stripe.com/v1/account','https://api.stripe.com/v1/invoices/'+ID,'https://api.stripe.com/v1/invoices/'+ID_B}
    calls.append('account' if url.endswith('/account') else 'invoice')
    (scratch/'source-calls.json').write_text(json.dumps(calls),encoding='utf-8')
    if url.endswith('/account'):return {'object':'account','id':ACCOUNT,'email':'PRIVATE_ACCOUNT_EMAIL'}
    second=url.endswith('/'+ID_B)
    return {'object':'invoice','id':ID_B if second else ID,'status':'void' if second else 'paid','paid':not second,'created':1792483200,
        'status_transitions':{'finalized_at':1792483260,('voided_at' if second else 'paid_at'):1792483500},
        'customer_email':'PRIVATE_CUSTOMER_EMAIL','total':99999999}
production=stripe_source_evidence.stripe_record_receipt
record_graph.stripe_record_receipt=lambda ref,include_lifecycle=False:production(ref,environment=ENV,
    resolver=resolve,reader=reader,include_lifecycle=include_lifecycle)

def seed():
    today=datetime.now(timezone.utc).date().isoformat()
    db.insert_events([{'event_id':'invoice-case-'+ref[-1]+str(i),'observed_at':today+f'T10:0{i}:00Z','device_id':'synthetic',
        'session_id':'owned-source-study','app':'Google Chrome','window_title':'Owned workflow','event_type':'browser_click',
        'source':'browser_extension','duration_seconds':1,'metadata':{'resource_reference':{
            'provider':'stripe','resource_kind':'invoice','host':'dashboard.stripe.com','resource_ref':ref}}} for ref in (REF,REF_B) for i in range(3)])
    with db.connect() as conn:
        (scratch/'original-events.json').write_text(json.dumps([list(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]),encoding='utf-8')
    (scratch/'source-calls.json').write_text('[]',encoding='utf-8')
extend_lifespan(app,startup=seed)
