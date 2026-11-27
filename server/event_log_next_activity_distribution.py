"""Full distribution validation of historical next-activity forecasts.

Scores the original empirical probability mass, including synthetic END,
against actual activity labels. Missing observations are not END. No
retrospective label vocabulary is used to train or narrow uncertainty.
"""
from math import log

EPSILON = 1e-12
ESTIMATORS = ("model", "overall_baseline", "last_activity_baseline")


class Distribution:
    def __init__(self, counts):
        self.counts=counts
        self.total=sum(counts.values())
        if self.total<=0:
            raise ValueError("empty next-activity distribution")
        # Cached once per retained model counter. No per-case scan of all
        # activities, nor normalization of the truncated top-k list.
        self.square_mass=sum(n*n for n in counts.values())/(self.total*self.total)
        self.max_actual_probability=max((n for a,n in counts.items() if a is not None),default=0)/self.total
        self.end_probability=counts.get(None,0)/self.total

    def scores(self, actual, unresolved):
        if not unresolved:
            if actual is None:
                raise ValueError("resolved next activity must be an actual label")
            p=self.counts.get(actual,0)/self.total
            brier=max(0.,1+self.square_mass-2*p)
            loss=-log(max(p,EPSILON))
            return {"brier":(brier,brier),"clipped_log_loss":(loss,loss),
                    "actual_probability":p,"end_probability":self.end_probability}
        # The unresolved activity could be any actual label, including a
        # previously unseen one. END cannot be the recorded next activity.
        p=self.max_actual_probability
        return {"brier":(max(0.,1+self.square_mass-2*p),1+self.square_mass),
                "clipped_log_loss":(-log(max(p,EPSILON)),-log(EPSILON)),
                "actual_probability":None,"end_probability":self.end_probability}


class Accumulator:
    def __init__(self):
        self.predictions=self.resolved=self.zero_probability=0
        self.end_mass=0.
        self.bounds={key:[0.,0.] for key in ("brier","clipped_log_loss")}
        self.resolved_sums={key:0. for key in self.bounds}

    def add(self, scores):
        self.predictions+=1
        p=scores["actual_probability"]
        resolved=p is not None
        self.resolved+=resolved
        self.zero_probability+=resolved and p==0
        self.end_mass+=scores["end_probability"]
        for key in self.bounds:
            lo,hi=scores[key]
            self.bounds[key][0]+=lo
            self.bounds[key][1]+=hi
            if resolved:self.resolved_sums[key]+=lo

    def report(self, population):
        n=self.predictions;r=self.resolved
        return {"population":population,"predicted":n,"resolved":r,"unresolved":n-r,
                "abstained":population-n,
                "resolved_zero_actual_probability":self.zero_probability,
                "mean_synthetic_end_mass":self.end_mass/n if n else None,
                **{key:{"resolved_mean":self.resolved_sums[key]/r if r else None,
                        "predicted_population_mean_bounds":{
                            "lower":lo/n if n else None,"upper":hi/n if n else None}}
                   for key,(lo,hi) in self.bounds.items()}}


class Paired:
    def __init__(self):
        self.n=self.resolved=0
        self.bounds={k:[0.,0.] for k in ("brier","clipped_log_loss")}
        self.resolved_sums={k:0. for k in self.bounds}

    def add(self, a,b,same_distribution):
        self.n+=1
        resolved=a["actual_probability"] is not None
        self.resolved+=resolved
        for key in self.bounds:
            # Same counter produces exactly the same score for every
            # possible actual label. Otherwise marginal subtraction is a
            # conservative outer bound, not necessarily attainable.
            lo,hi=(0.,0.) if same_distribution else (a[key][0]-b[key][1],a[key][1]-b[key][0])
            self.bounds[key][0]+=lo;self.bounds[key][1]+=hi
            if resolved:self.resolved_sums[key]+=lo

    def report(self):
        return {"paired_predictions":self.n,"paired_resolved":self.resolved,
                "difference_direction":"history_minus_baseline_lower_is_better",
                "unresolved_difference_bounds_are_conservative":True,
                **{key:{"resolved_mean_difference":self.resolved_sums[key]/self.resolved if self.resolved else None,
                        "paired_population_mean_difference_bounds":{
                            "lower":lo/self.n if self.n else None,
                            "upper":hi/self.n if self.n else None}}
                   for key,(lo,hi) in self.bounds.items()}}


class Review:
    def __init__(self):
        self.profiles={}
        self.acc={k:Accumulator() for k in ESTIMATORS}
        self.pairs={k:Paired() for k in ESTIMATORS[1:]}

    def add(self, counters, actual, unresolved):
        scores={}
        for name,counter in counters.items():
            if not counter:continue
            key=id(counter)
            if key not in self.profiles:
                self.profiles[key]=Distribution(counter)
            scores[name]=self.profiles[key].scores(actual,unresolved)
            self.acc[name].add(scores[name])
        for key,pair in self.pairs.items():
            if "model" in scores and key in scores:
                pair.add(scores["model"],scores[key],counters["model"] is counters[key])
        # Bounded per-case diagnostics: no full probability vectors retained.
        return scores

    def report(self,population):
        return {"format":"openworkgraph.next-activity-distribution-validation.v1",
                "estimators":{k:v.report(population) for k,v in self.acc.items()},
                "paired_comparisons":{k:v.report() for k,v in self.pairs.items()},
                "distinct_counter_profiles":len(self.profiles),
                "log_probability_floor":EPSILON,
                "multiclass_brier_includes_synthetic_end_mass":True,
                "unresolved_outcomes_imputed":False,
                "proper_log_score_clipped_for_finiteness":True,
                "interpretation":[
                    "Multiclass Brier is the sum of squared differences across all labels, including synthetic END. Range zero to two; lower is better. Probabilities use the full counter, not the displayed top three.",
                    "Clipped log loss is minus the natural logarithm of actual-label probability floored at 1e-12. Zero-probability resolved events are separately counted; clipping makes the diagnostic finite.",
                    "Missing or ambiguous next events may be any actual label, including unseen labels. They are never scored as END. Bounds cover predicted cases only and abstentions are disclosed.",
                    "Paired differences compare the same predicted cases. For unresolved outcomes, marginal subtraction gives conservative outer bounds; identical counters cancel exactly. These are not confidence intervals."]}
