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
import base64
import binascii
import csv
import io
from hashlib import sha256
import json
import math
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

MAX_K = 12
MAX_VARIANTS = 500
MAX_ACTIVITIES = 60
MAX_ITERATIONS = 60
MAX_EXPORT_CASES = 50_000


class ClusterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int | None = Field(default=None, ge=2, le=MAX_K)
    max_k: int = Field(default=8, ge=2, le=MAX_K)
    max_variants: int = Field(default=200, ge=2, le=MAX_VARIANTS)
    max_activities: int = Field(default=40, ge=2, le=MAX_ACTIVITIES)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


class _ClusterExportSpec(ClusterSpec):
    cluster_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    after: str | None = Field(default=None, min_length=1, max_length=8192)


class _ClusterCaseSpec(ClusterSpec):
    cluster_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    after: str | None = Field(default=None, min_length=1, max_length=200)
    limit: StrictInt = Field(default=50, ge=1, le=100)


_Settings = TypeVar("_Settings", bound=ClusterSpec)
_Vector = dict[tuple[str, ...], float]


def _parse(spec: Any, model: type[_Settings]) -> _Settings:
    try:
        parsed = model.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "trace clustering settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _features(path: list[str], vocabulary: set[str]) -> _Vector:
    counts = Counter(a for a in path if a in vocabulary)
    vector: _Vector = {("activity", a): math.log1p(n) for a, n in counts.items()}
    for x, y in zip(path, path[1:]):
        if x in vocabulary and y in vocabulary:
            vector[("follows", x, y)] = 1.0
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {k: v / norm for k, v in vector.items()} if norm else {}


def _dot(a: _Vector, b: _Vector) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _distance(a: _Vector, na: float, b: _Vector, nb: float) -> float:
    return math.sqrt(max(0.0, na + nb - 2 * _dot(a, b)))


def _norm2(v: _Vector) -> float:
    return sum(x * x for x in v.values())


def _centroid(members: list[int], vectors: list[_Vector], weights: list[int]) -> _Vector:
    total = sum(weights[i] for i in members)
    out: _Vector = {}
    for i in members:
        for key, value in vectors[i].items():
            out[key] = out.get(key, 0.0) + value * weights[i] / total
    return out


def _kmeans(vectors: list[_Vector], norms: list[float], weights: list[int], k: int) -> list[int]:
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


def _cluster_key(members: list[tuple[list[str], int]]) -> str:
    digest = sha256()
    for path, count in members:
        digest.update(json.dumps([path, count], ensure_ascii=False, separators=(",", ":")).encode())
    return digest.hexdigest()


def _case_page(conn, review: _ClusterCaseSpec, key_to_group: dict[str, int],
               sizes: Counter[int], ranks: dict[int, int]) -> dict[str, Any]:
    if review.cluster_key not in key_to_group:
        raise workspace.EventLogWorkspaceError("the cluster has changed; run the cluster report again")
    group = key_to_group[review.cluster_key]
    rank = ranks[group]
    rows = conn.execute("""SELECT t.case_id, t.seconds, t.events, t.path[1], t.path[-1]
        FROM tc t JOIN tcv v USING (path) WHERE v.cluster = ? AND (? IS NULL OR t.case_id > ?)
        ORDER BY t.case_id LIMIT ?""", [rank, review.after, review.after, review.limit + 1]).fetchall()
    return {"cluster": rank, "cluster_key": review.cluster_key, "total": sizes[group],
            "cases": [{"case_id": key, "duration_seconds": seconds, "events": events,
                       "first_activity": first, "last_activity": last}
                      for key, seconds, events, first, last in rows[:review.limit]],
            "next_after": rows[review.limit - 1][0] if len(rows) > review.limit else None}


