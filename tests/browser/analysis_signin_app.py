"""Owned RSA/PKCE provider reused with separately enabled analysis sessions."""
import os
os.environ['OWG_GATEWAY_ANALYSIS_SSO_ENABLED']='true'
from process_signin_app import app, scratch, settings
from test_shared_analysis_publications import snapshot

settings.group_scope_map['writers'] |= {'org:analysis:read','org:analysis:write'}
settings.group_scope_map['readers'] |= {'org:analysis:read'}
(scratch/'snapshot.json').write_text(snapshot().model_dump_json(),encoding='utf-8')
