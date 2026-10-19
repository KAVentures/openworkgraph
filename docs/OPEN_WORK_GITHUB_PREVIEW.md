# GitHub open pull-request Open Work preview

An **optional second Open Work source** using OWG's existing read-only
GitHub CLI pathway. It complements, but does not replace, Activity, workflow
mapping, Discovery Mode, procedural memory, context handoff or exports.

The local dashboard Privacy → Optional source connections also offers an
explicit GitHub preview. Enter a single `owner/name` repository slug and
confirm each scan. The authenticated local dashboard may call:

```http
GET /v1/open-work/github-preview?repository=owner/name&confirm_scan=true&limit=50
Authorization: OWG-Session <local-dashboard-session>
```

OWG requires **both** an existing authenticated `gh` executable and its
separate, pre-existing opt-in for GitHub outcome status reads. An AI bearer
or unauthenticated caller cannot perform the scan. The CLI uses
`gh pr list --repo owner/name --state open --limit N --json
number,state,isDraft,reviewDecision,updatedAt`. The repository argument is
strictly validated, shell execution is disabled and responses are bounded.
No titles, descriptions, comments, authors, native PR numbers or repository
names are exposed in the preview response.

The returned candidate ID `owg:w:` is process-local HMAC-pseudonymous and
**expires on local service restart**. The preview can identify source-reported
open PRs never previously opened on this computer. An open PR or GitHub
review-required label is a *candidate for attention*, not a verified
contractual/work obligation or authority for an agent to approve, merge or
write to GitHub.

The endpoint is read-only, does not persist provider contents or background
poll, does not change GitHub settings, and does not introduce remote AI
capabilities. A capped list may be incomplete. This is not a full integrated
GitHub case-context connector; case drilldowns remain Stripe-specific at
this stage. See issue #247 for the provider-neutral case graph and later
OAuth/source-inventory plans.

A real authenticated user-side `gh` pilot is needed before claiming this
source has been validated outside synthetic CI.
