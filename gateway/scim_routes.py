"""Authenticated, bounded provisioning protocol and existing-admin configuration."""
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import scim_provisioning as scim, scim_bulk as bulk, scim_groups as group_module
from . import scim_query as query
from .scim_assignment import Assignments
from .scim_client_policy import Policies
from .employees import EmployeeError

MEDIA = 'application/scim+json'
MAX_BODY = bulk.MAX_PAYLOAD
SPC = 'urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'
SCHEMA_URI = 'urn:ietf:params:scim:schemas:core:2.0:Schema'
RESOURCE_TYPE = 'urn:ietf:params:scim:schemas:core:2.0:ResourceType'


def _response(value, *, status=200, headers=None):
    return JSONResponse(value, status_code=status, media_type=MEDIA,
                        headers={'Cache-Control': 'no-store', **(headers or {})})


def _bearer(request):
    headers = request.headers.getlist('authorization')
    if len(headers) != 1 or not headers[0].lower().startswith('bearer '):
        raise scim.Error(401, 'Current provider-scoped provisioning credentials required')
    token = headers[0][7:].strip()
    if not token or len(token) > 200:
        raise scim.Error(401, 'Current provider-scoped provisioning credentials required')
    return token


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _constant(_):
    raise ValueError('Non-finite JSON number')


async def _body(request):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() not in {MEDIA, 'application/json'}:
        raise scim.Error(415, 'Use application/scim+json or application/json')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise scim.Error(413, 'Provisioning requests must fit 64 KiB')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError('Object required')
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise scim.Error(400, 'A bounded JSON object without duplicate keys is required', 'invalidSyntax') from None


def _list(resources):
    return {'schemas': [scim.LIST], 'totalResults': len(resources), 'startIndex': 1,
            'itemsPerPage': len(resources), 'Resources': resources}


def _schema():
    def attribute(name, kind='string', *, required=False, mutable='readWrite', exact=False, unique='none', multi=False):
        return {'name': name, 'type': kind, 'multiValued': multi, 'required': required,
                'caseExact': exact, 'mutability': mutable, 'returned': 'default', 'uniqueness': unique}
    groups = attribute('groups', 'complex', mutable='readOnly', multi=True)
    groups['subAttributes'] = [
        attribute('value', required=True),
        attribute('display'),
        {**attribute('type', mutable='readOnly'), 'canonicalValues': ['direct']},
    ]
    return {'schemas': [SCHEMA_URI], 'id': scim.CORE, 'name': 'User',
            'description': 'OWG bounded User projection; userName is a stable work-email actor. Groups are read-only provider metadata, not access grants.',
            'attributes': [attribute('userName', required=True, mutable='immutable', unique='server'),
                           attribute('displayName'), attribute('active', 'boolean'), groups]}


