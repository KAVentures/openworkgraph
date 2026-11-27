"""Deployment catalog onboarding and drift review, without source reads.

Unlike per-worker preflight, checks approved sources before a profile exists.
Never connects to a source database, requests OAuth grants or probes endpoints.
Aliases and local profile IDs are local operator metadata; exported support
summaries omit even those identifiers. Never emit secrets or exception strings.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import os
import re

from . import enterprise_rest_feed as rest
from . import enterprise_connector_secrets as secrets
from . import event_log_workspace as ws
from shared.enterprise_connector_contract import PROVIDERS

FORMAT = "owg_enterprise_source_onboarding_v1"
# Every reader below is explicitly local. SQL uses its path/DSN validation,
# not the driver's connection constructor. Other methods only resolve and
# validate the deployment-owned credential.
CATALOGS = {
    "sql": ("OWG_SQL_SOURCE_CATALOG", "_catalog", None),
    "sap": ("OWG_SAP_P2P_CATALOG", "catalog", "token"),
    "dataverse": ("OWG_DATAVERSE_CATALOG", "catalog", "secret"),
    "hubspot": ("OWG_HUBSPOT_CATALOG", "catalog", "token"),
    "databricks": ("OWG_DATABRICKS_CATALOG", "catalog", "token"),
    "snowflake": ("OWG_SNOWFLAKE_CATALOG", "catalog", "token"),
    "netsuite": ("OWG_NETSUITE_CATALOG", "catalog", "key_material"),
    "workday": ("OWG_WORKDAY_CATALOG", "catalog", "password"),
    "oracle_fusion": ("OWG_ORACLE_FUSION_CATALOG", "catalog", "password"),
    "bigquery": ("OWG_BIGQUERY_CATALOG", "catalog", "key_material"),
}
OTHER = {"google": "stored_oauth_grant", "salesforce": "stored_oauth_grant",
         "servicenow": "stored_oauth_grant", "slack": "reviewed_token_profile"}
SAFE_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}\Z")
SAFE_ERROR = re.compile(r"[a-z][a-z0-9_]{0,100}\Z")
# Never return an arbitrary exception's message, even if it matches a regex.
# Known codes come from the deployment parsers and local credential contract.
KNOWN_ERRORS = frozenset({
    "invalid_connector_secret_catalog", "invalid_connector_secret_root",
    "unsafe_connector_secret_ancestor", "connector_secret_root_must_be_owner_only",
    "unsafe_connector_secret_file", "connector_secret_file_unavailable",
    "connector_secret_changed_during_read", "invalid_connector_secret_file_content",
    "connector_secret_files_require_posix", "invalid_connector_secret_reference",
    "invalid_rest_endpoint_credential_catalog", "rest_endpoint_credential_pair_not_approved",
    "rest_endpoint_credential_catalog_required", "endpoint_not_deployment_allowlisted",
    "invalid_fixed_https_endpoint", "invalid_deployment_endpoint_allowlist",
    "credential_unavailable", "credential_changed_reconfigure_required",
    "sql_postgresql_driver_unavailable", "sql_credential_unavailable",
    "invalid_sql_connection_definition", "sql_postgresql_requires_exact_tls_verified_read_role",
    "sql_readonly_file_unavailable", "sql_source_not_deployment_allowlisted",
    "sql_source_definition_changed", "invalid_sql_deployment_catalog", "sql_catalog_too_large",
    "sap_credential_unavailable", "sap_source_definition_changed",
    "dataverse_secret_unavailable", "dataverse_source_definition_changed",
    "hubspot_private_token_unavailable", "hubspot_source_definition_changed",
    "hubspot_required_scope_missing", "databricks_credential_unavailable",
    "databricks_source_definition_changed", "snowflake_credential_unavailable",
    "snowflake_source_definition_changed", "netsuite_credential_unavailable",
    "invalid_netsuite_rsa_private_key", "netsuite_source_definition_changed",
    "workday_credential_unavailable", "workday_source_definition_changed",
    "oracle_fusion_credential_unavailable", "oracle_fusion_source_definition_changed",
    "oracle_fusion_business_unit_not_approved", "bigquery_credential_unavailable",
    "invalid_bigquery_rsa_private_key", "bigquery_source_definition_changed",
    "google_grant_unavailable", "google_grant_scope_mismatch", "google_reconnect_required",
    "salesforce_grant_unavailable", "salesforce_grant_scope_mismatch", "salesforce_reconnect_required",
    "servicenow_grant_unavailable", "servicenow_grant_scope_mismatch", "servicenow_reconnect_required",
    "slack_credential_unavailable", "slack_principal_changed_during_read",
    "invalid_sap_deployment_catalog", "invalid_dataverse_deployment_catalog",
    "invalid_hubspot_deployment_catalog", "invalid_databricks_deployment_catalog",
    "invalid_snowflake_deployment_catalog", "invalid_netsuite_deployment_catalog",
    "invalid_workday_deployment_catalog", "invalid_oracle_fusion_catalog",
    "invalid_bigquery_deployment_catalog",
})


def error_code(exc, fallback):
    code = exc.code if isinstance(exc, rest.FeedError) else None
    return code if code in KNOWN_ERRORS else fallback


def catalog_status(provider, configured, state, *, sources=0, error=None):
    return {"provider": provider, "deployment_setting_present": configured,
            "state": state, "source_count": sources, "error_code": error}


def credential_probe(adapter, src, method):
    """Return no credential value, fingerprint, path, host or identity."""
    try:
        if method is None:
            adapter._target(src)
        else:
            getattr(adapter, method)(src)
        return "locally_available", None
    except Exception as exc:
        return "blocked", error_code(exc, "local_credential_validation_failed")


def delivery(reference):
    if reference is None:
        return "local_file_source"
    try:
        return secrets.delivery(reference)
    except secrets.SecretError:
        return "unknown"


def catalog_entries():
    statuses, entries, refs = [], [], defaultdict(list)
    for provider, (env, method, probe) in CATALOGS.items():
        configured = env in os.environ
        try:
            adapter = rest._adapter(provider)
            sources = getattr(adapter, method)()
            if not isinstance(sources, dict):
                raise ValueError()
        except Exception as exc:
            statuses.append(catalog_status(provider, configured, "invalid",
                                           error=error_code(exc, "catalog_validation_failed")))
            continue
        statuses.append(catalog_status(provider, configured, "valid" if sources else "empty",
                                       sources=len(sources)))
        for alias, src in sorted(sources.items()):
            # SQL has a typed Source model and local SQLite path instead of
            # an external credential. The digest uses the canonical parser.
            if provider == "sql":
                definition = src.model_dump()
                reference = src.dsn_env
            else:
                definition = src
                reference = src["credential_env"]
            state, error = credential_probe(adapter, src, probe)
            item = {"provider": provider, "source_alias": alias,
                    "definition_sha256": rest._digest(definition),
                    "credential_delivery": delivery(reference),
                    "local_credential_state": state, "error_code": error,
                    "profile_ids": [], "definition_drift_profiles": [],
                    "shared_credential_reference": False,
                    "source_schema_verified": False, "provider_identity_verified": False}
            entries.append(item)
            if reference is not None:
                refs[reference].append(item)
    return statuses, entries, refs


def rest_pairs():
    """Inspect explicit REST endpoint/reference approval without emitting URLs."""
    setting = os.environ.get("OWG_REST_ALLOWED_ENDPOINTS")
    pairs_raw = os.environ.get("OWG_REST_ENDPOINT_CREDENTIALS")
    try:
        if setting is not None and len(setting) > 65536:
            raise ValueError()
        allowed = rest._strict_json((setting or "[]").encode())
        if not isinstance(allowed, list) or len(allowed) > 100 or any(type(x) is not str for x in allowed):
            raise ValueError()
        if len(set(allowed)) != len(allowed):
            raise ValueError()
        for endpoint in allowed:
            rest._endpoint(endpoint)
        if pairs_raw is None:
            return {"state": "no_explicit_pair_policy" if allowed else "empty",
                    "allowlisted_endpoint_count": len(allowed), "approved_pair_count": 0,
                    "locally_available_pairs": 0, "blocked_pairs": 0,
                    "unpaired_endpoint_count": len(allowed)}, []
        if len(pairs_raw) > 65536:
            raise ValueError()
        pairs = rest._strict_json(pairs_raw.encode())
        if (not isinstance(pairs, dict) or not 1 <= len(pairs) <= 100
                or any(k not in allowed or not isinstance(v, str) or not rest.ENV.fullmatch(v)
                       for k, v in pairs.items())):
            raise ValueError()
    except Exception:
        return {"state": "invalid", "allowlisted_endpoint_count": None,
                "approved_pair_count": None, "locally_available_pairs": None,
                "blocked_pairs": None, "unpaired_endpoint_count": None}, []
    ready = 0
    references = []
    for endpoint, reference in pairs.items():
        references.append(reference)
        try:
            rest._endpoint_credential(endpoint, reference, required=True)
            ready += 1
        except rest.FeedError:
            pass
    return {"state": "valid", "allowlisted_endpoint_count": len(allowed),
            "approved_pair_count": len(pairs), "locally_available_pairs": ready,
            "blocked_pairs": len(pairs)-ready, "unpaired_endpoint_count": len(allowed)-len(pairs)}, references


def load_profiles():
    with ws._LOCK:
        c = rest._connect()
        try:
            return [rest._get(c, row[0]) for row in c.execute(
                "SELECT profile_id FROM enterprise_rest_profiles ORDER BY profile_id").fetchall()]
        finally:
            c.close()


def profile_state(p, by_source, catalog_states):
    provider = p.get("provider", "rest")
    cfg = p["config"]
    alias = cfg.get("source_alias") if provider in CATALOGS else None
    item = by_source.get((provider, alias))
    pin_state = "not_catalog_based"
    if provider in CATALOGS:
        if catalog_states[provider]["state"] == "invalid":
            pin_state = "catalog_invalid"
        elif item is None:
            pin_state = "source_missing"
        elif item["definition_sha256"] != cfg.get("source_sha256"):
            pin_state = "definition_changed"
            item["definition_drift_profiles"].append(p["profile_id"])
        else:
            pin_state = "matches_reviewed_definition"
            item["profile_ids"].append(p["profile_id"])
    if p["status"] == "dataset_deleted":
        binding, error = "not_checked_dataset_deleted", None
    else:
        try:
            rest._check_binding(p)
            binding, error = "locally_available", None
        except Exception as exc:
            binding, error = "blocked", error_code(exc, "local_profile_binding_failed")
    return {"profile_id": p["profile_id"], "provider": provider, "revision": p["revision"],
            "source_alias": alias if isinstance(alias, str) and SAFE_ALIAS.fullmatch(alias) else None,
            "definition_state": pin_state, "binding_state": binding, "error_code": error,
            "enabled": p["enabled"], "retained_dataset_present": p["log_id"] is not None,
            "requires_operator_review": binding == "blocked" or pin_state in
                ("catalog_invalid", "source_missing", "definition_changed"),
            "provider_identity_verified": False, "execution_authorized": False}


def summarize(statuses, entries, profiles, rest_report, secret_report):
    providers = []
    for provider in sorted(PROVIDERS):
        catalog = next((s for s in statuses if s["provider"] == provider), None)
        sources = [e for e in entries if e["provider"] == provider]
        selected = [p for p in profiles if p["provider"] == provider]
        providers.append({"provider": provider, "catalog_state": catalog["state"] if catalog else "not_applicable",
                          "configured_sources": len(sources) if catalog and catalog["state"] != "invalid" else None,
                          "ready_local_sources": sum(e["local_credential_state"] == "locally_available" for e in sources),
                          "blocked_local_sources": sum(e["local_credential_state"] == "blocked" for e in sources),
                          "unconfigured_ready_sources": sum(e["local_credential_state"] == "locally_available"
                              and not e["profile_ids"] and not e["definition_drift_profiles"] for e in sources),
                          "retained_profiles": len(selected),
                          "profiles_requiring_review": sum(p["requires_operator_review"] for p in selected),
                          "onboarding_mode": "deployment_catalog" if provider in CATALOGS else
                              "fixed_endpoint_policy" if provider == "rest" else OTHER.get(provider, "unknown")})
    return {"providers": providers, "catalog_states": dict(Counter(s["state"] for s in statuses)),
            "source_credential_states": dict(Counter(e["local_credential_state"] for e in entries)),
            "profile_definition_states": dict(Counter(p["definition_state"] for p in profiles)),
            "profile_binding_states": dict(Counter(p["binding_state"] for p in profiles)),
            "error_codes": dict(sorted(Counter(x["error_code"] for x in [*statuses,*entries,*profiles]
                                               if x["error_code"]).items())),
            "rest_endpoint_policy": rest_report, "secret_file_inventory": secret_report}


def inspect(*, support_only=False):
    """Passive, local pre-onboarding and drift report. No external I/O."""
    statuses, entries, refs = catalog_entries()
    rest_report, rest_refs = rest_pairs()
    profiles_raw = load_profiles()
    by_source = {(e["provider"], e["source_alias"]): e for e in entries}
    catalog_states = {s["provider"]: s for s in statuses}
    profiles = [profile_state(p, by_source, catalog_states) for p in profiles_raw]
    used_references = set(refs) | set(rest_refs)
    for p in profiles_raw:
        reference = p["config"].get("credential_env")
        if isinstance(reference, str) and secrets.REF.fullmatch(reference):
            used_references.add(reference)
    for reference, items in refs.items():
        shared = len(items) + rest_refs.count(reference) > 1
        for item in items:
            item["shared_credential_reference"] = shared
    try:
        mappings = secrets.catalog()
        # Do not open orphaned files or disclose file names/reference names.
        secret_report = {"state": "valid", "configured_mappings": len(mappings),
                         "referenced_mappings": len(set(mappings) & used_references),
                         "not_referenced_by_inspected_sources_or_profiles": len(set(mappings)-used_references),
                         "unreferenced_mappings_are_not_proven_unused": True}
    except secrets.SecretError:
        secret_report = {"state": "invalid", "configured_mappings": None,
                         "referenced_mappings": None,
                         "not_referenced_by_inspected_sources_or_profiles": None,
                         "unreferenced_mappings_are_not_proven_unused": True}
    summary = summarize(statuses, entries, profiles, rest_report, secret_report)
    result = {"format": FORMAT, "observed_at": datetime.now(timezone.utc).isoformat(),
              "scope": "counts_only_support" if support_only else "local_operator",
              "summary": summary, "provider_reads_performed": False,
              "credential_refresh_performed": False, "provider_identity_verified": False,
              "provider_live_health_verified": False, "execution_authorized": False,
              "configuration_modified": False,
              "interpretation": "Local catalog and binding checks only; readiness does not establish access, source schema, completeness or live health."}
    if not support_only:
        result.update(catalogs=statuses, sources=entries, profiles=profiles)
    return result
