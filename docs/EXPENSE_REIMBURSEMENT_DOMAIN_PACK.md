# Expense reimbursement domain pack

The local event-log workspace offers `expense_reimbursement` version `1.0.0`
under **Domain packs**. It extends the existing catalog; no new connector,
recording, cloud sharing or payment capability is enabled.

Import an authorized CSV, XES or OCEL log with separate case IDs for each claim
and account. Select payable claims before import: rejected, withdrawn,
corporate-card and advance cases require their own reviewed model. The pack
cannot infer whether a reimbursement is owed.

Map the claim object type and submission, approval and recorded payment
activities. Receipt review, return for correction and closure are optional.
Review the assumptions, set the approval/payment targets and install. Defaults
are seven elapsed days from submission to approval and fourteen from approval
to recorded payment; neither is a statutory or adopted company deadline.

The report supplies five metrics: submission-to-approval, approval-to-payment,
submission-to-payment, payment record count and return record count. Eight
checks flag missing/late approvals and payments, approval/submission order,
receipt review before approval, exactly one recorded payment, different
submitter/approver resources, and payment before closure. Unmapped optional
controls appear as not applicable; missing resources remain unknown. Open and
partial histories can produce missing-event flags without proving an obligation.

The reference path permits one return before approval. Repeated correction
loops, split payments, reversals and retries need review; deviation does not
establish misconduct or duplicate disbursement. Time metrics use the existing
first-occurrence semantics. Tied timestamps follow the existing event-ID order,
which does not establish business causality. Durations measure elapsed time,
not employee effort or savings.

Installations pin the pack version, role mapping, parameters and resolved
SHA-256 digest. Reconfiguration retains bounded history. Uninstall removes
only pack-owned metrics; deleting the log removes its installation. Existing
owner-session authorization, local retention confirmation and no-store reports
apply. AI bearer tokens cannot install, report or uninstall.

A payment event is supplied evidence, not verified bank settlement, correct
amount/currency, receipt validity, tax treatment or financial compliance.
The pack makes no provider requests and authorizes no approvals or payments.

Validation uses synthetic normal, late, missing, repeated and out-of-order cases,
real authenticated ASGI import/report/delete requests, and the actual Chromium
mapping/install/report/reconfigure/uninstall journey with axe and mobile checks.
Real customer sample validation and independent settlement reconciliation remain
open. Native CI and review are required before merge.
