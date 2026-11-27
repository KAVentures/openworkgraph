"""Common-support standardization and exact descriptive case-mix decomposition."""
from collections import defaultdict, Counter
from fractions import Fraction
from hashlib import sha256
import json
from threading import BoundedSemaphore
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT="openworkgraph.intervention-case-mix.v1"
MAX_CASES=200000
MAX_STRATA=100
GUARD=BoundedSemaphore(1)
PERIODS=("baseline","follow_up")


class Settings(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    dimension: Literal["first_activity","first_resource","utc_start_weekday"]
    minimum_cases: int=Field(default=10,ge=5,le=10000)


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def number(x):
    return float(x) if x is not None else None


def exact(value):
    return Fraction(str(value))


def mean(cell):
    n,total=cell
    return total/n if n else None


def aggregate(cells):
    return sum(c[0] for c in cells),sum((c[1] for c in cells),Fraction(0))


def delta(a,b):
    return b-a if a is not None and b is not None else None


def _stats(cell,minimum):
    return {"cases":cell[0],"mean":number(mean(cell)) if cell[0]>=minimum else None,
            "minimum_support_met":cell[0]>=minimum}


def calculate(groups,labels,settings,at,start,end,recent,exclude_spanning,period_fn,basis,population_count):
    """Use one identical case population and exact rational sums.

    Group keys are fixed by the existing impact analysis, periods are
    case-start based. Label keys are (kind, label) with missing/tied labels
    separate from actual source strings. Never impute unsupported cells.
    """
    cells=[(name,period) for name in sorted(groups) for period in PERIODS]
    empty=(0,Fraction(0))
    stats=defaultdict(lambda:defaultdict(lambda:[0,Fraction(0)]))
    inventory=defaultdict(list)
    exclusions=defaultdict(Counter)
    stream=sha256()
    def feed(value):
        stream.update(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(",",":"),allow_nan=False).encode()+b"\n")
    feed([FORMAT,basis,settings.model_dump(),population_count])
    selected=0
    for name,rows in sorted(groups.items()):
        selected+=len(rows)
        if selected>MAX_CASES:raise ValueError("case mix review exceeds 200000 selected cases")
        for key,started,ended,value in rows:
            label=labels[key]
            period,_=period_fn(started,ended,at,start,end,recent,exclude_spanning)
            feed([name,key,started.isoformat(),ended.isoformat(),value,label,period])
            if period not in PERIODS:
                exclusions[name][period]+=1
                continue
            cell=(name,period)
            s=stats[label][cell];s[0]+=1;s[1]+=exact(value)
            inventory[(label,cell)].append((key,started,ended,value))
            if len(stats)>MAX_STRATA:raise ValueError("more than 100 observed strata; select a coarser dimension or narrower scope")
    if selected>population_count:raise ValueError("selected case populations overlap")
    totals={c:aggregate([v.get(c,empty) for v in stats.values()]) for c in cells}
    supported=[label for label,v in stats.items() if label[0]=="observed" and all(v.get(c,empty)[0]>=settings.minimum_cases for c in cells)]
    common={c:aggregate([stats[label][c] for label in supported]) for c in cells}
    weights={}
    if supported:
        for label in supported:
            weights[label]=sum((Fraction(stats[label][c][0],common[c][0]) for c in cells),Fraction(0))/len(cells)
    def weighted(cell):
        return sum((weights[label]*mean(stats[label][cell]) for label in supported),Fraction(0)) if supported else None
    standardized={c:weighted(c) for c in cells}
    details=[]
    for label,v in sorted(stats.items(),key=lambda x:(x[0][0],x[0][1] or "")):
        supported_label=label in weights
        reason=None if supported_label else "missing_or_ambiguous_start_label" if label[0]!="observed" else "insufficient_support_in_one_or_more_cells"
        counts={g:{p:_stats(v.get((g,p),empty),settings.minimum_cases) for p in PERIODS} for g in sorted(groups)}
        changes={}
        for g in sorted(groups):
            a,b=(v.get((g,p),empty) for p in PERIODS)
            changes[g]=number(delta(mean(a),mean(b))) if min(a[0],b[0])>=settings.minimum_cases else None
        details.append({"stratum_sha256":digest(label),"label_kind":label[0],"label":label[1],
                        "common_support":supported_label,"exclusion_reason":reason,
                        "common_reference_weight":number(weights.get(label)),"cells":counts,"within_stratum_changes":changes})
    reports={}
    for g in sorted(groups):
        a,b=((g,p) for p in PERIODS)
        raw_a,raw_b=mean(totals[a]),mean(totals[b])
        common_a,common_b=mean(common[a]),mean(common[b])
        raw=delta(raw_a,raw_b) if min(totals[a][0],totals[b][0])>=settings.minimum_cases else None
        matched=delta(common_a,common_b)
        within=mix=None
        if supported:
            within=mix=Fraction(0)
            for label in supported:
                n0,n1=(stats[label][c][0] for c in (a,b))
                w0,w1=Fraction(n0,common[a][0]),Fraction(n1,common[b][0])
                m0,m1=(mean(stats[label][c]) for c in (a,b))
                within+=(w0+w1)*(m1-m0)/2
                mix+=(m0+m1)*(w1-w0)/2
            if within+mix!=matched:raise ValueError("case mix decomposition did not close")
        residual=raw-matched if raw is not None and matched is not None else None
        reports[g]={"periods":{p:{"included_cases":totals[(g,p)][0],
                            "common_support_cases":common[(g,p)][0],
                            "excluded_from_common_support":totals[(g,p)][0]-common[(g,p)][0],
                            "common_support_share":number(Fraction(common[(g,p)][0],totals[(g,p)][0])) if totals[(g,p)][0] else None,
                            "raw_mean":number(mean(totals[(g,p)])) if totals[(g,p)][0]>=settings.minimum_cases else None,
                            "common_support_mean":number(mean(common[(g,p)])),
                            "standardized_mean":number(standardized[(g,p)])} for p in PERIODS},
                    "raw_change":number(raw),"common_support_change":number(matched),
                    "symmetric_within_stratum_component":number(within),"symmetric_composition_component":number(mix),
                    "raw_minus_common_support_residual":number(residual),
                    "standardized_change":number(delta(standardized[a],standardized[b])),
                    "decomposition_exact_before_display_rounding":within is not None,
                    "excluded_before_period_assignment":dict(sorted(exclusions[g].items())),
                    "selected_cases":len(groups[g])}
    adjusted=None
    if "treated" in reports and "control" in reports:
        def diff(k):
            a,b=reports["treated"][k],reports["control"][k]
            return a-b if a is not None and b is not None else None
        adjusted={"raw_difference_in_differences":diff("raw_change"),
                  "common_support_difference_in_differences":diff("common_support_change"),
                  "standardized_difference_in_differences":diff("standardized_change"),
                  "within_stratum_component_difference":diff("symmetric_within_stratum_component"),
                  "composition_component_difference":diff("symmetric_composition_component")}
    report={"format":FORMAT,"review_sha256":stream.hexdigest(),"settings":settings.model_dump(),
            "population":{"projected_cases":population_count,"selected_cases":selected,
                          "outside_selected_groups":population_count-selected,
                          "included_period_cases":sum(x[0] for x in totals.values()),
                          "excluded_before_period_assignment":selected-sum(x[0] for x in totals.values())},
            "common_support_strata":len(supported),"observed_strata":len(stats),"groups":reports,
            "control_adjusted":adjusted,"strata":details,
            "common_support_available":bool(supported),"common_weight_basis":"equal_weight_average_of_cell_specific_common_support_shares",
            "source_completeness_verified":False,"causal_effect_verified":False,"realized_savings_verified":False,
            "execution_authorized":False,
            "interpretation":"Common support requires every selected cohort/period to have the minimum number of cases in a known start-label stratum. "
                "All standardized means use the same reference shares. The symmetric decomposition partitions the common-support raw change into within-stratum and composition components. "
                "The difference between full and common-support changes is separately reported, not attributed to either component. "
                "No missing groups are imputed. Start labels are recorded observations, not verified pre-treatment covariates. "
                "No causal effect, confidence interval, staffing change or verified savings is established."}
    return report,inventory
