"""Opt-in label-topic linking on top of multi-signal work items (roadmap #326 A1).

The structural linker (:mod:`server.work_item_links`) deliberately reads no
titles or UI labels, so it cannot join records that are related only by what
they are about: a renewal email, the renewal account and the renewal price
sheet. When the user chooses to let OWG read the window titles and UI labels it
already captured, this module adds one more signal:

- ``shared_label_topic``: two observations in the same exact capture scope whose
  titles/labels share a distinctive topic word.

A topic word is a lowercase word of three or more letters that is not generic UI
vocabulary ("open", "save", "request", "message"...), not the name of the app or
site that rendered it, and appears in at least two but at most ``max_share`` of
the scope's observations, so a word that is everywhere joins nothing. Rarer
words are applied first.

Guards that keep the signal from contaminating separate work:

- it never merges two components that hold different records of the same
  provider and record kind (two opportunities stay two items);
- it never crosses organization/actor/device/session scope, and observations
  whose scope is missing or malformed take no part;
- reference-tainted rows (conflicting record claims) take no part.

Topic words are used only inside this function. They are never returned,
stored or logged: the output lists membership and counts, not words.
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from .work_item_links import link_observations

FORMAT = "openworkgraph.label-topic-items.v1"
_WORD = re.compile(r"[a-z]{3,}")
_TITLE_SEPARATORS = re.compile(r"\s+[·|–—-]\s+")
_MAX_TEXT = 300

# Generic UI vocabulary: actions, navigation and container nouns that name what
# a person did on screen rather than what the work was about. This is a fixed
# product lexicon, not derived from any evaluation case.
GENERIC_UI_WORDS = frozenset("""
about account accounts action actions activity add added all and any app apply approve approved archive
are back browse button cancel change changed check close closed comment comments compose confirm continue
copy copied create created dashboard delete deleted detail details dialog done download draft drafts edit
edited enter export file files filter find for form forward from get help history home import inbox info
item items list load login logout main menu message messages more move new next note notes notification
open opened options overview page paste pasted preview print profile read record records refresh reject
remove reply report reports request requests resolve resolved return returned review reviewed save saved
search select send sent set settings share show sign start status submit submitted sync tab table the
this ticket tickets untitled update updated upload view window with work your
""".split())


def _texts(event: dict[str, Any]) -> list[str]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    target = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
    page = metadata.get("page") if isinstance(metadata.get("page"), dict) else {}
    values = (event.get("window_title"), event.get("target_label"), target.get("label"), page.get("title"))
    return [value[:_MAX_TEXT] for value in values if isinstance(value, str) and value.strip()]


def _surface_words(event: dict[str, Any], texts: list[str]) -> set[str]:
    """Words naming the app/site, which say where the work happened, not what."""
    words: set[str] = set()
    app = event.get("app")
    if isinstance(app, str):
        words.update(_WORD.findall(app.casefold()))
    for name in ("page_host", "browser_hostname"):
        host = event.get(name)
        if isinstance(host, str):
            words.update(_WORD.findall(host.casefold()))
    for text in texts:
        parts = _TITLE_SEPARATORS.split(text)
        if len(parts) > 1:
            # "Gmail · Renewal request": the leading segment names the surface.
            words.update(_WORD.findall(parts[0].casefold()))
    return words


def topic_words(event: dict[str, Any]) -> set[str]:
    """Distinctive candidate topic words of one observation (internal only)."""
    texts = _texts(event)
    surface = _surface_words(event, texts)
    words: set[str] = set()
    for text in texts:
        parts = _TITLE_SEPARATORS.split(text)
        body = " ".join(parts[1:]) if len(parts) > 1 else text
        words.update(_WORD.findall(body.casefold()))
    return {word for word in words if word not in GENERIC_UI_WORDS and word not in surface}


def label_topic_members(
    events: list[dict[str, Any]], *, max_share: float = 0.6,
) -> dict[str, Any]:
    """Structural members merged by shared label topics, plus merge counts."""
    linked = link_observations(events)
    rows = linked["rows"]
    by_id = {event["event_id"]: event for event in events
             if isinstance(event, dict) and isinstance(event.get("event_id"), str)}
    parent: dict[str, str] = {}
    records: dict[str, dict[tuple[str, str], set[str]]] = {}
    for ids in linked["members"].values():
        root = ids[0]
        kinds: dict[tuple[str, str], set[str]] = defaultdict(set)
        for event_id in ids:
            parent[event_id] = root
        for row in rows:
            if row["event_id"] in ids and row["ref"]:
                kinds[(row["provider"], row["kind"])].add(row["ref"])
        records[root] = kinds

    def find(event_id: str) -> str:
        while parent[event_id] != event_id:
            parent[event_id] = parent[parent[event_id]]
            event_id = parent[event_id]
        return event_id

    merges = vetoed = 0

    def union(left: str, right: str) -> None:
        nonlocal merges, vetoed
        a, b = find(left), find(right)
        if a == b:
            return
        ra, rb = records[a], records[b]
        if any(ra[kind] != rb[kind] for kind in ra.keys() & rb.keys()):
            vetoed += 1
            return
        parent[b] = a
        for kind, refs in rb.items():
            ra.setdefault(kind, set()).update(refs)
        merges += 1

    by_scope: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["scope"] is not None and not row["reference_tainted"]:
            by_scope[row["scope"]].append(row)
    for scoped in by_scope.values():
        occurrences: dict[str, list[str]] = defaultdict(list)
        for row in scoped:
            for word in topic_words(by_id.get(row["event_id"], {})):
                occurrences[word].append(row["event_id"])
        ceiling = max(2, int(len(scoped) * max_share))
        for _, ids in sorted(occurrences.items(), key=lambda pair: (len(pair[1]), pair[0])):
            if 2 <= len(ids) <= ceiling:
                for other in ids[1:]:
                    union(ids[0], other)

    position = {row["event_id"]: index for index, row in enumerate(rows)}
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[find(row["event_id"])].append(row["event_id"])
    members = sorted(groups.values(), key=lambda ids: position[ids[0]])
    return {"members": members, "topic_merges": merges, "record_conflict_vetoes": vetoed}


def label_topic_work_items(
    events: list[dict[str, Any]], *, max_items: int = 20, max_share: float = 0.6,
) -> dict[str, Any]:
    """Privacy-minimized projection: membership and counts, never topic words."""
    result = label_topic_members(events, max_share=max_share)
    linked_groups = [ids for ids in result["members"] if len(ids) >= 2]
    linked_groups.sort(key=lambda ids: (-len(ids), ids[0]))
    bounded = max(1, min(int(max_items), 50))
    return {
        "format": FORMAT,
        "items": [{
            "item_id": f"label_topic_item_{number:02d}",
            "event_ids": ids[:50],
            "event_ids_truncated": len(ids) > 50,
            "observed_event_count": len(ids),
            "association_status": "label_topic_linked",
            "task_identity_inferred": False,
            "business_outcome_verified": False,
        } for number, ids in enumerate(linked_groups[:bounded], start=1)],
        "total_items": len(linked_groups),
        "topic_merges": result["topic_merges"],
        "record_conflict_vetoes": result["record_conflict_vetoes"],
        "unlinked_observation_count": sum(1 for ids in result["members"] if len(ids) == 1),
        "link_contract": {
            "requires_user_opt_in_to_label_reading": True,
            "labels_and_titles_read": True,
            "topic_words_returned": False,
            "urls_and_clipboard_contents_read": False,
            "different_records_of_same_kind_never_merged": True,
            "ephemeral_signals_scope": "exact_organization_actor_device_session",
            "time_alone_never_links": True,
            "linked_items_are_tasks": False,
        },
        "derived": True,
        "authoritative": False,
        "read_only": True,
    }


__all__ = ["FORMAT", "GENERIC_UI_WORDS", "label_topic_members", "label_topic_work_items", "topic_words"]
