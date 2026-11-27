# Synthetic enterprise ingestion scale and recovery acceptance

Parent: #1572, #326, #370 R3/R20. This is a local acceptance tool for the existing retained source-event engine, not another connector, scheduler or production event store.

## Run

From an installed development checkout with Python 3.11+:

    python -m scripts.enterprise_ingestion_acceptance --cases 2500
    python -m scripts.enterprise_ingestion_acceptance --cases 250000 --report 1m-report.json
    python -m scripts.enterprise_ingestion_acceptance --cases 2500000 --crash-timeout 2400 --report 10m-report.json

Four events per case means 10,000, 1,000,000 and 10,000,000 events respectively. Start small before attempting a large run: the workspace holds an original and a roundtrip copy, temporary staging, fixture CSVs and an exact export. Disk and memory usage can be substantial. Reports contain no fixture rows, credentials, personal data or source paths. An existing report is never overwritten.

Optional operator-chosen gates:

    python -m scripts.enterprise_ingestion_acceptance --cases 25000 --max-seconds 300 --max-rss-mib 2048 --min-import-rate 1000 --report gated-report.json

The numerical limits here are examples, not measured baselines or service-level commitments. A correctness failure or budget failure returns exit code 1; invalid arguments or inability to write the report returns 2. Configure process-level timeout externally if required; the child crash stage itself has an explicit timeout.

The manually triggered **synthetic enterprise ingestion acceptance** GitHub Actions workflow provides 10k, 100k, 1m and 10m tiers with optional gates and an uploaded JSON report. Large runs may exceed hosted runner disk, memory or time budgets; this is a failure/limitation to record, not a result to hide. Regular pytest includes a tiny real end-to-end workload and independent verifier failure cases without requiring the large workflow.

## What it checks

| Stage | Acceptance condition |
| --- | --- |
| Synthetic fixture generation | Two independently named sources intentionally reuse literal source IDs; a declared shared case namespace combines four exact steps per case. |
| Initial import | Exact two events per case, no rejected rows. |
| Hard exit and reopen | Child process exits inside the real transaction hook after mutation and before commit. Reopened summary and append history must exactly match pre-crash state. |
| Concurrent retry | Two simultaneous in-process append requests submit the same complete second-source batch; one adds every new event and the other adds none. |
| Historical retry | Replaying the original batch adds no events, with exact duplicate count. |
| Conflict rollback | A new valid record followed by an existing ID with a changed payload must reject the entire batch and leave summary/history unchanged. |
| Mining | Exact case/event counts, durations, complete variant population and directly-follows transition counts. |
| Export | Existing source CSV export, retaining source and case namespaces and literal source IDs. |
| Independent audit | Every exported row must match the synthetic population, including case, activity, UTC time, role, region, ID and namespace; no missing, extra or duplicate rows. |
| Roundtrip | Import the export into a separate retained log, repeat exact mining, then verify idempotent replay of every exported identity. |

The hard-exit stage uses a fresh process, not an ordinary caught exception. Concurrent retries are threads calling the existing local workspace interface; they are not competing Gateway processes. The exact export audit is independent of database counts and needs one byte per expected event. The source namespace and linkage are synthetic declarations, not independently authenticated provider identities.

## Interpret the report

- Each stage reports wall-clock seconds, input rows, rows/second (where meaningful) and sampled parent-process RSS. Some stages include multiple operations, so their rates are workload rates, not raw database throughput.
- RSS samples are every 50 ms; brief peaks can be missed. The hard-exit child is not included in parent RSS. The report records machine OS/architecture, CPU count, physical memory, Python and DuckDB versions.
- The total time gate sums measured stages, excluding setup/cleanup outside them. Disk bytes are a snapshot after roundtrip, not peak temporary usage.
- The report includes exact final population and explicit synthetic-only, no-live-provider, no-distributed-concurrency and no-cancellation flags. A failed stage is retained, and remaining stages are not described as executed.
- For comparable baseline measurements use the same code revision, case count, hardware, available disk, software versions and cold/warm conditions. Preserve the workflow artifact and commit reference; do not infer a production SLA from one run.

## Still required for #1572

This suite does not establish provider API coverage, continuous ingestion correctness, tenant isolation, cancellation under load, distributed leases, credential rotation, distributed crash recovery, backup/restore or performance in real customer environments. Those require separate Gateway/connector tests, production-like managed hardware and real sandbox tenant acceptance. It does not contact a provider or enable observation, AI sharing or business-system writes. Keep the parent roadmap scale and customer-acceptance gates open until measured and independently reviewed.
