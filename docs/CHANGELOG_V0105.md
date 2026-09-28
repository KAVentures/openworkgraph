# OpenWorkGraph v0.107 stack slice: do briefs help?

This unreleased slice measures whether opted-in session briefs help on the person's own work, without pretending historical ordinary briefs were randomized.

## Measure whether briefs help (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → "Measure whether briefs help".
- **The holdout:** a random 1 in 5 brief-eligible sessions are held back. Assignment uses `secrets.randbelow`, is stored explicitly in SQLite, and is sticky for `(trial, session)` so `/clear` or compaction stay in the same arm.
- **Trial isolation:** enabling evaluation starts an opaque trial ID. Turning evaluation off and on again starts a new trial. Ordinary briefs delivered before a trial are not treatment observations and never enter that trial's report.
- **What is compared**, per session and only on turns after assignment:
  - turns whose tests ended failing;
  - turns after which the person changed files the immediately preceding agent turn had edited;
  - pull-request merge rate (with outcome tracking);
  - tokens per turn;
  - turns per session.
- **PR right-censoring:** merge rate is `merged / (merged + closed_unmerged)`. Open/pending PRs are unresolved and excluded rather than silently counted as failures to merge.
- **How:**
  - The session is the unit.
  - Each measure shows arm means and the difference (briefed minus control) with a bootstrap 95% interval (2,000 resamples, fixed seed, reproducible).
  - A directional verdict requires the interval to exclude 0 and the metric to have a declared better direction.
- **Honest defaults:**
  - Nothing is claimed until each arm has at least 10 sessions with the measure.
  - "Turns per session" never claims a direction.
  - The method is labeled exploratory: several measures, no multiple-comparison correction.
- **Sources:** raw history and run memory, matched by keyed session refs.

## API
- `GET /v1/agent-brief/evaluation`, `PUT /v1/agent-brief/evaluation` (`enabled`). `GET /v1/agent-brief` includes the evaluation state and active trial metadata.

This slice does not publish a separate v0.105 release; the completed stack publishes as v0.107.0.
