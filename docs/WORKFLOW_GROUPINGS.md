# Reviewed workflow grouping corrections

The Overview page connects **Explore observed workflows** to **Reviewed workflow
groups**. Select a candidate or exact variant, then choose **Name or regroup
selected examples**. Review its exact execution references, observed time,
duration and structural steps. Enter a group name and explicitly confirm before
saving. Selection and inspection alone save nothing and contact no AI provider.

Groups classify only the selected inferred executions, with at most 25 references
per group and 100 local groups. A candidate can contain more runs than its bounded
example list; naming those examples does not rename the whole inferred family or
automatically classify future execution IDs. A label is a user declaration, not
independently verified business intent, a common case, human handoff, policy or
automation permission.

## Correct labels and memberships

- **Save reviewed group** creates a group or an immutable new revision of its
  current label and checked memberships. Unchecking a member excludes it from
  that saved classification; it does not remove the original execution.
- **Split checked executions into a new group** moves only those checked
  references into a newly named group.
- **Move checked executions to target group** moves only those references to the
  displayed target and revision. Selecting all source members combines groups.
  Target references are shown before confirmation. Save any label edit before
  moving memberships.
- Splits and moves commit both group revisions in one SQLite transaction. Stale
  source/target revisions fail together; neither side is partially changed. A
  reference cannot belong to two active reviewed groups in this local inventory.
  Empty source groups retain their earlier classifications until explicitly
  deleted.
- **Inspect group revision history** pages immutable prior names and memberships.
  **Delete this reviewed group** requires confirmation and removes its knowledge
  versions while preserving canonical evidence and other groups.

Loading a group draws its observed variants in the existing explorer using the
selected retained source executions. Separate variant lanes remain separate;
classification does not invent transitions or case associations. The diagram's
normal bounds and source-limit warnings still apply. Its AI handoff identifies
the reviewed knowledge ID and expected revision as well as the execution
references; the agent must recheck that revision before using the classification.

## Storage, permissions and source limits

The existing owner-scoped `workflow_knowledge` ledger stores the label and a
typed `execution_grouping` field. No second grouping database, event copy,
collector or model call is introduced. The field carries explicit false flags
for verified business intent, business-case joining and original-execution
modification. Normal knowledge records without this field keep their existing
payload shape. Generic knowledge updates cannot erase or manufacture grouping
metadata; changes use the dedicated reviewed controls and source validation.

Newly added references must resolve through the existing evidence engine within
the supplied dates and a 25,000-event source bound. Missing references fail the
save rather than silently creating a partial group. Renaming or moving already
reviewed references requires no new source verification. Derived references may
stop resolving after source history expires or derivation/window changes, and a
continuing execution can acquire later observations. The saved classification
does not retain or freeze its original event content. Unavailable source history
is shown as unavailable, not reconstructed from knowledge.

All grouping endpoints require a local dashboard session. Ordinary API/MCP
bearers cannot use these mutation routes. Saving explicitly supplied knowledge
does not enable observation, source connectors, AI context, cloud synchronization
or organization sharing. Shared process exports continue to exclude local
execution references; these classifications remain local personal knowledge.

- `GET /v1/workflow-groupings`: bounded local inventory.
- `GET /v1/workflow-groupings/revision?workflow_id=group_...`: latest classification.
- `GET /v1/workflow-groupings/history?workflow_id=...&before_revision=...&limit=20`:
  immutable history, up to 100 revisions per page.
- `POST /v1/workflow-groupings`: exact name, execution IDs, expected revision,
  optional source dates and `user_confirmed: true`.
- `POST /v1/workflow-groupings/move`: source ID/revision, selected references,
  existing target ID/revision or a new group name, and explicit confirmation.
- `POST /v1/workflow-groupings/forget`: exact ID/revision and confirmation.

JSON mutations are bounded to 128 KiB and reject duplicate keys, invalid
identifiers, nonstandard numbers and unknown fields. Responses use no-store
caching. Tests cover source resolution, ordinary-writer restrictions, owner
isolation, immutable history, quotas, duplicate assignment, concurrent conflicts,
transaction rollback and browser-to-SQLite classification without canonical row
changes.
