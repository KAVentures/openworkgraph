#!/usr/bin/env python3
"""Ephemeral Docker/PostgreSQL Gateway manager insight smoke, run in CI only.

Requires the same local test Gateway started by .github/workflows/tests.yml.
No production credentials or external network access.
"""
import json
import urllib.error
import urllib.request

BASE="http://127.0.0.1:8790"
ADMIN="test-admin-token"
ENROLL="test-enrollment-token"


def request(method,path,body=None,token=None):
    payload=None if body is None else json.dumps(body).encode()
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    r=urllib.request.Request(BASE+path,data=payload,headers=headers,method=method)
    with urllib.request.urlopen(r,timeout=15) as response:
        return json.loads(response.read().decode())


def main():
    path="/v1/admin/manager-insights/ci-manager"
    try:
        request("GET",path)
    except urllib.error.HTTPError as exc:
        assert exc.code==401,exc.code
    else:
        raise AssertionError("insights must require admin auth")
    apps=("Gmail","Microsoft Excel","Fortnox","Gmail")
    for person in range(3):
        device=request("POST","/v1/devices/enroll",{
            "organization_id":"ci-manager",
            "actor_id":f"ci-employee-{person}",
            "device_id":f"ci-manager-device-{person}",
        },ENROLL)
        events=[
            {
                "event_id":f"ci-manager-{person}-{i}",
                "observed_at":f"2026-10-10T{8+person:02d}:{i:02d}:00+00:00",
                "organization_id":"ignored-spoofed-org",
                "actor_id":"ignored-spoofed-actor",
                "device_id":"ignored-spoofed-device",
                "session_id":f"ci-manager-session-{person}",
                "app":app,
                "window_title":"CONFIDENTIAL_CLIENT_RECORD_NOT_FOR_MANAGER",
                "source":"desktop","event_type":"focus_span",
                "duration_seconds":90,
                "metadata":{"action":"focus","private_raw_text":"NOT_FOR_MANAGER"},
            } for i,app in enumerate(apps)
        ]
        uploaded=request("POST","/v1/evidence/batch",{"events":events},device["token"])
        assert len(uploaded["acknowledged_event_ids"])==4,uploaded
        results=request("GET",path,token=ADMIN)
        if person<2:
            assert results["workflows"]==[],results
        else:
            rows=results["workflows"]
            assert len(rows)==1,results
            top=rows[0]
            assert top["apps"]==["Gmail","Excel","Fortnox","Gmail"],top
            assert top["contributing_people"]==3,top
            assert top["observed_sessions"]==3,top
            assert top["automation_candidate"] is True
            assert top["observed_duration_hours"]>0,top
            assert results["overview"]["minimum_contributors"]==3
            for prohibited in ("ci-employee-","ci-manager-device-",
                "ci-manager-session-","CONFIDENTIAL_CLIENT","NOT_FOR_MANAGER",
                "ignored-spoofed-actor"):
                assert prohibited not in str(results),prohibited
    # The actual connector records browser-hosted apps as Chrome, with the
    # site in privacy-hardened metadata.page.hostname. A real recording run
    # is an entire day, not a conveniently segmented task per session.
    host_sequence = [
        ("Google Chrome", "mail.google.com"),
        ("Microsoft Excel", ""),
        ("Google Chrome", "app.slack.com"),  # incidental chatter
        ("Google Chrome", "app.fortnox.se"),
        ("Google Chrome", "mail.google.com"),
    ]
    browser_path="/v1/admin/manager-insights/ci-browser-real"
    for person in range(3):
        device=request("POST","/v1/devices/enroll",{
            "organization_id":"ci-browser-real",
            "actor_id":f"ci-browser-person-{person}",
            "device_id":f"ci-browser-device-{person}",
        },ENROLL)
        for day in (10,11):
            records=[]
            for i,(app,site) in enumerate(host_sequence):
                records.append({
                    "event_id":f"ci-browser-{person}-{day}-{i}",
                    "observed_at":f"2026-10-{day:02d}T{8+person:02d}:{i:02d}:00+00:00",
                    "session_id":f"long-recording-session-{person}-{day}",
                    "source":"browser_extension" if site else "desktop",
                    "app":app,"window_title":"PRIVATE_CLIENT_TITLE_NOT_FOR_INSIGHTS",
                    "event_type":"browser_navigation" if site else "focus_span",
                    "duration_seconds":75,
                    "metadata":{
                        "action":"navigate" if site else "focus",
                        "page":{"hostname":site} if site else {},
                        "private_raw_text":"MUST_NOT_BE_IN_INSIGHTS",
                    },
                })
            uploaded=request("POST","/v1/evidence/batch",{"events":records},device["token"])
            assert len(uploaded["acknowledged_event_ids"])==len(records),uploaded
        response=request("GET",browser_path,token=ADMIN)
        if person<2:
            assert response["workflows"]==[],response
        else:
            top=response["workflows"][0]
            assert top["apps"]==["Gmail","Excel","Fortnox","Gmail"],response
            assert top["contributing_people"]==3,top
            assert top["observed_sessions"]==6,top
            for secret in ("PRIVATE_CLIENT","MUST_NOT","ci-browser-person","long-recording-session"):
                assert secret not in str(response),secret
    print("PostgreSQL manager browser-shaped insights: site attribution, long sessions, noise, k=3 PASS")
    print("PostgreSQL manager insights: 401, <3 suppression, 3-person summary, no text leakage PASS")


if __name__=="__main__":
    main()
