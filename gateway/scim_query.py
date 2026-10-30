"""Bounded read-only SCIM query parsing and non-mutating core projections.

Selection never changes the source resource or its concurrency version. Unknown
paths are refused rather than silently returning a broader identity projection.
"""
from copy import deepcopy
import re

from . import scim_provisioning as scim
from .scim_groups import CORE as GROUP
from .scim_user_names import FIELDS as NAME_FIELDS

SEARCH = 'urn:ietf:params:scim:api:messages:2.0:SearchRequest'
MAX_PATHS = 32
MAX_SELECTION = 4096
MAX_START = 2**31 - 1
_COMMON = {'schemas': None, 'id': None, 'externalid': None,
           'meta': {'resourcetype', 'created', 'lastmodified', 'version', 'location'}}
_FIELDS = {
    scim.CORE: {**_COMMON, 'username': None, 'displayname': None, 'active': None,
                'groups': {'value', 'display', 'type'}, 'name': {key.lower() for key in NAME_FIELDS},
                'emails': {'value', 'type', 'display', 'primary'}},
    GROUP: {**_COMMON, 'displayname': None, 'members': {'value', 'type'}},
}


def _paths(value, core, *, posted):
    if posted:
        if not isinstance(value, list):
            raise scim.Error(400, 'Attribute selection requires an array of paths', 'invalidValue')
        parts = value
    else:
        if not isinstance(value, str) or len(value) > MAX_SELECTION:
            raise scim.Error(400, 'Attribute selection exceeds its bounds', 'invalidValue')
        parts = value.split(',')
    if not 1 <= len(parts) <= MAX_PATHS or any(not isinstance(x, str) for x in parts):
        raise scim.Error(400, 'Select 1–32 supported attribute paths', 'invalidValue')
    if sum(map(len, parts)) > MAX_SELECTION:
        raise scim.Error(400, 'Attribute selection exceeds its bounds', 'invalidValue')
    result = set()
    for part in parts:
        path = part.strip().lower()
        prefix = core.lower() + ':'
        if path.startswith(prefix):
            path = path[len(prefix):]
        names = path.split('.')
        if (len(names) > 2 or names[0] not in _FIELDS[core] or
                (len(names) == 2 and (not _FIELDS[core][names[0]] or names[1] not in _FIELDS[core][names[0]]))):
            raise scim.Error(400, 'Only supported core attribute paths can be selected', 'invalidPath')
        result.add(tuple(names))
    return result


def parse(values, core, *, posted=False, single=False):
    """Return existing list-service kwargs and an optional (include, paths)."""
    allowed = {'attributes', 'excludedattributes'}
    if not single:
        allowed |= {'filter', 'startindex', 'count'}
    if posted:
        allowed.add('schemas')
    normalized = scim._keys(values, allowed)
    if posted and normalized.pop('schemas', None) != [SEARCH]:
        raise scim.Error(400, 'A SearchRequest schema is required', 'invalidSyntax')
    if 'attributes' in normalized and 'excludedattributes' in normalized:
        raise scim.Error(400, 'Use attributes or excludedAttributes, not both', 'invalidValue')
    selection = None
    for name in ('attributes', 'excludedattributes'):
        if name in normalized:
            selection = (name == 'attributes', _paths(normalized[name], core, posted=posted))
    if single:
        return {}, selection
    pagination = {}
    for name, default, minimum, maximum in [('startindex', 1, 1, MAX_START),
            ('count', 100, 0, 200 if core == scim.CORE else 100)]:
        value = normalized.get(name, default)
        if not posted and isinstance(value, str) and re.fullmatch(r'[0-9]{1,10}', value):
            value = int(value)
        if type(value) is not int or not minimum <= value <= maximum:
            raise scim.Error(400, 'Pagination is outside the supported integer bounds', 'invalidValue')
        pagination['start_index' if name == 'startindex' else name] = value
    filter_text = normalized.get('filter', '')
    if not isinstance(filter_text, str) or len(filter_text) > 1000:
        raise scim.Error(400, 'A supported filter string of at most 1000 characters is required', 'invalidFilter')
    return {**pagination, 'filter_text': filter_text}, selection


def query_params(params):
    # Reject duplicates including case aliases before a dict could discard them.
    pairs = params.multi_items()
    if len({name.lower() for name, _ in pairs}) != len(pairs):
        raise scim.Error(400, 'Repeated query option', 'invalidValue')
    return dict(pairs)


def project(resource, selection):
    if selection is None:
        return deepcopy(resource)
    include, paths = selection
    result = {}
    for key, value in resource.items():
        name = key.lower()
        if name in {'id', 'schemas'}:
            result[key] = deepcopy(value)
            continue
        whole = (name,) in paths
        children = {p[1] for p in paths if len(p) == 2 and p[0] == name}
        if whole:
            if include:
                result[key] = deepcopy(value)
        elif children:
            def selected(item):
                return {k: deepcopy(v) for k, v in item.items() if (k.lower() in children) == include}
            if isinstance(value, dict):
                subset = selected(value)
                if subset:
                    result[key] = subset
            elif isinstance(value, list):
                # Preserve array cardinality/order; no members are invented or joined.
                result[key] = [selected(item) for item in value]
        elif not include:
            result[key] = deepcopy(value)
    return result
