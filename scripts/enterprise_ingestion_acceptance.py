"""Synthetic, isolated acceptance workload for the retained source-event engine.

Run with python -m scripts.enterprise_ingestion_acceptance. No provider, live
workspace, network, credentials, or customer data are used. Results describe
the exact workload on the recorded machine, not enterprise certification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
from typing import Any

FORMAT = "owg.synthetic-ingestion-acceptance.v1"
MAX_CASES = 2_500_000
CRASH_EXIT = 73
CASE_NAMESPACE = "acceptance_cases"
PROVIDERS = ("acceptance_front", "acceptance_back")
HEADER = ("event", "case", "activity", "time", "who", "region")
EXPORT_HEADER = ("source_namespace", "source_event_id", "case_namespace",
                 "case_id", "activity", "timestamp", "resource", "attributes")
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
EXPORT_MAPPING = {
    "case_column": "case_id", "activity_column": "activity",
    "timestamp_column": "timestamp", "resource_column": "resource",
    "event_id_column": "source_event_id",
    "source_namespace_column": "source_namespace",
    "case_namespace_column": "case_namespace",
    "attributes_json_column": "attributes",
}


class AcceptanceFailure(RuntimeError):
    """A synthetic workload assertion failed."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AcceptanceFailure(code)


def validate_cases(cases: int) -> None:
    if type(cases) is not int or not 1 <= cases <= MAX_CASES:
        raise ValueError(f"cases must be an integer in [1, {MAX_CASES}]")


