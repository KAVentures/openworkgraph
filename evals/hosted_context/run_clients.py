"""Execute fresh real CLI sessions; blocked attempts are NEVER scored as runs.

Run from an isolated venv. Results may be committed; the temporary Gateway,
credentials, CLI streams and transport files are deleted after collection.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .instrumentation import _argument_summary
from .score import load_cases, score_runs

ROOT = Path(__file__).resolve().parents[2]


def events(stream: str) -> list[dict]:
    result = []
    for line in stream.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except ValueError:
            pass
    return result


def classify(client: str, code: int, rows: list[dict], stderr: str) -> str:
    # Claude emits a result subtype=success even for authentication errors.
    text = json.dumps(rows) + stderr
    if any(s in text.lower() for s in ("authentication_failed", "not logged in",
            "401 unauthorized", "access token could not be refreshed", "invalid api key")):
        return "blocked_authentication"
    if code == 124:
        return "blocked_timeout"
    if code != 0:
        return "blocked_client_error"
    if client == "codex" and any(r.get("type") == "turn.completed" for r in rows):
        return "completed"
    if client == "claude" and any(r.get("type") == "result" and not r.get("is_error")
                                  for r in rows):
        return "completed"
    return "blocked_no_completed_model_turn"


def transport_calls(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = events(path.read_text())
    returned = {r["index"]: r for r in rows if r["phase"] == "response"}
    calls = []
    for row in rows:
        if row["phase"] != "request":
            continue
        response = returned.get(row["index"])
        if response:
            calls.append(response["call"] | {"is_error": response["is_error"],
                                            "transport_response_observed": True})
        else:
            calls.append({"name": row["name"], "arguments": _argument_summary(row["arguments"]),
                "event_ids": [], "response_chars": 0, "transport_response_observed": False})
    return calls


def command(client: str, executable: str, model: str, prompt: str, descriptor: dict | None) -> list[str]:
    if client == "codex":
        args = [executable, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                "--skip-git-repo-check", "--json", "--model", model, "--sandbox", "read-only",
                "-c", 'model_reasoning_effort="medium"', "-c", 'web_search="disabled"',
                "-c", "project_doc_max_bytes=0", "-c", "features.skip_host_skill_discovery=true"]
        for feature in ("shell_tool", "multi_agent", "apps", "browser_use", "computer_use",
                        "image_generation", "sleep_tool", "skill_search", "hooks"):
            args += ["-c", f"features.{feature}=false"]
        if descriptor:
            for key, value in descriptor.items():
                args += ["-c", f"mcp_servers.openworkgraph.{key}={toml(value)}"]
        args += [prompt]
        return args
    return [executable, "-p", "--model", model, "--effort", "medium", "--tools", "",
            "--disable-slash-commands", "--strict-mcp-config", "--mcp-config",
            json.dumps({"mcpServers": {"openworkgraph": descriptor} if descriptor else {}}),
            "--setting-sources", "", "--no-session-persistence", "--output-format",
            "stream-json", "--verbose", prompt]


def toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(k)}={toml(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


def execute(args: list[str], cwd: Path, timeout: int):
    process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return 124, stdout, stderr


def one(job, runtime, connection, timeout):
    client, executable, model, case, condition, trial = job
    label = f"{client}-{case['id']}-{condition}-{trial}"
    private = runtime / label
    private.mkdir()
    cwd = private / "session"
    cwd.mkdir()  # No repository, expected answers, controller instructions or prior sessions.
    calls, catalog = private / "calls.jsonl", private / "catalog.json"
    person = {"empty-gateway": "empty", "local-only": "local-only"}.get(case["id"], "populated")
    descriptor = {"command": sys.executable, "args": ["-m", "evals.hosted_context.proxy",
        "--connection", str(connection), "--person", person, "--calls", str(calls),
        "--catalog", str(catalog)], "env": {"PYTHONPATH": str(ROOT)}} if condition == "enabled" else None
    cmd = command(client, executable, model, case["prompt"], descriptor)
    started = time.monotonic()
    code, stdout, stderr = execute(cmd, cwd, timeout)
    rows = events(stdout)
    status = classify(client, code, rows, stderr)
    captured = transport_calls(calls)
    init = next((r for r in rows if r.get("type") == "system" and r.get("subtype") == "init"), {})
    final = next((r for r in reversed(rows) if r.get("type") in ("result", "turn.completed")), {})
    answers = [r.get("item", {}).get("text", "") for r in rows
               if r.get("type") == "item.completed" and r.get("item", {}).get("type") == "agent_message"]
    answer = "\n".join(answers) if client == "codex" else str(final.get("result", ""))
    record = {"case_id": case["id"], "client": client, "condition": condition,
        "trial": str(trial), "requested_model": model, "observed_model": init.get("model"),
        "status": status, "exit_code": code, "latency_seconds": round(time.monotonic() - started, 3),
        "prompt_sha256": hashlib.sha256(case["prompt"].encode()).hexdigest(),
        "mcp_catalog_available": catalog.exists(), "tool_calls": captured,
        "usage": final.get("usage"), "model_usage": final.get("modelUsage"),
        "reported_cost_usd": final.get("total_cost_usd"),
        "client_tool_catalog": init.get("tools"), "client_mcp_servers": init.get("mcp_servers"),
        "model_answer": answer if status == "completed" else None,
        "claim_review": None, "task_completion_review": None,
        "failure_reason": "provider rejected authentication before inference" if status == "blocked_authentication" else None,
        "command_template": [x.replace(str(runtime), "${RUNTIME}").replace(str(ROOT), "${REPO}") for x in cmd]}
    if status == "completed":
        record["harness_recorded"] = True
    snapshot = json.loads(catalog.read_text()) if catalog.exists() else None
    return record, snapshot


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--codex", default=shutil.which("codex"))
    p.add_argument("--claude", default=shutil.which("claude"))
    p.add_argument("--codex-model", default="gpt-5.4")
    p.add_argument("--claude-model", default="claude-sonnet-4-6")
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--case-id", action="append", help="Repeat to rerun affected cases only")
    args = p.parse_args()
    if args.out.exists():
        raise ValueError("output must be a new directory, to prevent mixing trials")
    args.out.mkdir(parents=True)
    os.chmod(args.out, 0o700)
    versions = {}
    clients = []
    for name, executable, model in (("codex", args.codex, args.codex_model),
                                    ("claude", args.claude, args.claude_model)):
        if executable:
            versions[name] = subprocess.check_output([executable, "--version"], text=True).strip()
            clients.append((name, executable, model))
    cases = load_cases()
    if args.case_id:
        unknown = set(args.case_id) - {case["id"] for case in cases}
        if unknown:
            raise ValueError(f"unknown case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in args.case_id]
    jobs = [(c, e, m, case, condition, trial) for c, e, m in clients for case in cases
            for condition in ("enabled", "disabled") for trial in range(1, args.trials + 1)]
    random.Random(20261010).shuffle(jobs)
    records, snapshots = [], []
    with tempfile.TemporaryDirectory(prefix="owg-real-clients-") as temp:
        runtime = Path(temp)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        state = runtime / "gateway"
        connection = state / "connection.json"
        with (runtime / "server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "evals.hosted_context.disposable",
                "--state", str(state), "--port", str(port)], cwd=ROOT, stdout=log, stderr=log,
                start_new_session=True)
            try:
                deadline = time.monotonic() + 30
                while not connection.exists():
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("disposable Gateway failed; inspect temporary server log")
                    time.sleep(0.1)
                # Wait for listener rather than exposing startup races to models.
                while True:
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=1):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError("Gateway did not listen")
                        time.sleep(0.1)
                shutil.copy(state / "seed.json", args.out / "seed.json")
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = [pool.submit(one, job, runtime, connection, args.timeout) for job in jobs]
                    with (args.out / "attempts.jsonl").open("w") as f:
                        for future in concurrent.futures.as_completed(futures):
                            record, snapshot = future.result()
                            records.append(record)
                            if snapshot and snapshot not in snapshots:
                                snapshots.append(snapshot)
                            f.write(json.dumps(record) + "\n")
                            f.flush()
                            print(f"{record['client']} {record['case_id']} {record['condition']}: {record['status']}", flush=True)
            finally:
                if server.poll() is None:
                    os.killpg(server.pid, signal.SIGTERM)
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(server.pid, signal.SIGKILL)
                        server.wait(timeout=5)
    traces = [{"case_id": r["case_id"], "client": r["client"],
               "model": r["observed_model"] or r["requested_model"],
               "trial": f"{r['condition']}-{r['trial']}", "harness_recorded": True,
               "tool_calls": r["tool_calls"]} for r in records if r["status"] == "completed"]
    (args.out / "traces.jsonl").write_text("".join(json.dumps(t) + "\n" for t in traces))
    report = score_runs(cases, traces)
    (args.out / "routing.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / "catalogs.json").write_text(json.dumps(snapshots, indent=2) + "\n")
    manifest = {"schema": "owg.real-cli-attempts.v1", "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "cli_versions": versions,
        "attempts": len(records), "completed": len(traces), "trials": args.trials,
        "case_ids": [case["id"] for case in cases],
        "source_files_sha256": {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in (ROOT / "evals/hosted_context/cases.json", ROOT / "evals/hosted_context/fixtures.py",
                         ROOT / "gateway/public_plugin_mcp.py")},
        "model_settings": {"effort": "medium", "provider_default_sampling": True},
        "permitted_other_tools": "no filesystem, shell, browser, source-system connectors or subagents",
        "installed_skill_condition": "hosted MCP instructions/descriptions only; no local desktop skills",
        "order_seed": 20261010, "cost_basis": "CLI-reported USD only; null means unknown",
        "exact_backend_snapshots": "not inferred from requested model aliases",
        "controller_separation": "fresh empty cwd; no resume; no controller prompt; built-in data access tools disabled",
        "limitations": ["No ChatGPT-web or Claude-web claim", "Claim and task completion review is independent and pending for completed answers",
                         "Single trial is not a reliability estimate", "CLI native prompts differ between clients"]}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
