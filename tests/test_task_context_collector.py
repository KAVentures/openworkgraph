import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
from evals import task_context_ablation as ab, task_context_collector as c


@pytest.fixture
def setup(tmp_path):
    settings={'model':'fixture','temperature':0,'max_tokens':128,'stream':False}
    requests={a:c.encode({**settings,'messages':[{'role':'system','content':'Read only'},{'role':'user','content':a}]}) for a in ab.ARMS}
    p=ab.plan([{'case_id':'case1','fold':'holdout','source_state_sha256':'a'*64,'task_sha256':'b'*64,'request_sha256_by_arm':{a:c.sha(v) for a,v in requests.items()}}],{'model_id':'fixture','settings_sha256':ab.digest(settings)})
    root=tmp_path/'runs';root.mkdir(mode=0o700)
    return p,requests,root


@pytest.fixture
def endpoint():
    received=[]; replies=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path,self.rfile.read(int(self.headers['Content-Length']))))
            status, body=replies.pop(0)
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server.server_port,received,replies
    server.shutdown();server.server_close();thread.join()


def reply(**changes):
    m={'role':'assistant','content':'Exact response å\n'};m.update(changes)
    return c.encode({'model':'fixture','choices':[{'finish_reason':'stop','message':m}]})


def run(setup,endpoint,n=0,**kw):
    p,requests,root=setup;slot=p['slots'][n]
    return c.run(p,p['plan_sha256'],slot['slot_id'],requests[slot['arm']],root,port=endpoint[0],confirmed=True,**kw)


def review(r):
    return {'reviewer_id':'reviewer','output_sha256':r['output_sha256'],'task_success':True,'consequential_wrong_action':False,'wrong_assumptions':0}


def test_exact_loopback_body_receipt_and_output_review(setup,endpoint):
    p,requests,root=setup;port,received,replies=endpoint;replies.append((200,reply()))
    r=run(setup,endpoint);sid=r['slot_id'];assert received==[('/v1/chat/completions',requests['control'])]
    assert r['status']=='completed' and r['output_sha256']==c.sha('Exact response å\n'.encode())
    assert (root/sid/'response.json').read_bytes()==reply()
    result=c.collect(p,p['plan_sha256'],root,{sid:review(r)})
    assert result['report']['by_fold']['holdout']['control']['reviewed_success']==1
    assert result['report']['recorded_attempts']==1 and not result['report']['all_attempts_recorded']
    with pytest.raises(FileExistsError):run(setup,endpoint)
    assert len(received)==1


def test_refuses_bad_requests_before_network(setup,endpoint):
    p,requests,root=setup;s=p['slots'][0];raw=requests['control']
    for kwargs in ({'confirmed':False},{'confirmed':True,'timeout':0}):
        with pytest.raises(ValueError):c.run(p,p['plan_sha256'],s['slot_id'],raw,root,port=endpoint[0],**kwargs)
    with pytest.raises(ValueError):c.run(p,p['plan_sha256'],s['slot_id'],raw+b' ',root,port=endpoint[0],confirmed=True)
    with pytest.raises(ValueError):run(setup,endpoint,1)
    bad=json.loads(raw);bad['tools']=[]
    with pytest.raises(ValueError):c.request_settings(c.encode(bad),'fixture')
    with pytest.raises(ValueError):c.request_settings(raw.replace(b'"model":"fixture"',b'"model":"other","model":"fixture"'),'fixture')
    assert not list(root.iterdir()) and not endpoint[1]


def test_failed_attempt_retained_and_no_tool_action(setup,endpoint):
    p,_,root=setup;endpoint[2].extend([(200,reply(tool_calls=[{'name':'delete'}])),(503,b'{}')])
    a=run(setup,endpoint);b=run(setup,endpoint,1)
    assert a['status']==b['status']=='failed' and a['response_sha256'] and b['response_sha256'] is None
    assert not (root/a['slot_id']/'output.txt').exists()
    result=c.collect(p,p['plan_sha256'],root,{})
    assert result['report']['recorded_attempts']==2 and result['report']['by_fold']['holdout']['control']['failed']==1
    with pytest.raises(ValueError):c.collect(p,p['plan_sha256'],root,{a['slot_id']:review(a)})


def test_tampering_and_missing_review_refused(setup,endpoint):
    p,_,root=setup;endpoint[2].append((200,reply()));r=run(setup,endpoint);sid=r['slot_id']
    with pytest.raises(ValueError):c.collect(p,p['plan_sha256'],root,{})
    with pytest.raises(ValueError):c.collect(p,p['plan_sha256'],root,{sid:{**review(r),'output_sha256':'0'*64}})
    (root/sid/'output.txt').write_text('tampered')
    with pytest.raises(ValueError):c.collect(p,p['plan_sha256'],root,{sid:review(r)})


def test_interrupted_slot_is_missing_and_never_retried(setup,endpoint):
    p,_,root=setup;sid=p['slots'][0]['slot_id'];(root/sid).mkdir(mode=0o700)
    with pytest.raises(FileExistsError):run(setup,endpoint)
    endpoint[2].append((200,reply()));r=run(setup,endpoint,1)
    result=c.collect(p,p['plan_sha256'],root,{r['slot_id']:review(r)})
    assert result['interrupted_slot_ids']==[sid] and result['report']['recorded_attempts']==1
