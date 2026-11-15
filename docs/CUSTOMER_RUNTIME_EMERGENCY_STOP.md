# R17 — Customer-owned emergency stop for generated automation runtimes

The generated Agents SDK project now includes **`stop.py`**, a standard-library-only operator control that blocks **future tool attempts** by `execute.py`. The generated process model never grants execution permission; this control does not weaken the existing exact-revision, source-read, customer-intent, tool-mapping or service-side authorization checks.

## To stop

From a generated customer SDK project directory:

```sh
python stop.py --status
python stop.py --stop --confirm-stop
python stop.py --status
```

The command creates `execution-journal/.owg-stop` with exclusive creation and private permissions where supported. It flushes the marker durably, and is idempotent. Its status/stop commands have **no provider, Gateway, AI model, or network side effects**. If the runtime was launched with `--journal-directory /your/other/path`, supply **the identical path** to `stop.py`.

Every new `execute.py --execute-approved` checks this marker before it creates an attempt journal or opens its tool client. A currently running execution rechecks the marker at existing source, review, intent and tool boundaries **and immediately before mapped tool calls**. A malformed marker or broken symlink is a stop, not an implicit release.

After observing a stop, the existing execution journal records `operator_stop_observed` (or `operator_stop_active_at_completion`) and the result flags `operator_stopped` with `declared_route_checks_satisfied=false`. It never automatically retries an action or marks an independently verified business outcome.

## Limits and recovery

**This cannot cancel an HTTP/MCP request already submitted, undo a previous business write, force a provider to roll back, or interrupt an uncooperative remote service.** A stop requested during the first tool call takes effect before subsequent local tool attempts. A provider request that was already running may still take effect even if its response later fails or is lost. The customer must inspect independent source receipts, provider outcomes and the execution journal.

There is deliberately **no automated resume**. Stop the local process, investigate any partial/uncertain writes, reconcile service-side state, reconfirm the current process and tool permissions, and create a **new separately reviewed customer execution intent** before an operator intentionally clears `.owg-stop` on the trusted local filesystem. Removing a marker does not authorize reusing an earlier intent; journal replay protection remains enforced.

This is not a signed administrative command or enterprise fleet kill mechanism. Anyone able to write or delete the selected local journal directory is inside the local operating-system trust boundary. Remote controls, fleet-wide propagation and independently verified rollback need separate deployment and customer-sandbox acceptance.

## Acceptance

Synthetic tests run the actual exported `stop.py` using Python with `-S` (standard-library-only), confirm no implicit stop without `--confirm-stop`, validate idempotent private durable marker and fail-closed corrupt/symlink behavior, reject a new run before any provider client is opened, and simulate an in-flight first customer tool attempt that triggers STOP before the second tool attempt. No real customer credentials, provider calls, external changes or claimed business outcomes are involved.
