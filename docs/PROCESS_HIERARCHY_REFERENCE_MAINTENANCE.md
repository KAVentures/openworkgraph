# Repository map reference maintenance (R12)

Saved repository maps reference exact process revisions, even after newer
process designs are published. A root editor can refresh several exact
occurrences without manually opening every child design.

## Operator workflow

1. Open the root design and load its saved repository map.
2. Expand **Batch reference maintenance and impact preview**. Choose explicit
   occurrences, select outdated occurrences, or select all. Repeated occurrences
   of one process are separate choices.
3. Preview. Inspect original pins, latest verified pins, proposed pins,
   independent design-review state, navigation-context ancestry and descendants,
   mixed revision reuse, and any blockers.
4. Optionally download the exact rechecked report. To prepare changes, choose
   **Recheck and stage selected pins in draft**. Staging requires the report
   basis to be unchanged and a current root editor. No map is saved by staging.
5. Review the normal map draft and explicitly confirm sharing and save through
   the existing save path. Its access revision and map revision checks still
   apply. A child design may acquire a newer revision after the preview; the
   saved draft still refers to the exact reviewed pin.

A stale root pin, unavailable or archived retained reference, historical map,
revision capacity, or missing current root edit permission blocks staging.
An explicitly selected invalid original reference may be repaired when the
latest replacement independently passes integrity and availability checks.
Unselected outdated but valid child references may remain. Different pins
across reuse occurrences are shown as warnings, not silently synchronized.

The ancestry impact is structural navigation context only. It does not
assert an execution dependency, business impact, automatic approval or benefit.

## API

POST /v1/human/processes/{workflow_id}/hierarchy/reference-refresh

Request:
- map_revision: exact retained map revision (1–20)
- expected_map_sha256: exact saved map content digest
- node_ids: 1–100 distinct exact occurrence IDs
- expected_report_sha256: optional prior report digest for a recheck

The existing human process scope and organization read transaction protect the
review. It uses existing exact process-reference integrity and current-review
resolution; no hidden model fields are disclosed for unresolved references.
Repeated exact references are resolved once per snapshot. The returned proposed
hierarchy changes only selected pins; tree validation and deterministic report
digest apply. A changed basis fails a recheck with 409. Counts-only audit
records pins, selected/change/blocker counts and report digest, not titles or
model bodies. The audit is written after the read snapshot, matching the
existing hierarchy comparison contract.

Preview and recheck are read-only with respect to the map and process designs.
No execution, policy approval, model review propagation, or benefit roll-up.
The report is not a permission grant or a substitute for the save transaction.

## Verification

Run python -m pytest -q tests/test_process_hierarchy_reference_refresh.py.
The new tests cover reused exact occurrences, mixed versions, structural context,
root updates, archived/deleted references, current independent review changes,
stale pins, revoked editor roles, foreign organizations, explicit save, historical
read-only maps and bounded input. The gateway database CI job runs this suite on
SQLite and PostgreSQL. The existing browser test covers preview, fresh JSON
download, staging without persistence and the subsequent separately confirmed save.