def mapping(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("unknown synthetic provider")
    return {"case_column": "case", "activity_column": "activity",
            "timestamp_column": "time", "resource_column": "who",
            "event_id_column": "event", "source_namespace": provider,
            "case_namespace": CASE_NAMESPACE, "attribute_columns": ["region"]}


def synthetic_row(case: int, step: int) -> tuple[str, ...]:
    # Deliberately reuse literal event IDs across the two providers.
    activity = ("Open", "Review" if case % 2 == 0 else "Escalate",
                "Approve", "Close")[step]
    stamp = BASE + timedelta(seconds=case * 10 + step)
    return (f"e{case}:{step % 2}", f"c{case}", activity, stamp.isoformat(),
            f"role{step % 2}", "north" if case % 2 == 0 else "south")


def write_fixture(path: Path, cases: int, provider: str) -> None:
    validate_cases(cases)
    if provider not in PROVIDERS:
        raise ValueError("unknown synthetic provider")
    start = 2 * PROVIDERS.index(provider)
    with path.open("x", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(HEADER)
        for case in range(cases):
            for step in (start, start + 1):
                writer.writerow(synthetic_row(case, step))


def write_conflict(path: Path, cases: int) -> None:
    with path.open("x", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(HEADER)
        writer.writerow(synthetic_row(cases, 0))  # valid new record first
        row = list(synthetic_row(0, 0))
        row[2] = "ConflictingActivity"
        writer.writerow(row)


def audit_export(path: Path, cases: int) -> dict[str, int]:
    """Independently check every namespace, ID, field and exact population.

    One bit per expected event would suffice; one byte keeps this verifier
    straightforward and bounded to 10 MB at the maximum workload.
    """
    validate_cases(cases)
    seen = bytearray(cases * 4)
    counts = dict.fromkeys(PROVIDERS, 0)
    with path.open(encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src)
        require(tuple(reader.fieldnames or ()) == EXPORT_HEADER, "export_header")
        for row in reader:
            require(None not in row and all(v is not None for v in row.values()),
                    "export_row_shape")
            provider = row["source_namespace"]
            require(provider in PROVIDERS, "export_namespace")
            key = row["source_event_id"]
            try:
                case_text, local_text = key[1:].split(":")
                case, local = int(case_text), int(local_text)
            except (ValueError, TypeError):
                raise AcceptanceFailure("export_id_shape") from None
            require(0 <= case < cases and local in (0, 1)
                    and key == f"e{case}:{local}", "export_id_bounds")
            step = 2 * PROVIDERS.index(provider) + local
            index = case * 4 + step
            require(not seen[index], "export_duplicate")
            expected = synthetic_row(case, step)
            require(row["case_namespace"] == CASE_NAMESPACE
                    and row["case_id"] == expected[1]
                    and row["activity"] == expected[2]
                    and row["resource"] == expected[4], "export_payload")
            try:
                stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                attributes = json.loads(row["attributes"])
            except (ValueError, TypeError):
                raise AcceptanceFailure("export_payload_parse") from None
            require(stamp.tzinfo is not None and
                    stamp == datetime.fromisoformat(expected[3]), "export_timestamp")
            require(attributes == {"region": expected[5]}, "export_attributes")
            seen[index] = 1
            counts[provider] += 1
    require(all(seen), "export_missing_event")
    return counts


@contextmanager
def isolated_workspace(folder: Path):
    # Existing public import/append/export operations share this local path.
    # The CLI creates a fresh directory; callers cannot select a live store.
    from server import event_log_workspace as workspace
    previous = workspace.DATA_DIR
    workspace.DATA_DIR = folder
    try:
        yield workspace
    finally:
        workspace.DATA_DIR = previous


class Measurements:
    """Wall time and sampled process RSS; short-lived peaks can be missed."""

    def __init__(self) -> None:
        import psutil
        self.process = psutil.Process()
        self.stages: list[dict[str, Any]] = []

    @contextmanager
    def stage(self, name: str, rows: int = 0):
        stop = threading.Event()
        start_rss = self.process.memory_info().rss
        peak = [start_rss]

        def sample():
            while not stop.wait(0.05):
                peak[0] = max(peak[0], self.process.memory_info().rss)

        thread = threading.Thread(target=sample, daemon=True)
        started = time.monotonic()
        status = "passed"
        thread.start()
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            elapsed = time.monotonic() - started
            stop.set()
            thread.join()
            peak[0] = max(peak[0], self.process.memory_info().rss)
            self.stages.append({"name": name, "status": status, "seconds": elapsed,
                                "input_rows": rows,
                                "rows_per_second": rows / elapsed if rows and elapsed else None,
                                "start_rss_bytes": start_rss,
                                "peak_sampled_rss_bytes": peak[0]})


def _append(workspace, log: str, file: Path, provider: str, **kwargs):
    with file.open("rb") as src:
        return workspace.append_log(log, src, source_format="csv",
                                    mapping=mapping(provider), **kwargs)


def _crash_worker(folder: str, log: str, file: str):
    # A true process exit after transaction writes and before commit. No
    # finally/rollback handler runs in the child. Only synthetic data exists.
    with isolated_workspace(Path(folder)) as workspace:
        def crash(_conn, _log):
            os._exit(CRASH_EXIT)
        _append(workspace, log, Path(file), PROVIDERS[1], _transaction_hook=crash)


def crash_and_reopen(folder: Path, log: str, file: Path, timeout: float) -> None:
    child = mp.get_context("spawn").Process(
        target=_crash_worker, args=(str(folder), log, str(file)))
    child.start()
    try:
        child.join(timeout)
        if child.is_alive():
            child.kill()
            child.join()
            raise AcceptanceFailure("crash_child_timeout")
        require(child.exitcode == CRASH_EXIT, "crash_hook_not_reached")
    finally:
        if child.is_alive():
            child.kill()
            child.join()
        child.close()


def verify_mining(workspace, log: str, cases: int) -> None:
    summary = workspace.summary(log, "source_case")
    require((summary["events"], summary["cases"]) == (4 * cases, cases),
            "summary_population")
    require(summary["case_duration_seconds"] == {"median": 3.0, "p90": 3.0, "mean": 3.0},
            "summary_duration")
    variants = workspace.variants(log, "source_case")
    expected = {("Open", "Review", "Approve", "Close"): (cases + 1) // 2}
    if cases // 2:
        expected[("Open", "Escalate", "Approve", "Close")] = cases // 2
    require({tuple(v["path"]): v["cases"] for v in variants["variants"]} == expected
            and variants["distinct_variants"] == len(expected), "variant_population")
    flows = workspace.directly_follows(log, "source_case")
    expected_edges = {}
    for path, count in expected.items():
        for pair in zip(path, path[1:]):
            expected_edges[pair] = expected_edges.get(pair, 0) + count
    require({(e["from"], e["to"]): e["transitions"] for e in flows["edges"]}
            == expected_edges, "edge_population")


def _run(folder: Path, cases: int, crash_timeout: float, meter: Measurements) -> dict[str, Any]:
    data = folder / "data"
    data.mkdir()
    front, back, conflict, exported = (folder / name for name in
                                       ("front.csv", "back.csv", "conflict.csv", "export.csv"))
    with meter.stage("fixture_generation", cases * 4):
        write_fixture(front, cases, PROVIDERS[0])
        write_fixture(back, cases, PROVIDERS[1])
        write_conflict(conflict, cases)
    with isolated_workspace(data) as workspace:
        with meter.stage("initial_import", cases * 2):
            with front.open("rb") as src:
                first = workspace.import_log(src, name="Synthetic scale acceptance",
                                             source_format="csv", mapping=mapping(PROVIDERS[0]))
            log = first["log_id"]
            require(first["events"] == cases * 2 and first["rejected_events"] == 0,
                    "initial_import_counts")
            initial_summary = workspace.summary(log, "source_case")
            initial_history = workspace.append_history(log)
        with meter.stage("hard_exit_and_reopen", cases * 2):
            crash_and_reopen(data, log, back, crash_timeout)
            require(workspace.summary(log, "source_case") == initial_summary
                    and workspace.append_history(log) == initial_history,
                    "hard_exit_changed_state")
        with meter.stage("concurrent_retry", cases * 4):
            # Two overlapping requests in one process. This tests workspace
            # serialization, NOT multi-process or distributed worker leases.
            barrier = threading.Barrier(2)
            def attempt():
                barrier.wait(timeout=10)
                return _append(workspace, log, back, PROVIDERS[1])
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(attempt) for _ in range(2)]
                results = [f.result() for f in futures]
            require(sorted(r["added_events"] for r in results) == [0, cases * 2]
                    and sorted(r["duplicate_events_skipped"] for r in results) == [0, cases * 2]
                    and all(r["rejected_events"] == 0 for r in results),
                    "concurrent_retry_counts")
        with meter.stage("historical_retry", cases * 2):
            retry = _append(workspace, log, front, PROVIDERS[0])
            require(retry["added_events"] == 0 and retry["duplicate_events_skipped"] == cases * 2,
                    "historical_retry_counts")
        with meter.stage("conflict_rollback", 2):
            before = workspace.summary(log, "source_case")
            history = workspace.append_history(log)
            try:
                _append(workspace, log, conflict, PROVIDERS[0])
            except workspace.EventLogWorkspaceError as exc:
                require("source event" in str(exc).lower(), "unexpected_conflict_reason")
            else:
                raise AcceptanceFailure("conflict_accepted")
            require(workspace.summary(log, "source_case") == before
                    and workspace.append_history(log) == history, "conflict_changed_state")
            require(len(history["appends"]) == 3, "unexpected_append_history")
        with meter.stage("mine_exact_population", cases * 4):
            verify_mining(workspace, log, cases)
        with meter.stage("identity_export", cases * 4):
            with exported.open("x", encoding="utf-8", newline="") as out:
                workspace.export_log(log, "sourcecsv", out, "source_case")
        with meter.stage("independent_export_audit", cases * 4):
            counts = audit_export(exported, cases)
        with meter.stage("export_roundtrip", cases * 4):
            with exported.open("rb") as src:
                copy = workspace.import_log(src, name="Synthetic roundtrip",
                                            source_format="csv", mapping=EXPORT_MAPPING)
            require(copy["events"] == cases * 4 and copy["rejected_events"] == 0,
                    "roundtrip_counts")
            verify_mining(workspace, copy["log_id"], cases)
            with exported.open("rb") as src:
                retry = workspace.append_log(copy["log_id"], src, source_format="csv",
                                             mapping=EXPORT_MAPPING)
            require(retry["added_events"] == 0 and retry["duplicate_events_skipped"] == cases * 4,
                    "roundtrip_retry")
        database_bytes = sum(p.stat().st_size for p in data.iterdir() if p.is_file())
        return {"source_events": counts, "exact_events": cases * 4,
                "exact_cases": cases, "append_history_batches": 3,
                "workspace_bytes_with_roundtrip_copy": database_bytes,
                "export_bytes": exported.stat().st_size}


def _positive(value: float | None, name: str) -> None:
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be finite and positive")


def evaluate_budgets(stages: list[dict[str, Any]], *, max_seconds=None,
                     max_rss_mib=None, min_import_rate=None) -> list[str]:
    for value, name in ((max_seconds, "max_seconds"), (max_rss_mib, "max_rss_mib"),
                        (min_import_rate, "min_import_rate")):
        _positive(value, name)
    failed = []
    if max_seconds is not None and sum(s["seconds"] for s in stages) > max_seconds:
        failed.append("total_measured_seconds")
    if max_rss_mib is not None and max((s["peak_sampled_rss_bytes"] for s in stages),
                                       default=0) > max_rss_mib * 1024**2:
        failed.append("sampled_parent_rss")
    initial = next((s for s in stages if s["name"] == "initial_import"), None)
    if min_import_rate is not None and (not initial or
            (initial["rows_per_second"] or 0) < min_import_rate):
        failed.append("initial_import_rate")
    return failed


def run_acceptance(cases: int, *, crash_timeout: float = 900,
                   max_seconds=None, max_rss_mib=None, min_import_rate=None) -> dict[str, Any]:
    validate_cases(cases)
    _positive(crash_timeout, "crash_timeout")
    evaluate_budgets([], max_seconds=max_seconds, max_rss_mib=max_rss_mib,
                     min_import_rate=min_import_rate)  # validate before expensive work
    import duckdb
    import psutil
    meter = Measurements()
    outcome: dict[str, Any] = {}
    failure = None
    try:
        with tempfile.TemporaryDirectory(prefix="owg-synthetic-acceptance-") as temp:
            outcome = _run(Path(temp), cases, crash_timeout, meter)
    except AcceptanceFailure as exc:
        failure = str(exc)
    except Exception as exc:
        # Keep arbitrary exception messages and local paths out of portable reports.
        failure = "unexpected_" + type(exc).__name__
    budget_failures = evaluate_budgets(meter.stages, max_seconds=max_seconds,
                                       max_rss_mib=max_rss_mib, min_import_rate=min_import_rate)
    return {"format": FORMAT, "status": "passed" if not failure and not budget_failures else "failed",
            "synthetic_only": True, "live_provider_acceptance": False,
            "distributed_concurrency_tested": False, "cancellation_tested": False,
            "memory_scope": "sampled parent RSS, excludes crash child, may miss brief peaks",
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "hardware": {"os": platform.system(), "machine": platform.machine(),
                         "logical_cpus": os.cpu_count(), "physical_memory_bytes": psutil.virtual_memory().total,
                         "python": platform.python_version(), "duckdb": duckdb.__version__},
            "workload": {"cases": cases, "events": cases * 4, "providers": list(PROVIDERS),
                         "crash_timeout_seconds": crash_timeout},
            "budgets": {"max_seconds": max_seconds, "max_rss_mib": max_rss_mib,
                        "min_import_rate": min_import_rate},
            "stages": meter.stages, "outcome": outcome, "failure": failure,
            "budget_failures": budget_failures}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=2500)
    parser.add_argument("--crash-timeout", type=float, default=900)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-rss-mib", type=float)
    parser.add_argument("--min-import-rate", type=float)
    parser.add_argument("--report", type=Path, help="new report path; never overwrites")
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(args.cases, crash_timeout=args.crash_timeout,
                                max_seconds=args.max_seconds, max_rss_mib=args.max_rss_mib,
                                min_import_rate=args.min_import_rate)
        serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.report:
            with args.report.open("x", encoding="utf-8") as out:
                out.write(serialized)
        else:
            sys.stdout.write(serialized)
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(2, f"Acceptance configuration/output failure: {type(exc).__name__}\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
