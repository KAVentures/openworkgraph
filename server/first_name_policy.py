from __future__ import annotations

"""v0.37 first-name confidence policy for presentation redaction.

This module installs a presentation-only wrapper around ``server.presentation``.
Capture, storage, contextualization, analytics and task inference remain untouched.

Identity policy:
- OWNER aliases are always rendered as OWNER.
- Strong full identities can retain stable PERSON_x tokens.
- First-name-only mentions are rendered as generic PERSON, never PERSON_x.
- Shared first names may point to multiple stable identities in the local registry.
- Ambiguous word-like names (May, Bill, Mark, Rose, Hope, ...) require stronger
  person context so workflow text is not corrupted.
"""

import json
import re
import threading
from typing import Any

AMBIGUOUS_SINGLE_NAME_WORDS = {
    "april", "august", "bill", "charity", "chase", "dawn", "faith", "grace",
    "grant", "hope", "hunter", "jordan", "joy", "june", "lane", "mark", "may",
    "paris", "pat", "reed", "robin", "rose", "sage", "summer", "will",
}

PERSON_FIELDS = {
    "sender", "recipient", "contact", "owner", "person", "display_name",
    "participant", "attendee", "assignee", "assigned_to",
}

PROTECTED_FIELDS = {
    "event_id", "session_id", "device_id", "sensor_id", "organization_id",
    "actor_id", "schema_version", "browser_session_id", "work_session_id",
    "observed_at", "generated_at", "run_started_at",
}


# Identities learned while *serving* data (read paths never write the registry
# file) are kept here, per data directory, for the life of the process. Without
# this, "Anna Svensson <anna@...>" shown in one response was forgotten by the
# next response, so a later title containing "Anna Svensson" leaked.
_MEMORY_LOCK = threading.RLock()
_MEMORY_REGISTRY: dict[str, dict[str, set[str]]] = {}
_MEMORY_VERSION = [0]


def memory_registry(presentation: Any) -> dict[str, set[str]]:
    with _MEMORY_LOCK:
        return {k: set(v) for k, v in _MEMORY_REGISTRY.get(str(presentation._data_dir()), {}).items()}


def memory_version() -> int:
    return _MEMORY_VERSION[0]


def clear_memory_registry(presentation: Any | None = None) -> None:
    with _MEMORY_LOCK:
        if presentation is None:
            _MEMORY_REGISTRY.clear()
        else:
            _MEMORY_REGISTRY.pop(str(presentation._data_dir()), None)
        _MEMORY_VERSION[0] += 1


def _remember_in_memory(presentation: Any, key: str, token: str) -> None:
    with _MEMORY_LOCK:
        scoped = _MEMORY_REGISTRY.setdefault(str(presentation._data_dir()), {})
        tokens = scoped.setdefault(key, set())
        if token not in tokens:
            tokens.add(token)
            _MEMORY_VERSION[0] += 1


