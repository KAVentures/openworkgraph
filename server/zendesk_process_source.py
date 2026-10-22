"""Opt-in, bounded, fixed-tenant OAuth reads for selected Zendesk ticket audits.

Credentials are supplied by the customer's local administrator, never the AI
bearer or browser. No observer enrollment, background polling or business writes.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import urlsplit, parse_qs

import httpx

from .zendesk_process_preview import minimize_ticket_audits, project_ticket_audits, zendesk_selected_cases_process
from .source_http_logging import private_source_http_logs

MAX_RESPONSE_BYTES=512*1024
MAX_PAGES=3


def _empty(reason):
    return {'format':'openworkgraph.zendesk-selected-cases-process.v1','availability':reason,
            'process':None,'result_persisted':False,'business_writes_performed':False,
            'execution_permissions_granted':False,'credentials_exposed':False}


def _configuration(environ):
    if environ.get('OWG_ZENDESK_PROCESS_LOGS_ENABLED')!='true':return None,'connector_disabled'
    tenant=environ.get('OWG_ZENDESK_SUBDOMAIN','')
    token=environ.get('OWG_ZENDESK_OAUTH_TOKEN','')
    if (not isinstance(tenant,str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',tenant)
            or not isinstance(token,str) or not 1<=len(token)<=4096
            or any(ord(char)<33 or ord(char)>126 for char in token)):
        return None,'invalid_connector_configuration'
    return (tenant+'.zendesk.com',token),None


def _json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate field')
            result[key]=value
        return result
    def invalid(_value):raise ValueError('nonstandard JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


async def _ticket(client,host,token,ticket_id):
    path=f'/api/v2/tickets/{ticket_id}/audits'
    audits=[];complete=False
    for page in range(1,MAX_PAGES+1):
        async with client.stream('GET','https://'+host+path,
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'},
                params=[('page',str(page)),('per_page','100'),('sort_order','asc'),
                        ('filter_events[]','Create'),('filter_events[]','Change')]) as response:
            if response.status_code in {401,403}:return {'availability':'source_access_denied'}
            if response.status_code==429:return {'availability':'source_rate_limited'}
            if response.status_code==404:return {'availability':'selected_ticket_unavailable'}
            if response.status_code!=200:return {'availability':'source_request_failed'}
            content=bytearray()
            async for chunk in response.aiter_bytes():
                if len(content)+len(chunk)>MAX_RESPONSE_BYTES:return {'availability':'source_response_too_large'}
                content.extend(chunk)
            try:data=_json(content)
            except (ValueError,UnicodeError,RecursionError):return {'availability':'invalid_audit_response'}
            finally:content.clear()
        if not isinstance(data,dict) or not isinstance(data.get('audits'),list) or len(data['audits'])>100:
            return {'availability':'invalid_audit_response'}
        minimized=minimize_ticket_audits(data['audits'],ticket_id)
        if minimized is None:return {'availability':'ticket_identity_mismatch'}
        audits.extend(minimized)
        has_pagination='next_page' in data;next_page=data.get('next_page')
        data.clear()
        if not has_pagination:break  # Missing completeness metadata stays unknown.
        if next_page is None:complete=True;break
        if not isinstance(next_page,str) or len(next_page)>2048:return {'availability':'invalid_pagination_metadata'}
        try:
            parts=urlsplit(next_page)
            if (parts.scheme!='https' or parts.hostname!=host or parts.port not in {None,443}
                    or parts.username is not None or parts.password is not None or parts.fragment
                    or parts.path not in {path,path+'.json'}
                    or parse_qs(parts.query).get('page')!=[str(page+1)]):
                return {'availability':'invalid_pagination_metadata'}
        except ValueError:return {'availability':'invalid_pagination_metadata'}
        # Never follow provider-supplied links. Reconstruct the next bounded
        # offset request against the same configured host and selected ticket.
    projected=project_ticket_audits(audits,ticket_id)
    return {'availability':projected['availability'],'projection':projected,
            'provider_pagination_complete':complete}


async def read_selected_zendesk_process(ticket_ids,*,confirmed,environ=None,client_factory=None):
    if confirmed is not True:raise ValueError('Explicit selected-ticket source reads must be confirmed')
    if (not isinstance(ticket_ids,list) or not 1<=len(ticket_ids)<=8
            or any(not isinstance(identifier,str) or not re.fullmatch(r'[1-9][0-9]{0,17}',identifier) for identifier in ticket_ids)
            or len(set(ticket_ids))!=len(ticket_ids)):
        raise ValueError('Select 1 to 8 distinct numeric Zendesk ticket IDs')
    configuration,error=_configuration(os.environ if environ is None else environ)
    if error:return _empty(error)
    host,token=configuration
    receipts=[]
    try:
        with private_source_http_logs():
            async with asyncio.timeout(30):
                async with (client_factory or httpx.AsyncClient)(timeout=10,follow_redirects=False) as client:
                    for identifier in ticket_ids:
                        receipt=await _ticket(client,host,token,int(identifier))
                        if receipt['availability'] in {'source_access_denied','source_rate_limited'}:
                            return _empty(receipt['availability'])
                        receipts.append(receipt)
    except (TimeoutError,httpx.HTTPError,OSError,ValueError):return _empty('source_request_failed')
    return zendesk_selected_cases_process(receipts)
