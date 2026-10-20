# Explicit Jira multi-case source process comparison

This extends the single-Jira-issue source audit preview without introducing
tenant-wide discovery, background scans or another OAuth connection.

## Usage

1. In Overview → Record-linked observations, expand at least two observed Jira
   records. The browser sensor must already have captured a valid opaque
   `owg:r:...` reference for each.
2. Select the checkboxes next to the exact records you want to compare.
3. Click **Compare selected Jira source audits**, then explicitly confirm the
   resulting source reads.
4. OWG uses its existing separately enabled Jira OAuth read-only source adapter
   once per selected record and reconstructs distinct source process paths.
   It displays aggregate observed variants and source-audit coverage.

The authenticated `POST /v1/process-mining/jira-selected-cases` requires an
active local dashboard session, the explicit confirmation flag, and 2–8
distinct opaque observed resource references. It rejects other payload keys,
unbounded bodies (>2 KiB), duplicates, unrecognized locators and missing
owner authentication *before* any provider call.

## Security and interpretation

- No new Jira credentials or permissions. The existing source reader checks
  tenant identity, record resolver, OAuth scopes and explicit user opt-in.
- No Jira issue inventory queries. Source reads are bounded to **exact records
  selected by the user**; a separate click is required for each comparison.
- Provider issue IDs, source audit event refs, actor IDs, custom status labels,
  customer fields and source values are never returned in the aggregated
  process output. Results are not saved in OWG.
- A record with missing/unavailable/partial audit is explicitly reported in
  the aggregate coverage. It is not discarded as proof that no work occurred.
- Each Jira issue remains a distinct case even if paths are identical. No
  multi-issue business-case equivalence, cross-employee handoff acceptance,
  policy compliance or customer success is inferred.
- Observed time gaps are not independently verified waiting or active labor.
- No agent execution rights, new background collection, external AI upload,
  write API or customer communication.

## Remaining functionality

Production-grade company process mining still requires explicit source event
logs from additional providers, verified cross-system shared case identities,
source coverage checks, user-reviewed process ownership and approved decision
rules. The separate agent starter is an implementation scaffold, not proof
of a functioning live automation.
