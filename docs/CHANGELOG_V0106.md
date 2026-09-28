# OpenWorkGraph (unreleased, planned v0.106): playbooks

Builds on v0.105. What OpenWorkGraph learns about a kind of work can now travel between people, devices and agents, without the work itself.

## Playbooks
- **Export:** History → Playbooks → Export turns one repeated workflow (at least 2 runs, from raw history or run memory) into a small JSON file. It holds:
  - the typical readable steps;
  - the commands and git/gh operations used;
  - how tests ended;
  - pull request outcomes;
  - how often the person reworked the result, and the time to the next prompt;
  - the typical files, lines and tokens.
- **What it never holds:** titles, paths, prompts, tool content, device-keyed file or workspace hashes, execution IDs or evidence references. Its name is the only free text, and it must be short and plain and must not look like an instruction.
- **Import:** another person imports the file through one strict gate. Unknown keys are dropped, and commands and git operations must be on the allowlists. The gate runs again on every read. Re-importing the same playbook keeps one entry.
- **Agents:** they read imported playbooks through the new MCP tool `get_playbooks`, now part of the default compact tool set and the MCPB manifest. With `include_my_workflows=true` they also see the person's own repeated workflows, which needs "All saved history" AI access. Imported playbooks need only normal AI access.
- **Run memory** now also keeps each run's readable steps and family, so playbooks work with "Don't keep after session".

## API
- `GET /v1/playbooks/local`, `GET /v1/playbooks/export?family_key=&name=`, `POST /v1/playbooks/import`, `GET /v1/playbooks/imported`, `DELETE /v1/playbooks/imported/{id}`.
