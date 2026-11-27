"""Reviewed credential replacement for providers with native identity readback.

No credential input in the API. Read the deployment's new secret and prove
the configured identity before changing the binding. Preserve source keys,
the retained log and checkpoint. The profile remains disabled.
"""
import hmac
import time

import httpx
from pydantic import Field,StrictBool,StrictInt,ValidationError

from . import enterprise_rest_feed as rest
from . import event_log_workspace as ws

from shared.enterprise_connector_contract import ROTATION_PROVIDERS as SUPPORTED


class Rotate(rest.Strict):
    revision:StrictInt=Field(ge=1)
    confirm_rotation:StrictBool
    confirm_identity_probe:StrictBool
    confirm_workers_stopped:StrictBool


def _candidate(p):
    if p["provider"]=="dataverse":
        from . import dataverse_audit_feed as dv
        return dv.secret(dv.source(p["config"])),dv.Reader
    if p["provider"]=="hubspot":
        from . import hubspot_history_feed as hs
        return hs.token(hs.source(p["config"])),hs.Reader
    raise rest.FeedError("provider_rotation_requires_new_profile",422)


def _disabled(p,revision):
    if p["revision"]!=revision:
        raise rest.FeedError("profile_changed")
    if p["provider"] not in SUPPORTED:
        raise rest.FeedError("provider_rotation_requires_new_profile",422)
    if p["enabled"] or p["run_id"] or p["status"]=="dataset_deleted":
        raise rest.FeedError("disable_and_reconcile_profile_before_rotation")
    if p["retry_not_before"]>time.time():
        raise rest.FeedError("provider_retry_cooldown")


def _same_live(c,p,candidate):
    live=rest._get(c,p["profile_id"])
    _disabled(live,p["revision"])
    if live!=p:
        raise rest.FeedError("profile_changed_during_rotation")
    if live["log_id"]:
        ws._check_log(c,live["log_id"])
    current,_=_candidate(live)
    if not hmac.compare_digest(current,candidate):
        raise rest.FeedError("credential_changed_during_rotation")
    return live


def rotate(profile_id,value,*,client_factory=httpx.Client):
    try:
        spec=Rotate.model_validate(value)
    except ValidationError:
        raise rest.FeedError("invalid_connector_rotation_request",422) from None
    if not (spec.confirm_rotation and spec.confirm_identity_probe and spec.confirm_workers_stopped):
        raise rest.FeedError("explicit_rotation_identity_probe_and_worker_reconciliation_required",403)
    with ws._LOCK:
        c=rest._connect()
        try:
            p=rest._get(c,profile_id)
            _disabled(p,spec.revision)
            candidate,reader=_candidate(p)
            binding=rest._key(p,"credential",candidate)
            if hmac.compare_digest(binding,p["credential_binding"]):
                raise rest.FeedError("credential_has_not_changed")
            if p["log_id"]:
                ws._check_log(c,p["log_id"])
        finally:
            c.close()

    def check(_):
        with ws._LOCK:
            c=rest._connect()
            try:
                _same_live(c,p,candidate)
                return candidate
            finally:
                c.close()

    try:
        with client_factory(timeout=10,follow_redirects=False,trust_env=False) as client:
            probe=reader(p,client,credential_check=check)
            probe.identity()
    except rest.FeedError as error:
        # Respect rate limits without changing credential binding or enabling
        # the profile. A concurrent change is never overwritten.
        if error.retry_after:
            with ws._LOCK:
                c=rest._connect()
                try:
                    live=rest._get(c,profile_id)
                    if live==p:
                        live["retry_not_before"]=max(live["retry_not_before"],time.time()+error.retry_after)
                        rest._put(c,live)
                finally:
                    c.close()
        raise
    except Exception:
        raise rest.FeedError("connector_identity_probe_failed",502) from None

    with ws._LOCK:
        c=rest._connect()
        try:
            live=_same_live(c,p,candidate)
            live.update(credential_binding=binding,revision=live["revision"]+1,
                        last_credential_rotation={"at":rest._iso(rest._now()),
                            "previous_revision":p["revision"],"identity_readback_verified":True,
                            "profile_remains_disabled":True,"provider_event_reads_performed":False})
            rest._put(c,live)
            return rest._public(live,time.time())
        finally:
            c.close()


def metadata(p):
    """Passive, sanitized status. Never return references, paths or hashes."""
    provider=p.get("provider","rest")
    info={"reviewed_rotation_supported":provider in SUPPORTED,
          "delivery":"provider_managed" if provider in ("google","salesforce","servicenow") else "unknown",
          "last_rotation":p.get("last_credential_rotation")}
    try:
        cfg=p["config"]
        ref=None
        if provider in ("rest","slack"):
            ref=cfg["credential_env"]
        elif provider=="sap":
            from . import sap_procurement_feed as sap
            ref=sap.source(cfg)["credential_env"]
        elif provider=="dataverse":
            from . import dataverse_audit_feed as dv
            ref=dv.source(cfg)["credential_env"]
        elif provider=="hubspot":
            from . import hubspot_history_feed as hs
            ref=hs.source(cfg)["credential_env"]
        elif provider=="workday":
            from . import workday_raas_feed as wd
            ref=wd.source(cfg)["credential_env"]
        elif provider=="netsuite":
            from . import netsuite_event_feed as ns
            ref=ns.source(cfg)["credential_env"]
        elif provider=="snowflake":
            from . import snowflake_event_feed as snow
            ref=snow.source(cfg)["credential_env"]
        elif provider=="databricks":
            from . import databricks_event_feed as dbx
            ref=dbx.source(cfg)["credential_env"]
        elif provider=="sql":
            from . import enterprise_sql_feed as sql
            source=sql._source(cfg["source_alias"])
            ref=source.dsn_env
            if ref is None:
                info["delivery"]="local_database"
        if ref:
            from .enterprise_connector_secrets import delivery
            info["delivery"]=delivery(ref)
    except (ValueError,TypeError,KeyError):
        info["delivery"]="unavailable"
    return info
