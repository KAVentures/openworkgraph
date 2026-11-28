# Local AI workforce monitoring overview

Open the Process catalog and select **Review AI workforce monitoring**.
The dashboard loads the session-only, no-store endpoint
GET /v1/process-catalog/procurement-workforce/overview. It reads a consistent
SQLite transaction of retained R26 monitoring journals on this installation.
It does not scan other installations, organizations or providers.

The overview prioritizes retained rollback incidents/referrals, overdue
windows and other review needs. It presents current and historical saved
revisions, each journal head, latest declared cutoff, observation counts,
credential-attributed counts, and separate incumbent/challenger cost and
outcome evidence. A link opens the exact saved revision. A deadline warning
uses the current local server clock; it does not persist a checkpoint or
perform any rollback. Use the existing explicit journal refresh to do that.

## Aggregation rules

- The overview includes all readable local journals in the queue, but excludes
  historical revisions, missing current process references, unavailable
  cohort identity, and future-dated snapshots from financial/case totals.
- Exact repeated case tokens (including scheduled cases not yet observed)
  or observation source references across any retained journals exclude
  every affected journal, including collisions involving historical revisions.
  No arbitrary winner is selected. Shared initial baseline evidence is not
  counted in the overview and is not considered a collision.
- Different identifiers might still represent the same business case; lack of
  a detected overlap is not proof of disjoint business records.
- Currency totals are separate. Each included journal contributes started
  cohort counts and declared cost bounds separately by arm. Unknown upper
  cost bounds remain unbounded. Future windows contribute scheduled counts,
  not started outcomes. Mean cost uses the entire enrolled started population.
- Arm totals reflect disjoint observation populations and different workloads;
  do not subtract them and call the difference realized savings. No currency
  conversion, causal effect, annual ROI or automatic allocation is calculated.
- Corrupt journals are shown by scope as unreadable and totals explicitly
  marked incomplete. Their cohort identity cannot be checked.

The dashboard can download the minimized overview JSON. It contains local
workflow titles and identifiers, counts, incident reasons and report pins,
not raw business inputs, observation tokens or producer secrets. Treat the
download as local business evidence. No automatic transfer or mutation is
performed. Authentication is that of the local dashboard session, not
an organization role system, and submitted outcomes remain unverified.

For constrained comparison of multiple explicitly selected conditional operating portfolios, see [workforce allocation](AGENT_PROCUREMENT_WORKFORCE_ALLOCATION.md). This is a separate scenario-planning operation, not a financial inference from monitoring totals.

For explicitly shared offline monitoring summaries across installations, use [workforce exchange](AGENT_PROCUREMENT_WORKFORCE_EXCHANGE.md). The common correlation key enables exact overlap detection without exporting raw case references or workflow titles.
