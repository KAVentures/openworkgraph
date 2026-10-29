"""Bounded matching queries; SQL columns come only from these fixed maps."""
import re

from . import scim_provisioning as scim

MAX_CLAUSES = 8
_FIELDS = {'User': {'username': 'e.email', 'externalid': 'l.external_id'},
           'Group': {'displayname': 'display_name', 'externalid': 'external_id'}}
_CLAUSE = re.compile(r'([A-Za-z][A-Za-z0-9]*)\s+eq\s+("(?:[^"\\]|\\.)*")', re.I)
_AND = re.compile(r'\s+and\s+', re.I)


def matching_where(text, resource):
    """Return a parameterized AND suffix, never interpolate client field names."""
    fields = _FIELDS[resource]
    if not isinstance(text, str) or len(text) > 1000:
        raise scim.Error(400, 'A matching filter must be at most 1000 characters', 'invalidFilter')
    if text == '':
        return '', ()
    position, columns, values = 0, [], []
    while True:
        clause = _CLAUSE.match(text, position)
        if not clause or clause[1].lower() not in fields or len(columns) >= MAX_CLAUSES:
            raise scim.Error(400, 'Use 1–8 supported equality clauses joined by and', 'invalidFilter')
        name = clause[1].lower()
        value = scim._filter_string(clause[2])
        columns.append(fields[name])
        values.append(value.strip().lower() if name == 'username' else value)
        position = clause.end()
        if position == len(text):
            return ''.join(' AND ' + column + '=?' for column in columns), tuple(values)
        separator = _AND.match(text, position)
        if not separator:
            raise scim.Error(400, 'Use only equality clauses joined by and', 'invalidFilter')
        position = separator.end()
