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
    create declarations, or casefold a quoted selector literal.
    """
    if path[:len(_CORE_USER_PREFIX)].casefold() == _CORE_USER_PREFIX.casefold():
        return path[len(_CORE_USER_PREFIX):]
    return path


def _supported_contact_patch_path(path: str) -> bool:
    path = _unqualified_contact_patch_path(path)
    if path == "emails" or path in {"emails." + child for child in _CONTACT_CHILDREN}:
        return True
    match = _CONTACT_SELECTOR.fullmatch(path)
    if match is None:
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


class InvalidMapping(ValueError):
    """Invalid local declaration; never include arbitrary user-supplied values."""


def _object(value: Any, path: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if type(value) is not dict:
        raise InvalidMapping(f"{path} must be an object")
    if not required <= value.keys() or value.keys() - (required | optional):
        raise InvalidMapping(f"{path} has missing or unsupported keys")
    return value


def _attributes(value: Any, path: str, *, minimum: int, max_path: int = 128) -> list[str]:
    if type(value) is not list or not minimum <= len(value) <= MAX_ATTRIBUTES:
        raise InvalidMapping(f"{path} must have {minimum}–{MAX_ATTRIBUTES} paths")
    folded = []
    for item in value:
        if type(item) is not str or not 1 <= len(item) <= max_path or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (65534, 65535) for c in item):
            raise InvalidMapping(f"{path} contains an invalid path")
        # Structural names are case-insensitive. Quoted selector literals must
        # reach the JSON parser unchanged: casefolding can turn an invalid
        # uppercase JSON escape into a valid lowercase escape.
        folded.append(item if "[" in item else item.casefold())
    if len({item.casefold() for item in folded}) != len(folded):
        raise InvalidMapping(f"{path} contains duplicate paths")
    return folded


def assess(mapping: Any) -> dict:
    """Return blockers and cautions; a clean static result still requires live tests."""
    root = _object(mapping, "mapping", {"client", "users", "groups", "provider_policy"})
    if root["client"] not in ("entra", "okta", "other"):
        raise InvalidMapping("client must be entra, okta or other")
    users = _object(root["users"], "users", {
        "match_attributes", "create_attributes", "patch_attributes", "write_mode",
    })
    groups = _object(root["groups"], "groups", {"enabled", "match_attributes"})
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

    for path, values, supported in (
        ("users.match_attributes", match, USER_MATCH),
        ("users.create_attributes", create, USER_CREATE),
        ("users.patch_attributes", patch, USER_PATCH),
        ("groups.match_attributes", group_match, GROUP_MATCH),
    ):
        for index, value in enumerate(values):
            if value not in supported and not (path == "users.patch_attributes" and _supported_contact_patch_path(value)):
                add("blocker", "unsupported_attribute", f"{path}[{index}]",
                    "This attribute or selector is not supported for this operation")
    # The shipped contact PATCH parser accepts the exact core User schema URN
    # as a prefix. Keep cautions consistent with accepted qualified paths.
    contact_patch_paths = [_unqualified_contact_patch_path(value) for value in patch]
    if any((contact_match := _CONTACT_SELECTOR.fullmatch(value)) is not None
           and contact_match.group(1).casefold() == "value" for value in contact_patch_paths):
        add("caution", "contact_value_selector", "users.patch_attributes",
            "A contact-value selector is data-dependent and is not an identity match or verification")
    if "emails" in create or any(value.casefold().startswith("emails") for value in contact_patch_paths):
        add("caution", "unverified_contact_metadata", "users",
            "Contact arrays are descriptive only; matching, login and adoption still use stable userName/externalId")
    # Different *supported* PATCH targets can overlap without being identical:
    # replacing an entire array and then a child, or a selected contact and
    # then one of its children, depends on operation order and current data.
    # Warn rather than block: these are legal shipped operations and a static
    # declaration cannot know whether a client actually sends them together.
    contact_keys = effective_contact_paths
    contact_overlap = ("field", "emails") in contact_keys and len(contact_keys) > 1
    if not contact_overlap:
        for child in _CONTACT_CHILDREN:
            if (("field", "emails." + child) in contact_keys and any(
                    key[0] == "selector" and key[3] in ("", child)
                    for key in contact_keys)):
                contact_overlap = True
                break
    if not contact_overlap:
        selected_children: dict[tuple[str, str], set[str]] = {}
        for key in contact_keys:
            if key[0] == "selector":
                selected_children.setdefault((key[1], key[2]), set()).add(key[3])
        contact_overlap = any("" in children and len(children) > 1
                              for children in selected_children.values())
    if contact_overlap:
        add("caution", "overlapping_contact_patch_paths", "users.patch_attributes",
            "Contact PATCH paths may overwrite overlapping targets; review client operation order and current data")
    if "username" not in create:
        add("blocker", "missing_stable_username", "users.create_attributes",
            "User creation requires the stable work-email userName")
    if "username" in patch:
        add("blocker", "immutable_username", "users.patch_attributes",
            "OWG does not support changing the stable userName/evidence actor")
    if "active" not in patch:
        # A declared PATCH mapping without active does not demonstrate that an
        # IdP can deactivate an employee. Do not block a possible separately
        # tested PUT/other lifecycle path or imply live provider certification.
        add("caution", "deactivation_patch_not_declared", "users.patch_attributes",
            "Declared User PATCH mapping omits active; verify a supported deactivate operation before rollout")
    for path, values in (("users.create_attributes", create), ("users.patch_attributes", patch)):
        if "name" in values and any(value.startswith("name.") for value in values):
            add("caution", "overlapping_name_paths", path,
                "Whole-name and child mappings overlap; verify the IdP's emitted request order")

    if not groups["enabled"] and group_match:
        add("blocker", "disabled_groups_with_mapping", "groups.match_attributes",
            "Group matching is declared although Group provisioning is disabled")
    if groups["enabled"]:
        if not group_match:
            add("blocker", "missing_group_match", "groups.match_attributes",
                "Enabled Groups require an explicit matching attribute")
        if root["client"] == "entra" and "displayname" in group_match and not policy.get("unique_group_names", False):
            add("blocker", "entra_group_name_uniqueness", "groups.match_attributes",
                "Declare and verify the provider's unique Group name policy before Entra displayName matching")
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
    elif mode != "etag":
        add("blocker", "unsupported_write_mode", "users.write_mode",
            "Only exact ETag writes or explicitly approved standalone unversioned User PATCH are supported")

    add("caution", "live_validation_required", "client",
        "Static mapping checks never establish live Entra/Okta interoperability or company rollout readiness")
    blocked = any(item["severity"] == "blocker" for item in findings)
    return {
        "status": "blocked" if blocked else "requires_live_validation",
        "scope": "offline_declared_mapping_only",
        "findings": findings,
        "checked": {"user_matching": len(match), "user_create": len(create),
                    "user_patch": len(patch), "group_matching": len(group_match)},
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
