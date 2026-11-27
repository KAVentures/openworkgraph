"""Shared enterprise connector wire contract.

Keep protocol identities stable across source profiles, Gateway jobs, and
published manifests. Capability flags distinguish adapter coverage from
support for private native crosswalks, secret delivery and identity-verified
replacement. None of these declarations assert live provider acceptance.
"""
from typing import Literal, get_args

Provider = Literal["rest", "google", "sql", "slack", "salesforce",
                   "servicenow", "sap", "dataverse", "hubspot", "databricks", "snowflake", "netsuite", "workday"]
PROVIDERS = frozenset(get_args(Provider))
CROSSWALK_PROVIDERS = frozenset({"rest", "sql", "salesforce", "servicenow",
                                 "sap", "dataverse", "hubspot"})
ROTATION_PROVIDERS = frozenset({"dataverse", "hubspot"})
FILE_SECRET_PROVIDERS = frozenset({"rest", "sql", "slack", "sap", "dataverse", "hubspot", "databricks", "snowflake", "netsuite", "workday"})

# Fixed reviewed dispatch registry; never import a module name supplied by
# a stored profile or request. REST is the common runtime itself.
NATIVE_ADAPTERS = {
    "google": "google_workspace_audit",
    "sql": "enterprise_sql_feed",
    "slack": "slack_native_feed",
    "salesforce": "salesforce_history_feed",
    "servicenow": "servicenow_operations_feed",
    "sap": "sap_procurement_feed",
    "dataverse": "dataverse_audit_feed",
    "hubspot": "hubspot_history_feed",
    "databricks": "databricks_event_feed",
    "snowflake": "snowflake_event_feed",
    "netsuite": "netsuite_event_feed",
    "workday": "workday_raas_feed",
}

def capabilities(provider):
    if provider not in PROVIDERS:
        raise ValueError("unknown enterprise connector provider")
    return {
        "provider": provider,
        "retained_ingestion": True,
        "recurring_dispatch": True,
        "exact_snapshot_publication": True,
        "private_native_crosswalk": provider in CROSSWALK_PROVIDERS,
        "deployment_secret_file_delivery": provider in FILE_SECRET_PROVIDERS,
        "reviewed_in_place_credential_rotation": provider in ROTATION_PROVIDERS,
        "live_provider_acceptance_verified": False,
    }
