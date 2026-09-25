# Trusted policy source synchronization

OpenWorkGraph can detect changes in explicitly approved local policy-manifest sources and prepare immutable policy proposals for human review.

This layer does **not** activate policy. It does not fetch remote content and it does not add REST or MCP policy-write capability.

## Trust model

The source configuration is local and explicit. Supported source types are:

- `local_file` — a local machine-readable declared-policy manifest.
- `git_file` — a machine-readable declared-policy manifest committed in an already checked-out local Git repository.

Natural-language SOP parsing is intentionally out of scope. Source files must already satisfy the declared-policy manifest schema.

For `git_file`, OpenWorkGraph reads `HEAD:<relative_path>` from the local repository object database. It does not read an uncommitted working-tree version of the policy file. If the configured path is dirty, that condition is reported in provenance while the committed content remains the proposal source.

OpenWorkGraph never runs `git fetch` or `git pull` in this layer.

## Configuration

Default configuration path:

```text
<data-dir>/policy_sources.json
```

Override it with:

```text
WORKFLOW_OBSERVER_POLICY_SOURCES_FILE=/path/to/policy_sources.json
```

Example:

```json
{
  "schema_version": "1.0",
  "sources": [
    {
      "source_id": "local-sop",
      "type": "local_file",
      "path": "~/company-policy/openworkgraph-policy.json"
    },
    {
      "source_id": "repo-sop",
      "type": "git_file",
      "repo_root": "~/company-policy-repo",
      "relative_path": "policies/openworkgraph.json"
    }
  ]
}
```

The repository root must be the actual Git toplevel. Git policy paths are deliberately restrictive: relative paths only, no traversal, and conservative path-component characters.

## Scan sources

Run:

```bash
python -m server.policy_admin sync-sources
```

Every scan re-reads and re-hashes every configured source. The state file is not trusted to decide whether validation or proposal creation should occur.

Possible source results:

- `proposal_ready` — the source represents a real policy change and an immutable proposal exists.
- `up_to_date` — the source is semantically equivalent to active policy.
- `error` — that source could not be read or validated; other configured sources still continue.

Repeated scans are idempotent. The proposal identity from the policy-authoring layer pins the current active-manifest digest plus candidate-manifest digest, so an unchanged candidate against an unchanged active manifest resolves to the same proposal.

## Review and activation remain separate

Source sync stops after proposal creation.

Review:

```bash
python -m server.policy_admin show proposal-...
```

Activate only from a real local interactive terminal:

```bash
python -m server.policy_admin apply proposal-...
```

There is no `--yes`, force, REST, MCP, or source-sync activation shortcut.

## Status and provenance

Run:

```bash
python -m server.policy_admin source-status
```

The sync-state file records privacy-minimized operational metadata such as:

- source content SHA-256;
- candidate manifest SHA-256;
- proposal ID;
- source type;
- hashed source location;
- Git commit SHA where applicable;
- whether the configured Git working-tree file differs from committed `HEAD`.

Raw source paths are not copied into the sync result/state. Raw declared-policy `source_ref` values remain subject to the existing policy privacy boundary and are not surfaced by source-sync status.

The state file is deliberately **non-authoritative**. If it is deleted, corrupted, or forged, the next scan still reads, hashes, validates, and proposes from the approved source itself.

Default state path:

```text
<data-dir>/policy_source_state.json
```

Override it with:

```text
WORKFLOW_OBSERVER_POLICY_SOURCE_STATE=/path/to/policy_source_state.json
```

## Scheduling

`sync-sources` is non-interactive and safe to invoke repeatedly from an external scheduler if desired. OpenWorkGraph does not install a scheduler or background watcher in this release, so existing runtime behavior is unchanged unless an operator explicitly runs or schedules the command.

Even when scheduled, source sync can only create proposals. It cannot activate policy.

## Failure isolation

A broken configured source is reported as `error` without blocking other approved sources from producing proposals.

Invalid configuration itself fails closed, because a malformed source allow-list should not be partially interpreted.

## Threat-model boundary

This feature prevents OpenWorkGraph agents, REST clients, MCP clients, and the sync process itself from activating policy. It does not attempt to sandbox a process that already has unrestricted same-user filesystem access to the configured source, proposal, or active-policy directories.

For stronger enterprise separation, those paths should ultimately be owned and writable only by a dedicated administrative identity/process.
