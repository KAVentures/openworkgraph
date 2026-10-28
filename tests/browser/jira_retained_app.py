"""Owned fake Jira provider attached only to this browser fixture."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from process_logs_app import app
from test_jira_retained_ingestion import Provider, ENV, REFS, HOST
import os
os.environ.update(ENV)
from server import jira_retained_profiles as profiles
from server.jira_retained_source import Extractor
provider = Provider()
provider.histories['OWG-1'][0]['items'][0]['toString'] = '<img src=x onerror=alert(1)>'
profiles.resolve_resource_reference = lambda ref: {
    'provider': 'jira', 'resource_kind': 'issue', 'host': HOST,
    'resolver_locator': 'OWG-' + str(REFS.index(ref) + 1)}
class OwnedExtractor(Extractor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, client_factory=provider.client, **kwargs)
profiles.Extractor = OwnedExtractor
