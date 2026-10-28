"""Provider-owned SCIM Groups. Metadata only: membership never grants OWG access.

Direct User members are namespaced to the provisioning provider. Each link is
bound to the employee's offboarding generation; a deleted/offboarded/rehired
employee cannot silently recover an old membership.
"""
import hashlib
import json
import re
import uuid

from . import employees as roster, scim_provisioning as scim

CORE = "urn:ietf:params:scim:schemas:core:2.0:Group"
MAX_GROUPS = 100
MAX_MEMBERS = 200
_GROUP_ID = re.compile(r"grp_[a-f0-9]{32}\Z")


def _fields(body, *, patch=False):
    values = scim._keys(body, {"schemas", "id", "meta", "displayname", "externalid", "members"})
    if values.get("schemas") != [CORE]:
        raise scim.Error(400, "Only the core Group schema is supported", "invalidSyntax")
    if patch and ("id" in values or "meta" in values):
        raise scim.Error(400, "Group identity and metadata cannot be modified", "mutability")
    return {key: val for key, val in values.items() if key not in {"schemas", "id", "meta"}}


def _values(values):
    out = {}
    for key, value in values.items():
        if key == "displayname":
            out[key] = " ".join(scim._text(value, 120).split())
        elif key == "externalid":
            out[key] = None if value is None or value == "" else scim._text(value, 200)
        elif key == "members":
            if not isinstance(value, list) or len(value) > MAX_MEMBERS:
                raise scim.Error(400, "Group members must be an array of at most 200 Users", "invalidValue")
            result, seen = [], set()
            for item in value:
                item = scim._keys(item, {"value", "type"})
                uid = scim._text(item.get("value"), 100)
                if item.get("type", "User") != "User" or uid in seen:
                    raise scim.Error(400, "Only distinct direct User members are supported", "invalidValue")
                seen.add(uid)
                result.append(uid)
            out[key] = sorted(result)
        else:
            raise scim.Error(400, "Unsupported Group attribute", "invalidPath")
    return out


def _epoch(db, conn, uid):
    row = roster._one(db, conn,
        "SELECT generation FROM gateway_employee_access_epochs WHERE employee_id=?", (uid,))
    return int(row["generation"]) if row else 0


def _effective(db, conn, provider, raw):
    """Filter revoked links and foreign/deleted employees without rewriting history."""
    entries = json.loads(raw)
    members = []
    for entry in entries:
        uid = entry["value"]
        row = roster._one(db, conn, """SELECT e.status, e.organization_id, l.deleted_at
            FROM gateway_scim_users l JOIN gateway_employees e ON e.employee_id=l.employee_id
            WHERE l.provider_id=? AND l.employee_id=?""", (provider["provider_id"], uid))
        if (row and row["organization_id"] == provider["organization_id"] and
                row["status"] == "active" and row["deleted_at"] is None and
                _epoch(db, conn, uid) == entry["epoch"]):
            members.append(uid)
    return sorted(members)


