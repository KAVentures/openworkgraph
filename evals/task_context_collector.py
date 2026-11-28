"""Explicit loopback-only collector for the frozen task-context ablation plan."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import time

from . import task_context_ablation as ablation

MAX_REQUEST = 1024 * 1024
MAX_REPLY = 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
require = ablation.require


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, 'Duplicate JSON key')
            result[k] = v
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    encode(value)  # Reject nonfinite values, including overflowing floats.
    return value


def load(path, limit):
    require(path.is_file() and not path.is_symlink(), 'Expected regular local file')
    with path.open('rb') as f:
        raw = f.read(limit + 1)
    require(0 < len(raw) <= limit, 'File exceeds bound or is empty')
    return raw


def save(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def slot_for(frozen, pin, slot_id):
    require(frozen == ablation.plan(frozen['cases'], frozen['settings'], frozen['replicates']) and frozen['plan_sha256'] == pin, 'Plan changed')
    rows = [s for s in frozen['slots'] if s['slot_id'] == slot_id]
    require(len(rows) == 1, 'Unknown slot')
    return rows[0]


def request_settings(raw, model):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_REQUEST, 'Invalid request size')
    r = decode(raw)
    require(isinstance(r, dict) and set(r) == {'model','messages','temperature','max_tokens','stream'}, 'Use exact no-tool request fields')
    require(r['model'] == model and isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}', model), 'Invalid model')
    require(type(r['temperature']) in (int,float) and 0 <= r['temperature'] <= 2 and type(r['max_tokens']) is int and 128 <= r['max_tokens'] <= 16384 and r['stream'] is False, 'Invalid request settings')
    messages = r['messages']
    require(isinstance(messages,list) and len(messages) == 2, 'Expected exactly system and user messages')
    for m, role in zip(messages, ('system','user')):
        require(isinstance(m,dict) and set(m) == {'role','content'} and m['role'] == role and isinstance(m['content'],str) and bool(m['content'].strip()), 'Invalid message')
    return ablation.digest({k:v for k,v in r.items() if k != 'messages'})


def completion(raw, model):
    r = decode(raw)
    require(isinstance(r,dict) and r.get('model') == model and isinstance(r.get('choices'),list) and len(r['choices']) == 1, 'Unexpected endpoint model or choices')
    choice = r['choices'][0]
    require(isinstance(choice,dict) and choice.get('finish_reason') == 'stop', 'Incomplete completion')
    m = choice.get('message')
    require(isinstance(m,dict) and m.get('role') == 'assistant' and not any(m.get(k) for k in ('tool_calls','function_call','refusal')) and isinstance(m.get('content'),str) and bool(m['content'].strip()), 'Unexpected completion action or empty content')
    return m['content'].encode('utf-8')


def root_dir(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Use an existing private local run directory')
    if os.name == 'posix':
        require(root.stat().st_mode & 0o077 == 0, 'Run directory must be private (chmod 700)')
    return root


def run(frozen, pin, slot_id, raw, root, *, port=11434, timeout=120, confirmed=False):
    require(confirmed is True, 'Explicit loopback inference confirmation required')
    require(type(port) is int and 1 <= port <= 65535 and type(timeout) is int and 1 <= timeout <= 300, 'Invalid loopback settings')
    slot = slot_for(frozen, pin, slot_id)
    settings = frozen['settings']
    require(sha(raw) == slot['request_sha256'] and request_settings(raw,settings['model_id']) == settings['settings_sha256'], 'Request or settings pin mismatch')
    root = root_dir(root)
    # One invocation at a time; the next slot may proceed after a crashed prior
    # attempt (its missing receipt remains visible). Never retry the same slot.
    for prior in frozen['slots']:
        if prior == slot:
            break
        require((root / prior['slot_id']).is_dir(), 'Start slots in frozen order')
    dest = root / slot_id
    dest.mkdir(mode=0o700)  # Exclusive attempt reservation before any network call.
    lineage = {**{k:slot[k] for k in ('slot_id','request_sha256','source_state_sha256')}, **settings}
    save(dest/'request.json',raw)
    save(dest/'started.json',encode({'plan_sha256':pin,**lineage,'port':port,'timeout_seconds':timeout}))
    status, error, response_pin, output_pin = 'failed', None, None, None
    conn = http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    start = time.monotonic()
    try:
        conn.request('POST','/v1/chat/completions',body=raw,headers={'Content-Type':'application/json','Accept':'application/json'})
        r = conn.getresponse()
        require(r.status == 200 and (r.getheader('Content-Type') or '').split(';',1)[0].strip().lower() == 'application/json' and (r.getheader('Content-Encoding') or 'identity').lower() == 'identity', 'Unexpected endpoint response')
        body = r.read(MAX_REPLY+1)
        require(0 < len(body) <= MAX_REPLY, 'Response exceeds bound')
        save(dest/'response.json',body)
        response_pin = sha(body)
        output = completion(body,settings['model_id'])
        save(dest/'output.txt',output)
        output_pin = sha(output)
        status = 'completed'
    except (TimeoutError,socket.timeout):
        status,error = 'timeout','TimeoutError'
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,RecursionError,http.client.HTTPException) as exc:
        error = type(exc).__name__
    finally:
        conn.close()
    receipt = {'format':'openworkgraph.task-context-attempt.v1','plan_sha256':pin,**lineage,'status':status,
               'output_sha256':output_pin,'response_sha256':response_pin,'error_class':error,
               'elapsed_ms':round((time.monotonic()-start)*1000),'endpoint_is_loopback':True,
               'inference_location_verified':False,'transport_attested':False,'execution_authorized':False}
    save(dest/'receipt.json',encode(receipt))
    return receipt


def verified_attempts(frozen,pin,root):
    require(frozen == ablation.plan(frozen['cases'],frozen['settings'],frozen['replicates']) and frozen['plan_sha256']==pin,'Plan changed')
    root = root_dir(root)
    rows, interrupted = [], []
    for slot in frozen['slots']:
        sid=slot['slot_id']; dest=root/sid
        if not dest.exists():
            continue
        require(dest.is_dir() and not dest.is_symlink(),'Invalid attempt directory')
        if not (dest/'receipt.json').exists():
            interrupted.append(sid);continue
        receipt=decode(load(dest/'receipt.json',MAX_MANIFEST))
        lineage={**{k:slot[k] for k in ('slot_id','request_sha256','source_state_sha256')},**frozen['settings']}
        require(receipt.get('format')=='openworkgraph.task-context-attempt.v1' and receipt.get('plan_sha256')==pin and all(receipt.get(k)==v for k,v in lineage.items()) and receipt.get('status') in ('completed','failed','timeout') and receipt.get('execution_authorized') is False,'Invalid attempt receipt')
        raw=load(dest/'request.json',MAX_REQUEST)
        require(sha(raw)==slot['request_sha256'] and request_settings(raw,lineage['model_id'])==lineage['settings_sha256'],'Saved request changed')
        status=receipt['status']; output_pin=receipt.get('output_sha256')
        if receipt.get('response_sha256') is not None:
            body=load(dest/'response.json',MAX_REPLY)
            require(sha(body)==receipt['response_sha256'],'Saved response changed')
        if status=='completed':
            require(receipt.get('response_sha256') is not None and ablation.pin(output_pin),'Missing completed output')
            output=load(dest/'output.txt',MAX_REPLY)
            require(sha(output)==output_pin and output==completion(body,lineage['model_id']),'Saved output changed')
        else:
            require(output_pin is None,'Failed attempt cannot have graded output')
        rows.append({**lineage,'status':status,'output_sha256':output_pin})
    return rows, interrupted


def collect(frozen,pin,root,reviews):
    rows, interrupted = verified_attempts(frozen,pin,root)
    require(isinstance(reviews,dict) and len(reviews)<=len(frozen['slots']),'Invalid review map')
    completed = {r['slot_id'] for r in rows if r['status']=='completed'}
    require(completed==set(reviews),'Completed outputs require independent review; no other reviews allowed')
    rows = [{**r,'review':reviews.get(r['slot_id'])} for r in rows]
    report=ablation.score(frozen,pin,rows)
    return {'format':'openworkgraph.task-context-collected-evaluation.v1','report':report,'interrupted_slot_ids':interrupted,'attempts':rows,'transport_attested':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ('run','collect'):
        c=sub.add_parser(name);c.add_argument('--plan',type=Path,required=True);c.add_argument('--plan-sha256',required=True);c.add_argument('--directory',type=Path,required=True)
        if name=='run':
            c.add_argument('--slot',required=True);c.add_argument('--request',type=Path,required=True);c.add_argument('--port',type=int,default=11434);c.add_argument('--timeout',type=int,default=120);c.add_argument('--confirm-loopback',action='store_true',required=True)
        else:
            c.add_argument('--reviews',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        frozen=decode(load(a.plan,MAX_MANIFEST))
        if a.command=='run':
            r=run(frozen,a.plan_sha256,a.slot,load(a.request,MAX_REQUEST),a.directory,port=a.port,timeout=a.timeout,confirmed=a.confirm_loopback)
            print(r['status']);return 0 if r['status']=='completed' else 2
        result=collect(frozen,a.plan_sha256,a.directory,decode(load(a.reviews,MAX_MANIFEST)))
        save(a.output,encode(result))
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,RecursionError,OverflowError) as exc:
        p.exit(2,'Collector refused: '+type(exc).__name__+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
