"""Group the cases of a saved event log into clusters of similar behaviour (trace clustering).

Celonis, Disco and ProM's trace clustering split a log whose map is a "spaghetti" of many
variants into a few groups that each read as one way of working. This does the same,
deterministically and locally:

1. every variant (the ordered activities of a case) becomes a feature vector of activity
   counts (``log(1 + n)``) and the directly-follows pairs it contains, scaled to unit length;
2. the most frequent variants are clustered with case-weighted k-means, starting from the
   most common variant and then the variant farthest from the chosen centres;
3. unless a number of clusters is given, the one with the best silhouette between 2 and
   ``max_k`` is used (over variants, each counted once, so one cluster per variant never wins);
4. the remaining rare variants join their nearest cluster.

Each cluster reports its cases, share, top variants, the activities that are more and less
common in it than in the whole log, its median throughput time and its events per case,
plus a cohort (``{"variants": [...]}``) that applies the cluster as a case filter
to the other analyses.

Clusters describe the structure of the imported log. They are not process types,
root causes or a judgement of how well any case was handled.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

_SEP = workspace._SEP
MAX_K = 12
MAX_VARIANTS = 500
MAX_ACTIVITIES = 60
MAX_ITERATIONS = 60


class ClusterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int | None = Field(default=None, ge=2, le=MAX_K)
    max_k: int = Field(default=8, ge=2, le=MAX_K)
    max_variants: int = Field(default=200, ge=2, le=MAX_VARIANTS)
    max_activities: int = Field(default=40, ge=2, le=MAX_ACTIVITIES)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> ClusterSpec:
    try:
        parsed = ClusterSpec.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "trace clustering settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _features(path: list[str], vocabulary: set[str]) -> dict[str, float]:
    counts = Counter(a for a in path if a in vocabulary)
    vector = {"a:" + a: math.log1p(n) for a, n in counts.items()}
    for x, y in zip(path, path[1:]):
        if x in vocabulary and y in vocabulary:
            vector["d:" + x + _SEP + y] = 1.0
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {k: v / norm for k, v in vector.items()} if norm else {}


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _distance(a: dict[str, float], na: float, b: dict[str, float], nb: float) -> float:
    return math.sqrt(max(0.0, na + nb - 2 * _dot(a, b)))


def _norm2(v: dict[str, float]) -> float:
    return sum(x * x for x in v.values())


def _centroid(members: list[int], vectors: list[dict[str, float]], weights: list[int]) -> dict[str, float]:
    total = sum(weights[i] for i in members)
    out: dict[str, float] = {}
    for i in members:
        for key, value in vectors[i].items():
            out[key] = out.get(key, 0.0) + value * weights[i] / total
    return out


def _kmeans(vectors: list[dict[str, float]], norms: list[float], weights: list[int], k: int) -> list[int]:
    """Case-weighted k-means with a deterministic farthest-point start."""
    order = sorted(range(len(vectors)), key=lambda i: (-weights[i], i))
    position = {i: r for r, i in enumerate(order)}
    centres = [dict(vectors[order[0]])]
    nearest = [_distance(vectors[i], norms[i], centres[0], _norm2(centres[0])) for i in range(len(vectors))]
    while len(centres) < k:
        pick = max(order, key=lambda i: (weights[i] * nearest[i] ** 2, -position[i]))
        if nearest[pick] == 0:
            break
        centres.append(dict(vectors[pick]))
        cn = _norm2(centres[-1])
        nearest = [min(nearest[i], _distance(vectors[i], norms[i], centres[-1], cn)) for i in range(len(vectors))]
    labels = [-1] * len(vectors)
    for _ in range(MAX_ITERATIONS):
        centre_norms = [_norm2(c) for c in centres]
        new = [min(range(len(centres)), key=lambda c: (_distance(vectors[i], norms[i], centres[c], centre_norms[c]), c))
               for i in range(len(vectors))]
        if new == labels:
            break
        labels = new
        for c in range(len(centres)):
            members = [i for i, label in enumerate(labels) if label == c]
            if members:
                centres[c] = _centroid(members, vectors, weights)
    return labels


def _silhouette(distances: list[list[float]], labels: list[int]) -> float:
    """Mean silhouette over variants, each counted once; a variant alone in its cluster scores 0.

    Weighting by cases would reward giving every variant its own cluster, since the cases of one
    variant are identical; counting variants keeps the choice about shared behaviour.
    """
    clusters = sorted(set(labels))
    if len(clusters) < 2:
        return 0.0
    members = {c: [j for j, label in enumerate(labels) if label == c] for c in clusters}
    score = 0.0
    for i, own in enumerate(labels):
        if len(members[own]) == 1:
            continue
        a = sum(distances[i][j] for j in members[own] if j != i) / (len(members[own]) - 1)
        b = min(sum(distances[i][j] for j in members[c]) / len(members[c]) for c in clusters if c != own)
        score += (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return score / len(labels)


def clusters(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE tc AS SELECT case_id,
                string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path,
                date_diff('millisecond', min(ts), max(ts)) / 1000.0 AS seconds, count(*) AS events
            FROM ce GROUP BY case_id""")
        rows = conn.execute("SELECT path, count(*) AS n FROM tc GROUP BY path ORDER BY n DESC, path").fetchall()
        cases = sum(n for _, n in rows)
        if not cases:
            return {"cases": 0, "clusters": [], "variants": 0}
        presence = Counter()
        for path, n in rows:
            for activity in set(path.split(_SEP)):
                presence[activity] += n
        vocabulary = {a for a, _ in sorted(presence.items(), key=lambda p: (-p[1], p[0]))[:settings.max_activities]}
        top, rest = rows[:settings.max_variants], rows[settings.max_variants:]
        paths = [p.split(_SEP) for p, _ in top]
        weights = [n for _, n in top]
        vectors = [_features(p, vocabulary) for p in paths]
        norms = [_norm2(v) for v in vectors]
        distances = [[_distance(vectors[i], norms[i], vectors[j], norms[j]) if i != j else 0.0
                      for j in range(len(vectors))] for i in range(len(vectors))]
        candidates = [settings.k] if settings.k else list(range(2, min(settings.max_k, len(top)) + 1))
        tried: list[dict[str, Any]] = []
        best: tuple[float, int, list[int]] | None = None
        for k in candidates:
            if k > len(top):
                continue
            labels = _kmeans(vectors, norms, weights, k)
            found = len(set(labels))
            sil = _silhouette(distances, labels)
            tried.append({"k": k, "clusters_found": found, "silhouette": round(sil, 4)})
            if best is None or sil > best[0] + 1e-9:
                best = (sil, k, labels)
        labels = best[2] if best else [0] * len(top)
        # Rare variants join the nearest cluster centre.
        groups = sorted(set(labels))
        centres = {c: _centroid([i for i, label in enumerate(labels) if label == c], vectors, weights) for c in groups}
        centre_norms = {c: _norm2(v) for c, v in centres.items()}
        assigned: list[tuple[str, int, int]] = [(p, n, labels[i]) for i, (p, n) in enumerate(top)]
        for path, n in rest:
            v = _features(path.split(_SEP), vocabulary)
            nv = _norm2(v)
            assigned.append((path, n, min(groups, key=lambda c: (_distance(v, nv, centres[c], centre_norms[c]), c))))
        sizes = Counter()
        for _, n, c in assigned:
            sizes[c] += n
        rank = {c: r for r, c in enumerate(sorted(groups, key=lambda c: (-sizes[c], c)), start=1)}
        conn.execute("CREATE OR REPLACE TEMP TABLE tcv AS SELECT unnest(?) AS path, unnest(?) AS cluster",
                     [[p for p, _, _ in assigned], [rank[c] for _, _, c in assigned]])
        stats = {r[0]: r[1:] for r in conn.execute("""SELECT v.cluster, quantile_cont(t.seconds, 0.5), avg(t.seconds),
                avg(t.events) FROM tc t JOIN tcv v USING (path) GROUP BY v.cluster""").fetchall()}
        out = []
        for c in sorted(groups, key=lambda c: rank[c]):
            members = [(p, n) for p, n, label in assigned if label == c]
            size = sizes[c]
            inside = Counter()
            for path, n in members:
                for activity in set(path.split(_SEP)):
                    inside[activity] += n
            contrast = []
            for activity, overall in presence.items():
                share_in, share_all = inside[activity] / size, overall / cases
                contrast.append({"activity": activity, "case_share_in_cluster": round(share_in, 4),
                                 "case_share_in_log": round(share_all, 4),
                                 "lift": round(share_in / share_all, 3) if share_all else None,
                                 "difference": round(share_in - share_all, 4)})
            more = sorted((x for x in contrast if x["difference"] > 0.05), key=lambda x: (-x["difference"], x["activity"]))[:6]
            less = sorted((x for x in contrast if x["difference"] < -0.05), key=lambda x: (x["difference"], x["activity"]))[:6]
            median, mean, events = stats.get(rank[c], (None, None, None))
            variants = [p.split(_SEP) for p, _ in members]
            out.append({
                "cluster": rank[c], "cases": size, "share": round(size / cases, 6), "variants": len(members),
                "top_variants": [{"activities": p.split(_SEP), "cases": n, "share_of_cluster": round(n / size, 6)}
                                 for p, n in members[:5]],
                "more_common_activities": more, "less_common_activities": less,
                "median_throughput_seconds": round(median, 3) if median is not None else None,
                "mean_throughput_seconds": round(mean, 3) if mean is not None else None,
                "mean_events_per_case": round(events, 3) if events is not None else None,
                "cohort": {"label": f"Cluster {rank[c]}", "variants": variants}
                if len(variants) <= cohorts.MAX_COHORT_VARIANTS else None,
            })
        return {"cases": cases, "variants": len(rows), "variants_clustered": len(top),
                "variants_assigned_to_nearest": len(rest), "activities_in_features": len(vocabulary),
                "activities_in_log": len(presence), "k": len(groups), "k_chosen_by": "given" if settings.k else "silhouette",
                "silhouette": round(best[0], 4) if best else None, "k_tried": tried, "clusters": out,
                "features": "log(1 + activity count) and directly-follows pairs per variant, unit length",
                "method": "case-weighted k-means, deterministic farthest-point start",
                "settings": settings.model_dump(exclude={"cohort", "case_type"}),
                "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
                "ordering_basis": "timestamp_then_event_id",
                "interpretation": ("Clusters group cases whose activities and orderings are similar in this imported log. "
                                   "They are not process types, causes or a judgement of how any case was handled.")}
    return workspace._query(log_id, settings.case_type, run)


__all__ = ["clusters"]
