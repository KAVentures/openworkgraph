"""Owned synthetic Zendesk protocol responses through the production connector."""
from process_logs_app import app,scratch
import json
import httpx
from server import zendesk_process_source as source

calls=[]
def dispatch(request):
    assert request.method=='GET'
    assert request.url.host=='owned-example.zendesk.com'
    assert request.headers['authorization']=='Bearer SYNTHETIC_PRIVATE_TOKEN'
    ticket=int(request.url.path.split('/')[-2])
    assert ticket in {17,19}
    assert request.url.params.get_list('filter_events[]')==['Create','Change']
    calls.append({'method':request.method,'ticket_id':ticket})
    (scratch/'zendesk-read-receipt.json').write_text(json.dumps(calls),encoding='utf-8')
    statuses=['new','open','solved'] if ticket==17 else ['new','pending','open','solved']
    return httpx.Response(200,json={'audits':[{
        'id':ticket*100+position,'ticket_id':ticket,'created_at':f'2026-10-{20 if ticket==17 else 21}T10:0{position}:00Z',
        'author_id':999,'metadata':{'ip':'PRIVATE_IP'},'events':[
            {'type':'Create' if position==0 else 'Change','field_name':'status','value':status},
            {'type':'Comment','body':'PRIVATE_COMMENT_BODY','author_id':999},
            {'type':'Change','field_name':'PRIVATE_CUSTOM_FIELD','value':'PRIVATE_CUSTOM_VALUE'}],
    } for position,status in enumerate(statuses)],'next_page':None})

production=source.read_selected_zendesk_process
async def owned(ids,*,confirmed):
    return await production(ids,confirmed=confirmed,environ={
        'OWG_ZENDESK_PROCESS_LOGS_ENABLED':'true','OWG_ZENDESK_SUBDOMAIN':'owned-example',
        'OWG_ZENDESK_OAUTH_TOKEN':'SYNTHETIC_PRIVATE_TOKEN'},client_factory=lambda **options:
            httpx.AsyncClient(transport=httpx.MockTransport(dispatch),**options))
source.read_selected_zendesk_process=owned
