# R4: Paired local-to-company analysis handoff

This feature closes part of the workflow between local event-log analysis and the existing governed company Shared analyses portal. It combines the already supported **minimized aggregate snapshot** and the separately permissioned **coded case dataset** into one versioned, bounded handoff file. Neither dataset becomes public merely by preparing a handoff.

## Local preparation

In **Large event logs → Analysis builder**, run a case-level analysis and select **Prepare paired company handoff**. This requires the existing case-sharing eligibility: a selected complete cohort with at most 500 cases and 5,000 events, no analysis-specific case or activity filters, and an aggregate with at most 100 result rows.

OWG computes both artifacts under the same local workspace lock and pins the full source and analysis fingerprints. Review the numeric aggregate cells **and every disclosed coded timeline**. Check both distinct local review acknowledgements before **Download paired company handoff** becomes enabled. This is a local JSON download; it never requests a Gateway token or uploads data.

The strict `openworkgraph.analysis-handoff-bundle.v1` contract includes only:

- `aggregate_snapshot`: bounded numeric cells and per-export group codes. No group values, raw events or case IDs.
- `case_dataset`: coded cases `C1`, `C2` etc., coded activity steps and relative elapsed microseconds, without original labels, attributes, people or absolute timestamps.
- Explicit `false` flags for independently verified source identity, authorized case attachment and authorized organizational publication.

Both documents must have identical full source and analysis fingerprints and the case dataset must contain exactly the aggregate's selected case count. An oversized, tampered, mismatched, cross-source or schema-expanded bundle is rejected.

## Company publication and permission boundaries

An authorized user signs in to the **Shared analyses** company portal and chooses the paired file. The browser validates the nested aggregate and case documents **before sending anything**. The aggregate is previewed and published only after the existing *organization publication* confirmation. Only that aggregate is sent in this POST.

If the signed-in user has the distinct case-write scope and owns the current publication revision, the browser stages the matching case dataset locally in the case section. **Staging does not upload the case data.** The existing independent case-sharing checkbox and **Attach case dataset** button still govern the case POST. The server checks the pinned parent revision and content fingerprint; no separate viewers are granted by attachment. Case-read scope and per-revision grants remain necessary for another person to read the case data.

If the user lacks case attachment permissions or the published revision is inaccessible, the aggregate remains published but case data are neither uploaded nor granted. The portal instructs the user to use the separate authorized case workflow. Disconnecting or switching identity clears staging.

## Guarantees and limits

This is a human-reviewed file handoff, **not automatic synchronization**, credential delegation, proof of successful business outcome or authorization to execute a workflow. Retention, revocation and tenant isolation continue to be enforced by the existing Gateway publication and case-data routes. A saved aggregate remains a snapshot: subsequent source changes do not update the published view.

Coded case topology/timing and numerical aggregates can still be sensitive. The file is *minimized*, not anonymized. Downloading does not imply permission to share. Source identity is locally fingerprinted, not independently corroborated.

Real-browser acceptance checks mismatch refusal, publication-only first POST, separate case confirmation before attachment, authenticated role boundaries and existing case grants/deletion. Synthetic tests cannot substitute for a consenting multi-identity enterprise pilot.
