# Quality-management domain packs (R23)

Four installable, versioned reference workflows use the existing saved-log
domain pack catalog, not a separate analytics engine or a new source connector.

| Pack | One case represents | Recorded reference path |
| --- | --- | --- |
| Corrective and preventive action management | One action record | Open → investigate → plan → approve → implement → effectiveness review → close |
| Calibration administration | One service-cycle administration record | Request → schedule → perform → certificate receipt → review → close |
| Controlled document revision | One approved publication revision | Request → draft → review → approve → publish → acknowledge supersession → close |
| Product recall notification administration | One independently closed notification campaign or batch | Open → scope → draft → approve → dispatch → reconcile → close |

Each includes seven metrics (four elapsed-time metrics and three recorded
activity counts), configurable elapsed-hour deadlines, sequence checks,
duplicate milestone checks, recorded resource separation and an optional
clarification or exception stage. These are 28 distinct metric definitions.

## Operator workflow

1. Open Evidence → saved event logs → domain packs. Import authorized
   selected CSV/XES/OCEL evidence, using pseudonymous case identifiers and
   the actual administrative milestone labels and timestamps.
2. Select the corresponding pack and map each required role to a literal
   retained activity label. Do not map different stages to a generic event
   without establishing the underlying scope.
3. Review the illustrative elapsed-hour parameters before installation.
   Optional roles can be left unmapped; affected definitions are disclosed
   as not applicable. A mapped optional role is not a required reference
   stage. Select only the intended case lifecycle.
4. Install and review the existing metric, compliance and reference-model
   report, including actual case witnesses and unknown resource evidence.
   Inspect missing, late and repeated records before attributing deviations.
5. Revise the mapping or targets to create a revised definition digest.
   Uninstall removes only the pack-owned metrics, not the underlying log
   or unrelated saved metrics.

These models do not verify root cause, effectiveness, calibration accuracy,
equipment fitness, publication validity, notice delivery, regulatory
compliance or actual product safety. In particular the recall pack is
administrative review only; its defaults are not regulatory deadlines or
permission to delay an urgent action. Resource labels are not verified
identities or organizational independence. No notice, publication, approval,
equipment operation or other business action is performed.

Use separate reviewed models for rejected or reopened records, parallel
actions, failed calibration disposition, multiple recipient acknowledgements
or other paths outside the selected reference scope. Missing records can
reflect unfinished work or incomplete evidence, not actual failure.

## Acceptance

```sh
python -m pytest -q tests/test_quality_management_pack_definitions.py tests/test_quality_management_domain_packs.py
```

Definition tests check the independent expected roles, sequence, optional
milestones, metrics and rules. Retained-log tests use synthetic CSV imports
and the real install/report/uninstall path with late, missing, duplicate,
wrong-order, optional-absent, same-resource and unknown-resource cases.
No live source validation or regulated-process certification is claimed.

This addition is stacked on the operational-assurance pack integration
(#1644) to avoid a competing registry change. Integrate the parent first
and validate the combined tree before merging the child.
