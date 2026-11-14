"""Guided clarification of a saved process design against owner-supplied documents (R11).

The owner pastes or uploads policy/SOP text. Each document is split into
statements with exact character offsets, so every finding cites the words it
relies on. The design's rules and work steps are matched to statements that
mention them; contradictions are flagged deterministically (one statement
requires what another forbids, or two give different numeric thresholds for
the same term). Questions are produced only for what remains unresolved:
unconfirmed rules, contradictions, missing citations, unassigned steps and
terminal steps without a confirmed outcome.

Documents are untrusted data. Text that reads like an instruction to the
system ("ignore", "grant access", "approve all") is flagged and never treated
as an answer. Nothing here changes the model, permissions or execution: a
human decision per question is recorded in a ledger bound to the exact process
revision, and a "confirm" yields a proposed model change the owner still saves
through the normal catalog flow.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .process_model import ProcessModel

MAX_DOCUMENTS = 5
MAX_DOCUMENT_CHARS = 60_000
MAX_STATEMENTS = 1500
MAX_CITATIONS = 5
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")
_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,]\d{3})+|\d+)(?:\.\d+)?\s*(%|percent|days?|hours?|eur|usd|sek|gbp|kr)?", re.I)
_REQUIRE = re.compile(r"\b(must|shall|always|required|requires|mandatory|need to|needs to|has to|have to)\b", re.I)
_FORBID = re.compile(r"\b(must not|mustn't|shall not|never|not allowed|prohibited|forbidden|may not|cannot|no longer)\b", re.I)
_INSTRUCTION = re.compile(r"\b(ignore (?:all|previous|the above)|disregard|grant (?:access|permission|admin)|"
                          r"approve all|you are an? (?:ai|assistant)|system prompt|execute|run this)\b", re.I)
_STOP = frozenset("""the and for with from that this then than when where which who whom into onto over under
are was were been being have has had not but all any each per via its our your their there here only also
must shall should will would could can may might always never required step process case cases request
""".split())
DECISIONS = ("confirm", "reject", "revise", "defer")


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr = Field(min_length=1, max_length=120)
    text: StrictStr = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Document] = Field(default_factory=list, max_length=MAX_DOCUMENTS)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: StrictStr = Field(pattern=r"^q_[0-9a-f]{16}$")
    decision: StrictStr = Field(pattern="^(confirm|reject|revise|defer)$")
    answer: StrictStr = Field(default="", max_length=2000)
    cited_statement_ids: list[StrictStr] = Field(default_factory=list, max_length=MAX_CITATIONS)
    expected_documents_sha256: StrictStr | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def documents_sha256(documents: list[Document]) -> str:
    """Fingerprint the exact ordered, opted-in source bundle without retaining it.

    A document change invalidates decisions on that source set. Length prefixes
    prevent ambiguous boundaries between titles and text; the domain tag
    prevents confusing this hash with a process-model or receipt digest.
    """
    digest = hashlib.sha256(b"openworkgraph.clarification-documents.v1\x00")
    digest.update(len(documents).to_bytes(4, "big"))
    for document in documents:
        for value in (document.title, document.text):
            raw = value.encode("utf-8")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return digest.hexdigest()


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def statements(documents: list[Document]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, document in enumerate(documents):
        doc_id = "d" + _digest(document.title, document.text)[:8]
        for match in _SENTENCE.finditer(document.text):
            raw = match.group(0)
            text = raw.strip()
            if len(text) < 8:
                continue
            start = match.start() + (len(raw) - len(raw.lstrip()))
            out.append({
                "statement_id": "s_" + _digest(doc_id, str(start), text),
                "document_index": index, "document_title": document.title,
                "start": start, "end": start + len(text), "text": text[:600],
                "terms": _terms(text),
                "modality": "forbids" if _FORBID.search(text) else "requires" if _REQUIRE.search(text) else "states",
                "numbers": [(m.group(1).replace(",", "").replace(" ", ""), (m.group(2) or "").lower().rstrip("s"))
                            for m in _NUMBER.finditer(text)],
                "instruction_like": bool(_INSTRUCTION.search(text)),
            })
            if len(out) >= MAX_STATEMENTS:
                return out
    return out


def _cite(statement: dict[str, Any]) -> dict[str, Any]:
    return {key: statement[key] for key in ("statement_id", "document_title", "start", "end", "text",
                                            "modality", "instruction_like")}


def _matches(subject: str, pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = _terms(subject)
    if not wanted:
        return []
    need = 1 if len(wanted) <= 2 else 2
    scored = [(len(wanted & s["terms"]), s) for s in pool if not s["instruction_like"]]
    return [s for score, s in sorted(scored, key=lambda pair: -pair[0]) if score >= need][:MAX_CITATIONS]


def _contradictions(cited: list[dict[str, Any]], subject: str) -> list[dict[str, Any]]:
    found = []
    wanted = _terms(subject)
    for i, a in enumerate(cited):
        for b in cited[i + 1:]:
            shared = wanted & a["terms"] & b["terms"]
            if not shared:
                continue
            if {a["modality"], b["modality"]} == {"requires", "forbids"}:
                found.append({"kind": "requirement_conflict", "statements": [_cite(a), _cite(b)]})
                continue
            units_a = {unit: value for value, unit in a["numbers"]}
            units_b = {unit: value for value, unit in b["numbers"]}
            for unit in units_a.keys() & units_b.keys():
                if units_a[unit] != units_b[unit]:
                    found.append({"kind": "threshold_conflict", "unit": unit or "number",
                                  "statements": [_cite(a), _cite(b)]})
                    break
    return found


def _question(kind: str, target: str, prompt: str, citations: list[dict[str, Any]],
              contradictions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"question_id": "q_" + _digest(kind, target), "kind": kind, "target_id": target, "prompt": prompt,
            "citations": [_cite(c) for c in citations], "contradictions": contradictions or [],
            "allowed_decisions": list(DECISIONS)}


def clarify(record: dict[str, Any], request: ClarificationRequest,
            decisions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Questions for one exact process revision; answered questions are not asked again."""
    model = ProcessModel.model_validate(record["process_model"])
    pool = statements(request.documents)
    questions: list[dict[str, Any]] = []
    reported: set[frozenset[str]] = set()

    def fresh(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Each conflicting statement pair is asked about once, on its first subject.
        out = []
        for conflict in conflicts:
            pair = frozenset(c["statement_id"] for c in conflict["statements"])
            if pair not in reported:
                reported.add(pair)
                out.append(conflict)
        return out

    for rule in model.business_rules:
        cited = _matches(rule.text, pool)
        conflicts = fresh(_contradictions(cited, rule.text))
        if conflicts:
            questions.append(_question("contradiction", rule.id,
                f"The documents disagree about rule {rule.id!r}: {rule.text[:200]}. Which statement applies?",
                cited, conflicts))
        elif not rule.user_confirmed:
            questions.append(_question("unconfirmed_rule", rule.id,
                f"Confirm, revise or reject rule {rule.id!r}: {rule.text[:200]}"
                + ("" if cited else " (no supporting statement found in the documents)"), cited))
        elif not cited and pool:
            questions.append(_question("uncited_rule", rule.id,
                f"Rule {rule.id!r} is confirmed but no supplied document mentions it. Keep it as is?", []))
    for activity in model.activities:
        if activity.kind not in {"activity", "decision"}:
            continue
        cited = _matches(activity.label, pool)
        conflicts = fresh(_contradictions(cited, activity.label))
        if conflicts:
            questions.append(_question("contradiction", activity.id,
                f"The documents disagree about step {activity.label!r}. Which statement applies?",
                cited, conflicts))
        if not activity.responsibility_label:
            questions.append(_question("responsibility", activity.id,
                f"Who is responsible for step {activity.label!r}?", cited))
    confirmed = {e.terminal_activity_id for e in model.outcome_expectations if e.user_confirmed}
    labels = {a.id: a.label for a in model.activities}
    for terminal in model.terminal_activity_ids:
        if terminal not in confirmed:
            questions.append(_question("outcome", terminal,
                f"What source-verifiable outcome confirms the process ended at {labels.get(terminal, terminal)!r}?",
                _matches(labels.get(terminal, ""), pool)))
    answered = decisions or {}
    open_questions = [q for q in questions if q["question_id"] not in answered]
    return {
        "format": "openworkgraph.process-clarification.v1",
        "workflow_id": record["workflow_id"], "revision": record["revision"],
        "content_sha256": record["content_sha256"],
        "documents_sha256": documents_sha256(request.documents),
        "decisions_are_pinned_to_exact_documents": True,
        "documents": [{"title": d.title, "characters": len(d.text)} for d in request.documents],
        "statement_count": len(pool),
        "instruction_like_statements": [_cite(s) for s in pool if s["instruction_like"]][:20],
        "questions": open_questions, "answered_question_count": len(questions) - len(open_questions),
        "documents_are_untrusted_data": True, "model_changed": False, "permissions_changed": False,
    }



def validate_decision_evidence(question: dict[str, Any], decision: Decision) -> None:
    """Do not silently treat both sides of a policy conflict as endorsed.

    Rejecting/deferring may leave a conflict unresolved. Resolving it must
    choose one governing cited statement from the disputed source set with a named human
    explanation. A decision is still only a ledger entry, never executable
    policy or a change to the saved process model.
    """
    if question.get("kind") != "contradiction" or decision.decision not in ("confirm", "revise"):
        return
    chosen = decision.cited_statement_ids
    disputed = {item["statement_id"]
                for conflict in question.get("contradictions", [])
                for item in conflict["statements"]}
    if len(chosen) != 1 or chosen[0] not in disputed:
        raise ValueError("choose one governing statement from the disputed sources")
    if not decision.answer.strip():
        raise ValueError("explain why this selected source governs the discrepancy")



def proposed_change(record: dict[str, Any], question: dict[str, Any], decision: Decision) -> dict[str, Any] | None:
    """A model change the owner may save; never applied automatically."""
    if decision.decision != "confirm" or question["kind"] != "unconfirmed_rule":
        return None
    model = dict(record["process_model"])
    model["business_rules"] = [dict(rule, user_confirmed=True) if rule["id"] == question["target_id"] else rule
                               for rule in model["business_rules"]]
    return {"workflow_id": record["workflow_id"], "expected_revision": record["revision"],
            "process_model": model, "apply_with": "POST /v1/process-catalog (normal save, new revision)"}


__all__ = ["ClarificationRequest", "DECISIONS", "Decision", "Document", "clarify", "documents_sha256", "proposed_change", "statements", "validate_decision_evidence"]
