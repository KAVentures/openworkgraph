from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from server.db import init_db, insert_events

WORKFLOWS = [
    ["Outlook", "Preview", "Excel", "Salesforce", "Excel", "Outlook"],
    ["Chrome", "Salesforce", "Excel", "SAP", "Outlook"],
    ["Teams", "Chrome", "Jira", "VS Code", "Chrome"],
]

def main():
    init_db()
    start = datetime.now(timezone.utc) - timedelta(hours=5)
    events=[]
    for s in range(18):
        session=str(uuid.uuid4())
        flow=random.choices(WORKFLOWS, weights=[7,5,2])[0]
        t=start+timedelta(minutes=s*14)
        for app in flow:
            events.append({
                "event_id": str(uuid.uuid4()), "observed_at": t.isoformat(), "device_id":"demo-laptop",
                "session_id":session, "app":app, "window_title":f"Demo work in {app}",
                "event_type":"window_change", "duration_seconds":random.randint(10,90), "metadata":{"demo":True}
            })
            t += timedelta(seconds=random.randint(20,120))
    print({"inserted": insert_events(events)})

if __name__ == "__main__": main()