class ProvisioningRoute(APIRoute):
    """Keep protocol errors local to this router, including sync handlers."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def respond(request):
            try:
                return await handler(request)
            except scim.Error as error:
                return _response(error.packet(), status=error.status,
                                 headers={'WWW-Authenticate': 'Bearer'} if error.status == 401 else {})
        return respond


def build_router(service, require_admin, base_url):
    router = APIRouter(route_class=ProvisioningRoute)
    groups = group_module.Groups(service)
    assignments = Assignments(service)
    policies = Policies(service)

    def authenticate(provider_id, request):
        token = _bearer(request)
        if request.query_params:
            raise scim.Error(400, 'Unsupported query option', 'invalidValue')
        with service.transaction() as conn:
            service.provider(conn, provider_id, token=token)
        return token

    def url(request, provider_id):
        return base_url(request) + '/scim/v2/' + quote(provider_id, safe='')

    def resource(request, provider_id, value):
        location = url(request, provider_id) + '/Users/' + quote(value['id'], safe='')
        return {**value, 'meta': {**value['meta'], 'location': location}}

    def user_response(request, provider_id, value, *, status=200, selection=None):
        value = resource(request, provider_id, value)
        return _response(query.project(value, selection), status=status,
                         headers={'ETag': value['meta']['version'], 'Location': value['meta']['location']})

    @router.get('/admin/scim.js')
    def admin_client():
        return FileResponse(Path(__file__).with_name('scim_admin_client.js'), media_type='application/javascript',
                            headers={'Cache-Control': 'no-store'})

    @router.get('/v1/admin/scim-providers/{organization}')
    def providers(organization: str, request: Request):
        require_admin(request.headers.get('authorization'))
        try:
            rows = service.list_providers(organization)
        except EmployeeError:
            raise scim.Error(400, 'Invalid organization') from None
        return JSONResponse({'items': [{**row, 'base_url': url(request, row['provider_id'])} for row in rows]},
                            headers={'Cache-Control': 'no-store'})

    @router.post('/v1/admin/scim-providers/{organization}')
    async def create_provider(organization: str, request: Request):
        principal = require_admin(request.headers.get('authorization'))
        body = scim._keys(await _body(request), {'label'})
        try:
            result = await run_in_threadpool(service.create_provider, organization, body.get('label'), principal.audit_id)
        except EmployeeError:
            raise scim.Error(400, 'Invalid organization') from None
        result['provider']['base_url'] = url(request, result['provider']['provider_id'])
        return JSONResponse(result, status_code=201, headers={'Cache-Control': 'no-store'})

    @router.patch('/v1/admin/scim-providers/{organization}/{provider_id}')
    async def configure_provider(organization: str, provider_id: str, request: Request):
        principal = require_admin(request.headers.get('authorization'))
        body = scim._keys(await _body(request), {'revision', 'enabled'})
        if 'enabled' not in body:
            raise scim.Error(400, 'An explicit enabled boolean is required', 'invalidValue')
        result = await run_in_threadpool(service.set_enabled, provider_id, body.get('revision'), body['enabled'],
                                        principal.audit_id, organization=organization)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.post('/v1/admin/scim-providers/{organization}/{provider_id}/revoke')
    async def revoke_provider(organization: str, provider_id: str, request: Request):
        principal = require_admin(request.headers.get('authorization'))
        body = scim._keys(await _body(request), {'revision'})
        result = await run_in_threadpool(service.revoke, provider_id, body.get('revision'), principal.audit_id,
                                        organization=organization)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.post('/v1/admin/scim-providers/{organization}/{provider_id}/assignment-preview')
    async def assignment_email_preview(organization: str, provider_id: str, request: Request):
        require_admin(request.headers.get('authorization'))
        if request.query_params:
            raise scim.Error(400, 'Preview options belong in the request body', 'invalidValue')
        body = scim._keys(await _body(request), {'username'})
        value = await run_in_threadpool(assignments.preview_email, organization, provider_id, body.get('username'))
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

    @router.get('/v1/admin/scim-providers/{organization}/{provider_id}/assignments/{employee_id}')
    def assignment_preview(organization: str, provider_id: str, employee_id: str, request: Request):
        require_admin(request.headers.get('authorization'))
        if request.query_params:
            raise scim.Error(400, 'Assignment previews do not accept query options', 'invalidValue')
        return JSONResponse(assignments.preview(organization, provider_id, employee_id),
                            headers={'Cache-Control': 'no-store'})

    @router.post('/v1/admin/scim-providers/{organization}/{provider_id}/assignments/{employee_id}')
    async def assign_employee(organization: str, provider_id: str, employee_id: str, request: Request):
        principal = require_admin(request.headers.get('authorization'))
        if request.query_params:
            raise scim.Error(400, 'Assignments do not accept query options', 'invalidValue')
        body = await _body(request)
        item = await run_in_threadpool(assignments.assign, organization, provider_id, employee_id, body, principal.audit_id)
        return user_response(request, provider_id, item, status=201)

    @router.get('/v1/admin/scim-providers/{organization}/{provider_id}/client-policy')
    def client_policy(organization: str, provider_id: str, request: Request):
        require_admin(request.headers.get('authorization'))
        if request.query_params:
            raise scim.Error(400, 'Client policy does not accept query options', 'invalidValue')
        return JSONResponse(policies.get(organization, provider_id), headers={'Cache-Control': 'no-store'})

    @router.patch('/v1/admin/scim-providers/{organization}/{provider_id}/client-policy')
    async def set_client_policy(organization: str, provider_id: str, request: Request):
        principal = require_admin(request.headers.get('authorization'))
        if request.query_params:
            raise scim.Error(400, 'Client policy does not accept query options', 'invalidValue')
        body = await _body(request)
        value = await run_in_threadpool(policies.set, organization, provider_id, body, principal.audit_id)
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

    @router.get('/scim/v2/{provider_id}/ServiceProviderConfig')
    def configuration(provider_id: str, request: Request):
        authenticate(provider_id, request)
        return _response({'schemas': [SPC], 'documentationUri': 'https://github.com/KAVentures/openworkgraph/blob/main/docs/SCIM_CLIENT_POLICY.md', 'patch': {'supported': True},
            'bulk': {'supported': True, 'maxOperations': bulk.MAX_OPERATIONS, 'maxPayloadSize': MAX_BODY},
            'filter': {'supported': True, 'maxResults': 200}, 'sort': {'supported': False},
            'changePassword': {'supported': False}, 'etag': {'supported': True},
            'authenticationSchemes': [{'type': 'oauthbearertoken', 'name': 'Provider-scoped bearer',
                'description': 'Explicitly enabled organization/provider-scoped credential; not an admin or evidence grant.',
                'specUri': 'https://www.rfc-editor.org/rfc/rfc6750', 'primary': True}]})

    def group_schema():
        def attribute(name, kind='string', *, required=False, multi=False, mutable='readWrite'):
            return {'name': name, 'type': kind, 'multiValued': multi,
                    'required': required, 'caseExact': True, 'mutability': mutable,
                    'returned': 'default', 'uniqueness': 'none'}
        members = attribute('members', 'complex', multi=True)
        members['subAttributes'] = [
            attribute('value', required=True),
            {**attribute('type'), 'canonicalValues': ['User']},
        ]
        return {'schemas': [SCHEMA_URI], 'id': group_module.CORE, 'name': 'Group',
                'description': 'Provider-owned direct User membership metadata. No OWG role/team/ACL mapping; no nested groups.',
                'attributes': [attribute('displayName', required=True),
                               attribute('externalId'), members]}

    @router.get('/scim/v2/{provider_id}/Schemas')
    def schemas(provider_id: str, request: Request):
        authenticate(provider_id, request)
        return _response(_list([_schema(), group_schema()]))

    @router.get('/scim/v2/{provider_id}/Schemas/{schema_id}')
    def schema(provider_id: str, schema_id: str, request: Request):
        authenticate(provider_id, request)
        if schema_id == scim.CORE:
            return _response(_schema())
        if schema_id == group_module.CORE:
            return _response(group_schema())
        raise scim.Error(404, 'Schema not found')

    def group_type():
        return {'schemas': [RESOURCE_TYPE], 'id': 'Group', 'name': 'Group',
                'endpoint': '/Groups', 'schema': group_module.CORE, 'schemaExtensions': [],
                'description': 'Provider-owned direct membership metadata; grants no OWG permissions.'}

    def user_type():
        return {'schemas': [RESOURCE_TYPE], 'id': 'User', 'name': 'User', 'endpoint': '/Users',
                'description': 'Bounded roster Users projection; see deployment contract for supported attributes/operations.',
                'schema': scim.CORE, 'schemaExtensions': []}

    @router.get('/scim/v2/{provider_id}/ResourceTypes')
    def resource_types(provider_id: str, request: Request):
        authenticate(provider_id, request)
        return _response(_list([user_type(), group_type()]))

    @router.get('/scim/v2/{provider_id}/ResourceTypes/User')
    def resource_type(provider_id: str, request: Request):
        authenticate(provider_id, request)
        return _response(user_type())

    @router.get('/scim/v2/{provider_id}/ResourceTypes/Group')
    def group_resource_type(provider_id: str, request: Request):
        authenticate(provider_id, request)
        return _response(group_type())

    def group_resource(request, provider_id, value):
        location = url(request, provider_id) + '/Groups/' + quote(value['id'], safe='')
        return {**value, 'meta': {**value['meta'], 'location': location}}

    def group_response(request, provider_id, value, *, status=200, selection=None):
        value = group_resource(request, provider_id, value)
        return _response(query.project(value, selection), status=status,
                         headers={'ETag': value['meta']['version'], 'Location': value['meta']['location']})

    def read_query(provider_id, request, token, values, core, *, posted=False):
        kwargs, selection = query.parse(values, core, posted=posted)
        if core == scim.CORE:
            result = service.list_users(provider_id, token, **kwargs)
            decorate = resource
        else:
            result = groups.list(provider_id, token, **kwargs)
            decorate = group_resource
        result['Resources'] = [query.project(decorate(request, provider_id, item), selection)
                               for item in result['Resources']]
        return _response(result)

    def read_token(provider_id, request):
        token = _bearer(request)
        with service.transaction() as conn:
            service.provider(conn, provider_id, token=token)
        return token

    @router.get('/scim/v2/{provider_id}/Groups')
    def list_groups(provider_id: str, request: Request):
        token = read_token(provider_id, request)
        return read_query(provider_id, request, token, query.query_params(request.query_params), group_module.CORE)

    @router.get('/scim/v2/{provider_id}/Groups/{group_id}')
    def get_group(provider_id: str, group_id: str, request: Request):
        token = read_token(provider_id, request)
        _, selection = query.parse(query.query_params(request.query_params), group_module.CORE, single=True)
        return group_response(request, provider_id, groups.get(provider_id, token, group_id), selection=selection)

    @router.post('/scim/v2/{provider_id}/Groups/.search')
    async def search_groups(provider_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        return await run_in_threadpool(read_query, provider_id, request, token, body, group_module.CORE, posted=True)

    @router.post('/scim/v2/{provider_id}/Groups')
    async def create_group(provider_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        item = await run_in_threadpool(groups.create, provider_id, token, body)
        return group_response(request, provider_id, item, status=201)

    @router.put('/scim/v2/{provider_id}/Groups/{group_id}')
    async def replace_group(provider_id: str, group_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        item = await run_in_threadpool(groups.replace, provider_id, token, group_id, request.headers.get('if-match'), body)
        return group_response(request, provider_id, item)

    @router.patch('/scim/v2/{provider_id}/Groups/{group_id}')
    async def patch_group(provider_id: str, group_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        item = await run_in_threadpool(groups.patch, provider_id, token, group_id, request.headers.get('if-match'), body)
        return group_response(request, provider_id, item)

    @router.delete('/scim/v2/{provider_id}/Groups/{group_id}')
    def delete_group(provider_id: str, group_id: str, request: Request):
        if request.query_params:
            raise scim.Error(400, 'Unsupported query option', 'invalidValue')
        groups.delete(provider_id, _bearer(request), group_id, request.headers.get('if-match'))
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})

    @router.get('/scim/v2/{provider_id}/Users')
    def users(provider_id: str, request: Request):
        token = read_token(provider_id, request)
        return read_query(provider_id, request, token, query.query_params(request.query_params), scim.CORE)

    @router.get('/scim/v2/{provider_id}/Users/{user_id}')
    def get_user(provider_id: str, user_id: str, request: Request):
        token = read_token(provider_id, request)
        _, selection = query.parse(query.query_params(request.query_params), scim.CORE, single=True)
        return user_response(request, provider_id, service.get_user(provider_id, token, user_id), selection=selection)

    @router.post('/scim/v2/{provider_id}/Users/.search')
    async def search_users(provider_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        return await run_in_threadpool(read_query, provider_id, request, token, body, scim.CORE, posted=True)

    @router.post('/scim/v2/{provider_id}/Users')
    async def create_user(provider_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        result = await run_in_threadpool(service.create_user, provider_id, token, body)
        return user_response(request, provider_id, result, status=201)

    @router.put('/scim/v2/{provider_id}/Users/{user_id}')
    async def replace_user(provider_id: str, user_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        result = await run_in_threadpool(service.replace_user, provider_id, token, user_id, request.headers.get('if-match'), body)
        return user_response(request, provider_id, result)

    @router.patch('/scim/v2/{provider_id}/Users/{user_id}')
    async def patch_user(provider_id: str, user_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        if len(request.headers.getlist('if-match')) > 1:
            raise scim.Error(400, 'Supply at most one exact If-Match header', 'invalidValue')
        result = await run_in_threadpool(service.patch_user, provider_id, token, user_id,
                                        request.headers.get('if-match'), body, standalone_client=True)
        return user_response(request, provider_id, result)

    @router.delete('/scim/v2/{provider_id}/Users/{user_id}')
    def delete_user(provider_id: str, user_id: str, request: Request):
        if request.query_params:
            raise scim.Error(400, 'Unsupported query option', 'invalidValue')
        service.delete_user(provider_id, _bearer(request), user_id, request.headers.get('if-match'))
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})

    @router.post('/scim/v2/{provider_id}/Bulk')
    async def bulk_users(provider_id: str, request: Request):
        token = await run_in_threadpool(authenticate, provider_id, request)
        body = await _body(request)
        result = await run_in_threadpool(bulk.execute, service, provider_id, token, body, url(request, provider_id))
        return _response(result)

    @router.api_route('/scim/v2/{provider_id}/{unsupported:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    def unsupported(provider_id: str, unsupported: str, request: Request):
        authenticate(provider_id, request)
        raise scim.Error(404, 'Resource or operation is not supported')

    return router
