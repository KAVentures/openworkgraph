from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import re
import statistics
from typing import Any

from .browser_signal_settings import load_settings
from .db import connect

AUTH_MARKERS = re.compile(r"/(?:login|signin|sign-in|sso|oauth|auth|authenticate|saml|callback)(?:/|$)", re.I)


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _rows(since: str | None) -> list[dict[str, Any]]:
    where = "WHERE observed_at >= ?" if since else ""
    params = (since,) if since else ()
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id,event_id,observed_at,session_id,surface,action,resource_locator,target_label,metadata_json FROM context_events {where} ORDER BY observed_at,id",
            params,
        ).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        try: item["metadata"]=json.loads(item.pop("metadata_json") or "{}")
        except Exception: item["metadata"]={}
        result.append(item)
    return result


def _rapid_click_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str,str,str,str], list[dict[str,Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("action") or "").lower() not in {"click","right_click"}: continue
        label=str(row.get("target_label") or "").strip()
        if not label: continue
        key=(str(row.get("session_id") or ""),str(row.get("surface") or "Unknown"),str(row.get("resource_locator") or ""),label)
        grouped[key].append(row)
    output=[]
    for (_session,surface,locator,label), items in grouped.items():
        times=[(_ts(x.get("observed_at")),x) for x in items]
        times=[x for x in times if x[0] is not None]
        start=0
        bursts=[]
        for end in range(len(times)):
            while times[end][0]-times[start][0] > 1.5: start+=1
            if end-start+1 >= 3:
                burst=times[start:end+1]
                if not bursts or burst[0][0] > bursts[-1][-1][0]: bursts.append(burst)
        if bursts:
            output.append({
                "surface":surface,"resource_locator":locator,"target_label":label,
                "burst_count":len(bursts),"max_clicks_in_1_5s":max(len(x) for x in bursts),
                "candidate_reason":"repeated_rapid_clicks_same_safe_target","needs_review":True,
                "example_event_ids":[x[1].get("event_id") for burst in bursts[:3] for x in burst[:3] if x[1].get("event_id")][:9],
            })
    return sorted(output,key=lambda x:(-int(x["burst_count"]),-int(x["max_clicks_in_1_5s"])))[:50]


def _auth_flow_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sessions: dict[str,list[dict[str,Any]]]=defaultdict(list)
    for row in rows: sessions[str(row.get("session_id") or "")].append(row)
    output=[]
    for session,items in sessions.items():
        active=None
        for row in items:
            locator=str(row.get("resource_locator") or "")
            is_auth=bool(AUTH_MARKERS.search("/"+locator.split("/",1)[1] if "/" in locator else locator))
            at=_ts(row.get("observed_at"))
            if at is None: continue
            if is_auth and active is None:
                active={"started_at":row.get("observed_at"),"start_ts":at,"surface":row.get("surface") or "Unknown","event_ids":[row.get("event_id")]}
            elif is_auth and active is not None:
                if row.get("event_id"): active["event_ids"].append(row.get("event_id"))
            elif not is_auth and active is not None:
                duration=max(0.0,at-float(active["start_ts"]))
                if duration <= 30*60:
                    output.append({
                        "surface":active["surface"],"started_at":active["started_at"],"ended_at":row.get("observed_at"),
                        "duration_seconds":round(duration,3),"candidate_reason":"auth_path_sequence","needs_review":True,
                        "example_event_ids":[x for x in active["event_ids"][:8] if x],
                    })
                active=None
    return output[-100:]


def _tool_waiting(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_surface: dict[str,list[float]]=defaultdict(list)
    response_by_surface: dict[str,list[float]]=defaultdict(list)
    for row in rows:
        if str(row.get("action") or "") != "performance_timing": continue
        meta=row.get("metadata") if isinstance(row.get("metadata"),dict) else {}
        try: load=float(meta.get("load_complete_ms") or 0)
        except Exception: load=0
        try: wait=float(meta.get("response_wait_ms") or 0)
        except Exception: wait=0
        if load>0: by_surface[str(row.get("surface") or "Unknown")].append(load)
        if wait>0: response_by_surface[str(row.get("surface") or "Unknown")].append(wait)
    output=[]
    for surface,values in by_surface.items():
        ordered=sorted(values)
        p95=ordered[min(len(ordered)-1,max(0,int(round(0.95*(len(ordered)-1)))))]
        waits=response_by_surface.get(surface,[])
        output.append({
            "surface":surface,"navigation_count":len(values),"median_load_ms":round(float(statistics.median(values)),1),
            "p95_load_ms":round(float(p95),1),"sum_observed_load_ms":round(sum(values),1),
            "median_response_wait_ms":round(float(statistics.median(waits)),1) if waits else 0.0,
            "interpretation":"Observed navigation timing, not automatically wasted time.",
        })
    return sorted(output,key=lambda x:-float(x["sum_observed_load_ms"]))


def _file_upload_categories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[tuple[str,str],int]=defaultdict(int)
    for row in rows:
        if str(row.get("action") or "") != "file_upload_category": continue
        meta=row.get("metadata") if isinstance(row.get("metadata"),dict) else {}
        cats=meta.get("categories") if isinstance(meta.get("categories"),dict) else {}
        for category,count in cats.items():
            try: totals[(str(row.get("surface") or "Unknown"),str(category))]+=int(count)
            except Exception: pass
    return [{"surface":s,"category":c,"file_count":n} for (s,c),n in sorted(totals.items(),key=lambda x:-x[1])]


def enrich_work_profile(profile: dict[str, Any], *, since: str | None) -> dict[str, Any]:
    rows=_rows(since)
    profile=dict(profile)
    profile["rapid_click_candidates"]=_rapid_click_candidates(rows)
    profile["auth_flow_candidates"]=_auth_flow_candidates(rows)
    profile["tool_waiting"]=_tool_waiting(rows)
    profile["file_upload_categories"]=_file_upload_categories(rows)
    profile["browser_signal_settings"]=load_settings()
    privacy=dict(profile.get("privacy") or {})
    privacy.update({"file_names_captured":False,"file_paths_captured":False,"exact_file_sizes_captured":False,"file_contents_captured":False,"resource_timing_urls_captured":False,"microphone_state_captured":False})
    profile["privacy"]=privacy
    interpretation=dict(profile.get("interpretation") or {})
    interpretation.update({"rapid_click_candidates_require_review":True,"auth_flow_candidates_require_review":True,"tool_waiting_is_observed_timing_not_wasted_time":True})
    profile["interpretation"]=interpretation
    return profile
