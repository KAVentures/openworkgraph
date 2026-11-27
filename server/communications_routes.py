"""Local routes for communications mining over explicitly selected exports.

The uploaded export is spooled to a private temporary file, analysed and
deleted in the same request. Only aggregates are returned; saving an
intent-only event log is a separate, explicit choice.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from shared import communications_mining as mining

from . import communications_review as review
from . import event_log_workspace as workspace
from .db import DATA_DIR
from .local_auth import dashboard_session_valid

router = APIRouter()
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "communications_mining.js"


def _taxonomy_path() -> Path:
    return DATA_DIR / "communications_taxonomy.json"


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for communications mining")


def _no_store(payload: Any) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def load_taxonomy() -> dict[str, Any]:
    try:
        saved = json.loads(_taxonomy_path().read_text(encoding="utf-8"))
        mining.parse_taxonomy(saved)
        return saved
    except (OSError, ValueError):
        return mining.DEFAULT_TAXONOMY


@router.get("/communications-mining.js")
def communications_script() -> FileResponse:
    """Static dashboard client; it only calls the authenticated routes below."""
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/communications/taxonomy")
def get_taxonomy(request: Request):
    _require_dashboard(request)
    taxonomy = load_taxonomy()
    return _no_store({"taxonomy": taxonomy, "is_default": taxonomy is mining.DEFAULT_TAXONOMY})


@router.put("/v1/communications/taxonomy")
async def put_taxonomy(request: Request):
    _require_dashboard(request)
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "taxonomy must be JSON")
    body = await request.body()
    if len(body) > 65536:
        raise HTTPException(413, "taxonomy is too large")
    try:
        taxonomy = json.loads(body)
        mining.parse_taxonomy(taxonomy)
    except ValueError as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, mining.CommunicationsError) else "taxonomy is not valid JSON") from None
    path = _taxonomy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(taxonomy, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return _no_store({"taxonomy": taxonomy, "is_default": False})


@router.post("/v1/communications/taxonomy/reset")
def reset_taxonomy(request: Request):
    _require_dashboard(request)
    _taxonomy_path().unlink(missing_ok=True)
    return _no_store({"taxonomy": mining.DEFAULT_TAXONOMY, "is_default": True})


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()][:50]


def _run(path: str, fmt: str, options: dict[str, Any], save: dict[str, Any] | None,
         learned: Any = None, review_sample: int = 0) -> dict[str, Any]:
    if fmt == "mbox":
        messages = mining.read_mbox(path, options["own_domains"])
    elif fmt == "eml":
        handle = open(path, "rb")
        messages = mining.read_eml(handle, options["own_domains"])
    else:
        handle = open(path, encoding="utf-8-sig", newline="")
        messages = mining.read_csv(handle, text_columns=options["text_columns"], time_column=options["time_column"],
                                   thread_column=options["thread_column"], direction_column=options["direction_column"],
                                   channel_column=options["channel_column"], delimiter=options["delimiter"])
    try:
        report = mining.analyze(messages, load_taxonomy(), learned=learned[0] if learned else None,
                                review_sample=review_sample,response_episode_settings=options.get("response_episode_settings"))
    finally:
        if fmt != "mbox":
            handle.close()
    events = report.pop("_events")
    candidates = report.pop("_review_candidates")
    if learned:
        report["model_reference"] = learned[1]
    if review_sample:
        report["review_examples"] = review.add_examples([{"text": t} for t in candidates], source="export_sample") \
            if candidates else {"added": 0, "skipped_duplicates_or_empty": 0, "texts_redacted": True}
    if save is not None:
        if not events:
            raise mining.CommunicationsError("no dated messages with a thread to save as an event log")
        report["event_log"] = workspace.import_log(BytesIO(mining.events_csv(events)), name=save["name"],
                                                   source_format="csv", mapping={
            "case_column": "thread", "activity_column": "intent", "timestamp_column": "time",
            "resource_column": "direction"})
    return report


@router.post("/v1/communications/analyze")
async def analyze_export(
    request: Request,
    format: str = Query(pattern="^(mbox|eml|csv)$"),
    text_columns: str = Query(default="", max_length=2000),
    time_column: str = Query(default="", max_length=200),
    thread_column: str = Query(default="", max_length=200),
    direction_column: str = Query(default="", max_length=200),
    channel_column: str = Query(default="", max_length=200),
    delimiter: str = Query(default=",", max_length=1),
    own_domains: str = Query(default="", max_length=2000),
    save_event_log: bool = Query(default=False),
    name: str = Query(default="Communications", max_length=120),
    classifier: str = Query(default="keyword", pattern="^(keyword|learned)$"),
    quality_gate_language: str | None = Query(default=None, max_length=12),
    review_sample: int = Query(default=0, ge=0, le=review.MAX_BATCH),
    response_cutoff: str | None = Query(default=None,max_length=64),
    response_deadlines: str | None = Query(default=None,max_length=100),
):
    _require_dashboard(request)
    if request.headers.get("x-owg-local-processing-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to analyse this export on this computer is required")
    if save_event_log and request.headers.get("x-owg-local-storage-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to store the intent event log on this computer is required")
    if review_sample and request.headers.get("x-owg-review-examples-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to keep redacted review examples is required")
    if quality_gate_language is not None and classifier != "learned":
        raise HTTPException(422, "Holdout quality gate applies only to learned classification")
    learned = None
    if classifier == "learned":
        try:
            learned = review.learned_classifier(_intent_names(),
                require_holdout_language=quality_gate_language)
        except review.ReviewError as exc:
            raise HTTPException(409, str(exc)) from None
        if learned is None:
            raise HTTPException(409, "train an intent model from reviewed examples first")
    if review_sample and not review.settings()["enabled"]:
        raise HTTPException(409, "turn on review examples first")
    options = {"text_columns": _split(text_columns), "time_column": time_column, "thread_column": thread_column,
               "direction_column": direction_column, "channel_column": channel_column,
               "delimiter": "\t" if delimiter == "t" else delimiter, "own_domains": _split(own_domains)}
    if format == "csv" and not options["text_columns"]:
        raise HTTPException(422, "choose at least one text column")
    if response_cutoff is not None or response_deadlines is not None:
        from shared import communications_response_episodes as episodes
        try:
            if response_cutoff is None or response_deadlines is None:
                raise ValueError("both response cutoff and deadlines are required")
            tokens=response_deadlines.split(",")
            if any(not t.isascii() or not t.isdecimal() or len(t)>5 for t in tokens):
                raise ValueError("response deadlines must be comma-separated whole minutes")
            options["response_episode_settings"]=episodes.settings(response_cutoff,[int(t) for t in tokens])
            if format=="csv" and not all(options[k] for k in ("time_column","thread_column","direction_column")):
                raise ValueError("response review needs explicit time, thread and direction columns")
            if format!="csv" and not options["own_domains"]:
                raise ValueError("response review needs your own email domains to distinguish replies")
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("wb", dir=str(DATA_DIR), prefix="owg-comms-", delete=False)
    try:
        with handle:
            try:
                os.chmod(handle.name, 0o600)
            except OSError:
                pass
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "export is larger than the supported limit")
                handle.write(chunk)
        save = {"name": name} if save_event_log else None
        try:
            report = await run_in_threadpool(_run, handle.name, format, options, save, learned, review_sample)
        except (mining.CommunicationsError, workspace.EventLogWorkspaceError, review.ReviewError) as exc:
            raise HTTPException(422, str(exc)) from None
        except (UnicodeDecodeError, OSError, ValueError):
            raise HTTPException(422, "the export could not be read in the chosen format") from None
    finally:
        os.unlink(handle.name)
    return _no_store(report)


# ------------------------------------------------------------ review examples and learned model

async def _json_body(request: Request, maximum: int = 1024 * 1024) -> Any:
    if not str(request.headers.get("content-type") or "").startswith("application/json"):
        raise HTTPException(415, "JSON required")
    body = await request.body()
    if len(body) > maximum:
        raise HTTPException(413, "request is too large")
    try:
        return json.loads(body)
    except ValueError:
        raise HTTPException(422, "invalid JSON") from None


def _review_call(operation, *args):
    try:
        return _no_store(operation(*args))
    except review.ReviewError as exc:
        raise HTTPException(409, str(exc)) from None


def _intent_names() -> list[str]:
    return [intent.name for intent in mining.parse_taxonomy(load_taxonomy())]


@router.get("/v1/communications/review/settings")
def get_review_settings(request: Request):
    _require_dashboard(request)
    return _review_call(review.settings)


@router.put("/v1/communications/review/settings")
async def put_review_settings(request: Request):
    _require_dashboard(request)
    return _review_call(review.configure, await _json_body(request, 4096))


@router.get("/v1/communications/review/examples")
def get_review_examples(request: Request, status: str = Query(default="all", pattern="^(all|labelled|unlabelled)$"),
                        limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    _require_dashboard(request)
    return _review_call(review.listing, status, limit, offset)


@router.get("/v1/communications/review/triage")
def get_active_review_queue(request: Request, limit: int = Query(default=20, ge=1, le=50),
                            offset: int = Query(default=0, ge=0, le=2000),
                            order: str = Query(default="priority", pattern="^(priority|diverse)$"),
                            language: str | None = Query(default=None,min_length=1,max_length=120),
                            keyword_intent: str | None = Query(default=None,min_length=1,max_length=120),
                            priority: str | None = Query(default=None,min_length=1,max_length=120),
                            expected_queue_sha256: str | None = Query(default=None,pattern="^[a-f0-9]{64}$")):
    """Human-only suggested review order; no labels, permissions or model changes."""
    _require_dashboard(request)
    from . import communications_review_triage
    try:
        result=communications_review_triage.queue(load_taxonomy(),limit=limit,offset=offset,order=order,
            language=language,keyword_intent=keyword_intent,priority=priority,
            expected_queue_sha256=expected_queue_sha256,current_taxonomy=load_taxonomy)
        _require_dashboard(request)
        return _no_store(result)
    except review.ReviewError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/v1/communications/review/examples")
async def add_review_examples(request: Request):
    _require_dashboard(request)
    if request.headers.get("x-owg-review-examples-confirmed") != "true":
        raise HTTPException(403, "Explicit confirmation to keep redacted review examples is required")
    body = await _json_body(request)
    if not isinstance(body, dict) or set(body) != {"examples"}:
        raise HTTPException(422, "send {examples: [...]}")
    return _review_call(review.add_examples, body["examples"])


@router.post("/v1/communications/review/label")
async def label_review_example(request: Request):
    _require_dashboard(request)
    return _review_call(review.label, await _json_body(request, 8192), _intent_names())


@router.post("/v1/communications/review/delete")
async def delete_review_examples(request: Request):
    _require_dashboard(request)
    body = await _json_body(request, 64 * 1024)
    if not isinstance(body, dict) or set(body) != {"example_ids"}:
        raise HTTPException(422, "send {example_ids: [...]}")
    return _review_call(review.delete, body["example_ids"])


@router.post("/v1/communications/review/train")
async def train_intent_model(request: Request):
    _require_dashboard(request)
    return _no_store(await run_in_threadpool(_trained))


def _trained():
    try:
        return review.train_model(_intent_names())
    except review.ReviewError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/v1/communications/review/model")
def get_intent_model(request: Request):
    _require_dashboard(request)
    return _review_call(review.model_status, _intent_names())


@router.get("/v1/communications/review/paired-evaluation")
async def get_paired_intent_evaluation(request: Request):
    """Compare both methods on the same retained, reviewed holdout."""
    _require_dashboard(request)
    result = await run_in_threadpool(_paired_evaluation)
    _require_dashboard(request)
    return _no_store(result)


def _paired_evaluation():
    from shared import communications_paired_evaluation as paired
    from shared import communications_learning as learning
    taxonomy = load_taxonomy()
    names = [i.name for i in mining.parse_taxonomy(taxonomy) if i.name.lower() not in review.RESERVED]
    taxonomy_digest = paired.digest(taxonomy)
    with review._LOCK:
        data = review._load()
        if not data['settings'].get('enabled'):
            raise HTTPException(409, 'turn on review examples first')
        model = review._load_model()
        if model is None or not review._freshness(model, data, names)[0]:
            raise HTTPException(409, "train a current model before paired evaluation")
        basis = review._training_basis(data["examples"], names)
        model_digest = model["model_sha256"]
        labelled = [e for e in data["examples"] if isinstance(e.get("labels"), list)]
        training = [e for e in labelled if e["split"] == "train"]
        holdout = [e for e in labelled if e["split"] == "holdout"]
    try:
        result = paired.evaluate(model, training, holdout, taxonomy, names)
    except (learning.LearningError, review.ReviewError) as exc:
        raise HTTPException(422, str(exc)) from None
    # A revoked grant, expired example, edited taxonomy or replaced model
    # invalidates the report; no per-example predictions leave the server.
    with review._LOCK:
        latest = review._load()
        current = review._load_model()
        if (current is None or current.get("model_sha256") != model_digest
                or not review._freshness(current, latest, names)[0]
                or review._training_basis(latest["examples"], names) != basis
                or paired.digest(load_taxonomy()) != taxonomy_digest):
            raise HTTPException(409, "review evidence or model changed during evaluation; inspect again")
    return {**result, "model_sha256": model_digest, "training_basis_sha256": basis,
            "trained_at": model["trained_at"], "local_only": True}


@router.post("/v1/communications/review/model/delete")
def delete_intent_model(request: Request):
    _require_dashboard(request)
    return _review_call(review.delete_model)