def _project(db, conn, provider, row):
    active = _effective(db, conn, provider, row["members_json"])
    resource = {
        "schemas": [CORE], "id": row["group_id"],
        "displayName": row["display_name"],
        "members": [{"value": uid, "type": "User"} for uid in active],
    }
    if row["external_id"] is not None:
        resource["externalId"] = row["external_id"]
    digest = hashlib.sha256(json.dumps(
        [resource, row["revision"], row["deleted_at"]],
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    resource["meta"] = {
        "resourceType": "Group", "created": row["created_at"],
        "lastModified": row["updated_at"], "version": 'W/"' + digest + '"',
    }
    return resource


class Groups:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def _load(self, conn, provider, gid):
        # UUID-only keys avoid paths/ambiguous externalId-based identity adoption.
        if not isinstance(gid, str) or not _GROUP_ID.fullmatch(gid):
            raise scim.Error(404, "Group not found")
        suffix = " FOR UPDATE" if self.db.is_postgres else ""
        row = roster._one(self.db, conn, """SELECT * FROM gateway_scim_groups
            WHERE provider_id=? AND group_id=? AND deleted_at IS NULL""" + suffix,
            (provider["provider_id"], gid))
        if not row:
            raise scim.Error(404, "Group not found")
        return row

    def _members(self, conn, provider, ids):
        entries = []
        for uid in ids:
            # Service.load enforces exact provider, current roster identity and tombstone.
            row = self.service.load(conn, provider, uid)
            if row["status"] != "active":
                raise scim.Error(400, "Inactive Users cannot be assigned to Groups", "invalidValue")
            entries.append({"value": uid, "epoch": _epoch(self.db, conn, uid)})
        return json.dumps(entries, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _conditional(current, version):
        if not version:
            raise scim.Error(428, "Read the current Group and supply its exact If-Match version")
        if version != current["meta"]["version"]:
            raise scim.Error(412, "Group changed; read the current version before updating")

    def create(self, provider_id, token, body):
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id, token=token)
            values = _values(_fields(body))
            if "displayname" not in values:
                raise scim.Error(400, "displayName is required", "invalidValue")
            n = roster._one(self.db, conn, """SELECT count(*) AS n FROM gateway_scim_groups
                WHERE provider_id=?""", (provider_id,))["n"]
            if n >= MAX_GROUPS:
                raise scim.Error(409, "Provider Group history is limited to 100 resources")
            gid, now = "grp_" + uuid.uuid4().hex, roster._now()
            members = self._members(conn, provider, values.get("members", []))
            self.db._execute(conn, """INSERT INTO gateway_scim_groups
                (provider_id,group_id,display_name,external_id,members_json,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,1,?,?)""",
                (provider_id, gid, values["displayname"], values.get("externalid"), members, now, now))
            self.service.audit(conn, provider["organization_id"], provider_id, "scim.group.created",
                {"group_id": gid, "member_count": len(values.get("members", []))})
            return _project(self.db, conn, provider, self._load(conn, provider, gid))

    def get(self, provider_id, token, gid):
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id, token=token)
            return _project(self.db, conn, provider, self._load(conn, provider, gid))

    def list(self, provider_id, token, *, start_index=1, count=100, filter_text=""):
        if type(start_index) is not int or start_index < 1 or type(count) is not int or not 0 <= count <= 100:
            raise scim.Error(400, "startIndex must be positive and count 0–100", "invalidValue")
        where, args = "", ()
        if filter_text:
            match = re.fullmatch(r'(displayName|externalId)\s+eq\s+("(?:[^"\\]|\\.)*")',
                                 filter_text, flags=re.I) if isinstance(filter_text, str) and len(filter_text) <= 1000 else None
            if not match:
                raise scim.Error(400, "Only displayName eq or externalId eq is supported", "invalidFilter")
            try:
                value = json.loads(match.group(2))
            except ValueError:
                raise scim.Error(400, "Invalid filter", "invalidFilter") from None
            where = " AND " + ("display_name" if match.group(1).lower() == "displayname" else "external_id") + "=?"
            args = (value,)
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id, token=token)
            params = (provider_id, *args)
            base = " FROM gateway_scim_groups WHERE provider_id=? AND deleted_at IS NULL" + where
            total = roster._one(self.db, conn, "SELECT count(*) AS n" + base, params)["n"]
            rows = roster._all(self.db, conn, "SELECT *" + base +
                " ORDER BY display_name,group_id LIMIT ? OFFSET ?", (*params, count, start_index - 1))
            return {"schemas": [scim.LIST], "totalResults": total, "startIndex": start_index,
                    "itemsPerPage": len(rows),
                    "Resources": [_project(self.db, conn, provider, row) for row in rows]}

    def replace(self, provider_id, token, gid, version, body):
        return self._update(provider_id, token, gid, version, body, patch=False)

    def patch(self, provider_id, token, gid, version, body):
        return self._update(provider_id, token, gid, version, body, patch=True)

    def _update(self, provider_id, token, gid, version, body, *, patch):
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id, token=token)
            row = self._load(conn, provider, gid)
            current = _project(self.db, conn, provider, row)
            self._conditional(current, version)
            if patch:
                envelope = scim._keys(body, {"schemas", "operations"})
                operations = envelope.get("operations")
                if envelope.get("schemas") != [scim.PATCH] or not isinstance(operations, list) or not 1 <= len(operations) <= 20:
                    raise scim.Error(400, "A PatchOp with 1–20 operations is required", "invalidSyntax")
                values = {}
                for item in operations:
                    op = scim._keys(item, {"op", "path", "value"})
                    verb, path = op.get("op"), op.get("path")
                    if not isinstance(verb, str) or not isinstance(path, str):
                        raise scim.Error(400, "Explicit Group patch operation/path required", "invalidSyntax")
                    path = path.lower()
                    if path not in {"displayname", "externalid", "members"}:
                        raise scim.Error(400, "Unsupported Group patch path", "invalidPath")
                    if verb.lower() == "remove":
                        if path == "displayname":
                            raise scim.Error(400, "displayName is required", "mutability")
                        values[path] = None if path == "externalid" else []
                    elif verb.lower() in {"add", "replace"} and "value" in op:
                        values.update(_values({path: op["value"]}))
                    else:
                        raise scim.Error(400, "Invalid Group patch operation", "invalidSyntax")
            else:
                values = _values(_fields(body, patch=True))
                if "displayname" not in values:
                    raise scim.Error(400, "displayName is required", "invalidValue")
                values.setdefault("externalid", None)
                values.setdefault("members", [])
            name = values.get("displayname", row["display_name"])
            external = values.get("externalid", row["external_id"])
            # Preserve any pre-existing deauthorized links unless an explicit member
            # replacement occurs; they remain invisible and non-authoritative.
            members = (self._members(conn, provider, values["members"]) if "members" in values
                       else row["members_json"])
            if (name, external, members) == (row["display_name"], row["external_id"], row["members_json"]):
                return current
            now = roster._now()
            self.db._execute(conn, """UPDATE gateway_scim_groups SET display_name=?,
                external_id=?,members_json=?,revision=revision+1,updated_at=? WHERE group_id=? AND provider_id=?""",
                (name, external, members, now, gid, provider_id))
            self.service.audit(conn, provider["organization_id"], provider_id, "scim.group.updated",
                {"group_id": gid})
            return _project(self.db, conn, provider, self._load(conn, provider, gid))

    def delete(self, provider_id, token, gid, version):
        with self.service.transaction() as conn:
            provider = self.service.provider(conn, provider_id, token=token)
            row = self._load(conn, provider, gid)
            self._conditional(_project(self.db, conn, provider, row), version)
            now = roster._now()
            self.db._execute(conn, """UPDATE gateway_scim_groups SET deleted_at=?,revision=revision+1,
                updated_at=? WHERE provider_id=? AND group_id=?""", (now, now, provider_id, gid))
            self.service.audit(conn, provider["organization_id"], provider_id, "scim.group.deleted",
                {"group_id": gid, "identity_deleted": False})
