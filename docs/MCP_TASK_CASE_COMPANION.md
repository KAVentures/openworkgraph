# Known-record case companion in local task context

The existing local `get_task_context` accepts an optional
`case_record_ref`, an exact opaque `owg:r:` record reference already
returned by permitted OWG evidence. The existing experimental Open Work
tool setting must be enabled. Normal AI access and history boundaries
continue to apply. If the argument is omitted, no additional read occurs.

This is useful for resuming a task related to a particular observed
ticket, PR or invoice. Combine the existing task context, optional
explicit reviewed knowledge references and one optional known record.
The record observations are retrieved from the existing case-context
endpoint with `include_source_status=false`. The client does not ask a
provider for current status, scan an Open Work inventory, or automatically
associate a workflow and record. The selected case is a separate
bounded 64 KiB companion; its original AI-visible API representation
has its own fingerprint and the existing observed-data protection is
applied before returning it. The original task API fingerprint stays
unchanged and the existing final protected fingerprint includes the
companion. Separate reads are not atomic.

A recognized, observed record type with complete identity within the
retained window can supply a candidate operation name for the existing
optional `verify_case_operation` tool. This is navigation, not a proof of
provider availability, granted permission or outcome. Actual verification
must be requested separately through the existing tool with its separate
source-read permission and safeguards. No outcome is verified by the
companion; missing or conflicting record identity and missing evidence
are unknown. No workflow-to-case linkage, customer success, causation,
policy or execution authority is inferred.

Run `python -m pytest -q tests/test_task_case_companion.py tests/test_task_case_companion_registration.py`.
The pure helper tests use synthetic case responses. The registration
regression uses the actual task-context entry points with substituted
authorization and transport; full local MCP transport, history/detail
middleware and enterprise acceptance still require integration validation.
