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
