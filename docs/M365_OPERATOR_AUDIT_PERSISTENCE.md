# Microsoft 365 operator audit persistence

When `OWG_M365_AUDIT_DIR` points to an existing **absolute, private (0700), real directory** on POSIX, successful operator CLI `review`, `extract`, `append` and `checkpoint-commit` phases create durable, exclusive 0600 audit receipts. The existing authenticated dashboard route `/v1/m365/operator-audit` reads the same directory. No directory is created implicitly; operators must provision and protect it.

Receipts contain a UTC timestamp, phase/status, scope/log hashes and allowlisted nonnegative counts/generations only. They never contain Graph continuation URLs, tokens, session keys, mailbox names, raw email content or source event IDs. No automatic retries occur. If audit persistence fails after an append or checkpoint commit, the CLI returns exit code **3** with `audit_uncertain`: **inspect the destination and audit directory; do not repeat the append/commit blindly**. Unconfigured audit storage retains the existing CLI behavior. This does not establish Microsoft tenant validation or replace independent event-level verification.

Tests: `python -m pytest tests/test_m365_operator_audit_writer.py tests/test_m365_operator_audit_routes.py tests/test_m365_operator_checkpoint_phase.py`. Full native/CI acceptance remains required before merge.
