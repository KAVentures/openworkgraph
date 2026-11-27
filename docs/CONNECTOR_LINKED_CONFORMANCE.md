# Linked-case evidence conformance

Enterprise roadmap #1572. Follow-up to consolidated integration #1589.
This optional owner workflow evaluates declared process expectations against
selected, already published and explicitly linked evidence. It does not
read upstream providers, infer business identity, automatically change a
process, or establish that a real-world compliance obligation was violated.

## Operator workflow

1. Open Gateway **Reviewed cross-source business cases**. Select exact
   unexpired publications, review member cases and preview the linkage.
2. Open **Optional linked-case conformance**. Approve inspection of activity
   labels/codes. The catalog is drawn from the linked groups only, not the
   unrelated unlinked cases. Each selector identifies an exact source job
   and exact activity value from that publication. Identical labels across
   jobs are not treated as identical activities.
3. Select activities from the paginated catalog and create up to 40 rules.
   The rule builder supports required/forbidden activities, count ranges,
   conditional presence, strict precedence, and subsequent response.
4. Review the rule list and explicitly approve evaluation. The report
   contains counts per rule, group outcome rollups, and paginated individual
   linked-case results. Filter to non-satisfied or indeterminate groups for
   review. An exception is a question to investigate, not proof of misconduct.
5. Download the current report page if needed. Separately authorize the
   complete exception worklist CSV to obtain every non-satisfied and
   indeterminate evaluation in the reviewed scope, not just the visible
   page. Retain the report and its rule definitions with the worklist.
   CSV contains only linked-case pseudonyms, safe rule IDs, fixed status and
   reason codes, counts and the exact conformance digest. It does not contain
   published activity labels or native source-case identities.

Example: in a reviewed purchasing flow, define an order activity in one
source and an approval activity in another. Conditional presence asks if
the approval is observed when the order is observed. Precedence checks
reported ordering; use it only if the timestamp semantics are appropriate.
A missing approval observation may reflect a filtered source, an incomplete
window or a late arrival, not an actual failure to obtain approval.

## Rule semantics

| Rule | Evidence question |
|---|---|
| required | Is the exact selected activity observed at least once? |
| forbidden | Is the exact selected activity absent from this sample? |
| count_range | Is the occurrence count in the inclusive reviewed range? |
| cooccurs | If the trigger is observed, is a response observed? |
| precedes | Is every reported trigger point strictly before every reported response point? |
| response | Does every trigger have at least one strictly later reported response? |

Selectors must appear in at least one of the selected linked groups. A
misspelled activity or a completely unobserved selector cannot silently
pass a forbidden rule. Rules are evaluated across all linked groups; there
is no hidden filter by source, business type or period beyond the original
review. Conditional rules with no observed trigger are not applicable.
Conditional rules with a trigger but no response are not satisfied in the
sample. A single response can satisfy multiple triggers; the response rule
does not pair events, measure waits or enforce a one-to-one relationship.

For strict precedence and response, SAP canonical midnight labels are
business dates rather than verified action times. When both sides are
observed and either source is SAP, the result is indeterminate. Otherwise
ordering compares normalized reported timestamp points. A strict reversal
does not satisfy the rule, a boundary tie is indeterminate, and a strictly
ordered reported sample satisfies it. None verifies physical occurrence
order or clock accuracy. The absence of a SAP flag does not certify point
precision in another provider.

Group rollup prioritizes not_satisfied, then indeterminate, then satisfied,
then not_applicable. Every rule result remains visible. This deliberately
does not hide an indeterminate rule because another rule passed.
All counts include only the selected linked groups, not unlinked source
cases, even when the ordinary export includes unlinked cases.

## API and integrity

Owner-only POST routes under /v1/admin/connector-events/{organization_id}:

- case-conformance-catalog: exact existing linkage Plan, expected_review_sha256,
  confirm_rule_catalog, offset, limit, and expected_catalog_sha256 for later pages.
- case-conformance-review: exact existing Plan, expected_review_sha256, rules,
  confirm_conformance_review, filter_status, offset, limit, and
  expected_conformance_sha256 for later pages.
- case-conformance-worklist: same review request, exact
  expected_conformance_sha256 and confirm_worklist_export. Requires offset 0
  and all outcomes; the response is CSV with X-OWG-Conformance-SHA256.

All calls reload and validate exact live grants and recompute the existing
linkage digest under the organization transaction. The canonical conformance
digest covers the exact linkage review, sorted rule definitions and all
group outcomes, independently of page and filter. Changed rule definitions,
source grants or linkage invalidate the pin. Catalog and analysis pages
recheck expiry before return. Separate owner consent is required for the
catalog, rules and full worklist. Audit records contain counts and digests,
not source activities or event rows. No separate persistent data store,
provider read, scheduled rule execution, or automated remediation is added.

Bounds: 10 publications, 20,000 total events, 500 linked groups, 2,000
members, 40 rules and at most 20,000 individual evaluations. Catalog pages
hold at most 100 selectors; case-result pages at most 50 groups. The
complete worklist has at most 20,000 exception rows. Rule IDs are restricted
to safe alphanumeric-leading identifiers, and the CSV never interpolates
free-form activity labels into spreadsheet cells.

## Validation

The exact pure rule engine (blob c37dc08027d9c3033e3a229ed612218e38bd96b5)
was checked locally against 5,000 randomized independent samples for six
rule types, including timestamp ties and SAP uncertainty. A synthetic
20,000-event index and 20,000 repeated rule evaluations took 0.0167 s in the
authoring environment. This is algorithm-only, not Gateway throughput.

Regression fixtures cover order semantics, reference oracles, exact
publication scope and catalog pagination, rule shape and unknown selectors,
digest-stable filtering, complete worklist export, consent, revocation,
and owner authorization. The Chromium fixture drives the rule builder,
report download, complete worklist and linkage-change invalidation.
The focused draft CI job runs the backend against the existing SQLite and
PostgreSQL fixtures and the browser lifecycle.

Direct git checkout is blocked by DNS in the authoring environment, so
integrated tests are pending GitHub Actions. Live customer acceptance,
rule governance across organizations, and parent #1589 full CI remain
outstanding. Reconcile overlapping route, case review, browser and workflow
follow-ups before landing.
