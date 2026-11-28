from fractions import Fraction
from itertools import product
import json
import subprocess
import sys

import pytest

from shared.agent_procurement_qualification import (
    DESIGN, PLAN, _critical, design, digest, freeze, review,
)


@pytest.mark.parametrize('rate,alpha,m', [(100,500,1), (2500,500,2), (5000,1000,1), (9999,500,12)])
def test_exact_threshold(rate, alpha, m):
    for n in range(1, 10):
        p = Fraction(rate, 10000)
        outcomes = list(product((0,1), repeat=n))
        probabilities = [p**sum(o)*(1-p)**(n-sum(o)) for o in outcomes]
        eligible = [x for x in range(n+1) if sum(w for o,w in zip(outcomes, probabilities)
                    if sum(o) <= x) <= Fraction(alpha,10000*m)]
        assert _critical(n,rate,alpha,m) == max(eligible, default=-1)


def test_design():
    report = design({'format':DESIGN,'alpha_bps':500,'strata':[
        {'stratum':'a','limit_bps':100,'assumed_bps':0,'cases':299}]})
    row = report['strata'][0]
    assert row['minimum_zero_failure_cases_within_cap'] == 299
    assert row['maximum_failures_to_qualify'] == 0
    assert row['qualification_probability']['bps_floor'] == 10000


def plan(n=6):
    return {'format':PLAN,'alpha_bps':500,'strata':[
        {'stratum':'a','limit_bps':5000,'case_hashes':[f'{i:064x}' for i in range(n)]}]}


def test_review_exhaustive():
    p = plan()
    for statuses in product(('failure','no_failure','unknown'), repeat=6):
        ledger = freeze(p)
        for row,status in zip(ledger['outcomes'],statuses):
            row['status'] = status
        result = review(p,ledger,digest(p))
        f,u = statuses.count('failure'),statuses.count('unknown')
        # At n=6 and null 1/2, only zero failures qualifies at alpha .05.
        assert result['status'] == ('not_qualified' if f else 'incomplete' if u else 'qualified')
        assert result['strata'][0]['best_case_qualifies'] == (f == 0)
        assert result['strata'][0]['worst_case_qualifies'] == (f+u == 0)
        assert result['execution_authorized'] is False


@pytest.mark.parametrize('mutation', ['missing','duplicate','unplanned','wrong_plan','invalid_status','wrong_pin'])
def test_refuses_bad_ledger(mutation):
    p=plan(); l=freeze(p); pin=digest(p)
    if mutation == 'missing': l['outcomes'].pop()
    elif mutation == 'duplicate': l['outcomes'][1]=l['outcomes'][0]
    elif mutation == 'unplanned': l['outcomes'][0]['case_hash']='f'*64
    elif mutation == 'wrong_plan': l['plan_sha256']='f'*64
    elif mutation == 'invalid_status': l['outcomes'][0]['status']='passed'
    else: pin='f'*64
    with pytest.raises(ValueError): review(p,l,pin)


def test_duplicate_across_strata():
    p=plan(); p['strata'].append({**p['strata'][0],'stratum':'b'})
    with pytest.raises(ValueError): freeze(p)


def test_cli(tmp_path):
    p=plan(); path=tmp_path/'plan.json'; path.write_text(json.dumps(p))
    base=[sys.executable,'-m','shared.agent_procurement_qualification']
    frozen=subprocess.run(base+['freeze',str(path)],check=True,capture_output=True,text=True)
    ledger=tmp_path/'ledger.json'; ledger.write_text(frozen.stdout)
    result=subprocess.run(base+['review',str(path),str(ledger),digest(p)],check=True,capture_output=True,text=True)
    assert json.loads(result.stdout)['status']=='incomplete'
    path.write_text('{"format":1,"format":2}')
    result=subprocess.run(base+['freeze',str(path)],capture_output=True,text=True)
    assert result.returncode==2 and not result.stdout
