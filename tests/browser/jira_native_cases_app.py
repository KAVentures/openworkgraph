"""Owned native-case browser fixture with no observed resource resolution."""
import os
os.environ['OWG_JIRA_NATIVE_CASE_INGESTION_ENABLED'] = 'true'
from jira_retained_app import app, profiles
profiles.resolve_resource_reference = lambda _ref: None
