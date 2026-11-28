# R23 administrative industry suite

Nine versioned profiles extend the existing saved-log domain pack catalog.
They are opt-in reference models, not operational authorization or certified
industry compliance templates. No extra collection, connector or execution is enabled.

## Single-case CSV or compatible object log

| Pack ID | Observed process | Four configurable clocks (elapsed hours) |
| --- | --- | --- |
| healthcare_referral_administration | Accepted referral administrative scheduling | receipt–check, receipt–route, route–schedule, schedule–notice |
| public_permit_administration | Decided permit application | filing–screen, screen–assessment, assessment–decision, decision–notice |
| construction_rfi_administration | Issued information request | submit–assign, assign–draft, draft–issue, issue–close |
| customs_documentation_administration | Declaration administration | open–documents, documents–file, file–disposition, disposition–archive |
| education_admissions_administration | Decided application administration | submit–check, check–assessment, assessment–decision, decision–communication |
| research_grant_administration | Selected awarded grant cycle | submit–review, award–setup, setup–report, report–close |

Each includes seven case metrics, reference-model conformance, explicit
recorded precedence and milestone-cardinality checks. Five include a
recorded-resource separation-of-duties rule; missing resource evidence is
unknown, not a pass. Optional unmapped roles are explicitly disclosed.

## Multi-object OCEL profiles

| Pack ID | Parent | Child | Default check / closure hours |
| --- | --- | --- | --- |
| audit_engagement_evidence_items | engagement | evidence item | 168 / 720 |
| utility_meter_reading_batch | reading batch | reading record | 24 / 72 |
| document_digitization_batch | digitization batch | document item | 48 / 168 |

All three preserve distinct parent and child populations, two adjustable clocks,
five metrics, eleven case checks, ten relationship checks and independent
reference-model lifecycles. Opening and closure events must link the actual
parent and children. Child check and exception events link the child.
A child linked to different parents at opening and closure is a separate
relationship deviation even when each event has valid local cardinality.

## Operator workflow

1. Import an authorized CSV or OCEL export with stable administrative case or
   object identifiers and literal event labels. For separation-of-duties checks,
   use a recorded resource column when authorized.
2. In the saved event-log workspace, choose the appropriate domain pack.
   Map its declared roles to exact retained labels and review its assumptions.
3. Review the elapsed-hour parameters; defaults are illustrative, not
   legally or contractually established deadlines. Install the selected version.
4. Review the retained metrics, compliance diagnostics and reference-model
   deviations. Optional role omissions and incomplete evidence are not passes.
5. Where available, use the existing deadline sensitivity, portfolio comparison,
   overlap and priority workbenches against the pinned installed definitions.
   Reconfiguration changes the definition digest. Uninstall removes only
   the pack-owned metrics, not retained source events.

## Limits

These profiles measure recorded milestones, not actual clinical care, legal
validity, eligibility, physical completion, billing, audit assurance or
independence of actors. Do not use administrative case identifiers as personal
identifiers. The models cover explicitly selected ordinary paths, not all
exceptions, appeals, partial completions, revised submissions or policy clocks.
Missing events may indicate a partial export or unfinished work. Parent and
child populations must not be added as unique business cases. No clinical,
legal, educational, audit or regulatory decisions are automated.

## Acceptance

The regression files import independent synthetic CSV and OCEL fixtures into the
existing saved-log store and exercise catalog registration, exact literal label
mapping, actual reports, late/missing responses, precedence, duplicate milestones,
unknown resources, optional omissions, parameter revision, failed installation,
uninstallation and cross-parent membership errors.

Run:
```sh
python -m pytest -q tests/test_regulated_service_domain_packs.py tests/test_regulated_object_domain_packs.py
```

Source-system acceptance and real operational validation remain separate.
