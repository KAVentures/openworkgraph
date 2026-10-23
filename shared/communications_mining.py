"""Communications mining over explicitly selected email and ticket exports.

Messages are parsed, redacted and classified in memory and then discarded:
the analysis keeps only aggregates (intent counts, entity-type counts,
volumes, thread timings and frequent themes). Intents come from a
transparent, user-editable keyword taxonomy; every classification can be
ambiguous or unclassified, and none is a verified reason for contact.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import html
import io
import mailbox
import re
from statistics import median
from typing import IO, Any, Iterable, Iterator

from shared.core.sensitive_identifiers import redact_sensitive_identifiers

FORMAT = "openworkgraph.communications-taxonomy.v1"
MAX_MESSAGES = 50000
MAX_TEXT_CHARS = 20000
MAX_INTENTS = 40
MAX_TERMS_PER_INTENT = 60
MIN_THEME_MESSAGES = 5
MIN_DOMAIN_MESSAGES = 5

DEFAULT_TAXONOMY: dict[str, Any] = {
    "format": FORMAT,
    "intents": [
        {"name": "Refund request", "terms": ["refund", "money back", "reimburse", "återbetalning", "pengarna tillbaka"]},
        {"name": "Cancellation", "terms": ["cancel", "cancellation", "terminate", "unsubscribe", "avsluta", "säga upp", "uppsägning", "avboka"]},
        {"name": "Billing or invoice", "terms": ["invoice", "billing", "charged", "charge", "payment", "receipt", "faktura", "betalning", "kvitto", "debiterad"]},
        {"name": "Order or delivery status", "terms": ["where is my order", "delivery", "shipping", "tracking", "shipped", "not arrived", "leverans", "beställning", "spårning", "paket"]},
        {"name": "Complaint", "terms": ["complaint", "unacceptable", "disappointed", "terrible", "angry", "klagomål", "reklamation", "missnöjd", "besviken"]},
        {"name": "Technical issue", "terms": ["error", "bug", "not working", "doesn't work", "broken", "crash", "fel", "fungerar inte", "krasch"]},
        {"name": "Account access", "terms": ["password", "log in", "login", "locked out", "reset", "two-factor", "lösenord", "logga in", "inloggning"]},
        {"name": "Change of details", "terms": ["change my address", "update my", "change my", "new address", "ändra", "uppdatera", "ny adress"]},
        {"name": "Scheduling", "terms": ["meeting", "schedule", "reschedule", "appointment", "available", "möte", "boka", "tid för"]},
        {"name": "Information request", "terms": ["question", "wondering", "could you tell", "information about", "how do i", "fråga", "undrar", "hur gör jag"]},
    ],
}

_ENTITY_PATTERNS = {
    "order_reference": re.compile(r"\b(?:order|ord|beställning|ordernummer)\s*(?:no\.?|nr\.?|number|#|:)?\s*[A-Z0-9-]{4,}\b", re.I),
    "invoice_reference": re.compile(r"\b(?:invoice|inv|faktura|fakturanummer)\s*(?:no\.?|nr\.?|number|#|:)?\s*[A-Z0-9-]{4,}\b", re.I),
    "ticket_reference": re.compile(r"(?:\b(?:ticket|case|ärende)\s*(?:no\.?|nr\.?|#|:)?\s*\d{3,}\b)|(?<![\w&])#\d{4,}\b", re.I),
    "money_amount": re.compile(r"(?:[$€£]\s?\d[\d ,.]*\d|\b\d[\d ,.]*\s?(?:kr|sek|eur|usd|gbp|nok|dkk)\b)", re.I),
    "date": re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b"),
    "email_address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://[^\s<>\"]+"),
    "phone_number": re.compile(r"(?<![\w])(?:\+\d{1,3}[ -]?)?(?:\(?\d{2,4}\)?[ -]?)\d{2,4}[ -]?\d{2,4}(?:[ -]?\d{2,4})?(?![\w])"),
}
# Every pattern the identifier redactor handles contains one of these (a digit run, "@", a URL scheme,
# a secret keyword or a known token prefix); text without any of them is left as is, which it would be anyway.
_MAY_HOLD_IDENTIFIER = re.compile(
    r"\d{4}|@|://|bearer|token|secret|passw|pwd|api[_-]?key|access[_-]?key|private|akia|asia|sk-|gh[pousr]_|"
    r"github_pat|xox[baprs]-|_live_|aiza|eyj|-----begin", re.I)
_REDACTION_TOKEN = re.compile(r"\b([A-Z][A-Z_]*[A-Z])_[0-9A-F]{6}\b")
_WORD = re.compile(r"[a-zåäöéüæø]+(?:'[a-z]+)?", re.I)
_QUOTE_HEADER = re.compile(r"^(?:on .{0,200} wrote:|den .{0,200} skrev .{0,200}:|-{2,} ?original message ?-{2,}|from: .+)$",
                           re.I | re.M)
_STOPWORDS = frozenset("""a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its just me more most my no nor not now of off on once only or other our out
over own same she should so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours hi hello dear thanks
thank regards best kind please also get got one us let know am pm re fw fwd sent via och att det är som en på för med
till av inte har jag du vi ni de den ett om men så kan vill hej tack mvh vänliga hälsningar här från eller nu bara""".split())


def _plausible_phone(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    return 8 <= len(digits) <= 15 and candidate.lstrip("(").startswith(("+", "0"))


class CommunicationsError(ValueError):
    """Safe user-facing error that never echoes message content."""


@dataclass
class Message:
    timestamp: datetime | None
    text: str
    thread: str = ""
    sender_domain: str = ""
    direction: str = ""   # inbound / outbound / ""
    channel: str = ""


@dataclass
class _Intent:
    name: str
    phrases: list[tuple[str, ...]] = field(default_factory=list)


_TOKEN = re.compile(r"[\w']+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


# ---------------------------------------------------------------- taxonomy

def parse_taxonomy(spec: Any) -> list[_Intent]:
    if not isinstance(spec, dict) or spec.get("format") != FORMAT or not isinstance(spec.get("intents"), list):
        raise CommunicationsError(f"taxonomy must use format {FORMAT} with a list of intents")
    if not 0 < len(spec["intents"]) <= MAX_INTENTS:
        raise CommunicationsError(f"taxonomy needs 1 to {MAX_INTENTS} intents")
    intents, names = [], set()
    for item in spec["intents"]:
        if not isinstance(item, dict) or set(item) - {"name", "terms"}:
            raise CommunicationsError("each intent has a name and terms")
        name, terms = item.get("name"), item.get("terms")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 80 or name.strip().lower() in names:
            raise CommunicationsError("intent names must be short and unique")
        if name.strip().lower() in ("ambiguous", "unclassified"):
            raise CommunicationsError("ambiguous and unclassified are reserved")
        if not isinstance(terms, list) or not 0 < len(terms) <= MAX_TERMS_PER_INTENT or \
                not all(isinstance(t, str) and 0 < len(t.strip()) <= 80 for t in terms):
            raise CommunicationsError(f"each intent needs 1 to {MAX_TERMS_PER_INTENT} short terms")
        names.add(name.strip().lower())
        phrases = [tuple(_tokens(t)) for t in dict.fromkeys(terms)]
        if not all(phrases):
            raise CommunicationsError("each term needs at least one letter or digit")
        intents.append(_Intent(name.strip(), list(dict.fromkeys(phrases))))
    return intents


def classify(text: str, intents: list[_Intent]) -> tuple[str, dict[str, int]]:
    """Intent with the highest matched-term score; ties are ambiguous, no match is unclassified.

    Terms match whole words in order (an n-gram of the message's words).
    """
    words = _tokens(text)
    longest = max((len(p) for i in intents for p in i.phrases), default=1)
    present = {tuple(words[k:k + n]) for n in range(1, longest + 1) for k in range(len(words) - n + 1)}
    # Longer phrases are more specific, so a matched phrase scores its word count.
    scores = {i.name: sum(len(p) for p in i.phrases if p in present) for i in intents}
    scores = {k: v for k, v in scores.items() if v}
    if not scores:
        return "unclassified", {}
    best = max(scores.values())
    top = [k for k, v in scores.items() if v == best]
    return (top[0] if len(top) == 1 else "ambiguous"), scores


# ---------------------------------------------------------------- parsing

def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)) if "<" in text and ">" in text else text
    match = _QUOTE_HEADER.search(text)
    if match:
        text = text[:match.start()]
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def _domain(address: str) -> str:
    _, _, domain = address.rpartition("@")
    return domain.strip().lower().strip(">") if "@" in address else ""


def _thread_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16] if value else ""


def _email_message(raw: Any, own_domains: set[str]) -> Message:
    subject = str(raw.get("subject") or "")
    body = ""
    try:
        part = raw.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part is not None else ""
    except Exception:  # malformed MIME parts are skipped, never fatal
        body = ""
    stamp = None
    try:
        stamp = parsedate_to_datetime(str(raw.get("date") or ""))
        if stamp.tzinfo is None:
            stamp = None
    except (TypeError, ValueError, IndexError):
        stamp = None
    senders = getaddresses([str(raw.get("from") or "")])
    domain = _domain(senders[0][1]) if senders else ""
    references = str(raw.get("references") or "").split()
    root = references[0] if references else str(raw.get("in-reply-to") or raw.get("message-id") or "").strip()
    direction = ("outbound" if domain in own_domains else "inbound") if own_domains and domain else ""
    return Message(stamp.astimezone(timezone.utc) if stamp else None, _clean(subject + "\n" + body),
                   _thread_key(root), domain, direction, "email")


def read_mbox(path: str, own_domains: Iterable[str] = ()) -> Iterator[Message]:
    own = {d.strip().lower() for d in own_domains if d.strip()}
    box = mailbox.mbox(path, factory=lambda f: BytesParser(policy=policy.default).parse(f), create=False)
    try:
        for raw in box:
            yield _email_message(raw, own)
    finally:
        box.close()


def read_eml(stream: IO[bytes], own_domains: Iterable[str] = ()) -> Iterator[Message]:
    own = {d.strip().lower() for d in own_domains if d.strip()}
    yield _email_message(BytesParser(policy=policy.default).parse(stream), own)


def read_csv(stream: IO[str], *, text_columns: list[str], time_column: str = "", thread_column: str = "",
             direction_column: str = "", channel_column: str = "", delimiter: str = ",") -> Iterator[Message]:
    if delimiter not in {",", ";", "\t", "|"}:
        raise CommunicationsError("unsupported delimiter")
    reader = csv.DictReader(stream, delimiter=delimiter)
    columns = set(reader.fieldnames or [])
    wanted = set(text_columns) | {c for c in (time_column, thread_column, direction_column, channel_column) if c}
    if not text_columns or wanted - columns:
        raise CommunicationsError("the selected columns are not all in the file")
    for row in reader:
        stamp = None
        if time_column:
            value = (row.get(time_column) or "").strip()
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                stamp = parsed.astimezone(timezone.utc) if parsed.tzinfo else None
            except ValueError:
                stamp = None
        direction = (row.get(direction_column) or "").strip().lower() if direction_column else ""
        direction = {"in": "inbound", "incoming": "inbound", "inbound": "inbound", "customer": "inbound",
                     "out": "outbound", "outgoing": "outbound", "outbound": "outbound", "agent": "outbound"}.get(direction, "")
        yield Message(stamp, _clean("\n".join(row.get(c) or "" for c in text_columns)),
                      _thread_key((row.get(thread_column) or "").strip()) if thread_column else "", "",
                      direction, (row.get(channel_column) or "").strip()[:40] if channel_column else "")


# ---------------------------------------------------------------- analysis

def _themes(texts: list[list[str]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for words in texts:
        counts.update({" ".join(pair) for pair in zip(words, words[1:])})
    return [{"terms": theme, "messages": n} for theme, n in counts.most_common(200) if n >= MIN_THEME_MESSAGES][:20]


def analyze(messages: Iterable[Message], taxonomy: Any = None) -> dict[str, Any]:
    intents = parse_taxonomy(taxonomy or DEFAULT_TAXONOMY)
    intent_counts: Counter[str] = Counter()
    entity_counts: Counter[str] = Counter()
    entity_messages: Counter[str] = Counter()
    entity_by_intent: dict[str, Counter[str]] = defaultdict(Counter)
    weekly: dict[str, Counter[str]] = defaultdict(Counter)
    channels: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    second_choice: Counter[str] = Counter()
    unclassified_words: list[list[str]] = []
    threads: dict[str, list[tuple[datetime | None, str, str]]] = defaultdict(list)
    total = undated = 0
    events: list[tuple[str, str, datetime, str]] = []
    for message in messages:
        total += 1
        if total > MAX_MESSAGES:
            raise CommunicationsError(f"at most {MAX_MESSAGES} messages per analysis")
        text = redact_sensitive_identifiers(message.text) if _MAY_HOLD_IDENTIFIER.search(message.text) else message.text
        # Replies are counted, not classified: the intent belongs to the inbound contact.
        label, scores = ("outbound reply", {}) if message.direction == "outbound" else classify(text, intents)
        intent_counts[label] += 1
        if label == "ambiguous":
            for name in scores:
                second_choice[name] += 1
        found = set()
        claimed: list[tuple[int, int]] = []
        for kind, pattern in _ENTITY_PATTERNS.items():
            spans = [m.span() for m in pattern.finditer(text)]
            if kind == "phone_number":
                spans = [sp for sp in spans if _plausible_phone(text[sp[0]:sp[1]])]
            # A span already counted (an order or invoice reference) is not counted again.
            spans = [sp for sp in spans if not any(a < sp[1] and sp[0] < b for a, b in claimed)]
            claimed += spans
            if spans:
                entity_counts[kind] += len(spans)
                found.add(kind)
        for token in _REDACTION_TOKEN.findall(text):
            kind = token.lower()
            entity_counts[kind] += 1
            found.add(kind)
        for kind in found:
            entity_messages[kind] += 1
            entity_by_intent[label][kind] += 1
        if label == "unclassified":
            words = [w.lower() for w in _WORD.findall(_REDACTION_TOKEN.sub(" ", text))]
            unclassified_words.append([w for w in words if w not in _STOPWORDS and len(w) > 2])
        if message.timestamp:
            year, week, _ = message.timestamp.isocalendar()
            weekly[f"{year}-W{week:02d}"][label] += 1
        else:
            undated += 1
        channels[message.channel or "unspecified"] += 1
        directions[message.direction or "unknown"] += 1
        if message.sender_domain:
            domains[message.sender_domain] += 1
        if message.thread:
            threads[message.thread].append((message.timestamp, message.direction, label))
            if message.timestamp:
                events.append((message.thread, label, message.timestamp, message.direction or "unknown"))
    if not total:
        raise CommunicationsError("no messages found")
    classified = total - intent_counts["outbound reply"]
    responses, recontacts, sizes = [], 0, []
    for items in threads.values():
        sizes.append(len(items))
        dated = sorted((t, d) for t, d, _ in items if t)
        first_in = next((t for t, d in dated if d == "inbound"), None)
        if first_in:
            reply = next((t for t, d in dated if d == "outbound" and t >= first_in), None)
            if reply:
                responses.append((reply - first_in).total_seconds())
        if sum(1 for _, d, _ in items if d == "inbound") > 1:
            recontacts += 1
    shared_domains = {d: n for d, n in domains.most_common() if n >= MIN_DOMAIN_MESSAGES}
    return {
        "messages": total,
        "classified_messages": classified,
        # Shares are of inbound/undirected messages; outbound replies are counted but not classified.
        "intents": [{"intent": k, "messages": n, "share": round(n / classified, 6) if k != "outbound reply" and classified else None}
                    for k, n in intent_counts.most_common()],
        "ambiguous_between": [{"intent": k, "messages": n} for k, n in second_choice.most_common(20)],
        "entities": [{"type": k, "mentions": entity_counts[k], "messages": entity_messages[k]}
                     for k in sorted(entity_counts, key=lambda k: (-entity_messages[k], k))],
        "entities_by_intent": {k: dict(v) for k, v in entity_by_intent.items()},
        "weekly_volume": [{"week": w, "messages": sum(c.values()), "by_intent": dict(c)} for w, c in sorted(weekly.items())],
        "undated_messages": undated,
        "channels": dict(channels.most_common(20)),
        "directions": dict(directions),
        "sender_domains": dict(list(shared_domains.items())[:20]),
        "sender_domains_below_threshold": sum(n for d, n in domains.items() if d not in shared_domains),
        "threads": {"threads": len(threads), "median_messages_per_thread": median(sizes) if sizes else None,
                    "threads_with_repeat_inbound": recontacts,
                    "first_response_seconds": {"median": median(responses), "count": len(responses)} if responses else None},
        "unclassified_themes": _themes(unclassified_words),
        "_events": events,
        "method": "keyword taxonomy after identifier redaction; ties are ambiguous",
        "raw_text_retained": False,
        "entity_values_returned": False,
        "intents_are_keyword_matches_not_verified_reasons": True,
        "outbound_messages_not_classified": True,
    }


def events_csv(events: list[tuple[str, str, datetime, str]]) -> bytes:
    """Intent-only event log (thread hash, intent, time, direction) for the event-log workspace."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["thread", "intent", "time", "direction"])
    for thread, label, stamp, direction in events:
        writer.writerow([thread, label, stamp.isoformat().replace("+00:00", "Z"), direction])
    return out.getvalue().encode("utf-8")
