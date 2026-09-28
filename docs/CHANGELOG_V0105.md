# OpenWorkGraph (unreleased, planned v0.105): do briefs help?

Builds on v0.104. Session briefs are only worth their context if they improve outcomes. OpenWorkGraph can now measure that on your own work, with a randomized comparison.

## Measure whether briefs help (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → "Measure whether briefs help".
- **The holdout:** a random 1 in 5 sessions that would have received a brief are held back. The choice is logged as `control` and is sticky for the session, so `/clear` or compaction keep it. Held-back sessions are not counted as briefs sent.
- **What is compared** ("Results"), per session and only on turns after the brief was, or would have been, delivered:
  - turns whose tests ended failing;
  - turns after which the person changed files the agent had just edited;
  - pull requests merged (with outcome tracking);
  - tokens per turn;
  - turns per session.
- **How:**
  - The session is the unit.
  - Each measure shows both arm means and the difference (briefed minus control) with a bootstrap 95% interval (2,000 resamples, fixed seed, reproducible).
  - The verdict is "briefs help", "briefs hurt" or "no clear difference".
- **Honest defaults:**
  - Nothing is claimed until each arm has at least 10 sessions with the measure ("not enough data yet").
  - "Turns per session" never claims a direction.
  - The method says it is exploratory: several measures, no multiple-comparison correction.
- **Sources:** raw history and run memory (matched by keyed session hashes), so it works with "Don't keep after session".

## API
- `GET /v1/agent-brief/evaluation`, `PUT /v1/agent-brief/evaluation` (`enabled`). `GET /v1/agent-brief` includes the evaluation state.
