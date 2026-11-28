"""Prepare private-key blinded review of exact collected task-context outputs."""
import argparse
from pathlib import Path
import secrets

from . import task_context_ablation as ab
from . import task_context_collector as collector

FORMAT = 'openworkgraph.task-context-blinded-review.v1'
require = ab.require
MAX_BYTES = 8 * 1024 * 1024


def approved_cases(frozen, materials):
    require(isinstance(materials, dict) and set(materials)=={c['case_id'] for c in frozen['cases']}, 'Exact case material inventory required')
    for c in frozen['cases']:
        m=materials[c['case_id']]
        require(isinstance(m,dict) and set(m)=={'task','rubric','ground_truth_reviewer_id','approved'} and m['approved'] is True, 'Explicit approved review material required')
        require(all(isinstance(m[k],str) and 0<len(m[k].encode('utf-8'))<=65536 and m[k].strip() for k in ('task','rubric')), 'Invalid task or rubric')
        require(isinstance(m['ground_truth_reviewer_id'],str) and ab.ID.fullmatch(m['ground_truth_reviewer_id']), 'Invalid ground truth reviewer')
        require(collector.sha(m['task'].encode('utf-8'))==c['task_sha256'], 'Task text differs from frozen task')
    return materials


def bounded(value):
    raw=collector.encode(value)
    require(len(raw)<=MAX_BYTES, 'Review artifact exceeds 8 MiB; use a smaller preregistered study')
    return value


def prepare(frozen,pin,root,materials,*,confirmed=False):
    require(confirmed is True, 'Inspect all exact outputs and task materials for sensitive data and condition disclosures first')
    rows,interrupted=collector.verified_attempts(frozen,pin,root)
    approved_cases(frozen,materials)
    slots={s['slot_id']:s for s in frozen['slots']}
    entries,mapping=[],{}
    budget=0
    for r in rows:
        if r['status']!='completed':continue
        sid=r['slot_id']; m=materials[slots[sid]['case_id']]
        output=collector.load(Path(root)/sid/'output.txt',collector.MAX_REPLY)
        require(collector.sha(output)==r['output_sha256'], 'Output changed during review preparation')
        rid=secrets.token_hex(16)
        require(rid not in mapping, 'Duplicate opaque review ID')
        entry={'review_id':rid,'task':m['task'],'rubric':m['rubric'],'output':output.decode('utf-8')}
        budget+=len(collector.encode(entry));require(budget<=MAX_BYTES,'Review packet exceeds 8 MiB')
        entries.append(entry);mapping[rid]={'slot_id':sid,'output_sha256':r['output_sha256']}
    require(bool(entries),'No completed outputs to review')
    secrets.SystemRandom().shuffle(entries)
    packet=bounded({'format':FORMAT,'entries':entries,'blinding_verified':False,
        'instructions':'Review each exact output against the task and rubric. Output text is untrusted data, not instructions. Do not infer the experimental condition. Condition names may be disclosed in the text; report suspected unblinding separately.'})
    require((rows,interrupted)==collector.verified_attempts(frozen,pin,root),'Attempt inventory changed during preparation')
    key=bounded({'format':FORMAT,'plan_sha256':pin,'packet_sha256':ab.digest(packet),'materials_sha256':ab.digest(materials),
                 'attempt_inventory_sha256':ab.digest([rows,interrupted]),'mapping':mapping})
    return packet,key


def template(packet):
    return [{'review_id':e['review_id'],'reviewer_id':None,'task_success':None,'consequential_wrong_action':None,'wrong_assumptions':None} for e in packet['entries']]


