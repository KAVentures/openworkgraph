"""Bounded side-by-side what-if alternatives on ONE frozen event-log calibration.

This is a local simulation comparison, not observed business value, a forecast
or an approval to execute a process. It never combines simulations from
different calibrations or sorts alternatives into a prescriptive winner.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import event_log_simulation as simulation
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.simulation-portfolio.v1"
MAX_ALTERNATIVES = 4
MAX_CASES_PER_RUN = 5000
BASE_FIELDS = frozenset((
    "cases", "seed", "activity_time_basis", "calendar", "arrivals_per_hour",
))
OVERRIDE_FIELDS = frozenset((
    "label", "activities", "paths", "distribution", "arrivals_per_hour",
    "scenario_calendar", "pools", "priorities",
))
CYCLE_NAMES = ("p50", "p90", "mean")


def _validate(spec: Any) -> tuple[dict, list[dict]]:
    if not isinstance(spec, dict) or set(spec) != {"format", "baseline", "alternatives"}:
        raise EventLogWorkspaceError("Provide format, one shared baseline, and 2–4 alternatives")
    if spec["format"] != FORMAT:
        raise EventLogWorkspaceError("Unsupported simulation-portfolio format")
    common, alternatives = spec["baseline"], spec["alternatives"]
    if not isinstance(common, dict) or set(common) - BASE_FIELDS:
        raise EventLogWorkspaceError("The shared baseline has unsupported fields")
    if not isinstance(alternatives, list) or not 2 <= len(alternatives) <= MAX_ALTERNATIVES:
        raise EventLogWorkspaceError("Compare 2 to 4 alternatives in one bounded review")

    cases = common.get("cases", 2000)
    seed = common.get("seed", 1)
    if type(cases) is not int or not 10 <= cases <= MAX_CASES_PER_RUN:
        raise EventLogWorkspaceError("Simulate 10–5000 cases per alternative")
    if type(seed) is not int or not 0 <= seed <= 2**31:
        raise EventLogWorkspaceError("Choose an integer simulation seed between 0 and 2^31")

    seen = set()
    for item in alternatives:
        if not isinstance(item, dict) or set(item) - OVERRIDE_FIELDS:
            raise EventLogWorkspaceError("An alternative has unsupported fields")
        label = item.get("label")
        if (not isinstance(label, str) or not 1 <= len(label) <= 80 or
                label != label.strip() or
                any(ord(c) < 32 or 127 <= ord(c) < 160 for c in label)):
            raise EventLogWorkspaceError("Each alternative needs a short printable name")
        if label in seen:
            raise EventLogWorkspaceError("Alternative names must be distinct")
        seen.add(label)
    return {**common, "cases": cases, "seed": seed}, alternatives


def _summary(raw: dict) -> dict:
    waits = sorted(
        (
            {"activity": row["activity"], "mean_wait_seconds": row["mean_wait_seconds"],
             "overloaded": row["overloaded"]}
            for row in raw["activities"]
        ),
        key=lambda row: (-row["mean_wait_seconds"], row["activity"]),
    )
    return {
        "cases_simulated": raw["cases_simulated"],
        "cases_measured": raw["cases_measured"],
        "cases_abandoned_after_warmup": raw.get("cases_abandoned_after_warmup", 0),
        "cases_waiting_in_unfilled_batches": raw.get("cases_waiting_in_unfilled_batches", 0),
        "cycle_time_seconds": raw["cycle_time_seconds"],
        "completed_per_day": raw["completed_per_day"],
        "unstable": raw["unstable"],
        "longest_wait_activities": waits[:5],
    }


def _difference(left: dict, right: dict) -> dict:
    total = left["cases_simulated"]
    warmup = int(total * simulation.WARM_UP_SHARE)
    available = total - warmup
    reasons = []
    if left["cases_measured"] != available or right["cases_measured"] != available:
        reasons.append("incomplete_or_different_completion_population")
    if left["unstable"] or right["unstable"]:
        reasons.append("overloaded_resource_pools")
    if (left.get("cases_abandoned_after_warmup", 0) or
            right.get("cases_abandoned_after_warmup", 0)):
        reasons.append("simulated_abandonment_changes_completion_population")
    if (left.get("cases_waiting_in_unfilled_batches", 0) or
            right.get("cases_waiting_in_unfilled_batches", 0)):
        reasons.append("unfinished_batched_cases")
    deltas: dict[str, float | None] = {}
    if not reasons:
        for field in CYCLE_NAMES:
            a, b = left["cycle_time_seconds"][field], right["cycle_time_seconds"][field]
            deltas[field] = round(b - a, 3) if a is not None and b is not None else None
    return {
        "descriptive_cycle_time_comparable": not reasons,
        "reasons_deltas_withheld": reasons,
        "cycle_time_change_seconds": deltas,
        "completion_population_basis": "all_post_warmup_cases",
        "shared_random_streams_not_identical_observed_cases": True,
    }


def compare(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    """Calibrate once, then simulate baseline and 2–4 alternatives with one seed.

    No shared source log is uploaded or mutated; this is the existing local
    dashboard-session-only scope. All branches see the identical calibration
    object even if the source subsequently changes.
    """
    common, variants = _validate(spec)
    base = simulation.calibrate(log_id, case_type)
    if not base["paths"]:
        raise EventLogWorkspaceError("No complete calibrated paths support this comparison")
    if base["arrivals_per_hour"] is None and "arrivals_per_hour" not in common:
        raise EventLogWorkspaceError(
            "Enter a shared arrival rate; the selected log cannot calibrate one"
        )

    # Parse every alternative before starting any simulation. A malformed
    # option cannot leave a misleading partial review in the response.
    baseline = simulation.parse_scenario(common, base)
    models = [
        simulation.parse_scenario({**common, **item}, base)
        for item in variants
    ]
    # Common cases and RNG are fixed before parsing, and not overridable by
    # any alternative. The raw alternatives are bounded by the HTTP route.
    if any(x["cases"] != baseline["cases"] or x["seed"] != baseline["seed"] for x in models):
        raise EventLogWorkspaceError("All simulation runs must share a case count and seed")

    try:
        observed = simulation.simulate(baseline)
        generated = [simulation.simulate(item) for item in models]
    except (OverflowError, ValueError) as error:
        if isinstance(error, ValueError) and "out of range" not in str(error):
            raise
        raise EventLogWorkspaceError(
            "The simulated time range exceeds supported calendar dates"
        ) from None

    reference = {
        "case_notion": base["case_notion"],
        "calibration": {
            field: base[field]
            for field in ("arrivals_per_hour", "paths", "paths_cover_cases_share",
                          "activities", "observed_cases", "calibrated_cases",
                          "excluded_case_reasons", "limits", "arrival_rate_basis")
            if field in base
        },
        "shared_inputs": common,
    }
    source_digest = hashlib.sha256(
        json.dumps(reference, sort_keys=True, separators=(",", ":"),
                   default=str, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()

    return {
        "format": FORMAT,
        "log": base["log"],
        "case_notion": base["case_notion"],
        "calibration_and_shared_inputs_sha256": source_digest,
        "shared_inputs": common,
        "baseline": _summary(observed),
        "alternatives": [
            {
                "label": spec["label"],
                "changes_reviewed": {k: v for k, v in spec.items() if k != "label"},
                "result": _summary(raw),
                **_difference(observed, raw),
            }
            for spec, raw in zip(variants, generated)
        ],
        "single_calibration_used": True,
        "identical_case_count_and_seed": True,
        "calibration_source_independently_verified": False,
        "observed_or_measured_improvement": False,
        "causal_attribution_or_production_forecast": False,
        "execution_authorized": False,
        "auto_selected_recommended_alternative": False,
        "assumptions": [
            "Every result is simulated from one selected, static event-log calibration. It is not a verified real workflow.",
            "Every run uses the same case count and seeded random streams; changed routing and arrivals can still alter per-case paths or time windows.",
            "Cycle-time differences are withheld if any compared run has incomplete post-warm-up completion, abandonment, unfilled batching or overloaded pools.",
            "An apparently faster model is not evidence of productivity gains, causal savings, or a permission to change live systems.",
        ],
    }


__all__ = ["FORMAT", "MAX_ALTERNATIVES", "MAX_CASES_PER_RUN", "compare"]
