# R12 — Organization repository intelligence

In the shared process portal, open **Process repository maps → Organization
repository intelligence**. Unlike the separately paginated saved-map inventory,
this review scans the complete bounded tenant catalog metadata and latest
saved map declarations in one authorized database read snapshot. Historical
map artifacts remain immutable and are inspected through the existing history
flow; unsaved drafts are not included.

## Seven complementary views

- **Process coverage and reuse:** distinguish active catalog processes with
  no occurrence from those without their own root map. Count distinct map
  roots, exact pins, repeated occurrences within one map and reused identities
  across maps. Multiple pins are version diversity, not automatically a defect.
  Unavailable referenced identities remain visible; unavailable titles do not.
- **Map readiness:** inspect each root, declared category/capability, node and
  unique-process counts, repeated occurrences, maximum level and references
  needing review. Archived-root maps remain visible and separately counted.
- **Exact occurrence paths:** inspect the saved root, map revision and digest,
  node, ancestry path, pinned model revision and digest, reference resolution
  and independent model review. Select a process identity to drill into all
  its occurrences, including reuse under different parents.
- **Overlapping map pairs:** count shared and union process identities, report
  exact Jaccard fraction and whether membership is identical. A repeated node
  never inflates set intersection. This is not a duplicate-case or dependency
  analysis. Identical membership does not prove identical hierarchy.
- **Exact map membership comparison:** select two saved map roots, even if
  disjoint. Inspect every union identity as shared, left-only or right-only,
  exact revision/digest sets and counts, and each map's ancestry paths. Filter
  shared identities whose exact pin sets differ. Overlap rows open this view
  against the same portfolio fingerprint; changed evidence refuses the drill-down.
- **Category coverage:** exact declared category groups, map counts, distinct
  identity union, occurrences and map readiness. Blank labels are retained.
- **Capability coverage:** equivalent exact-label coverage by capability.

Filters are evaluated over the full accepted scan, not the loaded page.
An optional exact workflow ID focuses process/occurrence views on that ID,
maps on containing roots, overlaps on pairs both containing it, membership on the selected identity within the pair, and taxonomy
on groups containing those roots. A catalog process without occurrences has
an empty occurrence result. Titles shown for available pinned references
come from those exact versions. A current title is shown only when a current
pin is available; an unmapped catalog identity is not resolved solely to
obtain its name.

## Freshness and privacy

The fingerprint binds organization, catalog identity/archive metadata,
latest immutable maps, exact paths, pin resolutions, independent review
states and all calculated rows. Changed map, archive, reference availability,
model version or review invalidates old pages and exports. The displayed
checked_at is not included in the semantic fingerprint. A new review is
always required after a mismatch. The report is a read snapshot, not live
monitoring. The portal clears results on identity changes, local map edits
and selection changes; held old-identity responses are ignored.

Existing tenant-wide process-read authorization applies; no new model,
case/evidence, agent or service-token permission is created. The same
PostgreSQL repeatable-read or SQLite read transaction used for hierarchy
reads is used for the entire scan. Audit records contain fingerprint,
view, offset and counts only; audit failure withholds the response.
Explicit page export performs fresh authorization, scan, pin comparison
and audit. The JSON file contains only the displayed page and overall
summary, not the complete unpaged dataset. A downloaded copy must be
handled according to the organization's retention rules.

Limits: 100 latest maps, 1,000 catalog identities, 1,000 unique exact model
references, 10,000 occurrences, 50 rows per page and 2 MiB response.
Oversized or corrupt map scans refuse the entire report rather than present
partial totals as complete. Corrupt/unavailable individual model references
are classified using existing reference diagnostics; no source model body
is returned. Process identities absent from catalog but retained in an
existing map remain in the declared population. No changes to storage or
map/history lifecycle are introduced.

## API

Dashboard human-process auth only, no-store:

- POST /v1/human/process-hierarchies/portfolio
- POST /v1/human/process-hierarchies/portfolio/export

Strict JSON up to 4 KiB. Request example:

    {"view":"occurrences","filter":"multiple_pins","focus_workflow_id":null,
     "offset":0,"limit":25}

For later pages and export supply expected_fingerprint from the exact
review; exports require it even at offset zero. Views are processes, maps,
occurrences, overlaps, membership, categories and capabilities. Allowed process filters
are all, unmapped, reused, multiple_pins, needs_review, archived and
unavailable; map filters all, needs_review, archived; occurrence filters
all, needs_review, multiple_pins. Membership requires left_root and right_root
and accepts all, shared, left_only, right_only, different_pins. Other views
accept all only. Map root pair fields are refused outside membership.

The portfolio does not infer a global executable process hierarchy,
dependency graph, responsibility assignment, case membership, cost, savings
or approval propagation. Map reuse may be intentional. Missing coverage
means no occurrence in these declared maps, not a missing actual business
process.

## Verification

New regressions cover real shared process storage, map overlap/reuse,
distinct pin accounting, unmapped/empty catalog, stale export after review
change, archived/deleted references, tenant/scope boundaries, minimized
audits, bounded and corrupt scan refusal, and 120 independent randomized
membership-set oracle populations. The real Chromium hierarchy journey
adds a second map, loads the portfolio, filters reused processes, drills to
three occurrences, exports the exact reviewed page and refuses stale
export after a child model revision. Native/browser CI remains required.
