"""Owned source-rule workflow through production canonical/Stripe adapter code."""
from stripe_process_app import app,scratch,REF,REF_B
from shared.process_catalog import CatalogWrite
from server.workflow_evidence_routes import _catalog
from shared.lifespan import extend_lifespan


def seed_design():
    _catalog.save(CatalogWrite(workflow_id='source_rules_owned',title='Owned current-source rules',
        user_confirmed=True,process_model={'goal':'Check current invoice state','baseline_source':'user_declared',
            'baseline_path':['Review','Close'],'activities':[{'id':'review','label':'Review'},{'id':'close','label':'Close'}],
            'transitions':[{'source':'review','target':'close','rule_id':'source_state'}],
            'business_rules':[{'id':'source_state','text':'Invoice state compared','user_confirmed':True,
                'predicate':{'match':'all','conditions':[{'path':'/source/state','operator':'eq','expected':'paid'}]}}],
            'manual_review_conditions':[{'activity_id':'review','kind':'exception_condition','condition_label':'Evidence review','response':'pause_for_review'}],
            'entry_activity_id':'review','terminal_activity_ids':['close']}))

extend_lifespan(app,startup=seed_design)
