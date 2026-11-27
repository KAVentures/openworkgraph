# Capture detail: independently annotated paired pilot (#634)

This offline workbench closes an engineering gap in the capture-detail evaluation
workflow. It does not close the real-device or independently labeled customer
acceptance gate. The original synthetic ablation remains intact.

For each reviewed recorded session, use the existing rich trace as the detailed
condition, and remove only the existing high-fidelity event types to create the
standard condition. Compare reconstruction **on the identical structural events**
in both arms. Full-detail scoring is a separate diagnostic, not a comparable
denominator. This is a retrospective information ablation, not a randomized
experiment of recording modes; it cannot measure altered user behavior or
capture overhead. Report sessions without detail rather than selecting only
sessions with successful capture.

## Collect and prepare

Use separately authorized recordings with explicit consent and approved review
access, including interleaved workflows, interruptions, missed modalities,
different apps, and difficult cases. Recruit independent reviewers with relevant
workflow knowledge. Choose and freeze a cohort before seeing predictions.
Prespecify a meaningful change threshold and stopping rule separately; the
workbench intentionally does not auto-approve a product rollout.

Supply one JSONL object per session with case_id, family_id, split (development
or holdout), origin (recorded or synthetic), and presented_evidence (the
chronological, already minimized rich trace rows). Use opaque case and family
IDs; keep all related sessions, including repeated captures by a person or
scenario, in the same family. The family identifier must be chosen by the study
designer, not inferred from event IDs. Rows require literal event IDs, event
types and offset-aware observation timestamps; order ties by ID. The input must
be the reviewed AI-facing evidence, not a raw database dump. Strip identity,
raw pointer coordinates, screenshots, work text and secrets. The nested
prohibited-key check is defense in depth, **not an anonymizer**: free-text values
can still contain identifying data. Inspect actual artifacts; do not commit them.

    python -m evals.reconstruction.capture_detail_pilot prepare \
      --cases /private/capture-sessions.jsonl --split holdout \
      --out-dir /private/capture-pilot --acknowledge-reviewed

A new exclusive directory contains the private plan with cohort and prompt
digests, two prediction batches with independent opaque arm IDs and
randomized per-session ordering, and the reviewer input with an empty annotation
template. On POSIX, the directory/files are created with owner-only permissions;
on Windows use an appropriately protected directory. The tool never calls a
provider, downloads, uploads, changes recording depth or starts Observe.

## Independent review and prediction

Keep private-plan.json separate from prediction producers and keep reviewer
annotations separate from both prediction arms. Give reviewers the full approved
evidence and, if independently available, contemporaneous notes or verified
workflow records; they must not consult each other's answers or the predictions.
The tool cannot attest their independence or the claimed origin of a recording.
Reviewers should assign each event once, explicitly flagging unknown membership.

Both annotations and predictions use a strict exact partition:

    {"case_id":"c1","workflows":[{"event_ids":["e1","e3"]}],
     "unassigned_event_ids":["e2"],"unknown_event_ids":["e4"]}

Unassigned means known interruption/noise; unknown means insufficient evidence.
Empty groups, duplicate, missing and extra event IDs are refused, not silently
corrected. The supplied case ID for prediction must be the opaque arm ID. For
each session evaluate both arms in separate stateless contexts with the same
model/version/settings and record that information outside the pilot. Arm IDs and
file ordering do not disclose the condition; the presence of detail itself is
necessarily visible. Do not send both arms in one conversation.

If reviewers disagree, obtain a separately documented adjudicator's complete
partition for that exact session; disagreement in group naming or order alone
does not require adjudication. No adjudication rows are accepted for already
agreed sessions. Save predictions for all planned arm IDs in one JSONL file.
This workbench does not run models or verify actual provider execution.

    python -m evals.reconstruction.capture_detail_pilot score \
      --cases /private/capture-sessions.jsonl \
      --plan /private/capture-pilot/private-plan.json \
      --reviewer-a /private/review-a.jsonl --reviewer-b /private/review-b.jsonl \
      --adjudications /private/adjudicated.jsonl \
      --predictions /private/predictions.jsonl --output /private/report.json

Omit the adjudications option only if there are no disagreements. Both plan
and complete supplied cohort are verified at score time, including unselected
development sessions; do not alter them between stages. The output path must
not already exist. The plan hashes are content-integrity checks against the
frozen plan, not signatures or protection against a party replacing both plan
and evidence. Keep the original plan under independent custody.

## Interpretation

The report contains existing assignment precision/recall/F1 and contamination
metrics, plus label-invariant exact pair counts, precision/recall/F1, missed
and false links and bounded false-link witnesses. Unknown ground-truth events
are excluded from the comparable denominator; predicted unknowns are not
treated as correctly rejected noise. Report reviewer disagreement and jointly
known coverage. Undefined metrics are null, with support counts, not perfect
scores. Compare per-session values and equal-weight family deltas, with
improved/regressed/tied sessions; higher contamination is worse. These are
descriptive summaries, not confidence intervals or significance tests.
Record any undefined or lost support in both arms. The full-detail diagnostic
also reports the number of pointer events assigned, not their coordinates.

Before claiming benefit, validate cohort independence, label provenance,
native macOS/Windows capture, exclusions and consent, modality completeness,
and long-session CPU/memory/battery cost. Collect a sufficiently large
prespecified held-out set, independently review false links and regressions,
and compare findings to the existing #326 acceptance criteria. Report lack of
gain honestly. No outputs authorize business actions, employee scoring,
policy changes or changes to existing privacy settings.
