"""Read-only, source-pinned comparison of authorized shared-analysis revisions.

Group codes are newly assigned on every local export and MUST NOT be joined.
A delta is descriptive arithmetic, not an observed intervention, causal proof,
or independent source verification. The parent publication store remains the
sole authority for tenant membership, expiry, revision integrity and auditing.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext

from .analysis_publications import AnalysisPublications, PublicationError


REVIEW_FORMAT = "openworkgraph.analysis-revision-comparison.v1"


def _basis(detail):
    doc = detail["snapshot"]
    return {
        "revision": detail["viewing_revision"],
        "title": detail["title"],
        "published_at": detail["published_at"],
        "generated_at": doc["generated_at"],
        "content_sha256": detail["content_sha256"],
        "source_content_sha256": doc["source_content_sha256"],
        "analysis_spec_sha256": doc["analysis_spec_sha256"],
        "metric_definition_sha256": doc["metric_definition_sha256"],
        "matching_cases": doc["matching_cases"],
        "matching_rows": doc["matching_rows"],
        "rows_truncated": doc["rows_truncated"],
    }


def _change(before, after):
    """Exact base-ten delta of the published JSON values, with a bounded %."""
    old, new = Decimal(str(before)), Decimal(str(after))
    with localcontext() as ctx:
        ctx.prec = 70
        delta = new - old
        change = format(delta, "f")
        relative = None
        if old != 0:
            ratio = delta * Decimal(100) / old
            # Percentages with extreme denominators are not useful or safe
            # to display as bounded UI numbers; suppress, don't round to 0.
            if ratio.is_finite() and abs(ratio) <= Decimal("1000000000000"):
                relative = format(ratio.quantize(Decimal("0.01")), "f")
    return change, relative


def compare_snapshots(left, right):
    """Compare one exact authorized revision pair; never align coded groups."""
    l, r = left["snapshot"], right["snapshot"]
    reasons = []

    def refuse(reason, condition):
        if condition:
            reasons.append(reason)

    refuse("different_analysis_specification",
           l["analysis_spec_sha256"] != r["analysis_spec_sha256"])
    refuse("different_event_or_case_level", l["level"] != r["level"])
    refuse("different_measure_definitions", l["columns"] != r["columns"])
    refuse("different_metric_definition_versions",
           l["metric_definition_sha256"] != r["metric_definition_sha256"])
    refuse("metric_definition_not_versioned",
           any(c["value_basis"] == "case_metric" for c in l["columns"] + r["columns"])
           and not l["metric_definition_sha256"])
    refuse("different_fingerprint_algorithm",
           l["fingerprint_algorithm"] != r["fingerprint_algorithm"])
    refuse("group_codes_have_no_cross_export_identity",
           any(c["kind"] == "group" for c in l["columns"] + r["columns"]))
    refuse("bounded_rows_are_not_complete",
           l["rows_truncated"] or r["rows_truncated"])
    refuse("requires_one_complete_ungrouped_row",
           len(l["rows"]) != 1 or len(r["rows"]) != 1)
    refuse("nonfinite_results_withheld",
           bool(l["non_finite_results_withheld"] or r["non_finite_results_withheld"]))
    refuse("incomplete_metric_coverage",
           any(c["without_value"] for c in l["metric_coverage"] + r["metric_coverage"]))
    refuse("missing_numeric_measure",
           any(v is None or type(v) not in (int, float)
               for row in l["rows"] + r["rows"] for v in row)
           if not any(c["kind"] == "group" for c in l["columns"] + r["columns"]) else False)
    # Identical claimed source+spec+metric basis should not produce different
    # populations or values. We cannot independently establish source truth.
    refuse("same_fingerprint_but_different_values",
           l["source_content_sha256"] == r["source_content_sha256"]
           and (l["rows"] != r["rows"] or
                l["matching_cases"] != r["matching_cases"] or
                l["matching_rows"] != r["matching_rows"]))

    changes = []
    if not reasons:
        for index, column in enumerate(l["columns"]):
            if column["kind"] != "number":
                continue
            before, after = l["rows"][0][index], r["rows"][0][index]
            change, percent = _change(before, after)
            changes.append({
                "heading": column["heading"],
                "aggregation": column["aggregation"],
                "value_basis": column["value_basis"],
                "unit": column["unit"],
                "before": before,
                "after": after,
                "change_decimal": change,
                "relative_percent_decimal": percent,
            })

    return {
        "format": REVIEW_FORMAT,
        "view_id": left["view_id"],
        "left": _basis(left),
        "right": _basis(right),
        "same_claimed_source": l["source_content_sha256"] == r["source_content_sha256"],
        "same_claimed_analysis_spec": l["analysis_spec_sha256"] == r["analysis_spec_sha256"],
        "comparable": not reasons,
        "refusal_reasons": reasons,
        "changes": changes,
        "numeric_changes_descriptive_only": True,
        "source_independently_verified": False,
        "independent_outcome_or_savings_proven": False,
        "execution_authorized": False,
        "case_identities_joined": False,
    }


def compare(store: AnalysisPublications, principal, view_id: str, left: int, right: int):
    """Both revision reads and audit share the current tenant/ACL transaction."""
    if type(left) is not int or type(right) is not int or left == right or not (
        1 <= left <= 10 and 1 <= right <= 10
    ):
        raise PublicationError("Choose two different retained analysis revisions", 422)
    with store.transaction(principal) as conn:
        meta = store.meta(conn, principal, view_id)
        one = store.detail_in(conn, principal, meta, left)
        two = store.detail_in(conn, principal, meta, right)
        result = compare_snapshots(one, two)
        store.audit(conn, principal, "analysis.revisions_compared",
                    view_id=view_id, left=left, right=right,
                    left_sha256=one["content_sha256"],
                    right_sha256=two["content_sha256"],
                    comparable=result["comparable"],
                    refusal_reasons=result["refusal_reasons"])
        return result
