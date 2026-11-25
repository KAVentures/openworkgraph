"""Bounded temporal sensitivity of a fixed what-if simulation configuration."""
from math import isfinite
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from . import event_log_workspace as workspace
from . import event_log_simulation as sim

FORMAT="openworkgraph.simulation-temporal-sensitivity.v1"
MAX_PROJECTED_EVENTS=100_000
METRICS=("p50","p90","mean")


class Request(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,allow_inf_nan=False)
    holdout_shares: list[float] = Field(min_length=2,max_length=4)
    seeds: list[StrictInt] = Field(min_length=1,max_length=2)
    cases: StrictInt = Field(default=500,ge=50,le=2000)
    distribution: str = "fixed"
    activity_time_basis: str = "median"
    routing: str = "paths"
    max_absolute_relative_error: float = Field(ge=0,le=10)


def split_summary(rows, shares, metrics=METRICS):
    """Separate random-seed sensitivity from differences between observed splits."""
    output=[]
    for share in shares:
        group=[r for r in rows if r["holdout_share"]==share]
        observed=group[0]["cycle_time_seconds"]["observed_held_out"]
        if any(r["cutoff"]!=group[0]["cutoff"] or r["cases"]!=group[0]["cases"]
               or r["cycle_time_seconds"]["observed_held_out"]!=observed for r in group):
            raise workspace.EventLogWorkspaceError("observed validation population changed between seeds")
        metric_rows={}
        for metric in metrics:
            available=[r for r in group if r["threshold_status"][metric]!="unresolved"]
            values=[r["cycle_time_seconds"]["simulated"][metric] for r in available]
            errors=[r["cycle_time_seconds"]["relative_error"][metric] for r in available]
            complete=len(available)==len(group)
            metric_rows[metric]={
                "observed_seconds":observed[metric],
                "resolved_seeds":len(available),"unresolved_seeds":len(group)-len(available),
                "simulated_seconds_minimum":min(values) if values else None,
                "simulated_seconds_maximum":max(values) if values else None,
                "simulated_seconds_spread":max(values)-min(values) if complete and len(values)>=2 else None,
                "relative_error_minimum":min(errors) if errors else None,
                "relative_error_maximum":max(errors) if errors else None,
                "seed_spread_established_for_all_selected_seeds":complete and len(values)>=2}
        output.append({"holdout_share":share,"cutoff":group[0]["cutoff"],"seed_count":len(group),
                       "cases":group[0]["cases"],"metrics":metric_rows})
    return output


def review(log_id,raw,case_type=None):
    try:
        spec=Request.model_validate(raw)
    except ValidationError:
        raise workspace.EventLogWorkspaceError("invalid temporal validation settings") from None
    shares=spec.holdout_shares
    seeds=spec.seeds
    if (any(type(x) not in (int,float) for x in raw["holdout_shares"])
            or any(not .05<=v<=.5 for v in shares) or len(set(shares))!=len(shares)
            or any(not 0<=s<=2147483647 for s in seeds) or len(set(seeds))!=len(seeds)):
        raise workspace.EventLogWorkspaceError("select distinct holdout shares from .05 to .5 and distinct nonnegative seeds")
    if type(raw["max_absolute_relative_error"]) not in (int,float):
        raise workspace.EventLogWorkspaceError("error tolerance must be a number")
    if (spec.distribution not in ("fixed","exponential") or spec.routing not in ("paths","transitions")
            or spec.activity_time_basis not in ("median","mean")):
        raise workspace.EventLogWorkspaceError("unsupported simulation distribution, time basis or routing")
    # Same local retained log and projection for all runs. The existing
    # workspace uses a reentrant lock; no mutation is performed here.
    with workspace._LOCK:
        source=workspace._query(log_id,case_type,lambda c:{"projected_events":c.execute("SELECT count(*) FROM ce").fetchone()[0]})
        if source["projected_events"]>MAX_PROJECTED_EVENTS:
            raise workspace.EventLogWorkspaceError("temporal review exceeds projected-event budget")
        rows=[]
        for share in shares:
            for seed in seeds:
                settings={"holdout_share":share,"seed":seed,"cases":spec.cases,
                          "distribution":spec.distribution,"activity_time_basis":spec.activity_time_basis,
                          "routing":spec.routing}
                report=sim.validate(log_id,settings,case_type)
                errors=report["cycle_time_seconds"]["relative_error"]
                stopped=report.get("simulated_routes",{}).get("stopped_at_path_limit",0)
                classifications={}
                for metric in METRICS:
                    value=errors[metric]
                    classifications[metric]=("unresolved" if stopped or value is None or not isfinite(value)
                        else "within" if abs(value)<=spec.max_absolute_relative_error else "outside")
                rows.append({"holdout_share":share,"seed":seed,"cutoff":report["cutoff"],
                    "cases":report["cases"],"cycle_time_seconds":report["cycle_time_seconds"],
                    "distribution_distance":report["distribution_distance"],
                    "arrivals_per_hour":report["arrivals_per_hour"],
                    "routes_stopped_at_path_limit":stopped,
                    "threshold_status":classifications})
    summary={}
    for metric in METRICS:
        statuses=[r["threshold_status"][metric] for r in rows]
        errors=[r["cycle_time_seconds"]["relative_error"][metric] for r in rows if r["threshold_status"][metric]!="unresolved"]
        summary[metric]={"within":statuses.count("within"),"outside":statuses.count("outside"),
            "unresolved":statuses.count("unresolved"),
            "minimum_relative_error":min(errors) if errors else None,
            "maximum_relative_error":max(errors) if errors else None,
            "status":"observed_miss" if "outside" in statuses else "unresolved" if "unresolved" in statuses else "within_all_tested"}
    distinct=len({r["cutoff"] for r in rows})
    return {"format":FORMAT,"log":source["log"],"case_notion":source["case_notion"],
        "settings":spec.model_dump(mode="json"),"projected_events":source["projected_events"],
        "run_count":len(rows),"requested_split_count":len(shares),"distinct_cutoffs":distinct,
        "repeated_cutoffs":distinct<len(shares),"metric_summary":summary,"runs":rows,
        "split_sensitivity":split_summary(rows,shares),
        "all_metrics_within_all_tested":all(s["status"]=="within_all_tested" for s in summary.values()),
        "simulated_not_observed":True,"forecast_or_guarantee":False,"execution_authorized":False,
        "independent_holdouts_verified":False,"model_selected_using_holdout":False,
        "interpretation":[
            "Each prespecified share uses the existing earlier-case calibration and later-case comparison; held-out populations overlap and cutoff ties may yield identical splits.",
            "No parameter search or best seed selection is performed. Relative error thresholds are explicit descriptive tolerances, not significance tests; the underlying evaluator rounds relative errors to six decimals.",
            "Stopped routes and undefined relative errors are unresolved for the tolerance summary. Observed cases can be unfinished, and calibration may exclude cases outside model limits.",
            "Per-split simulated seconds spread is reported only with at least two resolved seeds and no unresolved seeds. Differences between observed periods are not random-seed variation.",
            "Results do not establish independent replications, representative coverage, completion, causal effects or forecast accuracy."]}
