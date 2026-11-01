"""Synthetic, offline mapping acceptance: no IdP tokens, network or live claims."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.scim_idp_mapping_preflight import InvalidMapping, assess, load_text

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scim_idp_mapping_preflight.py"


def mapping(**changes):
    base = {
        "client": "entra",
        "users": {
            "match_attributes": ["userName", "externalId"],
            "create_attributes": ["userName", "externalId", "active", "name.givenName"],
            "patch_attributes": ["active", "name.givenName"],
            "write_mode": "etag",
        },
        "groups": {"enabled": False, "match_attributes": []},
        "provider_policy": {"allow_unversioned_user_patch": False},
    }
    base.update(changes)
    return base


def codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_minimal_supported_mapping_is_not_certification():
    report = assess(mapping())
    assert report["status"] == "requires_live_validation"
    assert codes(report) == {"live_validation_required"}
    assert report["checked"] == {"user_matching": 2, "user_create": 4,
                                  "user_patch": 2, "group_matching": 0}


def test_unsupported_email_match_and_roles_fail_closed_without_echoing_values():
    users = mapping()["users"]
    users["match_attributes"] = ['emails[type eq "work"].value']
    users["create_attributes"] = ["userName", "roles", "urn:private:token"]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert "unsupported_attribute" in codes(report)
    serialized = json.dumps(report)
    assert "private" not in serialized and "emails[type" not in serialized


def test_immutable_actor_and_missing_username_are_blockers():
    users = mapping()["users"]
    users["create_attributes"] = ["displayName"]
    users["patch_attributes"] = ["userName"]
    report = assess(mapping(users=users))
    assert {"missing_stable_username", "immutable_username"} <= codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
@pytest.mark.parametrize("match_attrs,created_externalid,expect_caution", [
    (["externalId"], False, True),
    (["userName", "externalId"], False, True),
    (["userName"], False, False),
    (["externalId"], True, False),
], ids=["externalid-only-missing", "username-fallback-missing",
        "username-only", "externalid-created"])
def test_declared_externalid_matching_requires_creation_lineage_review(
        client, match_attrs, created_externalid, expect_caution):
    users = mapping()["users"]
    users["match_attributes"] = match_attrs
    users["create_attributes"] = ["userName", "active"] + (
        ["externalId"] if created_externalid else [])
    report = assess(mapping(client=client, users=users))
    assert report["status"] == "requires_live_validation"
    assert ("externalid_match_not_created" in codes(report)) is expect_caution
    assert "missing_stable_username" not in codes(report)
    assert "unsupported_attribute" not in codes(report)
    assert report["checked"]["user_matching"] == len(match_attrs)
    assert report["checked"]["user_create"] == len(users["create_attributes"])
    assert "live_validation_required" in codes(report)


def test_externalid_lineage_caution_never_masks_missing_username_blocker():
    users = mapping()["users"]
    users["match_attributes"] = ["externalId"]
    users["create_attributes"] = ["active"]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert {"externalid_match_not_created", "missing_stable_username"} <= codes(report)


def test_unversioned_patch_requires_explicit_admin_policy_and_warns_when_approved():
    users = mapping()["users"]
    users["write_mode"] = "unversioned_user_patch"
    denied = assess(mapping(users=users))
    assert "missing_patch_policy" in codes(denied)
    approved = assess(mapping(users=users, provider_policy={"allow_unversioned_user_patch": True}))
    assert approved["status"] == "requires_live_validation"
    assert "unversioned_patch_overwrite" in codes(approved)
    users["write_mode"] = "unversioned_put"
    assert "unsupported_write_mode" in codes(assess(mapping(users=users)))


def test_entra_group_name_collision_is_explicit_blocker():
    groups = {"enabled": True, "match_attributes": ["displayName"]}
    report = assess(mapping(groups=groups))
    assert "entra_group_name_uniqueness" in codes(report)
    assert "groups_not_certified" in codes(report)
    groups["match_attributes"] = ["externalId"]
    report = assess(mapping(groups=groups))
    assert report["status"] == "requires_live_validation"
    assert "groups_not_certified" in codes(report)
    assert "entra_group_name_uniqueness" not in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
@pytest.mark.parametrize("create_attrs,expected", [
    (["displayName", "externalId"], None),
    (["DISPLAYNAME", "members"], "group_externalid_match_not_created"),
    (["externalId"], "missing_group_create_displayname"),
    (["displayName"], "group_externalid_match_not_created"),
], ids=["matched-create", "member-only-externalid-gap",
        "missing-required-name", "externalid-not-created"])
def test_group_create_declaration_matches_shipped_required_name_and_lineage(
        client, create_attrs, expected):
    groups = {"enabled": True, "match_attributes": ["externalId"],
              "create_attributes": create_attrs}
    report = assess(mapping(client=client, groups=groups))
    assert report["status"] == ("blocked" if expected == "missing_group_create_displayname"
                                else "requires_live_validation")
    assert ("group_externalid_match_not_created" in codes(report)) == (
        "displayname" in {value.lower() for value in create_attrs}
        and "externalid" not in {value.lower() for value in create_attrs})
    assert ("missing_group_create_displayname" in codes(report)) == (
        "displayname" not in {value.lower() for value in create_attrs})
    assert "group_create_mapping_not_declared" not in codes(report)
    assert "groups_not_certified" in codes(report)
    assert "live_validation_required" in codes(report)
    assert "unsupported_attribute" not in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
@pytest.mark.parametrize("members_declared", [False, True], ids=["no-members", "members"])
def test_group_create_members_require_live_provider_scoped_user_id_resolution(
        client, members_declared):
    groups = {
        "enabled": True,
        "match_attributes": ["externalId"],
        "create_attributes": ["displayName", "externalId"] + (["members"] if members_declared else []),
    }
    report = assess(mapping(client=client, groups=groups))
    assert report["status"] == "requires_live_validation"
    assert ("group_member_id_resolution_unverified" in codes(report)) is members_declared
    assert "missing_group_create_displayname" not in codes(report)
    assert "group_externalid_match_not_created" not in codes(report)
    assert "groups_not_certified" in codes(report)
    assert report["scope"] == "offline_declared_mapping_only"
    assert "members" not in json.dumps(report)


def test_group_member_id_caution_requires_enabled_declared_group_creation():
    undeclared = {"enabled": True, "match_attributes": ["externalId"]}
    assert "group_member_id_resolution_unverified" not in codes(
        assess(mapping(groups=undeclared)))
    disabled = {"enabled": False, "match_attributes": [],
                "create_attributes": ["displayName", "members"]}
    report = assess(mapping(groups=disabled))
    assert report["status"] == "blocked"
    assert "disabled_groups_with_create_mapping" in codes(report)
    assert "group_member_id_resolution_unverified" not in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
def test_group_create_mapping_absence_is_unknown_not_inferred_from_matching(client):
    groups = {"enabled": True, "match_attributes": ["externalId"]}
    report = assess(load_text(json.dumps(mapping(client=client, groups=groups))))
    assert report["status"] == "requires_live_validation"
    assert "group_create_mapping_not_declared" in codes(report)
    assert "group_externalid_match_not_created" not in codes(report)
    assert "missing_group_create_displayname" not in codes(report)
    assert "groups_not_certified" in codes(report)


@pytest.mark.parametrize("create_attrs", [[], ["displayName"], ["displayName", "externalId"]])
def test_disabled_groups_cannot_declare_create_even_with_empty_mapping(create_attrs):
    groups = {"enabled": False, "match_attributes": [], "create_attributes": create_attrs}
    report = assess(mapping(groups=groups))
    assert report["status"] == "blocked"
    assert "disabled_groups_with_create_mapping" in codes(report)
    assert "group_create_mapping_not_declared" not in codes(report)


@pytest.mark.parametrize("create_attrs", [
    ["displayName", "roles"], ["displayName", "groups"],
    ["displayName", "members.value"], ["displayName", "DISPLAYNAME"],
])
def test_group_create_declaration_rejects_unsupported_or_duplicate_attributes(create_attrs):
    groups = {"enabled": True, "match_attributes": ["externalId"],
              "create_attributes": create_attrs}
    if create_attrs[-1] == "DISPLAYNAME":
        with pytest.raises(InvalidMapping):
            assess(mapping(groups=groups))
    else:
        report = assess(mapping(groups=groups))
        assert report["status"] == "blocked"
        assert "unsupported_attribute" in codes(report)


def test_group_create_declaration_does_not_imply_group_permissions_or_live_idp_acceptance():
    groups = {"enabled": True, "match_attributes": ["displayName"],
              "create_attributes": ["displayName", "members"]}
    report = assess(mapping(client="okta", groups=groups))
    assert report["status"] == "requires_live_validation"
    assert {"groups_not_certified", "group_displayname_collision_risk",
            "live_validation_required"} <= codes(report)
    assert "missing_group_create_displayname" not in codes(report)
    assert "group_externalid_match_not_created" not in codes(report)


def test_group_mapping_cannot_be_enabled_implicitly():
    report = assess(mapping(groups={"enabled": False, "match_attributes": ["displayName"]}))
    assert "disabled_groups_with_mapping" in codes(report)
    report = assess(mapping(groups={"enabled": True, "match_attributes": []}))
    assert "missing_group_match" in codes(report)


@pytest.mark.parametrize("text", [
    '{"client":"entra","CLIENT":"okta"}',
    '{"client":"entra","client":"okta"}',
    '{"client":NaN}',
    '{"client":Infinity}',
    "[" * 1100 + "0" + "]" * 1100,
    "x" * 32769,
], ids=["case-colliding-keys", "duplicate-keys", "nonfinite-nan",
        "nonfinite-infinity", "excessive-nesting", "oversized-input"])
def test_bad_json_is_refused_before_mapping(text):
    with pytest.raises(InvalidMapping):
        load_text(text)


def test_unknown_keys_and_ambiguous_mapping_types_fail_closed():
    with pytest.raises(InvalidMapping):
        assess(mapping(token="secret"))
    with pytest.raises(InvalidMapping):
        assess(mapping(groups={"enabled": 1, "match_attributes": []}))
    with pytest.raises(InvalidMapping):
        assess(mapping(users={**mapping()["users"], "match_attributes": ["userName", "USERNAME"]}))
    with pytest.raises(InvalidMapping):
        assess(mapping(client="unknown"))


def test_cli_exit_codes_and_no_sensitive_echo(tmp_path):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(mapping()), encoding="utf-8")
    passed = subprocess.run([sys.executable, str(SCRIPT), str(ok)], capture_output=True, text=True)
    assert passed.returncode == 0 and json.loads(passed.stdout)["status"] == "requires_live_validation"
    blocked = tmp_path / "blocked.json"
    blocked.write_text(json.dumps(mapping(users={**mapping()["users"], "match_attributes": ["emails"]})), encoding="utf-8")
    failed = subprocess.run([sys.executable, str(SCRIPT), str(blocked)], capture_output=True, text=True)
    assert failed.returncode == 2 and json.loads(failed.stdout)["status"] == "blocked"
    invalid = subprocess.run([sys.executable, str(SCRIPT), "-"], input=b'{"token":"secret"}', capture_output=True)
    assert invalid.returncode == 1 and b"secret" not in invalid.stdout + invalid.stderr


def test_shipped_contact_array_writes_are_descriptive_only():
    users = mapping()["users"]
    users["create_attributes"].append("emails")
    users["patch_attributes"].extend([
        "emails", "emails.primary", 'emails[type eq "work"].value',
        'emails[value eq "alias@example.invalid"].display',
    ])
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "unsupported_attribute" not in codes(report)
    assert {"unverified_contact_metadata", "contact_value_selector"} <= codes(report)
    assert "alias@example.invalid" not in json.dumps(report)


@pytest.mark.parametrize("bad_path", [
    "emails.value", "emails[type co \"work\"].value",
    'emails[type eq "work" and primary eq true].value',
    'emails[type eq "work"].unknown',
    'emails[type eq ""].value',
    'emails[type eq "work"].value.extra',
    'emails[value eq "bad\\u0000"].primary',
    "emails[0].value",
], ids=[
    "create-child", "unsupported-operator", "compound-selector",
    "unknown-child", "empty-selector", "nested-child",
    "control-selector", "array-index",
])
def test_contact_create_does_not_accept_patch_only_or_malformed_paths(bad_path):
    users = mapping()["users"]
    users["create_attributes"].append(bad_path)
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert "unsupported_attribute" in codes(report)


def test_contact_matching_remains_blocked_even_with_shipped_contact_schema():
    users = mapping()["users"]
    users["match_attributes"] = ['emails[type eq "work"].value']
    users["create_attributes"].append("emails")
    users["patch_attributes"].append('emails[type eq "work"].value')
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert "unsupported_attribute" in codes(report)
    assert "unverified_contact_metadata" in codes(report)


def test_contact_patch_rejects_invalid_selector_syntax_and_does_not_echo_values():
    users = mapping()["users"]
    users["patch_attributes"].extend([
        'emails[type eq "work"].value',
        'emails[value eq "private@example.invalid"].primary',
    ])
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "private@example.invalid" not in json.dumps(report)
    for bad in ('emails[type co "work"].value',
                'emails[type eq "work" and value eq "x"].primary',
                'emails[type eq "work"].password',
                'emails[type eq ""].primary',
                'emails[value eq "bad\\u0000"].primary'):
        users["patch_attributes"] = [bad]
        assert "unsupported_attribute" in codes(assess(mapping(users=users)))


@pytest.mark.parametrize("path,accepted", [
    ('EMAILS[TYPE EQ "WoRK"].VALUE', True),
    (r'emails[type eq "\u0041"].value', True),
    (r'emails[type eq "\U0041"].value', False),
    ('emails[type eq "' + 'x' * 64 + '"].value', True),
    ('emails[type eq "' + 'x' * 65 + '"].value', False),
    ('emails[type eq "' + 'ß' * 64 + '"].value', True),
    ('emails[value eq "' + 'x' * 320 + '"].primary', True),
    ('emails[value eq "' + 'x' * 321 + '"].primary', False),
    ('emails[type\u00a0eq\u00a0"work"].value', False),
    ('emails[type\u2003eq\u2003"work"].value', False),
    (r'emails[value eq "bad\u0000"].primary', False),
    (r'emails[value eq "bad\\u0000"].primary', True),
    (r'emails[type eq "wo\u0080rk"].value', False),
    (r'emails[type eq "wo\u0085rk"].value', False),
    (r'emails[type eq "wo\u009frk"].value', False),
    (r'emails[value eq "wo\uffferk"].primary', False),
    (r'emails[value eq "wo\uffffrk"].primary', False),
    (r'emails[value eq "wo\ufffdrk"].primary', True),
])
def test_selector_declarations_agree_with_shipped_patch_parser(path, accepted):
    # Differential check against the real shipped grammar, not a mirrored
    # validator. Literal escapes must be parsed before structural case folding.
    from gateway.scim_user_emails import _path
    from gateway.scim_provisioning import Error

    try:
        _path(path)
        shipped_accepts = True
    except Error:
        shipped_accepts = False
    assert shipped_accepts is accepted
    users = mapping()["users"]
    users["patch_attributes"] = [path]
    result = assess(mapping(users=users))
    assert ("unsupported_attribute" not in codes(result)) is accepted
    assert result["checked"]["user_matching"] == 2
    assert path not in json.dumps(result)


@pytest.mark.parametrize("character", ['\u0080', '\u0085', '\u009f', '\ufffe', '\uffff'])
def test_raw_control_or_noncharacter_in_declared_path_is_invalid(character):
    users = mapping()["users"]
    users["patch_attributes"] = ['emails[type eq "wo' + character + 'rk"].value']
    with pytest.raises(InvalidMapping, match="invalid path"):
        assess(mapping(users=users))


def test_declared_group_name_policy_is_optional_and_not_server_configuration():
    groups = {"enabled": True, "match_attributes": ["displayName"]}
    assert "entra_group_name_uniqueness" in codes(assess(mapping(groups=groups)))
    declared = mapping(groups=groups, provider_policy={
        "allow_unversioned_user_patch": False, "unique_group_names": True})
    report = assess(declared)
    assert report["status"] == "requires_live_validation"
    assert "entra_group_name_uniqueness" not in codes(report)
    assert {"declared_group_name_policy", "groups_not_certified", "live_validation_required"} <= codes(report)
    for invalid in [1, "true", None]:
        declared["provider_policy"]["unique_group_names"] = invalid
        with pytest.raises(InvalidMapping):
            assess(declared)


@pytest.mark.parametrize("token", [
    "1e309", "-1e309", "1.5e999999", "9.9e400",
])
def test_json_exponent_overflow_is_refused_even_in_unused_nested_data(token):
    # Python's JSON parser otherwise converts valid exponent syntax to inf,
    # unlike the already-refused literal NaN/Infinity constants.
    for payload in (token, '{"unused":{"nested":[' + token + ']}}'):
        with pytest.raises(InvalidMapping):
            load_text(payload)


@pytest.mark.parametrize("sign", ["", "-"])
def test_json_integer_digit_bound_is_enforced_before_conversion(sign):
    allowed = sign + "9" * 128
    oversized = sign + "9" * 129
    assert load_text(allowed) == int(allowed)
    with pytest.raises(InvalidMapping):
        load_text(oversized)
    with pytest.raises(InvalidMapping):
        load_text('{"discarded":[' + oversized + ']}')


def test_finite_exponents_and_normal_mapping_remain_compatible():
    assert load_text('{"finite":[1e308,-1e-308,0.5]}') == {
        "finite": [1e308, -1e-308, 0.5],
    }
    assert assess(load_text(json.dumps(mapping())))["status"] == "requires_live_validation"


@pytest.mark.parametrize("payload", [
    r'"\ud800"',
    r'"\udfff"',
    r'{"unused":["\ud800"]}',
    r'{"unused":{"\udfff":"value"}}',
    r'{"unused":{"nested":{"key":"\ud83d"}}}',
], ids=["lone-high", "lone-low", "nested-value", "nested-key", "deep-value"])
def test_unpaired_surrogate_escapes_are_refused_even_in_unused_fields(payload):
    with pytest.raises(InvalidMapping):
        load_text(payload)


@pytest.mark.parametrize("text", [
    chr(0xD800),
    '{"unused":"' + chr(0xDFFF) + '"}',
], ids=["raw-high", "raw-low-in-object"])
def test_direct_load_text_normalizes_unencodable_unicode_to_invalid_mapping(text):
    with pytest.raises(InvalidMapping):
        load_text(text)


def test_valid_supplementary_unicode_surrogate_pair_is_preserved():
    assert load_text(r'{"unused":"\ud83d\ude80"}') == {"unused": "\U0001f680"}
    assert load_text('{"unused":"\U0001f680"}') == {"unused": "\U0001f680"}


def test_cli_surrogate_rejection_is_generic_and_does_not_echo_input():
    invalid = subprocess.run(
        [sys.executable, str(SCRIPT), "-"],
        input=br'{"unused":"\ud800"}',
        capture_output=True,
    )
    assert invalid.returncode == 1
    assert json.loads(invalid.stdout)["status"] == "invalid_input"
    assert b"unused" not in invalid.stdout + invalid.stderr
    assert b"ud800" not in invalid.stdout + invalid.stderr


CORE_USER = "urn:ietf:params:scim:schemas:core:2.0:User:"


@pytest.mark.parametrize("path,accepted", [
    (CORE_USER + "emails", True),
    (CORE_USER.upper() + "EMAILS.PRIMARY", True),
    (CORE_USER + 'emails[type eq "work"].value', True),
    (CORE_USER + 'emails[value eq "alias@example.invalid"].display', True),
    (CORE_USER + r'emails[type eq "\u0041"].value', True),
    (CORE_USER + r'emails[type eq "\U0041"].value', False),
    (CORE_USER + r'emails[value eq "bad\u0000"].primary', False),
    (CORE_USER + 'emails[type eq ""].value', False),
    (CORE_USER + 'emails[type eq "work"].password', False),
    (CORE_USER + 'emails[type\u00a0eq\u00a0"work"].value', False),
    (CORE_USER + ":emails", False),
    ("urn:ietf:params:scim:schemas:core:2.0:Group:emails", False),
    ("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:emails", False),
], ids=[
    "whole-array", "mixed-case-child", "type-selector", "value-selector",
    "valid-json-escape", "invalid-json-escape", "decoded-control",
    "empty-selector", "unsupported-child", "unicode-separator",
    "extra-colon", "foreign-group-urn", "foreign-extension-urn",
])
def test_core_qualified_contact_patch_matches_shipped_parser(path, accepted):
    # Differential against the actual shipped PATCH path parser, not a
    # mirrored regex. Preflight never makes a provider or provisioning call.
    from gateway.scim_user_emails import _path
    from gateway.scim_provisioning import Error

    try:
        _path(path)
        shipped_accepts = True
    except Error:
        shipped_accepts = False
    assert shipped_accepts is accepted

    users = mapping()["users"]
    users["patch_attributes"] = [path]
    report = assess(mapping(users=users))
    assert ("unsupported_attribute" not in codes(report)) is accepted
    assert report["checked"]["user_matching"] == 2
    assert path not in json.dumps(report)
    if accepted:
        assert report["status"] == "requires_live_validation"
        assert "unverified_contact_metadata" in codes(report)
    else:
        assert report["status"] == "blocked"


def test_qualified_contact_value_selector_retains_cautions_without_value_echo():
    users = mapping()["users"]
    users["patch_attributes"] = [
        CORE_USER + 'emails[value eq "private@example.invalid"].primary',
    ]
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert {"contact_value_selector", "unverified_contact_metadata",
            "live_validation_required"} <= codes(report)
    assert "private@example.invalid" not in json.dumps(report)


def test_core_qualified_contact_does_not_enable_matching_or_creation():
    users = mapping()["users"]
    qualified = CORE_USER + "emails"
    users["match_attributes"] = [qualified]
    users["create_attributes"].append(qualified)
    users["patch_attributes"] = [qualified]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert sum(item["code"] == "unsupported_attribute" for item in report["findings"]) == 2
    assert "unverified_contact_metadata" in codes(report)
    assert qualified not in json.dumps(report)


@pytest.mark.parametrize("first,second", [
    ("emails", CORE_USER + "emails"),
    ("emails.primary", CORE_USER + "EMAILS.PRIMARY"),
    ('emails[type eq "work"].value', r'emails[type eq "\u0077ork"].value'),
    ('emails[type eq "work"].value',
     CORE_USER + 'emails[type eq "WORK"].value'),
    ('emails[type eq "ss"].primary', r'emails[type eq "\u00df"].primary'),
    ('emails[value eq "private@example.invalid"].display',
     CORE_USER + r'emails[value eq "private\u0040example.invalid"].display'),
    ('emails[value eq "alias@example.invalid"].primary',
     r'emails[value eq "\u0061lias@example.invalid"].primary'),
], ids=[
    "qualified-whole-array", "qualified-child", "decoded-type-literal",
    "qualified-casefolded-type", "unicode-casefolded-type",
    "qualified-decoded-value", "decoded-value-literal",
])
def test_semantically_duplicate_contact_patch_targets_fail_closed(first, second):
    # Shipped selector matching decodes JSON escapes and casefolds literals.
    # An IdP mapping must not declare the same effective write target twice,
    # including when its two raw paths are not byte-for-byte duplicates.
    from gateway.scim_user_emails import _path

    def effective(path):
        field, literal, child = _path(path)
        return field, literal.casefold() if literal is not None else None, child

    assert effective(first) == effective(second)
    users = mapping()["users"]
    users["patch_attributes"].extend([first, second])
    with pytest.raises(InvalidMapping, match="duplicate effective contact paths") as exc:
        assess(mapping(users=users))
    assert "private@example.invalid" not in str(exc.value)
    assert "alias@example.invalid" not in str(exc.value)


def test_distinct_contact_patch_targets_remain_supported_and_private():
    users = mapping()["users"]
    users["patch_attributes"].extend([
        "emails", "emails.primary",
        'emails[type eq "work"].value',
        'emails[type eq "work"].primary',
        'emails[value eq "work"].value',
        'emails[type eq "home"].value',
    ])
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "unsupported_attribute" not in codes(report)
    assert {"unverified_contact_metadata", "contact_value_selector"} <= codes(report)


@pytest.mark.parametrize("paths", [
    ["emails", "emails.primary"],
    ["emails", CORE_USER + 'emails[type eq "work"].value'],
    ["emails.primary", 'emails[type eq "work"].primary'],
    ['emails[type eq "work"]', CORE_USER + 'emails[type eq "WORK"].primary'],
    ['emails[type eq "work"]', r'emails[type eq "\u0077ork"].value'],
    ["emails.value", 'emails[type eq "work"]'],
], ids=[
    "whole-array-and-child", "whole-array-and-qualified-selector",
    "all-contacts-child-and-selector-child", "selected-contact-and-child",
    "selected-contact-and-decoded-child", "all-contacts-child-and-selected-contact",
])
def test_overlapping_contact_patch_paths_warn_without_claiming_duplicate_target(paths):
    # The shipped parser supports both operations, but execution order and
    # current stored contacts determine whether one write overwrites another.
    from gateway.scim_user_emails import _path
    for path in paths:
        _path(path)
    users = mapping()["users"]
    users["patch_attributes"] = paths
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert {"overlapping_contact_patch_paths", "unverified_contact_metadata",
            "live_validation_required"} <= codes(report)
    assert "unsupported_attribute" not in codes(report)
    assert not any(path in json.dumps(report) for path in paths)


@pytest.mark.parametrize("paths", [
    ["emails"],
    ["emails.primary", "emails.display"],
    ["emails.value", 'emails[type eq "work"].display'],
    ['emails[type eq "work"].value', 'emails[type eq "work"].primary'],
    ['emails[type eq "work"]', 'emails[type eq "home"].value'],
    ['emails[type eq "work"].value', 'emails[value eq "work"].value'],
], ids=[
    "whole-array-only", "distinct-all-contacts-children",
    "different-child-fields", "distinct-selected-children",
    "distinct-selected-type", "different-selector-kinds",
])
def test_nonoverlapping_declared_contact_paths_do_not_get_overlap_warning(paths):
    users = mapping()["users"]
    users["patch_attributes"] = paths
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "overlapping_contact_patch_paths" not in codes(report)
    assert "unsupported_attribute" not in codes(report)


def test_unsupported_contact_paths_remain_explicit_blockers_not_duplicates():
    users = mapping()["users"]
    users["patch_attributes"] = [
        "emails[unknown eq \"work\"].value",
        CORE_USER + "emails[unknown eq \"work\"].value",
    ]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert sum(finding["code"] == "unsupported_attribute"
               for finding in report["findings"]) == 2


@pytest.mark.parametrize("patch_paths", [
    [],
    ["displayName"],
    ["name.givenName", "externalId"],
    ["emails"],
    ['emails[type eq "private@example.invalid"].primary'],
], ids=[
    "no-patch-attributes", "display-only", "name-and-external-id",
    "contact-only", "selected-contact-only",
])
@pytest.mark.parametrize("client", ["entra", "okta"])
def test_declared_patch_without_active_warns_about_deactivation_gap(patch_paths, client):
    # A syntactically supported IdP mapping can still omit the one attribute
    # needed for PATCH-based deactivation. This is a static rollout caution,
    # not evidence that PUT or another separately tested path is unavailable.
    users = mapping()["users"]
    users["patch_attributes"] = patch_paths
    report = assess(mapping(client=client, users=users))
    assert report["status"] == "requires_live_validation"
    assert "deactivation_patch_not_declared" in codes(report)
    assert "live_validation_required" in codes(report)
    assert "unsupported_attribute" not in codes(report)
    assert "private@example.invalid" not in json.dumps(report)


@pytest.mark.parametrize("patch_paths", [
    ["active"],
    ["ACTIVE"],
    ["name.givenName", "active"],
])
def test_declared_active_patch_does_not_claim_deactivation_certification(patch_paths):
    users = mapping()["users"]
    users["patch_attributes"] = patch_paths
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "deactivation_patch_not_declared" not in codes(report)
    assert "live_validation_required" in codes(report)


def test_missing_active_patch_warning_is_independent_of_unversioned_policy():
    users = mapping()["users"]
    users["write_mode"] = "unversioned_user_patch"
    users["patch_attributes"] = ["displayName"]
    denied = assess(mapping(users=users))
    assert {"deactivation_patch_not_declared", "missing_patch_policy"} <= codes(denied)
    approved = assess(mapping(users=users, provider_policy={
        "allow_unversioned_user_patch": True,
    }))
    assert approved["status"] == "requires_live_validation"
    assert {"deactivation_patch_not_declared", "unversioned_patch_overwrite"} <= codes(approved)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
@pytest.mark.parametrize("create_paths", [
    ["userName"],
    ["userName", "externalId", "displayName"],
    ["userName", "emails"],
], ids=["username-only", "descriptive-fields", "descriptive-contacts"])
def test_create_without_active_cautions_about_default_active_state(client, create_paths):
    # Shipped create_user offboards only for literal active=False. Without an
    # active field in the declared create mapping, disabled-user creation has
    # not been demonstrated. The warning is static, not a provider test.
    users = mapping()["users"]
    users["create_attributes"] = create_paths
    report = assess(mapping(client=client, users=users))
    assert report["status"] == "requires_live_validation"
    assert "creation_active_not_declared" in codes(report)
    assert "deactivation_patch_not_declared" not in codes(report)
    assert "unsupported_attribute" not in codes(report)
    assert "live_validation_required" in codes(report)
    # Reports identify declaration positions/codes, not contact values.
    assert "private@example.invalid" not in json.dumps(report)


@pytest.mark.parametrize("create_paths", [
    ["userName", "active"],
    ["userName", "ACTIVE"],
    ["userName", "active", "name.givenName"],
], ids=["minimal", "case-insensitive", "with-name"])
def test_create_with_declared_active_is_not_certified(create_paths):
    users = mapping()["users"]
    users["create_attributes"] = create_paths
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "creation_active_not_declared" not in codes(report)
    assert "live_validation_required" in codes(report)


def test_missing_create_active_is_independent_of_patch_and_username_blockers():
    users = mapping()["users"]
    users["create_attributes"] = ["displayName"]
    users["patch_attributes"] = ["displayName"]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert {"creation_active_not_declared", "deactivation_patch_not_declared",
            "missing_stable_username"} <= codes(report)
    assert "unsupported_attribute" not in codes(report)


@pytest.mark.parametrize("attribute", ["uſerName", "diſplayName"])
def test_unicode_casefold_alias_does_not_claim_supported_user_attribute(attribute):
    # Python casefold() converts long-s to ASCII s, but shipped SCIM _keys()
    # deliberately uses lower(), so these names cannot be provisioned.
    from gateway import scim_provisioning as scim

    assert attribute.casefold() in {"username", "displayname"}
    assert attribute.lower() not in {"username", "displayname"}
    with pytest.raises(scim.Error):
        scim._keys({attribute: "example"}, {"username", "displayname"})

    users = mapping()["users"]
    users["create_attributes"] = ["userName", "active", attribute]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert "unsupported_attribute" in codes(report)
    assert attribute not in json.dumps(report)


@pytest.mark.parametrize("prefix", [
    "urn:ietf:paramſ:scim:schemas:core:2.0:User:",
    "urn:ietf:params:scim:ſchemas:core:2.0:User:",
])
def test_unicode_casefold_alias_does_not_claim_supported_core_user_prefix(prefix):
    # The shipped _unqualified() uses lower(), not casefold(); the fake
    # core prefix must never be treated as a supported contact write.
    from gateway import scim_provisioning as scim
    from gateway.scim_user_emails import _path

    path = prefix + 'emails[type eq "work"].value'
    with pytest.raises(scim.Error):
        _path(path)
    users = mapping()["users"]
    users["patch_attributes"] = [path, "active"]
    report = assess(mapping(users=users))
    assert report["status"] == "blocked"
    assert "unsupported_attribute" in codes(report)
    assert path not in json.dumps(report)


def test_ascii_case_insensitive_core_prefix_and_unicode_selector_literal_still_work():
    from gateway.scim_user_emails import _path

    path = 'URN:IETF:PARAMS:SCIM:SCHEMAS:CORE:2.0:USER:emails[type eq "Straße"].value'
    assert _path(path) == ("type", "Straße", "value")
    users = mapping()["users"]
    users["patch_attributes"] = [path, "active"]
    report = assess(mapping(users=users))
    assert report["status"] == "requires_live_validation"
    assert "unsupported_attribute" not in codes(report)
    assert "Straße" not in json.dumps(report)


@pytest.mark.parametrize("client", ["okta", "other"])
def test_non_entra_group_displayname_without_unique_policy_warns_not_blocks(client):
    report = assess(mapping(
        client=client,
        groups={"enabled": True, "match_attributes": ["displayName"]},
    ))
    assert report["status"] == "requires_live_validation"
    assert "group_displayname_collision_risk" in codes(report)
    assert "entra_group_name_uniqueness" not in codes(report)
    assert "groups_not_certified" in codes(report)
    assert "live_validation_required" in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
def test_group_displayname_policy_declaration_only_removes_collision_finding(client):
    report = assess(mapping(
        client=client,
        groups={"enabled": True, "match_attributes": ["displayName"]},
        provider_policy={
            "allow_unversioned_user_patch": False,
            "unique_group_names": True,
        },
    ))
    assert report["status"] == "requires_live_validation"
    assert "group_displayname_collision_risk" not in codes(report)
    assert "entra_group_name_uniqueness" not in codes(report)
    assert "declared_group_name_policy" in codes(report)
    assert "groups_not_certified" in codes(report)
    assert "live_validation_required" in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
def test_group_externalid_matching_does_not_infer_displayname_collision(client):
    report = assess(mapping(
        client=client,
        groups={"enabled": True, "match_attributes": ["externalId"]},
    ))
    assert report["status"] == "requires_live_validation"
    assert "group_displayname_collision_risk" not in codes(report)
    assert "entra_group_name_uniqueness" not in codes(report)
    assert "groups_not_certified" in codes(report)


def test_entra_group_displayname_still_blocks_without_unique_policy():
    report = assess(mapping(
        client="entra",
        groups={"enabled": True, "match_attributes": ["displayName", "externalId"]},
    ))
    assert report["status"] == "blocked"
    assert "entra_group_name_uniqueness" in codes(report)
    assert "group_displayname_collision_risk" not in codes(report)


@pytest.mark.parametrize("client", ["entra", "okta", "other"])
@pytest.mark.parametrize("allow_missing_version", [False, True])
def test_etag_mapping_with_permissive_declared_policy_requires_review(
    client, allow_missing_version,
):
    # A client that normally supplies If-Match does not disable a separately
    # approved server-side exception for standalone User PATCH.
    report = assess(mapping(
        client=client,
        provider_policy={"allow_unversioned_user_patch": allow_missing_version},
    ))
    assert report["status"] == "requires_live_validation"
    expected = "etag_mode_with_unversioned_patch_policy"
    assert (expected in codes(report)) is allow_missing_version
    assert "unversioned_patch_overwrite" not in codes(report)
    assert "missing_patch_policy" not in codes(report)
    assert "live_validation_required" in codes(report)
    if allow_missing_version:
        findings = [item for item in report["findings"] if item["code"] == expected]
        assert len(findings) == 1
        assert findings[0]["severity"] == "caution"
        assert findings[0]["path"] == "provider_policy.allow_unversioned_user_patch"
        assert "current provider policy" in findings[0]["message"]
    assert "private@example.invalid" not in json.dumps(report)


@pytest.mark.parametrize("approved", [False, True])
def test_declared_unversioned_mode_preserves_existing_policy_gate(approved):
    users = mapping()["users"]
    users["write_mode"] = "unversioned_user_patch"
    report = assess(mapping(
        users=users, provider_policy={"allow_unversioned_user_patch": approved},
    ))
    assert "etag_mode_with_unversioned_patch_policy" not in codes(report)
    if approved:
        assert report["status"] == "requires_live_validation"
        assert "unversioned_patch_overwrite" in codes(report)
        assert "missing_patch_policy" not in codes(report)
    else:
        assert report["status"] == "blocked"
        assert "missing_patch_policy" in codes(report)
        assert "unversioned_patch_overwrite" not in codes(report)


def test_unsupported_write_mode_is_still_blocked_with_permissive_policy():
    users = mapping()["users"]
    users["write_mode"] = "unversioned_put"
    report = assess(mapping(
        users=users, provider_policy={"allow_unversioned_user_patch": True},
    ))
    assert report["status"] == "blocked"
    assert "unsupported_write_mode" in codes(report)
    assert "etag_mode_with_unversioned_patch_policy" not in codes(report)


@pytest.mark.parametrize("path,owned", [
    ('emailſ[type eq "work"].value', False),
    ('emails[type eq "work"].prımary', True),
    ('emails[type eq "work"].diſplay', True),
])
def test_unicode_contact_structure_cannot_bypass_shipped_patch_dispatch(path, owned):
    # The shipped regex accepts some Unicode IGNORECASE aliases, but the
    # dispatcher and downstream canonical field validation do not.
    from gateway.scim_user_emails import FIELDS, _path, owns

    assert owns(path) is owned
    field, literal, child = _path(path)
    assert field == "type" and literal == "work"
    if owned:
        assert child not in FIELDS
    users = mapping()["users"]
    users["patch_attributes"] = [path]
    result = assess(mapping(users=users))
    assert result["status"] == "blocked"
    assert "unsupported_attribute" in codes(result)
    assert path not in json.dumps(result)


@pytest.mark.parametrize("path", [
    'EMAILS[TYPE EQ "work"].PRIMARY',
    'urn:ietf:params:scim:schemas:core:2.0:User:Emails[Type Eq "work"].Display',
])
def test_ascii_case_contact_selector_remains_supported_by_shipped_dispatch(path):
    from gateway.scim_user_emails import FIELDS, _path, owns

    assert owns(path)
    field, literal, child = _path(path)
    assert field == "type" and literal == "work" and child in FIELDS
    users = mapping()["users"]
    users["patch_attributes"] = [path]
    result = assess(mapping(users=users))
    assert result["status"] == "requires_live_validation"
    assert "unsupported_attribute" not in codes(result)
    assert path not in json.dumps(result)