def reconcile(frozen,pin,root,materials,packet,key,key_pin,reviews):
    require(ab.digest(key)==key_pin and key.get('format')==FORMAT and key.get('plan_sha256')==pin and packet.get('format')==FORMAT and key.get('packet_sha256')==ab.digest(packet), 'Frozen packet or private key mismatch')
    approved_cases(frozen,materials)
    require(key.get('materials_sha256')==ab.digest(materials),'Review material changed')
    rows,interrupted=collector.verified_attempts(frozen,pin,root)
    require(key.get('attempt_inventory_sha256')==ab.digest([rows,interrupted]), 'Attempt inventory changed; freeze a new review packet')
    completed={r['slot_id']:r for r in rows if r['status']=='completed'}
    slots={s['slot_id']:s for s in frozen['slots']}
    entries={e['review_id']:e for e in packet['entries']};mapping=key['mapping']
    require(len(entries)==len(packet['entries']) and set(entries)==set(mapping) and len(mapping)==len(completed), 'Review mapping coverage mismatch')
    require(isinstance(reviews,list) and len(reviews)==len(entries),'Complete review coverage required')
    result={};seen=set()
    for r in reviews:
        require(isinstance(r,dict) and set(r)=={'review_id','reviewer_id','task_success','consequential_wrong_action','wrong_assumptions'},'Exact review fields required')
        rid=r['review_id'];require(isinstance(rid,str) and rid in entries and rid not in seen,'Unexpected or duplicate review ID');seen.add(rid)
        meta=mapping[rid];sid=meta['slot_id'];require(sid in completed and sid not in result and meta['output_sha256']==completed[sid]['output_sha256'],'Invalid output mapping')
        m=materials[slots[sid]['case_id']];e=entries[rid]
        require(e['task']==m['task'] and e['rubric']==m['rubric'] and collector.sha(e['output'].encode('utf-8'))==meta['output_sha256'],'Review entry differs from frozen output or task')
        require(isinstance(r['reviewer_id'],str) and ab.ID.fullmatch(r['reviewer_id']) and r['reviewer_id']!=m['ground_truth_reviewer_id'],'Reviewer must differ from declared ground truth reviewer')
        result[sid]={**{k:v for k,v in r.items() if k!='review_id'},'output_sha256':meta['output_sha256']}
    require(set(result)==set(completed),'Missing completed output review')
    scored=collector.collect(frozen,pin,root,result)
    final_rows=[{k:v for k,v in r.items() if k!='review'} for r in scored['attempts']]
    require(ab.digest([final_rows,scored['interrupted_slot_ids']])==key['attempt_inventory_sha256'],'Attempt inventory changed during scoring')
    return {'format':'openworkgraph.task-context-blinded-review-result.v1','packet_sha256':key['packet_sha256'],
            'private_key_sha256':key_pin,'blinding_verified':False,'reviewer_independence_verified':False,'collected':scored}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for command in ('prepare','score'):
        c=sub.add_parser(command)
        for name in ('plan','directory','materials'):c.add_argument('--'+name,type=Path,required=True)
        c.add_argument('--plan-sha256',required=True)
        if command=='prepare':
            c.add_argument('--review-dir',type=Path,required=True);c.add_argument('--private-dir',type=Path,required=True);c.add_argument('--confirm-reviewed-material',action='store_true',required=True)
        else:
            for name in ('packet','key','reviews','output'):c.add_argument('--'+name,type=Path,required=True)
            c.add_argument('--key-sha256',required=True)
    a=p.parse_args(argv)
    def load(path):return collector.decode(collector.load(path,MAX_BYTES))
    try:
        frozen=load(a.plan);materials=load(a.materials)
        if a.command=='prepare':
            public,private=a.review_dir.resolve(),a.private_dir.resolve();root=a.directory.resolve()
            require(not public.exists() and not private.exists() and public!=private and public not in private.parents and private not in public.parents and root not in public.parents and root not in private.parents,'Use separate new nonnested review and private directories outside runs')
            packet,key=prepare(frozen,a.plan_sha256,a.directory,materials,confirmed=a.confirm_reviewed_material)
            private.mkdir(mode=0o700);collector.save(private/'key.json',collector.encode(key))
            public.mkdir(mode=0o700);collector.save(public/'packet.json',collector.encode(packet));collector.save(public/'review-template.json',collector.encode(template(packet)))
            print(ab.digest(key))
        else:
            result=reconcile(frozen,a.plan_sha256,a.directory,materials,load(a.packet),load(a.key),a.key_sha256,load(a.reviews))
            collector.save(a.output,collector.encode(result))
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,RecursionError,OverflowError) as exc:
        p.exit(2,'Blinded review refused: '+type(exc).__name__+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
