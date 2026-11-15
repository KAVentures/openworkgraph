# R23 — Employee onboarding lifecycle domain pack

**Version:** `employee_onboarding@1.0.0`. This extends the existing installed
domain-pack catalog; it does not access an HRIS, identity provider, or device.

## Install and map

In **Activity → Large event logs**, import the owner's event log, open
**Domain packs** and select **Employee onboarding and access readiness**.
Map one onboarding case object type and the required activity roles:
request opened, access provisioning recorded and start of work recorded.

Optionally map manager approval, training completion, access review and
case closure. An unmapped optional role is reported as **not applicable**,
not assumed to have occurred or failed. Each mapping is user-asserted against
the names in the supplied log. Retained case and activity labels are
not verified employee identities.

The saved installation records the source log, template version, editable
policy targets, resolved-definition SHA-256 digest and a bounded history.
Reconfiguring a target changes that digest; downgrades are refused.
Uninstall removes pack-owned metrics while retaining independently authored
metrics.

## Checks and metrics

The bundle adds five typed case metrics:

- request → provisioning, request → first start of work, provisioning →
  start of work (elapsed times);
- number of recorded provisioning events and training completions.

Ten template rules check (when the relevant roles are mapped):

- request → recorded provisioning within a configurable **48 elapsed hours**,
  and request → work start within **14 elapsed days** (defaults; editable);
- request before provisioning, provisioning before start, and one recorded
  provisioning activity per selected onboarding case;
- optional manager approval before provisioning, training and access review
  before work start, start before closure, and owner/approver separation of
  recorded resources.

The reference model is a versioned object-centric path for one onboarding case.
It is **not** an executable employee lifecycle workflow or an access-control
policy. Extra provisioning events may reflect separate applications, retries,
or legitimate split provisioning. Optional checks may not fit contractor,
transfers, emergency access or role-change workflows; map them only after
reviewing the relevant process.

The default elapsed-time targets are examples, not guarantees, working hours,
employment obligations, or legal requirements.

## Evidence limits

- A logged provisioning event **does not prove** that an account exists, MFA
  is active, access is appropriate, or sign-in succeeds.
- An absent event may mean partial extraction, asynchronous source feeds,
  or an unfinished case; it does not prove an identity-provider failure.
- Unknown resource attribution remains unknown for segregation-of-duties
  checks. Results cannot be used to score workers.
- The pack cannot approve employees, provision accounts, notify anyone, or
  grant agent execution. `source_system_validated`,
  `policy_compliance_verified` and `execution_authorized` remain false.

## Validation

`tests/test_event_log_domain_pack_employee_onboarding.py` uses a six-case
retained synthetic CSV with missing, delayed, repeated and out-of-order
events. It checks versioned catalog visibility, install/report, unmapped
optional steps, editable targets and unsupported/malformed inputs.

This is a working domain-pack first slice, not verified integration with
Microsoft Entra, Workday, Google Workspace, Okta or another production source.
Real-provider evidence and task acceptance are still required.
