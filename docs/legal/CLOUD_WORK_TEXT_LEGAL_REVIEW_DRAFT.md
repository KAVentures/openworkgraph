# DRAFT ONLY — OpenWorkGraph cloud work-content legal review

**Not a published Terms of Service, Privacy Policy or legal advice.**
Before cloud content synchronization becomes publicly available, qualified EU/
Swedish privacy counsel should review these texts alongside the actual data
flows, contracts, security controls and international transfers. The feature
must stay behind its disabled-by-default operator flag until that review.

## Proposed user disclosure (in-product)

“When enabled, OpenWorkGraph will send **newly captured**, redacted portions
of permitted browser pages and text drafts from this computer to your personal
OpenWorkGraph account. Content can include personal information about other
people and confidential information despite filtering. We store bounded
excerpts, webpage host/title, capture time and page/draft type for up to seven
days, to let your authorized connected AI apps search past work even when
your computer is off. Do not enable this unless you are authorized to share
the content. You may switch this feature off; cloud deletion will be requested
the next time your desktop connects. Previously sent content may remain
available until that happens or it expires, and copies sent into AI chats
cannot be recalled.”

Provide affirmative, unticked acceptance of this specific disclosure.
Record an immutable version ID, server-generated timestamp, and source device.
Do not bundle this permission into general terms or automatically broaden it
for existing installations. Review whether GDPR consent is the appropriate
legal basis for the relevant controller; employment contexts commonly require
a different legal basis than employee consent.

## Privacy Policy sections to complete and publish

- Controller / processor roles, identities, contact details, and DPO contact.
- Categories and sources of data (visible browser page text, drafts, hostname,
  redacted title, timestamps, associated resource references), including
  personal data of third parties who did not install OWG.
- Purposes and legal bases of local recording and **separate cloud sharing**.
- Data recipients/subprocessors, cloud hosting region, AI model providers,
  subprocessors, international transfers and safeguards.
- Storage security: HTTPS, database access restrictions, managed at-rest
  encryption (not end-to-end encryption), logging and incident handling.
- Retention: maximum seven-day active cloud access, cleanup cadence, backup
  schedules and their separate expiry; how local historical data differs.
- How to stop future collection, revoke remote access, view what is stored,
  request deletion, exercise access/rectification/objection and lodge an IMY
  complaint; clarify offline-device deletion latency.
- Special-category data safeguards and categories blocked by default.
- Children's data, employee monitoring / collective bargaining where applicable,
  and cases requiring DPIA before deployment.
- Whether OWG/connected AI vendors use content for training (must be checked,
  not assumed), product analytics and profiling, with separate opt-ins if any.

## Terms of Service clauses to review

- User authority and right to record/share content; prohibited unauthorized
  employee/company/third-party data uploads; reasonable use restrictions.
- Personal vs organization modes; what a company admin can allow or block.
- Limits of browser capture coverage/redaction accuracy; OWG is not a
  replacement for access-control or records-management systems.
- Security and user responsibilities; versioning of future amendments.
- How users cancel, export or delete data; limits of deletion of existing
  downstream AI chat transcripts.
- Clear product claims and applicable governing law, liability and remedies
  reviewed by counsel; avoid attempting to disclaim mandatory GDPR duties.

## B2B processing / DPA readiness

- Define enterprise controller–processor relationship and written Article 28
  instructions; legal basis remains with the controller where appropriate.
- Support admin-off-by-default, granular org policy, audit and data-subject
  request procedures. Enterprise work-text sharing is **not implemented** here.
- Complete subprocessor listing, cross-border transfer assessment,
  breach-notification process, data return/deletion, audit assurances,
  technical/organizational measures and retention/backup schedules.
- Complete a DPIA screening; a full DPIA may be mandatory for systematic
  employee monitoring or high-risk processing.

This draft is a checklist, NOT sufficient permission to enable the feature.