def _export_scope(log_id: str, selected: _ClusterExportSpec) -> str:
    document = {"log_id": log_id, "settings": selected.model_dump(exclude={"after"})}
    return sha256(json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _export_cursor(log_id: str, selected: _ClusterExportSpec, case_id: str, snapshot: str) -> str:
    payload = json.dumps({"case_id": case_id, "scope": _export_scope(log_id, selected), "snapshot": snapshot}, ensure_ascii=True,
                         sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _cursor_case(log_id: str, selected: _ClusterExportSpec) -> tuple[str | None, str | None]:
    if selected.after is None:
        return None, None
    try:
        encoded = selected.after.encode("ascii")
        payload = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        document = json.loads(payload)
        if (not isinstance(document, dict) or set(document) != {"case_id", "scope", "snapshot"}
                or not isinstance(document["case_id"], str) or not document["case_id"]
                or len(document["case_id"]) > 200 or document["scope"] != _export_scope(log_id, selected)
                or not isinstance(document["snapshot"], str) or len(document["snapshot"]) != 64
                or any(c not in "0123456789abcdef" for c in document["snapshot"])
                or _export_cursor(log_id, selected, document["case_id"], document["snapshot"]) != selected.after):
            raise ValueError
        return document["case_id"], document["snapshot"]
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise workspace.EventLogWorkspaceError("invalid cluster export cursor or settings changed; start again") from None


def _export_snapshot(conn, rank: int) -> str:
    """Hash the entire ordered exported projection without retaining the case list."""
    digest = sha256()
    cursor = conn.execute("""SELECT t.case_id, CAST(t.seconds AS DOUBLE), t.events, t.path[1], t.path[-1]
        FROM tc t JOIN tcv v USING (path) WHERE v.cluster = ? ORDER BY t.case_id""", [rank])
    while batch := cursor.fetchmany(4096):
        for row in batch:
            encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _case_export(conn, log_id: str, selected: _ClusterExportSpec, key_to_group: dict[str, int],
                 sizes: Counter[int], ranks: dict[int, int]) -> dict[str, Any]:
    if selected.cluster_key not in key_to_group:
        raise workspace.EventLogWorkspaceError("the cluster has changed; run the cluster report again")
    group = key_to_group[selected.cluster_key]
    rank = ranks[group]
    after, expected_snapshot = _cursor_case(log_id, selected)
    snapshot = _export_snapshot(conn, rank) if after is not None or sizes[group] > MAX_EXPORT_CASES else None
    if expected_snapshot is not None and expected_snapshot != snapshot:
        raise workspace.EventLogWorkspaceError("cluster case export changed between pages; start again")
    rows = conn.execute("""SELECT t.case_id, CAST(t.seconds AS DOUBLE), t.events, t.path[1], t.path[-1]
        FROM tc t JOIN tcv v USING (path) WHERE v.cluster = ? AND (? IS NULL OR t.case_id > ?)
        ORDER BY t.case_id LIMIT ?""", [rank, after, after, MAX_EXPORT_CASES + 1]).fetchall()
    return {"cluster": rank, "cluster_key": selected.cluster_key, "total": sizes[group], "rows": rows, "snapshot": snapshot}


def _analyse(log_id: str, settings: ClusterSpec, review: _ClusterCaseSpec | None = None,
             export: _ClusterExportSpec | None = None) -> dict[str, Any]:

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        conn.execute("""CREATE TEMP TABLE tc AS SELECT case_id,
                list(activity ORDER BY ts, event_id) AS path,
                date_diff('millisecond', min(ts), max(ts)) / 1000.0 AS seconds, count(*) AS events
            FROM ce GROUP BY case_id""")
        rows = conn.execute("SELECT path, count(*) AS n FROM tc GROUP BY path ORDER BY n DESC, path").fetchall()
        cases = sum(n for _, n in rows)
        if not cases:
            if review is not None or export is not None:
                raise workspace.EventLogWorkspaceError("the cluster has changed; run the cluster report again")
            return {"cases": 0, "clusters": [], "variants": 0}
        presence = Counter()
        for path, n in rows:
            for activity in set(path):
                presence[activity] += n
        vocabulary = {a for a, _ in sorted(presence.items(), key=lambda p: (-p[1], p[0]))[:settings.max_activities]}
        top, rest = rows[:settings.max_variants], rows[settings.max_variants:]
        paths = [p for p, _ in top]
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
        assigned: list[tuple[list[str], int, int]] = [(p, n, labels[i]) for i, (p, n) in enumerate(top)]
        for path, n in rest:
            v = _features(path, vocabulary)
            nv = _norm2(v)
            assigned.append((path, n, min(groups, key=lambda c: (_distance(v, nv, centres[c], centre_norms[c]), c))))
        sizes: Counter[int] = Counter()
        members_by_group: dict[int, list[tuple[list[str], int]]] = {group: [] for group in groups}
        for path, n, c in assigned:
            sizes[c] += n
            members_by_group[c].append((path, n))
        cluster_keys = {c: _cluster_key(members) for c, members in members_by_group.items()}
        rank = {c: r for r, c in enumerate(sorted(groups, key=lambda c: (-sizes[c], c)), start=1)}
        conn.execute("CREATE OR REPLACE TEMP TABLE tcv AS SELECT unnest(?) AS path, unnest(?) AS cluster",
                     [[p for p, _, _ in assigned], [rank[c] for _, _, c in assigned]])
        if review is not None:
            return _case_page(conn, review, {key: c for c, key in cluster_keys.items()}, sizes, rank)
        if export is not None:
            return _case_export(conn, log_id, export, {key: c for c, key in cluster_keys.items()}, sizes, rank)
        stats = {r[0]: r[1:] for r in conn.execute("""SELECT v.cluster, quantile_cont(t.seconds, 0.5), avg(t.seconds),
                avg(t.events) FROM tc t JOIN tcv v USING (path) GROUP BY v.cluster""").fetchall()}
        out = []
        for c in sorted(groups, key=lambda c: rank[c]):
            members = members_by_group[c]
            size = sizes[c]
            inside = Counter()
            for path, n in members:
                for activity in set(path):
                    inside[activity] += n
            contrast = []
            for activity, overall in presence.items():
                share_in, share_all = inside[activity] / size, overall / cases
                contrast.append({"activity": activity, "cases_in_cluster": inside[activity],
                                 "cases_in_log": overall, "case_share_in_cluster": round(share_in, 4),
                                 "case_share_in_log": round(share_all, 4),
                                 "lift": round(share_in / share_all, 3) if share_all else None,
                                 "difference": round(share_in - share_all, 4)})
            more = sorted((x for x in contrast if x["difference"] > 0.05), key=lambda x: (-x["difference"], x["activity"]))[:6]
            less = sorted((x for x in contrast if x["difference"] < -0.05), key=lambda x: (x["difference"], x["activity"]))[:6]
            median, mean, events = stats.get(rank[c], (None, None, None))
            variants = [p for p, _ in members]
            out.append({
                "cluster": rank[c], "cluster_key": cluster_keys[c], "cases": size, "share": round(size / cases, 6),
                "variants": len(members),
                "top_variants": [{"activities": p, "cases": n, "share_of_cluster": round(n / size, 6)}
                                 for p, n in members[:5]],
                "more_common_activities": more, "less_common_activities": less,
                "median_throughput_seconds": round(median, 3) if median is not None else None,
                "mean_throughput_seconds": round(mean, 3) if mean is not None else None,
                "mean_events_per_case": round(events, 3) if events is not None else None,
                "cohort": {"label": f"Cluster {rank[c]}", "variants": variants}
                if len(variants) <= cohorts.MAX_COHORT_VARIANTS
                and all(len(path) <= cohorts.MAX_VARIANT_LENGTH for path in variants) else None,
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


def clusters(log_id: str, spec: Any = None) -> dict[str, Any]:
    return _analyse(log_id, _parse(spec, ClusterSpec))


def cluster_cases(log_id: str, spec: Any) -> dict[str, Any]:
    settings = _parse(spec, _ClusterCaseSpec)
    data = _analyse(log_id, settings, settings)
    return {key: data[key] for key in ("case_notion", "cluster", "cluster_key", "total", "cases", "next_after")}


def cluster_cases_csv(log_id: str, spec: Any) -> tuple[str, dict[str, Any]]:
    from .event_log_variant_export import _cell
    settings = _parse(spec, _ClusterExportSpec)
    data = _analyse(log_id, settings, export=settings)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["case_id", "duration_seconds", "events", "first_activity", "last_activity"])
    for case_id, seconds, events, first, last in data["rows"][:MAX_EXPORT_CASES]:
        writer.writerow([_cell(case_id), seconds, events, _cell(first), _cell(last)])
    return out.getvalue(), {"cluster": data["cluster"], "cluster_key": data["cluster_key"],
                            "case_notion": data["case_notion"], "total": data["total"],
                            "written": min(len(data["rows"]), MAX_EXPORT_CASES),
                            "truncated": len(data["rows"]) > MAX_EXPORT_CASES,
                            "next_after": _export_cursor(log_id, settings, data["rows"][MAX_EXPORT_CASES - 1][0], data["snapshot"])
                            if len(data["rows"]) > MAX_EXPORT_CASES else None}


__all__ = ["clusters", "cluster_cases", "cluster_cases_csv"]
