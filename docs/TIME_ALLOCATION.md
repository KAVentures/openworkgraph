# Time allocation

**Export › Time allocation** shows where recorded foreground time went on this computer, by
day and app, in your own time zone, and downloads it as CSV.

## What is counted

Every recorded focus span (an app in the foreground, with its start and duration) counts
towards its app on the local day it fell on. A span that crosses midnight is split at the day
boundary, and the time zone you choose decides where midnight is. The report shows:

- **per day**: recorded time and the time in each app (the top 50 apps, with the rest summed);
- **per app**: recorded time, its share, and the engaged and idle seconds the collector
  measured;
- **hours of the day**: when the recorded time fell (the dashboard names the busiest hours).

A report covers up to 92 days. Window titles are left out unless `titles=true` is asked for
(JSON only), and even then names in titles stay pseudonymized as they were stored.

## What it is not

Foreground time is not effort, productivity or billable time. Gaps are times nothing was
recorded (paused, away or not captured), not proof that no work happened. The report reads
only this computer's local history and sends nothing anywhere.

## API (dashboard session only)

`GET /v1/time-allocation?start=YYYY-MM-DD&end=YYYY-MM-DD&tz=Europe/Stockholm[&titles=true][&format=csv]`
