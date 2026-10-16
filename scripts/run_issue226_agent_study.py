#!/usr/bin/env python3
"""Run REAL external agent commands through an instrumented A/B/C evidence broker.

Unlike evaluate_record_graph_issue226.py (offline scorer), this collector launches
an explicit user-supplied agent executable once per case/arm/replicate and logs
the broker calls actually made. It cannot certify that the executable is an AI,
the truth labels are correct, or that the held-out tasks are genuinely real.
No model calls are made by this script unless the caller supplies an agent CLI.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from scripts.evaluate_record_graph_issue226 import (
    CONDITIONS, _OUTCOMES, _REF, _validate_cases, evaluate,
)

_MAX_RESPONSE = 250_000
_MAX_SCENARIO_BYTES = 2_000_000
_MAX_PROMPT_CHARS = 8_000
_MAX_AGENT_OUTPUT = 40_000


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _scenarios(path: Path, cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if path.stat().st_size > _MAX_SCENARIO_BYTES:
        raise ValueError("scenario file exceeds bounded study size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("scenarios must be a JSON array")
    indexed: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid scenario row")
        cid = item.get("case_id")
        if not isinstance(cid, str) or cid not in cases or cid in indexed:
            raise ValueError("each case needs one exact, unique scenario")
        # No truth labels or assessment keys are allowed in agent-visible data.
        for field in ("gold_outcome", "expected_outcome", "correct_answer", "truth"):
            if field in item:
                raise ValueError("ground truth must not appear in agent-visible scenarios")
        if not isinstance(item.get("prompt"), str) or len(item["prompt"]) > _MAX_PROMPT_CHARS:
            raise ValueError("scenario prompt missing or oversized")
        if _json_bytes(item).__len__() > _MAX_RESPONSE:
            raise ValueError("one scenario exceeds bounded size")
        if item.get("resource_ref") != cases[cid].get("resource_ref"):
            raise ValueError("scenario resource must exactly match ground truth's opaque ref")
        source = item.get("source_snapshot")
        if source is not None and not isinstance(source, dict):
            raise ValueError("source_snapshot must be JSON object or null for unavailable")
        context = item.get("owg_record_graph")
        if context is not None and not isinstance(context, dict):
            raise ValueError("owg_record_graph must be an object or null")
        indexed[cid] = item
    if set(indexed) != set(cases):
        raise ValueError("scenario cases do not match adjudicated case IDs")
    return indexed


def _local_owg_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("live OWG endpoint must use plain HTTP on loopback only")
    if not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("live OWG endpoint must be a fixed loopback origin with explicit port")
    if parsed.path not in {"", "/", "/v1/record-graph"}:
        raise ValueError("OWG endpoint path must be /v1/record-graph")
    return url[:url.index("/", url.index("://") + 3)] + "/v1/record-graph" if "/" in url[url.index("://")+3:] else url.rstrip("/") + "/v1/record-graph"


def _live_owg(ref: str, endpoint: str, auth_token: str) -> dict[str, Any]:
    url = endpoint + "?" + urlencode({"resource_ref": ref, "include_source_status": "false"})
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = "Bearer " + auth_token
    req = Request(url, headers=headers)
    with urlopen(req, timeout=7) as response:
        content = response.read(_MAX_RESPONSE + 1)
    if len(content) > _MAX_RESPONSE:
        raise ValueError("OWG response too large")
    value = json.loads(content)
    if not isinstance(value, dict) or value.get("resource_ref") != ref:
        raise ValueError("OWG returned a different or invalid record")
    return value


class TrialBroker:
    """One isolated HTTP listener per run; all logs come from HTTP observations."""

    def __init__(self, case: dict[str, Any], scenario: dict[str, Any],
                 condition: str, *, owg_endpoint: str = "", owg_token: str = "",
                 live_reader: Callable[[str, str, str], dict[str, Any]] = _live_owg):
        self.case = case
        self.scenario = scenario
        self.condition = condition
        self.owg_endpoint = owg_endpoint
        self.owg_token = owg_token
        self.live_reader = live_reader
        self.secret = secrets.token_urlsafe(32)
        self.logs: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def _record(self, row: dict[str, Any]) -> None:
        with self._lock:
            if len(self.logs) < 100:
                self.logs.append({"origin": "orchestrator_tool_execution", **row})

    @contextmanager
    def serving(self) -> Iterator[str]:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                # Never print refs, tokens, source data or client IP.
                return

            def _reply(self, status: int, value: dict[str, Any]) -> None:
                payload = _json_bytes(value)
                if len(payload) > _MAX_RESPONSE:
                    status, payload = 413, b'{"availability":"unknown"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                if self.headers.get("X-OWG-Eval-Token") != broker.secret:
                    self._reply(401, {"error": "unauthorized"})
                    return
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=False)
                ref = query.get("resource_ref", [""])[0]
                if (len(query.get("resource_ref", [])) != 1
                        or not _REF.fullmatch(ref)
                        or ref != broker.case.get("resource_ref")):
                    self._reply(404, {"availability": "unknown", "reason": "unmatched_resource"})
                    return
                if parsed.path == "/v1/source-audit":
                    if broker.condition not in {"B", "C"}:
                        self._reply(403, {"error": "arm_not_authorized"})
                        return
                    data = broker.scenario.get("source_snapshot")
                    if data is None or data.get("availability") == "unavailable":
                        broker._record({"tool": "source_audit_log_read", "status": "unavailable",
                                        "source_record_match": True})
                        self._reply(503, {"availability": "unavailable"})
                        return
                    if not isinstance(data, dict):
                        broker._record({"tool": "source_audit_log_read", "status": "error",
                                        "source_record_match": True})
                        self._reply(502, {"availability": "unknown"})
                        return
                    canonical = _json_bytes(data)
                    broker._record({
                        "tool": "source_audit_log_read", "status": "success",
                        "source_record_match": True,
                        "source_snapshot_sha256": sha256(canonical).hexdigest(),
                    })
                    self._reply(200, data)
                    return
                if parsed.path == "/v1/owg-record":
                    if broker.condition != "C":
                        self._reply(403, {"error": "arm_not_authorized"})
                        return
                    try:
                        if broker.owg_endpoint:
                            data = broker.live_reader(ref, broker.owg_endpoint, broker.owg_token)
                        else:
                            data = broker.scenario.get("owg_record_graph")
                        if not isinstance(data, dict) or data.get("resource_ref") != ref:
                            raise ValueError("missing or mismatched OWG record")
                    except (ValueError, HTTPError, URLError, OSError, TimeoutError):
                        broker._record({"tool": "get_record_graph", "status": "error",
                                        "resource_ref": ref})
                        self._reply(503, {"availability": "unknown", "reason": "owg_unavailable"})
                        return
                    broker._record({"tool": "get_record_graph", "status": "success",
                                    "resource_ref": ref})
                    self._reply(200, data)
                    return
                self._reply(404, {"error": "no_such_tool"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield "http://127.0.0.1:" + str(server.server_address[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self._server = None


def _parse_agent_output(stdout: str) -> str:
    lines = stdout.strip().splitlines()
    if not lines or len(stdout) > _MAX_AGENT_OUTPUT:
        raise ValueError("agent output missing or oversized")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("agent must output a JSON object as its final line") from exc
    outcome = result.get("predicted_outcome") if isinstance(result, dict) else None
    if outcome not in _OUTCOMES:
        raise ValueError("agent did not supply a valid predicted_outcome")
    return str(outcome)


def run_study(
    cases_input: list[dict[str, Any]], scenarios_input: dict[str, dict[str, Any]],
    argv: list[str], *, replicates: int = 2, seed: int = 2026,
    timeout_seconds: int = 150, owg_endpoint: str = "",
    owg_token: str = "", runner: Callable[..., Any] = subprocess.run,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = _validate_cases(cases_input)
    if not cases or set(cases) != set(scenarios_input):
        raise ValueError("scenarios must exactly match cases")
    if replicates < 2 or replicates > 30:
        raise ValueError("replicates must be 2..30")
    if timeout_seconds < 1 or timeout_seconds > 3600:
        raise ValueError("agent timeout must be 1..3600 seconds")
    if not argv or any(not item or "\x00" in item for item in argv):
        raise ValueError("executable argv required (never shell=True)")
    if owg_endpoint:
        owg_endpoint = _local_owg_endpoint(owg_endpoint)
    trials = [(case_id, cond, rep) for case_id in sorted(cases)
              for rep in range(1, replicates+1) for cond in CONDITIONS]
    random.Random(seed).shuffle(trials)
    results: list[dict[str, Any]] = []
    for case_id, condition, replicate in trials:
        case, scenario = cases[case_id], scenarios_input[case_id]
        broker = TrialBroker(case, scenario, condition,
                             owg_endpoint=owg_endpoint, owg_token=owg_token)
        with tempfile.TemporaryDirectory(prefix="owg-agent-trial-") as workdir:
            with broker.serving() as base:
                ref = case.get("resource_ref")
                prompt = (
                    "You are executing a blinded, independent operational outcome assessment.\n"
                    "Respond with exactly one final JSON line: "
                    '{"predicted_outcome":"resolved|unresolved|unknown|paid|unpaid|refunded|denied"}.\n'
                    "Do not report your tool calls yourself. Use the instrumented host tools below "
                    "if they materially help you; the host records HTTP invocations separately.\n"
                    "For the source-audit tool (B/C only) GET "
                    + base + "/v1/source-audit?resource_ref=" + (ref or "NONE") + "\n"
                    "For OWG record-graph tool (C only) GET "
                    + base + "/v1/owg-record?resource_ref=" + (ref or "NONE") + "\n"
                    "Send X-OWG-Eval-Token from your OWG_EVAL_TOKEN environment variable.\n"
                    "If there is no opaque record reference, the host tools cannot resolve a record.\n"
                    "Only infer a business outcome if supported; unknown is an acceptable answer.\n"
                    "Case goal:\n" + str(scenario["prompt"])
                )
                env = os.environ.copy()
                # This capability belongs only to the coordinator. Even when
                # reading actual local OWG evidence, never hand credentials to agents.
                env.pop("OWG_EVAL_OWG_BEARER", None)
                env.update({
                    "OWG_EVAL_BASE_URL": base, "OWG_EVAL_TOKEN": broker.secret,
                    "OWG_EVAL_CONDITION": condition, "OWG_EVAL_CASE_ID": case_id,
                    "OWG_EVAL_REPLICATE": str(replicate),
                })
                try:
                    complete = runner(
                        argv, input=prompt, capture_output=True, text=True,
                        timeout=timeout_seconds, cwd=workdir, env=env, shell=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        f"agent execution timed out: {case_id}/{condition}/{replicate}"
                    ) from exc
                if complete.returncode != 0:
                    # Deliberately omit stdout/stderr: they may contain secrets,
                    # patient information or untrusted model-generated content.
                    raise RuntimeError(
                        f"agent exited nonzero: {case_id}/{condition}/{replicate}"
                    )
                outcome = _parse_agent_output(complete.stdout)
        results.append({
            "case_id": case_id, "condition": condition, "replicate": replicate,
            "predicted_outcome": outcome,
            "host_tool_logs": broker.logs,
            "collector": "owg_loopback_http_broker_v1",
            "owg_source_mode": "local_api" if owg_endpoint else "operator_supplied_snapshot",
            "recorded_model_completion": True,
            # Whether argv is truly a model is not independently attested.
            "model_provenance_verified": False,
        })
    report = evaluate(cases_input, results, minimum_replicates=replicates)
    report["agent_commands_executed_by_collector"] = len(results)
    report["broker_tool_calls_are_runner_observed"] = True
    report["provider_live_calls_performed_by_broker"] = False
    report["real_provider_data_not_proven"] = True
    report["command_is_a_real_model_not_attested"] = True
    return results, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True,
                        help="Private adjudicated cases; never put truth in the agent prompt")
    parser.add_argument("--scenarios", type=Path, required=True,
                        help="Privacy-reviewed prompts/source snapshots/optional OWG snapshots")
    parser.add_argument("--agent-cmd", required=True,
                        help="Exact executable argv (shell disabled); e.g. 'python /abs/path/agent.py'")
    parser.add_argument("--runs-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--timeout-seconds", type=int, default=150)
    parser.add_argument("--owg-local-url", default="",
                        help="Optional already running and authorized loopback OWG API origin")
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    scenarios = _scenarios(args.scenarios, _validate_cases(cases))
    results, report = run_study(
        cases, scenarios, shlex.split(args.agent_cmd),
        replicates=args.replicates, seed=args.seed,
        timeout_seconds=args.timeout_seconds,
        owg_endpoint=args.owg_local_url,
        owg_token=os.getenv("OWG_EVAL_OWG_BEARER", ""),
    )
    for path, value in ((args.runs_output, results), (args.report_output, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if os.name == "posix":
            path.chmod(0o600)
    print(json.dumps({
        "runs": len(results), "report_status": report["status"],
        "c_minus_b_accuracy": report["c_minus_b_accuracy"],
        "traces": str(args.runs_output), "report": str(args.report_output),
        "live_external_source_provider_verified": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
