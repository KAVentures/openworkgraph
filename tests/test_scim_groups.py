"""Provider-scoped SCIM Groups HTTP contract; metadata is never an access grant."""
import pytest

from gateway import scim_groups as groups, scim_provisioning as scim, employees as roster
from test_scim_routes import http, user
from test_process_collaboration import store  # shared SQLite/PostgreSQL fixture used by http


def group(**values):
    return {"schemas": [groups.CORE], "displayName": "Analysts", **values}


def test_group_create_schema_query_etag_and_delete(http):
    client, db, owner, h, base, pid = http
    types = client.get(base + "/ResourceTypes", headers=h).json()["Resources"]
    assert {"User", "Group"} == {x["id"] for x in types}
    assert client.get(base + "/ResourceTypes/Group", headers=h).json()["schema"] == groups.CORE
    assert client.get(base + "/Schemas/" + groups.CORE, headers=h).status_code == 200

    created = client.post(base + "/Groups", headers=h, json=group(externalId="analytics"))
    assert created.status_code == 201, created.text
    result = created.json()
    assert result["members"] == [] and result["meta"]["resourceType"] == "Group"
    location, etag = created.headers["location"], created.headers["etag"]
    assert etag == result["meta"]["version"] and created.headers["cache-control"] == "no-store"
    assert client.get(location, headers=h).json() == result
    query = client.get(base + "/Groups", headers=h,
                       params={"filter": 'externalId eq "analytics"'}).json()
    assert query["totalResults"] == 1 and query["Resources"] == [result]
    assert client.get(base + "/Groups", headers=h, params={"count": 0}).json()["Resources"] == []
    assert client.get(base + "/Groups", headers=h, params={"startIndex": 2}).json()["Resources"] == []

    assert client.put(location, headers=h, json=group()).status_code == 428
    assert client.delete(location, headers={**h, "If-Match": "stale"}).status_code == 412
    unchanged = client.put(location, headers={**h, "If-Match": etag}, json=group(externalId="analytics"))
    assert unchanged.status_code == 200 and unchanged.headers["etag"] == etag
    changed = client.patch(location, headers={**h, "If-Match": etag},
                           json={"schemas": [scim.PATCH], "Operations": [
                               {"op": "replace", "path": "displayName", "value": "Operations"}]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["displayName"] == "Operations" and changed.headers["etag"] != etag
    assert client.delete(location, headers={**h, "If-Match": changed.headers["etag"]}).status_code == 204
    assert client.get(location, headers=h).status_code == 404
    assert client.get(base + "/Groups", headers=h).json()["totalResults"] == 0


def test_direct_members_are_provider_scoped_and_offboard_rehire_never_restores_old_link(http):
    client, db, owner, h, base, pid = http
    created_user = client.post(base + "/Users", headers=h, json=user())
    assert created_user.status_code == 201, created_user.text
    uid = created_user.json()["id"]
    made = client.post(base + "/Groups", headers=h, json=group(
        members=[{"value": uid, "type": "User"}]))
    assert made.status_code == 201, made.text
    group_url, before = made.headers["location"], made.headers["etag"]
    assert made.json()["members"] == [{"value": uid, "type": "User"}]
    assert client.get(group_url, headers=h).json()["members"] == [{"value": uid, "type": "User"}]
    assert client.get("/v1/human/processes", headers=h).status_code == 401

    # Ordinary roster offboarding is independent of SCIM (the group must not
    # silently acquire the user's new epoch after a rehire).
    roster.offboard_employee(db, "acme", uid)
    stale = client.get(group_url, headers=h)
    assert stale.status_code == 200 and stale.json()["members"] == []
    assert stale.headers["etag"] != before
    assert client.patch(group_url, headers={**h, "If-Match": before},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "replace", "path": "displayName", "value": "New"}]}).status_code == 412
    # User reactivation by the current provider never restores the old link.
    inactive = client.get(base + "/Users/" + uid, headers=h)
    activation = client.patch(base + "/Users/" + uid,
        headers={**h, "If-Match": inactive.headers["etag"]},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "replace", "path": "active", "value": True}]})
    assert activation.status_code == 200, activation.text
    assert client.get(group_url, headers=h).json()["members"] == []
    latest = client.get(group_url, headers=h)
    repaired = client.patch(group_url, headers={**h, "If-Match": latest.headers["etag"]},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "replace", "path": "members", "value": [{"value": uid}]}]})
    assert repaired.status_code == 200 and repaired.json()["members"] == [{"value": uid, "type": "User"}]


