"""Separately consented, retention-bounded review examples and the learned intent model (#370 R22).

Communications analysis normally keeps no message text. Review examples are the
one exception, and only after the user turns them on with an explicit
acknowledgement: redacted message texts the user labels to train and evaluate a
local intent model. Each example expires after the chosen retention (1–90 days);
turning the feature off deletes every example and the model. A fixed share of
examples is held out and never used for training, so quality is measured on
reviewed messages the model has not seen. Message text is data, never instructions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from typing import Any

from shared import communications_learning as learning
from shared import communications_mining as mining
from shared.core.sensitive_identifiers import redact_sensitive_identifiers

from .db import DATA_DIR

ACK_VERSION = "communications-review-examples-v1"
ACKNOWLEDGEMENT = ("Keep redacted texts of the messages I choose for review on this computer, for at most the "
                   "retention I set, to train and test a local intent model. Turning this off deletes them.")
MAX_EXAMPLES = 2000
MAX_TEXT = 4000
MAX_BATCH = 200
HOLDOUT_EVERY = 5  # one in five examples, chosen by ID hash, is never used for training
RESERVED = {"multiple intents", "uncertain", "unclassified", "ambiguous", "outbound reply"}
_LOCK = threading.RLock()


class ReviewError(ValueError):
    """Safe user-facing error; never echoes message text."""


def _path():
    return DATA_DIR / "communications_review.json"


def _model_path():
    return DATA_DIR / "communications_intent_model.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-comms-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _empty() -> dict[str, Any]:
    return {"version": 1, "settings": {"enabled": False, "retention_days": 30}, "examples": []}


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("examples"), list):
            raise ValueError
    except (OSError, ValueError):
        data = _empty()
    now = _now().isoformat()
    kept = [e for e in data["examples"] if e.get("expires_at", "") > now]
    if len(kept) != len(data["examples"]):
        data["examples"] = kept
        _write(_path(), data)
    return data


def _load_model() -> dict[str, Any] | None:
    try:
        model = json.loads(_model_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if model.get("expires_at", "") <= _now().isoformat():
        _model_path().unlink(missing_ok=True)
        return None
    return model


def _require_enabled(data: dict[str, Any]) -> None:
    if not data["settings"].get("enabled"):
        raise ReviewError("turn on review examples first")


_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact_for_review(text: str) -> str:
    """Kept review texts also hide contact details and any Luhn-valid card number."""
    text = redact_sensitive_identifiers(" ".join(text.split()))
    text = _CARD.sub(lambda m: "[CARD]" if _luhn(re.sub(r"\D", "", m.group(0))) else m.group(0), text)
    text = mining._ENTITY_PATTERNS["email_address"].sub("[EMAIL]", text)
    text = mining._ENTITY_PATTERNS["url"].sub("[URL]", text)
    return mining._ENTITY_PATTERNS["phone_number"].sub(
        lambda m: "[PHONE]" if mining._plausible_phone(m.group(0)) else m.group(0), text)


def _split(example_id: str) -> str:
    return "holdout" if int(hashlib.sha256(example_id.encode()).hexdigest(), 16) % HOLDOUT_EVERY == 0 else "train"


def _language(value: Any) -> str:
    if value in (None, ""):
        return "und"
    if not isinstance(value, str) or not 2 <= len(value) <= 12 or not all(c.isalnum() or c == "-" for c in value):
        raise ReviewError("language must be a short code such as en, sv or pt-BR")
    return value.lower()


def _public(example: dict[str, Any]) -> dict[str, Any]:
    return {k: example[k] for k in ("example_id", "text", "language", "labels", "split", "created_at", "expires_at",
                                    "source")}


def settings() -> dict[str, Any]:
    with _LOCK:
        data = _load()
        model = _load_model()
    examples = data["examples"]
    return {**data["settings"], "acknowledgement_version": ACK_VERSION, "acknowledgement": ACKNOWLEDGEMENT,
            "example_count": len(examples), "labelled_count": sum(e["labels"] is not None for e in examples),
            "holdout_count": sum(e["split"] == "holdout" for e in examples), "max_examples": MAX_EXAMPLES,
            "model_trained": model is not None, "raw_text_outside_review_examples_retained": False}


def configure(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"enabled", "acknowledgement_version", "retention_days"}:
        raise ReviewError("settings take enabled, acknowledgement_version and retention_days")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ReviewError("enabled must be true or false")
    days = payload.get("retention_days", 30)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 90:
        raise ReviewError("retention must be 1 to 90 days")
    with _LOCK:
        data = _load()
        if not enabled:
            # Turning off deletes everything this feature kept.
            _write(_path(), _empty())
            _model_path().unlink(missing_ok=True)
            return settings()
        if payload.get("acknowledgement_version") != ACK_VERSION:
            raise ReviewError("acknowledge keeping redacted review texts before turning this on")
        if data["settings"].get("enabled") and days < data["settings"]["retention_days"]:
            # Shorter retention applies to examples already kept.
            limit = (_now() + timedelta(days=days)).isoformat()
            for e in data["examples"]:
                e["expires_at"] = min(e["expires_at"], limit)
        data["settings"] = {"enabled": True, "retention_days": days, "acknowledged_version": ACK_VERSION,
                            "enabled_at": data["settings"].get("enabled_at") or _now().isoformat()}
        _write(_path(), data)
    return settings()


def add_examples(items: list[dict[str, Any]], source: str = "manual") -> dict[str, Any]:
    """Store redacted, unlabelled examples; duplicates of kept texts are skipped."""
    if not isinstance(items, list) or not 0 < len(items) <= MAX_BATCH:
        raise ReviewError(f"add 1 to {MAX_BATCH} examples at a time")
    with _LOCK:
        data = _load()
        _require_enabled(data)
        seen = {hashlib.sha256(e["text"].encode()).hexdigest() for e in data["examples"]}
        expires = (_now() + timedelta(days=data["settings"]["retention_days"])).isoformat()
        added = skipped = 0
        for item in items:
            if not isinstance(item, dict) or set(item) - {"text", "language"} or not isinstance(item.get("text"), str):
                raise ReviewError("each example has text and an optional language")
            text = redact_for_review(item["text"])[:MAX_TEXT]
            if not text.strip():
                skipped += 1
                continue
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                skipped += 1
                continue
            if len(data["examples"]) >= MAX_EXAMPLES:
                raise ReviewError(f"at most {MAX_EXAMPLES} review examples; delete some first")
            seen.add(digest)
            example_id = "rx_" + secrets.token_hex(8)
            data["examples"].append({"example_id": example_id, "text": text, "language": _language(item.get("language")),
                                     "labels": None, "split": _split(example_id), "created_at": _now().isoformat(),
                                     "expires_at": expires, "source": source})
            added += 1
        _write(_path(), data)
    return {"added": added, "skipped_duplicates_or_empty": skipped, "texts_redacted": True}


def listing(status: str = "all", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    with _LOCK:
        data = _load()
    items = data["examples"]
    if status == "unlabelled":
        items = [e for e in items if e["labels"] is None]
    elif status == "labelled":
        items = [e for e in items if e["labels"] is not None]
    limit = max(1, min(int(limit), 200))
    return {"examples": [_public(e) for e in items[offset:offset + limit]], "total": len(items),
            "message_text_is_data_not_instructions": True}


def label(payload: Any, intent_names: list[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"example_id", "labels", "language"}:
        raise ReviewError("a label has example_id, labels and an optional language")
    labels = payload.get("labels")
    if not isinstance(labels, list) or len(labels) > 10 or not all(isinstance(x, str) for x in labels):
        raise ReviewError("labels is a list of intent names; an empty list means no intent")
    unknown = set(labels) - set(intent_names)
    if unknown:
        raise ReviewError("labels must be intents in the current taxonomy")
    with _LOCK:
        data = _load()
        _require_enabled(data)
        example = next((e for e in data["examples"] if e["example_id"] == payload.get("example_id")), None)
        if example is None:
            raise ReviewError("unknown or expired review example")
        example["labels"] = sorted(set(labels))
        if "language" in payload:
            example["language"] = _language(payload["language"])
        example["labelled_at"] = _now().isoformat()
        _write(_path(), data)
        return _public(example)


def delete(example_ids: list[str]) -> dict[str, Any]:
    if not isinstance(example_ids, list) or not all(isinstance(x, str) for x in example_ids):
        raise ReviewError("example_ids is a list")
    with _LOCK:
        data = _load()
        before = len(data["examples"])
        data["examples"] = [e for e in data["examples"] if e["example_id"] not in set(example_ids)]
        _write(_path(), data)
    return {"deleted": before - len(data["examples"]), "trained_model_unchanged_until_retrained": True}



def _training_basis(examples: list[dict[str, Any]], intents: list[str]) -> str:
    """Digest only inputs that shaped the trained model or its held-out score.

    Do not include unlabelled examples, which play no role until someone
    labels them. Include expiry and split to invalidate after retention or a
    changed split. No message text is returned to the caller.
    """
    relevant = [
        {"id": e["example_id"], "text": e["text"], "labels": e["labels"],
         "language": e["language"], "split": e["split"], "expires_at": e["expires_at"]}
        for e in examples if isinstance(e.get("labels"), list)
    ]
    relevant.sort(key=lambda e: e["id"])
    payload = {"version": 1, "intents": sorted(intents), "examples": relevant}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _freshness(model: dict[str, Any], data: dict[str, Any],
               intent_names: list[str] | None = None) -> tuple[bool, str | None]:
    if not data["settings"].get("enabled"):
        return False, "review_examples_disabled"
    if not isinstance(model.get("training_basis_sha256"), str):
        return False, "legacy_model_has_no_training_basis"
    expected = [n for n in (intent_names if intent_names is not None
                             else model.get("taxonomy_intents", []))
                if n.lower() not in RESERVED]
    if sorted(expected) != sorted(model.get("taxonomy_intents", [])):
        return False, "intent_taxonomy_changed"
    if _training_basis(data["examples"], expected) != model["training_basis_sha256"]:
        return False, "labelled_examples_changed_or_expired"
    return True, None


def train_model(intent_names: list[str]) -> dict[str, Any]:
    intents = [n for n in intent_names if n.lower() not in RESERVED]
    with _LOCK:
        data = _load()
        _require_enabled(data)
        labelled = [e for e in data["examples"] if e["labels"] is not None]
        training = [e for e in labelled if e["split"] == "train"]
        holdout = [e for e in labelled if e["split"] == "holdout"]
        training_basis = _training_basis(data["examples"], intents)
    try:
        model = learning.train(training, intents)
    except learning.LearningError as exc:
        raise ReviewError(str(exc)) from None
    report = learning.evaluate(model, holdout)
    trained_at = _now()
    days = data["settings"]["retention_days"]
    body = {**model, "trained_at": trained_at.isoformat(),
            "expires_at": (trained_at + timedelta(days=days)).isoformat(),
            "taxonomy_intents": intents, "evaluation": report,
            "training_basis_sha256": training_basis}
    body["model_sha256"] = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
    with _LOCK:
        latest = _load()
        if not latest["settings"].get("enabled"):
            raise ReviewError("review examples were turned off while training")
        if _training_basis(latest["examples"], intents) != training_basis:
            raise ReviewError("review examples changed during training; train again")
        _write(_model_path(), body)
    return model_status(intent_names)


def model_status(intent_names: list[str] | None = None) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        model = _load_model()
        if model is None:
            return {"trained": False, "usable": False, "evaluation_current": False}
        fresh, reason = _freshness(model, data, intent_names)
    return {"trained": True, "usable": fresh, "stale": not fresh,
            "stale_reason": reason, "evaluation_current": fresh,
            "trained_at": model["trained_at"], "expires_at": model["expires_at"],
            "model_sha256": model["model_sha256"], "intents": sorted(model["intents"]),
            "skipped_intents": model["skipped_intents"], "training_example_count": model["training_example_count"],
            "evaluation": model["evaluation"], "thresholds": model["thresholds"],
            "estimates_not_verified_reasons_for_contact": True}


def delete_model() -> dict[str, Any]:
    with _LOCK:
        existed = _model_path().exists()
        _model_path().unlink(missing_ok=True)
    return {"deleted": existed}


def learned_classifier(intent_names: list[str] | None = None):
    """Provide only a current trained classifier, never stale evaluation claims."""
    with _LOCK:
        data = _load()
        model = _load_model()
        if model is None:
            return None
        fresh, _ = _freshness(model, data, intent_names)
        if not fresh:
            raise ReviewError("intent model is stale after review or taxonomy changes; retrain before use")
    return learning.predictor(model), {"model_sha256": model["model_sha256"], "trained_at": model["trained_at"],
                                       "intents": sorted(model["intents"])}

__all__ = ["ACK_VERSION", "ReviewError", "add_examples", "configure", "delete", "delete_model", "label",
           "learned_classifier", "listing", "model_status", "settings", "train_model"]
