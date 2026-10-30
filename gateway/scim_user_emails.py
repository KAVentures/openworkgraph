"""Bounded unverified provider contacts, never roster/login identity authority."""
import json
import re

from . import scim_provisioning as scim

MAX_EMAILS = 16
FIELDS = {'value': 320, 'type': 64, 'display': 512, 'primary': None}
SCHEMA = '''CREATE TABLE IF NOT EXISTS gateway_scim_user_emails (
 provider_id TEXT NOT NULL, employee_id TEXT NOT NULL, emails_json TEXT NOT NULL,
 PRIMARY KEY(provider_id,employee_id),
 FOREIGN KEY(provider_id,employee_id) REFERENCES gateway_scim_users(provider_id,employee_id)
 ON DELETE CASCADE)'''
_PATH = re.compile(r'emails(?:\[(value|type) +eq +("(?:[^"\\]|\\.)*")\])?(?:\.(value|type|display|primary))?', re.I)
_ADDRESS = re.compile(r'[^@\s<>"\\,;]+@[^@\s<>"\\,;]+')


def _fields(value):
    fields = scim._keys(value, set(FIELDS))
    for key, item in fields.items():
        if item is None:
            continue
        if key == 'primary':
            if type(item) is not bool:
                raise scim.Error(400, 'Contact primary must be a JSON boolean', 'invalidValue')
        else:
            scim._text(item, FIELDS[key], blank=key == 'display')
            if key == 'value' and not _ADDRESS.fullmatch(item):
                raise scim.Error(400, 'A bare contact address is required', 'invalidValue')
    return fields


def _entry(value):
    fields = {k: v for k, v in _fields(value).items() if v is not None}
    if 'value' not in fields:
        raise scim.Error(400, 'Each retained contact requires a value', 'invalidValue')
    fields.setdefault('primary', False)
    return fields


def _key(entry):
    return entry['value'].casefold(), entry.get('type', '').casefold()


def normalize(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_EMAILS:
        raise scim.Error(400, 'At most sixteen contact objects are supported', 'invalidValue')
    entries = [_entry(item) for item in value]
    if len({_key(item) for item in entries}) != len(entries):
        raise scim.Error(400, 'Duplicate contact value/type pairs are unsupported', 'invalidValue')
    if sum(item['primary'] for item in entries) > 1:
        raise scim.Error(400, 'Only one contact may be primary', 'invalidValue')
    return sorted(entries, key=_key)


def stored(encoded):
    if encoded is None:
        return []
    try:
        return normalize(json.loads(encoded))
    except (scim.Error, ValueError, TypeError, RecursionError):
        raise scim.Error(503, 'Stored contact metadata is unavailable') from None


def save(db, conn, provider_id, employee_id, emails):
    if emails:
        db._execute(conn, '''INSERT INTO gateway_scim_user_emails
            (provider_id,employee_id,emails_json) VALUES (?,?,?)
            ON CONFLICT(provider_id,employee_id) DO UPDATE SET emails_json=excluded.emails_json''',
            (provider_id, employee_id, json.dumps(emails, sort_keys=True, ensure_ascii=True)))
    else:
        db._execute(conn, 'DELETE FROM gateway_scim_user_emails WHERE provider_id=? AND employee_id=?',
                    (provider_id, employee_id))


def _unqualified(path):
    prefix = scim.CORE + ':'
    return path[len(prefix):] if path[:len(prefix)].lower() == prefix.lower() else path


def owns(path):
    path = _unqualified(path).lower()
    return path == 'emails' or path.startswith(('emails.', 'emails['))


def _path(path):
    scim._text(path, 1024)
    match = _PATH.fullmatch(_unqualified(path))
    if not match:
        raise scim.Error(400, 'Unsupported contact patch path', 'invalidPath')
    field, literal, child = match.groups()
    field, child = field.lower() if field else None, child.lower() if child else None
    if field:
        literal = scim._filter_string(literal)
        scim._text(literal, FIELDS[field])
    return field, literal, child


def _merge(entry, delta):
    result = dict(entry)
    for key, item in delta.items():
        if item is None:
            result.pop(key, None)
        else:
            result[key] = item
    return _entry(result)


def _prefer(entries, selected):
    if len(selected) != 1:
        raise scim.Error(400, 'A primary update must select exactly one contact', 'invalidValue')
    for index, entry in enumerate(entries):
        entry['primary'] = index in selected


def patch(current, path, verb, value=None):
    field, literal, child = _path(path)
    result = [dict(item) for item in current]
    if field is None and child is None:
        if verb == 'remove' or value is None:
            return []
        incoming = normalize(value)
        if verb == 'replace':
            return incoming
        # Validate the whole incoming array before staging any changes. Only
        # supplied children overwrite a matching pair; omitted primary/display
        # cannot accidentally clear an existing preference or descriptive text.
        preferred = None
        for raw in value:
            delta = _fields(raw)
            entry = _entry(raw)
            index = next((i for i, item in enumerate(result) if _key(item) == _key(entry)), len(result))
            if index == len(result):
                result.append(entry)
            else:
                result[index] = _merge(result[index], delta)
            if delta.get('primary') is True:
                preferred = index
        if preferred is not None:
            _prefer(result, [preferred])
        return normalize(result)
    selected = [i for i, item in enumerate(result) if field is None or
                item.get(field, '').casefold() == literal.casefold()]
    if not selected:
        if verb == 'remove':
            return result
        # Explicit bounded interpretation of Microsoft's documented zero-match
        # add shape. Other missing targets never synthesize partial contacts.
        if verb == 'add' and field == 'type' and child == 'value':
            return normalize([*result, {'type': literal, 'value': value}])
        raise scim.Error(400, 'Contact patch target does not exist', 'noTarget')
    if verb == 'remove' and child in (None, 'value'):
        return normalize([item for i, item in enumerate(result) if i not in selected])
    if child:
        delta = _fields({child: None if verb == 'remove' else value})
    else:
        delta = _fields(value)
    for index in selected:
        result[index] = _merge(result[index], delta)
    if delta.get('primary') is True:
        _prefer(result, selected)
    return normalize(result)