def test_cross_provider_invalid_members_duplicate_and_stale_versions(http):
    client, db, owner, h, base, pid = http
    user_result = client.post(base + "/Users", headers=h, json=user())
    uid = user_result.json()["id"]
    other = client.post("/v1/admin/scim-providers/acme", headers=owner, json={"label": "Other IdP"})
    assert other.status_code == 201, other.text
    second_id, second_token = other.json()["provider"]["provider_id"], other.json()["token"]
    client.patch("/v1/admin/scim-providers/acme/" + second_id,
        headers=owner, json={"revision": 1, "enabled": True})
    other_h = {"Authorization": "Bearer " + second_token, "Content-Type": "application/scim+json"}
    other_url = "/scim/v2/" + second_id
    assert client.post(other_url + "/Groups", headers=other_h,
        json=group(members=[{"value": uid}])).status_code == 404
    created = client.post(base + "/Groups", headers=h, json=group(members=[{"value": uid}]))
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    assert client.get(other_url + "/Groups/" + gid, headers=other_h).status_code == 404
    assert client.get(base + "/Groups/" + gid, headers=other_h).status_code == 401
    assert client.get(other_url + "/Groups", headers=other_h).json()["totalResults"] == 0
    assert client.post(base + "/Groups", headers=h, json=group(
        members=[{"value": uid}, {"value": uid}])).status_code == 400
    assert client.post(base + "/Groups", headers=h, json=group(
        members=[{"value": "unknown"}])).status_code == 404
    assert client.post(base + "/Groups", headers=h, json=group(
        members=[{"value": uid, "type": "Group"}])).status_code == 400
    assert client.post(base + "/Groups", headers=h, json=group(
        members=[{"value": uid, "display": "secret"}])).status_code == 400
    assert client.get(base + "/Groups", headers=h,
        params={"filter": "members pr"}).status_code == 400
    assert client.get(base + "/Groups", headers=h, params={"count": 101}).status_code == 400
    assert client.get(base + "/Groups", headers=h,
        params=[("count", "1"), ("count", "2")]).status_code == 400


def test_member_add_and_selected_remove_do_not_clobber_other_members(http):
    client, db, owner, h, base, pid = http
    first = client.post(base + "/Users", headers=h, json=user()).json()["id"]
    second_payload = {**user(), "userName": "second@acme.test", "externalId": "second"}
    second_response = client.post(base + "/Users", headers=h, json=second_payload)
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()["id"]
    created = client.post(base + "/Groups", headers=h, json=group(members=[{"value": first}]))
    assert created.status_code == 201, created.text
    loc, etag = created.headers["location"], created.headers["etag"]
    added = client.patch(loc, headers={**h, "If-Match": etag},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "add", "path": "members", "value": {"value": second}}]})
    assert added.status_code == 200, added.text
    assert {m["value"] for m in added.json()["members"]} == {first, second}
    # Exact member removal, including removing an absent member, is a no-op
    # for the other members and does not rewrite unrelated metadata.
    removed = client.patch(loc, headers={**h, "If-Match": added.headers["etag"]},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "remove", "path": f'members[value eq "{first}"]'}]})
    assert removed.status_code == 200, removed.text
    assert removed.json()["members"] == [{"value": second, "type": "User"}]
    again = client.patch(loc, headers={**h, "If-Match": removed.headers["etag"]},
        json={"schemas": [scim.PATCH], "Operations": [
            {"op": "remove", "path": f'members[value eq "{first}"]'}]})
    assert again.status_code == 200 and again.headers["etag"] == removed.headers["etag"]



def test_user_groups_is_read_only_and_changes_user_etag(http):
    client, db, owner, h, base, pid = http
    created_user = client.post(base + "/Users", headers=h, json=user())
    assert created_user.status_code == 201, created_user.text
    uid = created_user.json()["id"]
    original_etag = created_user.headers["etag"]
    created_group = client.post(base + "/Groups", headers=h,
        json=group(members=[{"value": uid}]))
    assert created_group.status_code == 201, created_group.text
    gid = created_group.json()["id"]
    current = client.get(base + "/Users/" + uid, headers=h)
    assert current.status_code == 200 and current.headers["etag"] != original_etag
    assert current.json()["groups"] == [{"value": gid, "display": "Analysts", "type": "direct"}]
    assert client.get(base + "/Users", headers=h).json()["Resources"][0]["groups"] == current.json()["groups"]
    assert client.get(base + "/Schemas/" + scim.CORE, headers=h).json()["attributes"][-1]["mutability"] == "readOnly"
    patch = {"schemas": [scim.PATCH], "Operations": [
        {"op": "replace", "path": "displayName", "value": "Current"}]}
    assert client.patch(base + "/Users/" + uid, headers={**h, "If-Match": original_etag},
        json=patch).status_code == 412
    # SCIM readOnly groups may be echoed but cannot be edited by a User PUT.
    echoed = {**user(displayName="Updated"), "groups": [{"value": "injected"}]}
    updated = client.put(base + "/Users/" + uid, headers={**h, "If-Match": current.headers["etag"]}, json=echoed)
    assert updated.status_code == 200, updated.text
    assert updated.json()["groups"] == current.json()["groups"]
    assert updated.json()["displayName"] == "Updated"
    removal = client.delete(created_group.headers["location"],
        headers={**h, "If-Match": created_group.headers["etag"]})
    assert removal.status_code == 204
    after = client.get(base + "/Users/" + uid, headers=h)
    assert "groups" not in after.json() and after.headers["etag"] != updated.headers["etag"]


def test_group_audit_failure_is_atomic(http, monkeypatch):
    client, db, owner, h, base, pid = http
    # Patch the same instance used by the actual enterprise app, not a new one.
    from gateway import scim_provisioning
    original = scim_provisioning.Provisioning.audit
    def fail(self, *args):
        if len(args) >= 4 and str(args[3]).startswith("scim.group."):
            raise RuntimeError("owned injected group audit failure")
        return original(self, *args)
    monkeypatch.setattr(scim_provisioning.Provisioning, "audit", fail)
    with pytest.raises(RuntimeError):
        # TestClient's exception propagation is intentional; the DB must roll back.
        client.post(base + "/Groups", headers=h, json=group(externalId="rollback"))
    assert client.get(base + "/Groups", headers=h).json()["totalResults"] == 0
