"""Dedicated dashboard-only rule history endpoint and static client."""
from pathlib import Path
from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "signal_rule_history.js"


@router.get("/signal-rule-history.js")
def script():
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/event-logs/signal-rule-history")
def review(request: Request, start_date: str = Query(max_length=10), end_date: str = Query(max_length=10),
           deadline_seconds: int = Query(ge=1,le=31622400), recurrence_seconds: int = Query(ge=1,le=31622400),
           sort: str = Query(default="deadline",max_length=16),
           log_id: str | None = Query(default=None,pattern=r"^[A-Za-z0-9_-]{1,128}$"),
           offset: int = Query(default=0,ge=0,le=100000), limit: int = Query(default=30,ge=1,le=100),
           expected_review_sha256: str | None = Query(default=None,pattern=r"^[a-f0-9]{64}$")):
    from .event_log_routes import _require_dashboard, _respond
    from . import event_log_signal_rule_history as history
    _require_dashboard(request)
    return _respond(history.review,start_date=start_date,end_date=end_date,
                    deadline_seconds=deadline_seconds,recurrence_seconds=recurrence_seconds,
                    sort=sort,log_id=log_id,offset=offset,limit=limit,
                    expected_review_sha256=expected_review_sha256)
