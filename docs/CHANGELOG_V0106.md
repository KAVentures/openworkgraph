# OpenWorkGraph v0.107 stack slice: playbooks

This unreleased slice makes structural workflow knowledge portable between people, devices and agents without carrying the work itself.

## Playbooks
- **Export:** History → Playbooks → Export turns one repeated workflow (at least 2 runs, from raw history or run memory) into a small JSON file. It holds:
  - canonical readable structural steps;
  - allowlisted commands and git/gh operations;
  - test and pull-request outcomes;
  - how often the person reworked the immediately preceding agent turn, and time to the next prompt;
  - typical files, lines and tokens.
- **What it never holds:** titles, paths, prompts, tool content, device-keyed file/workspace refs, execution IDs or evidence references. Its name is the only free text and must be short, plain and not instruction-like.
- **Import is an untrusted agent-visible boundary:** another person's file goes through one strict gate on import and again on every read.
  - `typical_steps` must match the actual OpenWorkGraph structural grammar (`model_call`, approvals/errors, or canonical `tool:<category>:<safe-label>[:failure]`). A safe character set alone is not accepted, so strings such as `ignore_previous_instructions` or `run_rm_rf` are dropped.
  - Device-local opaque prefixes (`f:`, `w:`, `event:`, `execution:`, `run:`, `s:`) cannot survive portable step validation.
  - Family keys must be actual generated `agent:workflow:<hex>` or `agent:structure:<hex>` values; framework labels are restricted to known structural runtimes.
  - Commands/git/gh operations are allowlisted; numbers/rates are bounded; unknown keys are dropped; total size is capped at 32 KB.
- **Agents:** imported playbooks are available through compact-MCP `get_playbooks`. `include_my_workflows=true` separately requires "All saved history" AI access. The legacy 24-tool server remains frozen.
- **Run memory** keeps readable structural steps and family so playbooks can work after raw ephemeral history is purged.

## API
- `GET /v1/playbooks/local`, `GET /v1/playbooks/export?family_key=&name=`, `POST /v1/playbooks/import`, `GET /v1/playbooks/imported`, `DELETE /v1/playbooks/imported/{id}`.

This slice does not publish a separate v0.106 release; the completed stack publishes as v0.107.0.
