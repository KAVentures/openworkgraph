"""Offline, fail-closed preflight for *declared* SCIM IdP attribute mappings.

This does not contact an IdP or OWG, provision users, or certify compatibility.
It models the shipped bounded Users/Groups subset, including descriptive
contact write paths but never email-based identity matching.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 32_768
MAX_JSON_INTEGER_DIGITS = 128
MAX_ATTRIBUTES = 32
USER_MATCH = frozenset({"username", "externalid"})
USER_CREATE = frozenset({
    "username", "externalid", "displayname", "active", "name",
    "name.formatted", "name.familyname", "name.givenname", "name.middlename",
    "name.honorificprefix", "name.honorificsuffix", "emails",
})
USER_PATCH = USER_CREATE - {"username"}
GROUP_MATCH = frozenset({"displayname", "externalid"})
GROUP_CREATE = frozenset({"displayname", "externalid", "members"})
GROUP_PATCH = GROUP_CREATE
_CORE_USER_PREFIX = "urn:ietf:params:scim:schemas:core:2.0:User:"


# The shipped contact array is descriptive only. These are PATCH path shapes,
# not User search/matching filters or authenticated identity claims.
_CONTACT_CHILDREN = frozenset({"value", "type", "display", "primary"})
_CONTACT_SELECTOR = re.compile(
    r'emails\[(type|value) +eq +("(?:[^"\\]|\\.)*")\](?:\.(value|type|display|primary))?',
    re.IGNORECASE,
)


def _unqualified_contact_patch_path(path: str) -> str:
    """Mirror the shipped contact parser's exact core User URN stripping.

    Only PATCH contact paths are normalized; never reinterpret matching or
    create declarations, or casefold a quoted selector literal. Mirror the
    shipped parser's lower(), not Unicode casefold(), for URN structure.
    """
    if path[:len(_CORE_USER_PREFIX)].lower() == _CORE_USER_PREFIX.lower():
        return path[len(_CORE_USER_PREFIX):]
    return path


def _supported_contact_patch_path(path: str) -> bool:
    path = _unqualified_contact_patch_path(path)
    # The shipped PATCH dispatcher first calls scim_user_emails.owns(),
    # which uses lower(), not the regex engine's broader Unicode IGNORECASE.
    # For example, re.I matches emailſ as emails, but the dispatcher does not.
    lowered = path.lower()
    if not (lowered == "emails" or lowered.startswith(("emails.", "emails["))):
        return False
    if lowered == "emails" or lowered in {"emails." + child for child in _CONTACT_CHILDREN}:
        return True
    match = _CONTACT_SELECTOR.fullmatch(path)
    if match is None:
        return False
    # re.I also matches Unicode confusables in selector/child structure.
    # Shipped _path() lowercases these groups, and downstream field validation
    # only recognizes the literal supported keys. Do not certify those aliases.
    if match.group(1).lower() not in {"type", "value"} or (match.group(3) and match.group(3).lower() not in _CONTACT_CHILDREN):
        return False
    try:
        literal = json.loads(match.group(2))
    except (ValueError, UnicodeError):
        return False
    return (
        type(literal) is str
        and bool(literal.strip())
        and len(literal) <= (64 if match.group(1).casefold() == "type" else 320)
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159
                    or 0xD800 <= ord(char) <= 0xDFFF
                    or ord(char) in (65534, 65535) for char in literal)
    )


# Shipped Groups PATCH accepts this selector only with the remove verb.
# Keep structural matching ASCII-only: Unicode re.I can match lookalikes that
# the actual Group PATCH path.lower() dispatcher does not recognize.
_GROUP_MEMBER_SELECTOR = re.compile(
    r'members\[value eq ("(?:[^"\\]|\\.)*")\]', re.I | re.ASCII,
)


def _supported_group_patch_path(path: str) -> bool:
    if path.lower() in GROUP_PATCH:
        return True
    match = _GROUP_MEMBER_SELECTOR.fullmatch(path)
    if match is None:
        return False
    try:
        value = json.loads(match.group(1))
    except (ValueError, UnicodeError):
        return False
    return (type(value) is str and 1 <= len(value) <= 100
            and not any(ord(char) < 32 or 127 <= ord(char) <= 159
                        or 0xD800 <= ord(char) <= 0xDFFF
                        or ord(char) in (65534, 65535) for char in value))


class InvalidMapping(ValueError):
    """Invalid local declaration; never include arbitrary user-supplied values."""


def _object(value: Any, path: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if type(value) is not dict:
        raise InvalidMapping(f"{path} must be an object")
    if not required <= value.keys() or value.keys() - (required | optional):
        raise InvalidMapping(f"{path} has missing or unsupported keys")
    return value


def _attributes(value: Any, path: str, *, minimum: int, max_path: int = 128,
                check_duplicates: bool = True) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= MAX_ATTRIBUTES:
        raise InvalidMapping(f"{path} must have {minimum}–{MAX_ATTRIBUTES} paths")
    folded = []
    for item in value:
        if type(item) is not str or not 1 <= len(item) <= max_path or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (65534, 65535) for c in item):
            raise InvalidMapping(f"{path} contains an invalid path")
        # Structural names are case-insensitive. Quoted selector literals must
        # reach the JSON parser unchanged: casefolding can turn an invalid
        # uppercase JSON escape into a valid lowercase escape. Match the
        # shipped attribute parser's lower(), not Unicode casefold(), so
        # long-s Unicode names cannot impersonate ASCII SCIM attributes.
        folded.append(item if "[" in item else item.lower())
    if check_duplicates and len({item.casefold() for item in folded}) != len(folded):
        raise InvalidMapping(f"{path} contains duplicate paths")
    return folded


def assess(mapping: Any) -> dict:
    """Return blockers and cautions; a clean static result still requires live tests."""
    root = _object(mapping, "mapping", {"client", "users", "groups", "provider_policy"})
    if root["client"] not in ("entra", "okta", "other"):
        raise InvalidMapping("client must be entra, okta or other")
    users = _object(root["users"], "users", {
        "match_attributes", "create_attributes", "patch_attributes", "write_mode",
    }, {"patch_operations"})
    groups = _object(root["groups"], "groups", {"enabled", "match_attributes"},
                     {"create_attributes", "patch_attributes", "patch_operations"})
    policy = _object(root["provider_policy"], "provider_policy", {"allow_unversioned_user_patch"},
                     {"unique_group_names"})
    if type(policy.get("unique_group_names", False)) is not bool:
        raise InvalidMapping("unique_group_names must be a boolean")
    if type(groups["enabled"]) is not bool or type(policy["allow_unversioned_user_patch"]) is not bool:
        raise InvalidMapping("enabled and allow_unversioned_user_patch must be booleans")
    if type(users["write_mode"]) is not str:
        raise InvalidMapping("users.write_mode must be a string")

    match = _attributes(users["match_attributes"], "users.match_attributes", minimum=1)
    create = _attributes(users["create_attributes"], "users.create_attributes", minimum=1)
    patch = _attributes(users["patch_attributes"], "users.patch_attributes", minimum=0, max_path=1024)
    group_match = _attributes(groups["match_attributes"], "groups.match_attributes", minimum=0)
    # Optional for old declaration files. Absence means the Group creation
    # mapping has not been reviewed, not that the IdP sends these attributes.
    group_create = (_attributes(groups["create_attributes"], "groups.create_attributes", minimum=0)
                    if "create_attributes" in groups else None)
    group_patch = (_attributes(groups["patch_attributes"], "groups.patch_attributes",
                               minimum=0, max_path=1024, check_duplicates=False)
                   if "patch_attributes" in groups else None)
    # Optional operation-level declaration supplements (never replaces) the
    # legacy path-only Group mapping. The shipped Group PATCH handler has
    # verb-specific restrictions that cannot be checked from paths alone.
    group_patch_ops = None
    if "patch_operations" in groups:
        raw_ops = groups["patch_operations"]
        if type(raw_ops) is not list or len(raw_ops) > MAX_ATTRIBUTES:
            raise InvalidMapping("groups.patch_operations must be a bounded list")
        group_patch_ops = []
        for index, raw_op in enumerate(raw_ops):
            item = _object(raw_op, f"groups.patch_operations[{index}]", {"op", "path"},
                           {"value_present"})
            if "value_present" in item and type(item["value_present"]) is not bool:
                raise InvalidMapping("groups.patch_operations value_present must be a boolean")
            verb = item["op"]
            if type(verb) is not str or re.fullmatch(r"[A-Za-z]{1,16}", verb) is None:
                raise InvalidMapping("groups.patch_operations contains an invalid verb")
            declared_path = _attributes(
                [item["path"]], f"groups.patch_operations[{index}].path",
                minimum=1, max_path=1024, check_duplicates=False,
            )[0]
            group_patch_ops.append((verb.lower(), declared_path, item.get("value_present")))
    # Optional operation-level User PATCH declarations capture request shapes
    # that a path-only mapping cannot distinguish (notably Okta's pathless
    # replace-active example). They contain names/flags, never payload values.
    user_patch_ops = None
    if "patch_operations" in users:
        raw_ops = users["patch_operations"]
        if type(raw_ops) is not list or len(raw_ops) > MAX_ATTRIBUTES:
            raise InvalidMapping("users.patch_operations must be a bounded list")
        user_patch_ops = []
        for index, raw_op in enumerate(raw_ops):
            item = _object(raw_op, f"users.patch_operations[{index}]",
                           {"op", "path"}, {"value_present", "value_attributes"})
            if "value_present" in item and type(item["value_present"]) is not bool:
                raise InvalidMapping("users.patch_operations value_present must be a boolean")
            verb = item["op"]
            if type(verb) is not str or re.fullmatch(r"[A-Za-z]{1,16}", verb) is None:
                raise InvalidMapping("users.patch_operations contains an invalid verb")
            raw_path = item["path"]
            if type(raw_path) is not str:
                raise InvalidMapping("users.patch_operations contains an invalid path")
            declared_path = (_attributes(
                [raw_path], f"users.patch_operations[{index}].path",
                minimum=1, max_path=1024, check_duplicates=False,
            )[0] if raw_path else "")
            if declared_path and "value_attributes" in item:
                raise InvalidMapping("users.patch_operations value_attributes requires an empty path")
            fields = (_attributes(
                item["value_attributes"], f"users.patch_operations[{index}].value_attributes",
                minimum=0,
            ) if "value_attributes" in item else None)
            user_patch_ops.append((verb.lower(), declared_path,
                                   item.get("value_present"), fields))
    if group_patch is not None:
        # Group member IDs are opaque, case-sensitive SCIM User resource IDs.
        # The shipped Group PATCH selector decodes JSON and compares the
        # resulting ID exactly; raw casefold() would conflate distinct IDs,
        # while differently escaped JSON literals could target one ID twice.
        # Normalize only ASCII-insensitive selector structure, never the ID.
        effective_group_paths: set[tuple[str, str]] = set()
        for value in group_patch:
            selector = _GROUP_MEMBER_SELECTOR.fullmatch(value)
            if selector is not None and _supported_group_patch_path(value):
                effective = ("member", json.loads(selector.group(1)))
            elif value.lower() in GROUP_PATCH:
                effective = ("field", value.lower())
            else:
                # Unsupported paths will also receive the usual blocker;
                # preserve generic duplicate rejection without echoing input.
                effective = ("unsupported", value.casefold())
            if effective in effective_group_paths:
                raise InvalidMapping("groups.patch_attributes contains duplicate effective paths")
            effective_group_paths.add(effective)
    # Different spellings of the same *supported* contact PATCH target must
    # not pass as independent mappings. The shipped contact parser strips the
    # exact core User URN, decodes JSON selector literals and compares selected
    # values with casefold(). A raw-path duplicate check misses all three.
    # Unsupported paths retain their ordinary explicit blocker below.
    effective_contact_paths: set[tuple[str, ...]] = set()
    for value in patch:
        if not _supported_contact_patch_path(value):
            continue
        unqualified = _unqualified_contact_patch_path(value)
        selector = _CONTACT_SELECTOR.fullmatch(unqualified)
        if selector is None:
            effective = ("field", unqualified.casefold())
        else:
            effective = (
                "selector", selector.group(1).casefold(),
                json.loads(selector.group(2)).casefold(),
                (selector.group(3) or "").casefold(),
            )
        if effective in effective_contact_paths:
            raise InvalidMapping("users.patch_attributes contains duplicate effective contact paths")
        effective_contact_paths.add(effective)
    findings: list[dict[str, str]] = []

    def add(severity: str, code: str, path: str, message: str) -> None:
        findings.append({"severity": severity, "code": code, "path": path, "message": message})

    # The shipped User PATCH endpoint accepts 1–20 operations per HTTP
    # request. Declared possible operations can span multiple requests, so
    # 21–32 entries require a batching caution, never a false blocker.
    if user_patch_ops is not None and len(user_patch_ops) > 20:
        add("caution", "user_patch_single_request_limit", "users.patch_operations",
            "The User endpoint permits at most 20 operations per PATCH request; verify how the IdP batches declared operations")

    if user_patch_ops is not None:
        # The shipped User PATCH handler accepts pathless add/replace with an
        # object-valued value, but not pathless remove. A declared list is not
        # evidence of the actual HTTP envelope, ETag or object payload.
        pathless_fields = frozenset({"displayname", "externalid", "active", "name", "emails"})
        for index, (verb, declared_path, value_present, fields) in enumerate(user_patch_ops):
            location = f"users.patch_operations[{index}]"
            if verb not in {"add", "replace", "remove"}:
                add("blocker", "unsupported_user_patch_verb", location + ".op",
                    "Declared User PATCH operation uses an unsupported verb")
                continue
            if not declared_path:
                if verb == "remove":
                    add("blocker", "user_pathless_remove_forbidden", location + ".op",
                        "Shipped User PATCH requires a path for remove")
                    continue
                if fields is None:
                    add("caution", "user_pathless_fields_undeclared",
                        location + ".value_attributes",
                        "Pathless User PATCH object fields were not declared; verify the emitted object")
                else:
                    for field_index, field in enumerate(fields):
                        if field not in pathless_fields:
                            add("blocker", "unsupported_user_pathless_field",
                                location + f".value_attributes[{field_index}]",
                                "Pathless User PATCH object contains an unsupported or immutable field")
                    if not fields:
                        add("caution", "user_pathless_empty_object",
                            location + ".value_attributes",
                            "Declared pathless User PATCH object contains no fields")
            elif (declared_path not in USER_PATCH
                  and not _supported_contact_patch_path(declared_path)):
                add("blocker", "unsupported_user_patch_path", location + ".path",
                    "Declared User PATCH operation uses an unsupported path")
            elif verb == "remove" and declared_path not in {"displayname", "externalid"} and not (
                    declared_path == "name" or declared_path.startswith("name.")
                    or _supported_contact_patch_path(declared_path)):
                add("blocker", "unsupported_user_patch_remove", location + ".op",
                    "This User attribute cannot be removed by the shipped PATCH handler")
            if verb in {"add", "replace"}:
                if value_present is False:
                    add("blocker", "user_patch_required_value_missing",
                        location + ".value_present",
                        "Declared User add/replace omits the required value property")
                elif value_present is None:
                    add("caution", "user_patch_value_presence_undeclared",
                        location + ".value_present",
                        "User add/replace value-property presence is unknown; verify actual IdP payload")
    declared_paths = [
        ("users.match_attributes", match, USER_MATCH),
        ("users.create_attributes", create, USER_CREATE),
        ("users.patch_attributes", patch, USER_PATCH),
        ("groups.match_attributes", group_match, GROUP_MATCH),
    ]
    if group_create is not None:
        declared_paths.append(("groups.create_attributes", group_create, GROUP_CREATE))
    if group_patch is not None:
        declared_paths.append(("groups.patch_attributes", group_patch, GROUP_PATCH))
    for path, values, supported in declared_paths:
        for index, value in enumerate(values):
            if (value not in supported
                    and not (path == "users.patch_attributes" and _supported_contact_patch_path(value))
                    and not (path == "groups.patch_attributes" and _supported_group_patch_path(value))):
                add("blocker", "unsupported_attribute", f"{path}[{index}]",
                    "This attribute or selector is not supported for this operation")
    # The shipped contact PATCH parser accepts the exact core User schema URN
    # as a prefix. Keep cautions consistent with accepted qualified paths.
    contact_patch_paths = [_unqualified_contact_patch_path(value) for value in patch]
    # Operation-level declarations are independent of the legacy path list.
    # A client declaring contact writes only in patch_operations must still
    # receive the same descriptive-only and value-selector cautions. Do not
    # include opaque selector literals in any finding or infer actual writes.
    contact_operation_paths = [
        _unqualified_contact_patch_path(path)
        for _, path, _, _ in (user_patch_ops or [])
        if path and _supported_contact_patch_path(path)
    ]
    declared_contact_paths = contact_patch_paths + contact_operation_paths
    def has_value_selector(paths: list[str]) -> bool:
        return any((match := _CONTACT_SELECTOR.fullmatch(value)) is not None
                   and match.group(1).casefold() == "value" for value in paths)

    legacy_value_selector = has_value_selector(contact_patch_paths)
    if legacy_value_selector or has_value_selector(contact_operation_paths):
        # Attribute the finding to a declaration that actually contains the
        # selector. Legacy reports retain their original finding location.
        location = ("users.patch_attributes" if legacy_value_selector
                    else "users.patch_operations")
        add("caution", "contact_value_selector", location,
            "A contact-value selector is data-dependent and is not an identity match or verification")
    if ("emails" in create
            or any(value.casefold().startswith("emails") for value in declared_contact_paths)
            or any(fields is not None and "emails" in fields
                   for _, _, _, fields in (user_patch_ops or []))):
        add("caution", "unverified_contact_metadata", "users",
            "Contact arrays are descriptive only; matching, login and adoption still use stable userName/externalId")
    # Different *supported* PATCH targets can overlap without being identical:
    # replacing an entire array and then a child, or a selected contact and
    # then one of its children, depends on operation order and current data.
    # Warn rather than block: these are legal shipped operations and a static
    # declaration cannot know whether a client actually sends them together.
    # The legacy path list and optional operation declarations describe the
    # same possible PATCH targets. An operation-only mapping (or a combination
    # of the two) can overwrite overlapping contact data just as a legacy-only
    # mapping can. Normalize supported paths only, without exposing literals.
    operation_contact_keys: set[tuple[str, ...]] = set()
    # Pathless add/replace can carry an object with an `emails` member.
    # That is a whole-array contact write even without a literal PATCH path.
    # A pathless remove or explicitly missing value is already unsupported
    # and must not manufacture a valid contact target for overlap analysis.
    if any(not path and verb in {"add", "replace"}
           and value_present is not False and fields is not None
           and "emails" in fields
           for verb, path, value_present, fields in (user_patch_ops or [])):
        operation_contact_keys.add(("field", "emails"))
    for path in contact_operation_paths:
        selector = _CONTACT_SELECTOR.fullmatch(path)
        if selector is None:
            operation_contact_keys.add(("field", path.casefold()))
        else:
            operation_contact_keys.add((
                "selector", selector.group(1).casefold(),
                json.loads(selector.group(2)).casefold(),
                (selector.group(3) or "").casefold(),
            ))

    def overlapping_contact_paths(keys: set[tuple[str, ...]]) -> bool:
        if ("field", "emails") in keys and len(keys) > 1:
            return True
        # One stored contact can satisfy both a type selector and a value
        # selector. Distinct selector fields are not evidence of distinct
        # contacts, even when their literal values differ. Preserve the
        # case-insensitive effective-target checks above, but caution rather
        # than reject this data-dependent cross-selector overlap.
        selector_fields = {key[1] for key in keys if key[0] == "selector"}
        if {"type", "value"} <= selector_fields:
            return True
        for child in _CONTACT_CHILDREN:
            if (("field", "emails." + child) in keys and any(
                    key[0] == "selector" and key[3] in ("", child)
                    for key in keys)):
                return True
        selected_children: dict[tuple[str, str], set[str]] = {}
        for key in keys:
            if key[0] == "selector":
                selected_children.setdefault((key[1], key[2]), set()).add(key[3])
        return any("" in children and len(children) > 1
                   for children in selected_children.values())

    legacy_contact_overlap = overlapping_contact_paths(effective_contact_paths)
    contact_overlap = overlapping_contact_paths(
        effective_contact_paths | operation_contact_keys)
    if contact_overlap:
        # Preserve the existing legacy-only finding path for consumers. When
        # optional operations introduce the overlap, point to their declaration
        # without echoing the target selector or its contact value.
        location = ("users.patch_attributes" if legacy_contact_overlap
                    else "users.patch_operations")
        add("caution", "overlapping_contact_patch_paths", location,
            "Contact PATCH paths may overwrite overlapping targets; review client operation order and current data")
    if "username" not in create:
        add("blocker", "missing_stable_username", "users.create_attributes",
            "User creation requires the stable work-email userName")
    if "externalid" in match and "externalid" not in create:
        # externalId is a client-issued, provider-scoped correlation key.
        # A declared create without it cannot demonstrate that subsequent
        # externalId matching will find the created resource. userName may
        # provide a separate fallback, so warn rather than reject the mapping.
        add("caution", "externalid_match_not_created", "users.create_attributes",
            "Matching declares externalId but creation omits it; verify stable provider-scoped matching and fallback before rollout")
    if "active" not in create:
        # Shipped SCIM User creation starts a new roster employee active unless
        # the actual request carries literal active=False. A declared mapping
        # without this attribute cannot demonstrate disabled-user creation.
        # Warn, rather than reject a separately validated provider lifecycle.
        add("caution", "creation_active_not_declared", "users.create_attributes",
            "Declared User create mapping omits active; OWG creates users active unless the request explicitly sends false")
    if "username" in patch:
        add("blocker", "immutable_username", "users.patch_attributes",
            "OWG does not support changing the stable userName/evidence actor")
    # Either declaration format may describe a supported User deactivation
    # PATCH. Operation-level pathless objects are used by some IdPs (notably
    # Okta); checking only legacy paths incorrectly reports active as missing.
    # Unsupported remove and explicitly value-less operations do not establish
    # a usable declaration. Unknown value presence retains its own caution.
    declared_deactivation = "active" in patch or any(
        verb in {"add", "replace"} and value_present is not False
        and (declared_path == "active"
             or (not declared_path and fields is not None and "active" in fields))
        for verb, declared_path, value_present, fields in (user_patch_ops or [])
    )
    if not declared_deactivation:
        # A missing declaration cannot demonstrate that the IdP will
        # deactivate; PUT or another separately tested lifecycle path may.
        add("caution", "deactivation_patch_not_declared", "users.patch_attributes",
            "Declared User PATCH mapping omits a supported active update; verify a deactivate operation before rollout")
    for path, values in (("users.create_attributes", create), ("users.patch_attributes", patch)):
        if "name" in values and any(value.startswith("name.") for value in values):
            add("caution", "overlapping_name_paths", path,
                "Whole-name and child mappings overlap; verify the IdP's emitted request order")

    if not groups["enabled"] and group_match:
        add("blocker", "disabled_groups_with_mapping", "groups.match_attributes",
            "Group matching is declared although Group provisioning is disabled")
    if not groups["enabled"] and group_create is not None:
        add("blocker", "disabled_groups_with_create_mapping", "groups.create_attributes",
            "Group creation is declared although Group provisioning is disabled")
    if not groups["enabled"] and group_patch is not None:
        add("blocker", "disabled_groups_with_patch_mapping", "groups.patch_attributes",
            "Group PATCH is declared although Group provisioning is disabled")
    if not groups["enabled"] and group_patch_ops is not None:
        add("blocker", "disabled_groups_with_patch_operations", "groups.patch_operations",
            "Group PATCH operations are declared although Group provisioning is disabled")
    # The shipped Group PATCH endpoint accepts 1–20 operations *per request*.
    # This optional list may instead describe alternatives across requests,
    # so >20 declarations warrant a caution, not an unsupported-mapping block.
    if group_patch_ops is not None and len(group_patch_ops) > 20:
        add("caution", "group_patch_single_request_limit", "groups.patch_operations",
            "The Group endpoint permits at most 20 operations per PATCH request; verify how the IdP batches declared operations")
    if group_patch_ops is not None:
        for index, (verb, declared_path, value_present) in enumerate(group_patch_ops):
            location = f"groups.patch_operations[{index}]"
            if not _supported_group_patch_path(declared_path):
                add("blocker", "unsupported_group_patch_path", location + ".path",
                    "Declared Group PATCH operation uses an unsupported path")
            elif verb not in {"add", "replace", "remove"}:
                add("blocker", "unsupported_group_patch_verb", location + ".op",
                    "Declared Group PATCH operation uses an unsupported verb")
            elif (_GROUP_MEMBER_SELECTOR.fullmatch(declared_path) is not None
                  and verb != "remove"):
                add("blocker", "group_member_selector_requires_remove", location + ".op",
                    "Group member selectors support only remove operations")
            elif declared_path.lower() == "displayname" and verb == "remove":
                add("blocker", "group_displayname_remove_forbidden", location + ".op",
                    "Required Group displayName cannot be removed")
            elif verb in {"add", "replace"}:
                # The shipped Group PATCH handler requires an explicit value
                # key for add/replace, regardless of whether its payload is
                # valid. Do not accept a declared-absent key as compatible;
                # an omitted declaration is unknown, not a live test pass.
                if value_present is False:
                    add("blocker", "group_patch_required_value_missing",
                        location + ".value_present",
                        "Declared Group add/replace operation omits the required value property")
                elif value_present is None:
                    add("caution", "group_patch_value_presence_undeclared",
                        location + ".value_present",
                        "Group add/replace value-property presence was not declared; verify actual IdP payload")
    if groups["enabled"]:
        if group_create is None:
            add("caution", "group_create_mapping_not_declared", "groups.create_attributes",
                "Group creation mapping was not declared; verify required displayName and stable externalId lineage")
        else:
            # Shipped Group creation requires displayName; externalId is
            # optional, but externalId-based matching cannot be demonstrated
            # when the declared create flow omits that correlation key.
            if "displayname" not in group_create:
                add("blocker", "missing_group_create_displayname", "groups.create_attributes",
                    "Shipped Group creation requires displayName")
            if "externalid" in group_match and "externalid" not in group_create:
                add("caution", "group_externalid_match_not_created", "groups.create_attributes",
                    "Group matching declares externalId but Group creation omits it; verify provider-scoped correlation")
            if "members" in group_create:
                # Group members[].value is an OWG User resource ID returned
                # from this provider's SCIM Users endpoint, never an email,
                # externalId, OIDC subject, or authority to grant access.
                # A declared IdP field cannot establish how it resolves IDs.
                add("caution", "group_member_id_resolution_unverified",
                    "groups.create_attributes",
                    "Declared Group members require current provider-scoped OWG User IDs; verify IdP member-value resolution with disposable Users")
        if group_patch is not None or group_patch_ops is not None:
            selectors = [value for value in (group_patch or [])
                         if (_GROUP_MEMBER_SELECTOR.fullmatch(value) is not None
                             and _supported_group_patch_path(value))]
            selectors.extend(path for _, path, _ in (group_patch_ops or [])
                             if (_GROUP_MEMBER_SELECTOR.fullmatch(path) is not None
                                 and _supported_group_patch_path(path)))
            whole_members = ("members" in (group_patch or []) or
                             any(path.lower() == "members" for _, path, _ in (group_patch_ops or [])))
            if selectors:
                add("caution", "group_member_selector_remove_only", "groups.patch_attributes",
                    "Shipped Group member selectors support removal only; verify actual PATCH verbs and current ETags")
            if whole_members or selectors:
                add("caution", "group_member_patch_ids_unverified", "groups.patch_attributes",
                    "Group PATCH members require current provider-scoped OWG User IDs; static paths do not verify resolution")
            if whole_members and selectors:
                # Whole-member updates and selector removals can touch the
                # same provider-scoped membership set. Both are valid paths,
                # but an IdP may emit them in an order that changes the result.
                # Never include the opaque selector ID in diagnostics.
                add("caution", "overlapping_group_member_patch_paths", "groups.patch_attributes",
                    "Whole-members and member-selector PATCH paths overlap; verify actual operation order and ETags")
        if not group_match:
            add("blocker", "missing_group_match", "groups.match_attributes",
                "Enabled Groups require an explicit matching attribute")
        if "displayname" in group_match and not policy.get("unique_group_names", False):
            if root["client"] == "entra":
                add("blocker", "entra_group_name_uniqueness", "groups.match_attributes",
                    "Declare and verify the provider's unique Group name policy before Entra displayName matching")
            else:
                # Not a general SCIM requirement: other clients may match by
                # exact name, but the declaration cannot rule out collisions.
                # Warn rather than claim an unverified policy or block rollout.
                add("caution", "group_displayname_collision_risk", "groups.match_attributes",
                    "Group displayName matching may be ambiguous without a verified provider-scoped uniqueness policy")
        if policy.get("unique_group_names", False):
            add("caution", "declared_group_name_policy", "provider_policy.unique_group_names",
                "This declaration does not enable or verify the server policy; check current provider revision and worker versions")
        add("caution", "groups_not_certified", "groups.enabled",
            "Group provisioning requires a customer-controlled IdP test; group names grant no OWG permissions")

    mode = users["write_mode"]
    if mode == "unversioned_user_patch":
        if not policy["allow_unversioned_user_patch"]:
            add("blocker", "missing_patch_policy", "provider_policy.allow_unversioned_user_patch",
                "Unversioned standalone User PATCH requires explicit administrator approval")
        else:
            add("caution", "unversioned_patch_overwrite", "users.write_mode",
                "Approved unversioned PATCH can overwrite newer targeted values")
    elif mode == "etag":
        if policy["allow_unversioned_user_patch"]:
            # The declared client's normal ETag behavior does not disable a
            # separately approved server-side missing-If-Match exception.
            # A static declaration cannot verify the current provider policy.
            add("caution", "etag_mode_with_unversioned_patch_policy",
                "provider_policy.allow_unversioned_user_patch",
                "ETag mapping coexists with a declared missing-If-Match User PATCH allowance; verify the current provider policy")
    else:
        add("blocker", "unsupported_write_mode", "users.write_mode",
            "Only exact ETag writes or explicitly approved standalone unversioned User PATCH are supported")

    add("caution", "live_validation_required", "client",
        "Static mapping checks never establish live Entra/Okta interoperability or company rollout readiness")
    blocked = any(item["severity"] == "blocker" for item in findings)
    # Count only *declared* paths, never inferred or actually executed
    # operations. Keep the four legacy keys stable for existing consumers.
    # Optional Group declarations must remain distinguishable from absence:
    # an explicitly empty list is reviewed (count zero), whereas an omitted
    # field is unknown and must not acquire a fabricated count.
    checked = {"user_matching": len(match), "user_create": len(create),
               "user_patch": len(patch), "group_matching": len(group_match)}
    if group_create is not None:
        checked["group_create"] = len(group_create)
    if group_patch is not None:
        checked["group_patch"] = len(group_patch)
    if group_patch_ops is not None:
        checked["group_patch_operations"] = len(group_patch_ops)
    if user_patch_ops is not None:
        checked["user_patch_operations"] = len(user_patch_ops)
    return {
        "status": "blocked" if blocked else "requires_live_validation",
        "scope": "offline_declared_mapping_only",
        "findings": findings,
        "checked": checked,
    }


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    seen = set()
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen:
            raise InvalidMapping("JSON contains duplicate or case-colliding keys")
        seen.add(folded)
        result[key] = value
    return result


def _bounded_tree(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise InvalidMapping("Mapping JSON nesting exceeds 32 levels")
    if type(value) is str:
        # Python's JSON decoder permits lone UTF-16 surrogate escapes. They
        # are not Unicode scalar values and must not survive strict preflight,
        # including in fields the mapping schema will otherwise discard.
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InvalidMapping("Mapping JSON contains an unpaired Unicode surrogate")
    elif type(value) is dict:
        for key, child in value.items():
            _bounded_tree(key, depth)
            _bounded_tree(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _bounded_tree(child, depth + 1)


def _bounded_json_int(token: str) -> int:
    """Reject enormous numeric tokens before constructing Python integers."""
    if len(token) - int(token.startswith("-")) > MAX_JSON_INTEGER_DIGITS:
        raise InvalidMapping("Mapping JSON integer exceeds bounded digits")
    return int(token)


def _finite_json_float(token: str) -> float:
    """JSON exponents may overflow to infinity despite valid JSON syntax."""
    value = float(token)
    if not math.isfinite(value):
        raise InvalidMapping("Nonfinite JSON is refused")
    return value


def load_text(text: str) -> Any:
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        # Direct Python callers must get the same generic validation error as
        # CLI callers, not a raw UnicodeEncodeError with their input attached.
        raise InvalidMapping("Mapping is not bounded, strict JSON") from exc
    if encoded_size > MAX_BYTES:
        raise InvalidMapping("Mapping exceeds the 32 KiB input limit")
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicates,
                            parse_int=_bounded_json_int, parse_float=_finite_json_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(InvalidMapping("Nonfinite JSON is refused")))
        _bounded_tree(parsed)
        return parsed
    except (ValueError, RecursionError) as exc:
        raise InvalidMapping("Mapping is not bounded, strict JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", nargs="?", default="-", help="Local JSON file or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.mapping == "-":
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.mapping).open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InvalidMapping("Mapping exceeds the 32 KiB input limit")
        result = assess(load_text(raw.decode("utf-8")))
        code = 2 if result["status"] == "blocked" else 0
    except (OSError, UnicodeError, InvalidMapping):
        result = {"status": "invalid_input", "scope": "offline_declared_mapping_only",
                  "findings": [{"severity": "blocker", "code": "invalid_input",
                                "message": "Provide bounded strict JSON with only documented mapping keys"}]}
        code = 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
