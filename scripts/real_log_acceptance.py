"""Real-log acceptance sweep: import one local event log, then call every read-only event-log route.

Run it on a public log you downloaded yourself, for example a BPI Challenge log from 4TU.ResearchData:

    python scripts/real_log_acceptance.py BPI_Challenge_2012.xes.gz --format xes > result.json

The log is imported into a temporary workspace that is deleted afterwards; nothing is downloaded or
uploaded. Every GET route of the form /v1/event-logs/{log_id}/... is called with no parameters, and
every POST analysis route with an empty body and with a one-activity case filter. Routes that change
data (delete, append, save, share, tag, notes, settings) are not called. The JSON output lists each
call's status and seconds, the routes that answered with a server error or raised, and the routes
slower than --slow seconds. A 4xx answer means the route needs parameters the sweep does not invent.

Timings depend on the machine; the output records CPU count, Python and DuckDB versions.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

_UNSAFE = ("delete", "append", "derive", "agent-share", "combine", "tags", "notes", "glossary", "reference-nets",
           "signals/", "saved", "filter-sets", "cost-model", "mappings", "hidden", "case-table", "quarantine/",
           "rename", "description", "folder", "domain-pack", "jobs", "sharing")


def _routes(method: str) -> list[str]:
    import server
    found = set()
    for module in pkgutil.iter_modules(server.__path__):
        if "route" not in module.name:
            continue
        try:
            router = getattr(importlib.import_module("server." + module.name), "router", None)
        except Exception:
            continue
        for route in getattr(router, "routes", []):
            path = getattr(route, "path", "")
            if method in (getattr(route, "methods", None) or ()) and path.startswith("/v1/event-logs/{log_id}/") \
                    and path.count("{") == 1:
                found.add(path)
    return sorted(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log", type=Path, help="local event-log file (CSV, XES, OCEL; may be gzip-compressed)")
    parser.add_argument("--format", choices=["csv", "xes", "ocel", "ocelxml"], default="xes")
    parser.add_argument("--slow", type=float, default=20.0, help="report calls slower than this many seconds")
    parser.add_argument("--activity", default="", help="activity for the one-activity case filter "
                                                         "(default: the most frequent one)")
    parser.add_argument("--no-post", action="store_true", help="only call GET routes")
    parser.add_argument("--case-column", default="case", help="CSV only: the case ID column")
    parser.add_argument("--activity-column", default="activity", help="CSV only: the activity column")
    parser.add_argument("--time-column", default="time", help="CSV only: the timestamp column (with offsets)")
    parser.add_argument("--resource-column", default="", help="CSV only: the resource column, if any")
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix="owg-real-log-"))
    os.environ["WORKFLOW_OBSERVER_DATA"] = str(folder / "data")
    os.environ["WORKFLOW_OBSERVER_AUTH_DIR"] = str(folder / "auth")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import duckdb
        from fastapi.testclient import TestClient
        from server import local_auth
        from server.secure_app import app
        result: dict = {"format": "openworkgraph.real-log-acceptance.v1", "log": args.log.name,
                        "machine": {"cpus": os.cpu_count(), "python": platform.python_version(),
                                    "duckdb": duckdb.__version__, "platform": platform.platform()},
                        "calls": [], "server_errors": [], "slow": []}
        with TestClient(app) as client:
            headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session(),
                       "X-OWG-Local-Storage-Confirmed": "true"}
            started = time.perf_counter()
            from urllib.parse import urlencode
            query = {"format": args.format, "name": "acceptance"}
            if args.format == "csv":
                query.update(case_column=args.case_column, activity_column=args.activity_column,
                             timestamp_column=args.time_column, resource_column=args.resource_column)
            imported = client.post("/v1/event-logs/import?" + urlencode(query), content=args.log.read_bytes(),
                                   headers=headers)
            result["import"] = {"status": imported.status_code, "seconds": round(time.perf_counter() - started, 2)}
            if imported.status_code != 200:
                result["import"]["detail"] = imported.text[:500]
                print(json.dumps(result, indent=2))
                return 1
            body = imported.json()
            log_id = body["log_id"]
            result["import"].update(events=body.get("events"), objects=body.get("objects"),
                                    rejected_events=body.get("rejected_events"))
            activity = args.activity or (client.get(f"/v1/event-logs/{log_id}/activities", headers=headers)
                                         .json().get("activities") or [{}])[0].get("activity", "")
            plan = [("GET", path, None) for path in _routes("GET")]
            if not args.no_post:
                plan += [("POST", path, payload) for path in _routes("POST") if not any(s in path for s in _UNSAFE)
                         for payload in ({}, {"cohort": {"activity": activity}})]
            for method, path, payload in plan:
                url = path.replace("{log_id}", log_id)
                started = time.perf_counter()
                try:
                    response = client.get(url, headers=headers) if method == "GET" else \
                        client.post(url, json=payload, headers=headers)
                    status = response.status_code
                except Exception as exc:  # a raised exception is a finding, not a crash of the sweep
                    status = "exception:" + type(exc).__name__
                seconds = round(time.perf_counter() - started, 2)
                call = {"method": method, "route": path, "filter": bool(payload), "status": status, "seconds": seconds}
                result["calls"].append(call)
                if not isinstance(status, int) or status >= 500:
                    result["server_errors"].append(call)
                if seconds > args.slow:
                    result["slow"].append(call)
        statuses: dict[str, int] = {}
        for call in result["calls"]:
            statuses[str(call["status"])] = statuses.get(str(call["status"]), 0) + 1
        result["summary"] = {"calls": len(result["calls"]), "by_status": statuses,
                             "server_errors": len(result["server_errors"]), "slow": len(result["slow"])}
        print(json.dumps(result, indent=2))
        return 1 if result["server_errors"] else 0
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
