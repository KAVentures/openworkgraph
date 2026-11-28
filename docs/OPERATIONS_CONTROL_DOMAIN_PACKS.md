# Operational-control process mining suite (R23)

Three new versioned domain packs use the existing OWG event-log catalog,
mapping editor, metric library, compliance engine and reference-model report.
No new service, source access, data capture mode or execution permission is added.

| Pack ID | Case scope | Required mapped activities | Illustrative targets |
| --- | --- | --- | --- |
| `manufacturing_quality` | Nonconformance record | Report, investigation, disposition, closure | Investigation 48 elapsed hours; closure 30 elapsed days |
| `supplier_qualification` | Qualification request | Request, recorded checks, recorded decision | Checks 10 elapsed days; decision 20 elapsed days |
| `financial_close` | Financial close task | Assignment, preparation, review, closure | Preparation 48 elapsed hours; closure 7 elapsed days |

All three are version 1.0.0. These are editable **examples**, not certified
industry policy, legal obligations, work calendars or an approved procedure.

## Analyst journey

1. Import an explicitly authorized CSV, XES or OCEL log. Use a stable case
   identifier unique within the chosen entity, period and provider scope.
2. Open the saved log's Domain packs view, select a pack and map its semantic
   roles to actual log names. Optional roles may be omitted explicitly.
3. Review targets and assumptions, then install. The existing installation
   mechanism pins the mapping, parameters and definition digest and owns the
   installed metrics. Use a report to inspect timing, missing observations,
   repeated steps, precedence and the example reference lifecycle.
4. Inspect the underlying case evidence before drawing conclusions. A partial
   export may omit legitimate progress; an observed terminal event is not
   independent business-outcome verification.
5. Reconfigure using the existing revision/history workflow, or uninstall.
   Uninstall removes only metrics owned by this pack, not analyst definitions.

## Operational controls

- **Manufacturing:** elapsed investigation/closure time, corrective-action
  verification interval, action/reopen/disposition counts, order of
  investigation, disposition and closure. The model does not judge defect
  severity, product release, CAPA effectiveness or regulatory compliance.
- **Supplier qualification:** check and decision turnaround, document request
  turnaround and counts, reopening and recorded decision counts, order of
  request/check/decision/activation. A recorded decision is outcome-neutral;
  a subsequent activation does not establish entitlement or actual activation.
- **Financial close:** assignment-to-preparation, preparation-to-review and
  assignment-to-close time, adjustment/reopening/review counts, recorded
  review before closure and segregation-of-duties checks. Missing recorded
  resources cannot establish independent review. The pack needs no balances,
  journal contents or personal identifiers and cannot post or approve entries.

The reference models show one example path for each case type. Reopening,
alternative paths, multiple decisions and repeated closure may be legitimate
and are not personnel performance measures. Optional unmapped activities are
reported as not applicable. The current definitions do not model cross-object
relationships, business-day calendars, source-specific schemas or accounting
and manufacturing system semantics.

## Validation

`pytest tests/test_event_log_domain_pack_operations.py` exercises all three
packs against synthetic saved CSV logs and the existing authenticated local
HTTP routes. It covers discovery, installation, exact mapped labels, timing and
ordering violations, missing/repeated terminal events, optional-role omissions,
parameter overrides, invalid configuration rollback, digest changes, and
uninstall while preserving an unrelated metric. Bearer API access is refused
for pack installation, report and removal. Run the full repository CI matrix
before merging; synthetic examples do not establish real ERP/CRM/quality-system
acceptance. Source-specific mappings and consenting customer pilot validation
remain outstanding.

Related: [#370 R23](https://github.com/KAVentures/openworkgraph/issues/370),
[#326](https://github.com/KAVentures/openworkgraph/issues/326).