def build_redactor(presentation: Any, *, persist_registry: bool = False):
    """Build the first-name redactor without mutating module globals."""

    def load_registry() -> dict[str, set[str]]:
        out = load_file_registry()
        for key, tokens in memory_registry(presentation).items():
            out.setdefault(key, set()).update(tokens)
        return out

    def load_file_registry() -> dict[str, set[str]]:
        try:
            data = json.loads(presentation._people_registry_path().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            out: dict[str, set[str]] = {}
            for key, value in data.items():
                if not isinstance(key, str):
                    continue
                # v0.35/v0.36 stored one string. Accept it as a one-candidate set.
                if isinstance(value, str) and value:
                    out[key] = {value}
                elif isinstance(value, list):
                    tokens = {str(v) for v in value if isinstance(v, str) and v}
                    if tokens:
                        out[key] = tokens
            return out
        except Exception:
            return {}

    def save_registry(registry: dict[str, set[str]]) -> None:
        try:
            presentation._save_people_registry_data(
                {k: sorted(v) for k, v in registry.items() if v}
            )
        except Exception:
            pass

    def name_words(value: str) -> list[str]:
        return presentation._name_words(value)

    def remember_alias(alias: str, token: str, registry: dict[str, set[str]]) -> None:
        cleaned = presentation._clean_name_candidate(alias)
        if not cleaned or not token:
            return
        variants = [cleaned]
        words = name_words(cleaned)
        if (
            len(words) > 1
            and len(words[0]) >= 3
            and words[0].casefold() not in presentation.NON_NAME_WORDS
        ):
            variants.append(words[0])

        changed = False
        for variant in variants:
            key = presentation._alias_hash(variant)
            tokens = registry.setdefault(key, set())
            if token not in tokens:
                tokens.add(token)
                changed = True
            if not persist_registry:
                _remember_in_memory(presentation, key, token)
        if changed and persist_registry:
            # Persist only file-backed entries; memory-only aliases stay in memory.
            file_registry = load_file_registry()
            for variant in variants:
                file_registry.setdefault(presentation._alias_hash(variant), set()).add(token)
            save_registry(file_registry)

    def registry_replacement(alias: str, registry: dict[str, set[str]]) -> str | None:
        tokens = registry.get(presentation._alias_hash(alias)) or set()
        if not tokens:
            return None
        # Never pretend a bare first name identifies one particular person.
        if len(name_words(alias)) == 1:
            return "PERSON"
        if len(tokens) == 1:
            return next(iter(tokens))
        # Even a full-name collision is better represented generically than
        # falsely linked to one identity.
        return "PERSON"

    def merge_alias(aliases: dict[str, str], alias: str, replacement: str) -> None:
        key = presentation._clean_name_candidate(alias).casefold()
        if not key:
            return
        old = aliases.get(key)
        if old is None:
            aliases[key] = replacement
        elif old != replacement:
            aliases[key] = "PERSON"

    def strong_person_cue(text: str, candidate: str) -> bool:
        name = re.escape(candidate)
        prefix = (
            r"(?:reply\s+to|from|to|cc|bcc|sender|recipient|contact|owner|"
            r"participant|attendee|assignee|assigned\s+to|meeting\s+with|"
            r"call\s+with|message\s+from|email\s+from|"
            r"(?:select|deselect)(?:\s+(?:email|message))?(?:\s+from)?)"
            r"\s*[:\-]?\s+"
        )
        return bool(re.search(rf"\b{prefix}{name}\b", text, flags=re.IGNORECASE))

    def single_should_redact(
        text: str,
        candidate: str,
        *,
        field_name: str,
        name_sensitive_context: bool,
    ) -> bool:
        if field_name.casefold() in PERSON_FIELDS:
            return True
        if strong_person_cue(text, candidate):
            return True
        if not name_sensitive_context:
            return False
        if candidate.casefold() in AMBIGUOUS_SINGLE_NAME_WORDS:
            return False
        # This branch only applies to a name already learned from stronger
        # evidence in the current payload or local hashed registry.
        return True

    def discover_aliases(value: Any) -> dict[str, str]:
        aliases: dict[str, str] = {}
        registry = load_registry()

        def process_text(text: str, *, email_context: bool) -> None:
            for match in presentation.DISPLAY_EMAIL_RE.finditer(text):
                _prefix, name = presentation._tail_name_candidate(match.group("name"))
                email = match.group("email").casefold()
                if name:
                    token = presentation._token("PERSON", email)
                    merge_alias(aliases, name, "PERSON" if len(name_words(name)) == 1 else token)
                    remember_alias(name, token, registry)

            for email_match in presentation.EMAIL_RE.finditer(text):
                email = email_match.group(1)
                local = email.split("@", 1)[0].casefold()
                if local in presentation.GENERIC_LOCALPARTS:
                    continue
                parts = [x for x in re.split(r"[._-]+", local) if x.isalpha() and len(x) > 1]
                if 2 <= len(parts) <= 4:
                    alias = " ".join(parts)
                    token = presentation._token("PERSON", email.casefold())
                    merge_alias(aliases, alias, token)
                    remember_alias(alias.title(), token, registry)

            for match in presentation.CUE_RE.finditer(text):
                tail = text[match.end():]
                words: list[str] = []
                for raw in re.findall(r"[^\s,;<>|()]+", tail)[:4]:
                    candidate = raw.strip(" \t\r\n\"'[]{}:-")
                    if not candidate:
                        break
                    tentative = " ".join(words + [candidate])
                    if presentation._looks_like_person_name(tentative, allow_single=True):
                        words.append(candidate)
                    else:
                        break
                if words and presentation._looks_like_person_name(" ".join(words), allow_single=True):
                    name = " ".join(words)
                    if len(name_words(name)) == 1:
                        merge_alias(aliases, name, "PERSON")
                    else:
                        key = presentation._clean_name_candidate(name).casefold()
                        known = aliases.get(key) or registry_replacement(name, registry)
                        if known:
                            # Stronger evidence in this payload (for example a
                            # display-name + email pair) already established the
                            # identity. Reuse it instead of minting a second token
                            # from the plain name and turning one person into an
                            # artificial collision.
                            merge_alias(aliases, name, known)
                            if known != "PERSON":
                                remember_alias(name, known, registry)
                        else:
                            token = presentation._token("PERSON", name)
                            merge_alias(aliases, name, token)
                            remember_alias(name, token, registry)

            if email_context:
                for match in presentation.EMAIL_SELECT_RE.finditer(text):
                    name = presentation._clean_name_candidate(match.group("name"))
                    if not presentation._looks_like_person_name(name, allow_single=True):
                        continue
                    if len(name_words(name)) == 1:
                        merge_alias(aliases, name, "PERSON")
                    else:
                        key = presentation._clean_name_candidate(name).casefold()
                        known = aliases.get(key) or registry_replacement(name, registry)
                        if known:
                            merge_alias(aliases, name, known)
                            if known != "PERSON":
                                remember_alias(name, known, registry)
                        else:
                            token = presentation._token("PERSON", name)
                            merge_alias(aliases, name, token)
                            remember_alias(name, token, registry)

        def visit(item: Any, *, email_context: bool = False) -> None:
            if isinstance(item, dict):
                scoped = email_context or presentation._contains_email_context(item)
                for child in item.values():
                    visit(child, email_context=scoped)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child, email_context=email_context)
            elif isinstance(item, str):
                process_text(item, email_context=email_context)

        visit(value)
        return aliases

    def replace_aliases(
        text: str,
        aliases: dict[str, str],
        *,
        field_name: str = "",
        name_sensitive_context: bool = False,
        force_single: bool = False,
    ) -> str:
        out = text
        for alias, replacement in sorted(aliases.items(), key=lambda x: len(x[0]), reverse=True):
            if not alias:
                continue
            if (
                len(name_words(alias)) == 1
                and not force_single
                and not single_should_redact(
                    out,
                    alias,
                    field_name=field_name,
                    name_sensitive_context=name_sensitive_context,
                )
            ):
                continue
            out = re.sub(
                rf"(?<!\w){re.escape(alias)}(?!\w)",
                replacement,
                out,
                flags=re.IGNORECASE,
            )
        return out

    def replace_known_people(
        text: str,
        registry: dict[str, set[str]],
        *,
        field_name: str,
        name_sensitive_context: bool,
    ) -> str:
        matches = list(presentation.WORD_RE.finditer(text))
        if not matches or not registry:
            return text

        replacements: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []
        for size in (4, 3, 2, 1):
            for i in range(0, len(matches) - size + 1):
                start = matches[i].start()
                end = matches[i + size - 1].end()
                if any(not (end <= a or start >= b) for a, b in occupied):
                    continue
                gaps = [
                    text[matches[j].end():matches[j + 1].start()]
                    for j in range(i, i + size - 1)
                ]
                if any(not gap.isspace() for gap in gaps):
                    continue
                candidate = text[start:end]
                replacement = registry_replacement(candidate, registry)
                if not replacement:
                    continue
                if size == 1 and not single_should_redact(
                    text,
                    candidate,
                    field_name=field_name,
                    name_sensitive_context=name_sensitive_context,
                ):
                    continue
                replacements.append((start, end, replacement))
                occupied.append((start, end))

        out = text
        for start, end, replacement in sorted(replacements, reverse=True):
            out = out[:start] + replacement + out[end:]
        return out

    def redact_embedded_names(
        text: str,
        *,
        owner_aliases: dict[str, str],
        registry: dict[str, set[str]],
    ) -> str:
        replacements: list[tuple[int, int, str]] = []
        for start, end, candidate in presentation._embedded_name_spans(text):
            if candidate.casefold() in owner_aliases:
                replacement = "OWNER"
            else:
                replacement = registry_replacement(candidate, registry)
                if replacement is None:
                    replacement = presentation._token("PERSON", candidate)
            replacements.append((start, end, replacement))
        out = text
        for start, end, replacement in sorted(replacements, reverse=True):
            out = out[:start] + replacement + out[end:]
        return out

    def redact_text(
        text: str,
        *,
        aliases: dict[str, str],
        owner_aliases: dict[str, str],
        owner_emails: set[str],
        owner_phones: set[str],
        registry: dict[str, set[str]],
        email_context: bool,
        name_sensitive_context: bool,
        field_name: str,
    ) -> str:
        if not text:
            return text
        if re.fullmatch(
            r"(?:OWNER|OWNER_EMAIL|OWNER_PHONE|PERSON|PERSON_[0-9A-F]{6}|"
            r"EMAIL_[0-9A-F]{6}|PHONE_[0-9A-F]{6})",
            text,
        ):
            return text

        out = presentation.DISPLAY_EMAIL_RE.sub(
            lambda m: presentation._redact_display_email(
                m,
                owner_aliases=owner_aliases,
                owner_emails=owner_emails,
            ),
            text,
        )
        out = presentation.EMAIL_RE.sub(
            lambda m: (
                "OWNER_EMAIL"
                if m.group(1).casefold() in owner_emails
                else presentation._token("EMAIL", m.group(1).casefold())
            ),
            out,
        )

        def phone_replacement(match: re.Match[str]) -> str:
            value = match.group(0)
            digits = re.sub(r"\D", "", value)
            if not 7 <= len(digits) <= 15:
                return value
            if re.fullmatch(r"20\d{2}[ -]\d{1,2}[ -]\d{1,2}", value.strip()):
                return value
            return (
                "OWNER_PHONE"
                if digits in owner_phones
                else presentation._token("PHONE", digits)
            )

        out = presentation.PHONE_CANDIDATE_RE.sub(phone_replacement, out)

        # Owner aliases are explicit/local identity and can safely match globally.
        out = replace_aliases(out, owner_aliases, force_single=True)
        out = replace_aliases(
            out,
            aliases,
            field_name=field_name,
            name_sensitive_context=(email_context or name_sensitive_context),
        )
        out = replace_known_people(
            out,
            registry,
            field_name=field_name,
            name_sensitive_context=(email_context or name_sensitive_context),
        )

        if (
            field_name.casefold() in PERSON_FIELDS
            and presentation._looks_like_person_name(out, allow_single=True)
        ):
            if out.casefold() in owner_aliases:
                out = "OWNER"
            elif len(name_words(out)) == 1:
                out = "PERSON"
            else:
                out = registry_replacement(out, registry) or presentation._token("PERSON", out)

        if email_context:
            out = presentation._redact_email_title_segments(
                out,
                owner_aliases=owner_aliases,
            )

        if (
            (email_context or name_sensitive_context)
            and field_name.casefold() in presentation.TITLEISH_FIELDS
        ):
            out = redact_embedded_names(
                out,
                owner_aliases=owner_aliases,
                registry=registry,
            )
        return out

    def redact_for_display(value: Any) -> Any:
        owner_aliases, owner_emails, owner_phones = presentation._owner_identity()
        aliases = discover_aliases(value)
        registry = load_registry()

        def transform(
            item: Any,
            *,
            inherited_email_context: bool = False,
            inherited_name_sensitive_context: bool = False,
            field_name: str = "",
        ) -> Any:
            if isinstance(item, dict):
                email_context = (
                    inherited_email_context
                    or presentation._contains_email_context(item)
                )
                name_context = (
                    inherited_name_sensitive_context
                    or presentation._contains_name_sensitive_context(item)
                )
                return {
                    key: transform(
                        val,
                        inherited_email_context=email_context,
                        inherited_name_sensitive_context=name_context,
                        field_name=str(key),
                    )
                    for key, val in item.items()
                }
            if isinstance(item, list):
                return [
                    transform(
                        value,
                        inherited_email_context=inherited_email_context,
                        inherited_name_sensitive_context=inherited_name_sensitive_context,
                        field_name=field_name,
                    )
                    for value in item
                ]
            if isinstance(item, tuple):
                return tuple(
                    transform(
                        value,
                        inherited_email_context=inherited_email_context,
                        inherited_name_sensitive_context=inherited_name_sensitive_context,
                        field_name=field_name,
                    )
                    for value in item
                )
            if isinstance(item, str):
                if field_name in PROTECTED_FIELDS:
                    return item
                return redact_text(
                    item,
                    aliases=aliases,
                    owner_aliases=owner_aliases,
                    owner_emails=owner_emails,
                    owner_phones=owner_phones,
                    registry=registry,
                    email_context=inherited_email_context,
                    name_sensitive_context=inherited_name_sensitive_context,
                    field_name=field_name,
                )
            return item

        return transform(value)

    return redact_for_display


def install(presentation: Any) -> None:
    """Backward-compatible installer; production uses privacy_pipeline explicitly."""
    previous = presentation.redact_for_display
    if getattr(previous, "_openworkgraph_first_name_policy", False):
        return
    redactor = build_redactor(presentation)
    redactor._openworkgraph_first_name_policy = True  # type: ignore[attr-defined]
    presentation.redact_for_display = redactor
