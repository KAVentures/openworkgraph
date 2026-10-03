# Security Policy

OpenWorkGraph is local-first workflow-observation software. Security and privacy issues that could expose captured work evidence, local API/MCP capabilities, browser pairing secrets, organization Gateway data, or installer/update integrity should be reported privately.

## Report a vulnerability

Please do **not** open a public GitHub issue for an unpatched vulnerability.

Use GitHub's private vulnerability reporting / Security advisory flow for this repository when available. Include:

- affected OpenWorkGraph version or commit;
- operating system and installation method;
- the smallest reproducible example;
- impact and the data/capability exposed;
- whether the issue requires another process running as the same OS user;
- any suggested mitigation or patch.

Do not include real customer, employee, patient, credential, email-body, clipboard, or other sensitive content in a report. Use synthetic evidence.

## Scope

Particularly important areas include:

- localhost API/MCP authentication and origin/host controls;
- browser-sensor pairing and resource-reference handling;
- Discovery Mode scope enforcement and export approval;
- local evidence/redaction boundaries and Full AI context;
- organization Gateway tenant isolation and enrollment tokens;
- agent-ingest and session-continuity authorization;
- installer signing, notarization, update integrity, and dependency supply chain;
- path traversal, arbitrary file reads, or reads that bypass observed-file fingerprints.

OpenWorkGraph's local capability controls are intended to reduce accidental exposure and unauthorized local clients. They are not a security boundary against malware already executing with the same operating-system user privileges.

## Supported versions

Security fixes are applied to the current mainline release. Users should update to the latest available release after a security fix is published.
