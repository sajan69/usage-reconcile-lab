"""Bounded synthetic import/query measurements; no external accounts or data."""
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time
import duckdb
import pytz
from . import __version__
from .analysis import compare
from .contract import canonical, digest, require
from .storage import ingest


def validate_bounds(rows, repeats):
    require(type(rows) is int and 80 <= rows <= 8000 and rows % 80 == 0, "invalid_benchmark_rows")
    require(type(repeats) is int and 1 <= repeats <= 5, "invalid_benchmark_repeats")


def _fixture(rows):
    events, totals = [], {}
    for index in range(rows):
        group = index % 8
        day, project, model = group // 4, (group // 2) % 2, group % 2
        usage = dict(input_tokens=(index % 17) * 10, output_tokens=index % 7,
                     input_cached_tokens=(index % 17) * 5)
        event = dict(event_id=f"event-{index:05d}", response_id=f"response-{index:05d}",
                     occurred_at=f"2026-10-0{day + 1}T12:00:00Z", project_id=f"project-{project}",
                     model=f"synthetic-model-{model}", usage=None if index % 10 == 0 else usage)
        events.append(event)
        total = totals.setdefault(group, dict(object="organization.usage.completions.result",
            project_id=event["project_id"], model=event["model"], num_model_requests=0,
            input_tokens=0, output_tokens=0, input_cached_tokens=0))
        total["num_model_requests"] += 1
        for field, value in usage.items(): total[field] += value
    # Exact repeated event IDs, not separate retry attempts. No double counting.
    events.extend(events[index] for index in range(0, rows, 20))
    totals[1]["input_tokens"] += 3  # Intentional known difference in a fully observed group.
    buckets = [dict(object="bucket", start_time=1790812800 + day * 86400,
                    end_time=1790899200 + day * 86400,
                    results=[totals[group] for group in range(day * 4, day * 4 + 4)]) for day in (0, 1)]
    context = dict(schema_version=1, start_utc="2026-10-01T00:00:00Z", end_utc="2026-10-03T00:00:00Z",
                   acquired_at="2026-10-04T00:00:00Z", coverage="complete", group_by=["project_id", "model"],
                   batch=False, modalities=["text"], reconciliation=dict(organization_id="synthetic-org",
                   project_ids=["project-0", "project-1"], models=["synthetic-model-0", "synthetic-model-1"],
                   time_basis="provider_utc_bucket"))
    return {
        "app.jsonl": ("\n".join(canonical(event) for event in events) + "\n").encode(),
        "provider.json": (canonical(dict(pages=[dict(object="page", has_more=False, next_page=None, data=buckets)])) + "\n").encode(),
        "app-manifest.json": (canonical(dict(context, source="app_events_v1")) + "\n").encode(),
        "provider-manifest.json": (canonical(dict(context, source="openai_completions_v1")) + "\n").encode(),
    }


def _check(app, provider, repeat, report, rows):
    require(app["inserted"] == rows and app["quality"]["exact_projected_duplicates"] == rows // 20 and
            app["quality"]["unknown_usage_records"] == rows // 10 and
            app["quality"]["repeated_response_ids"] == 0, "benchmark_import_mismatch")
    require(provider["inserted"] == 8 and repeat["inserted"] == 0 and repeat["reused"], "benchmark_reimport_mismatch")
    require(report["blockers"] == [] and report["summary"] ==
            dict(groups=8, comparable_metrics=20, withheld_metrics=12), "benchmark_comparison_mismatch")
    for row in report["rows"]:
        project = int(row["project_id"].rsplit("-", 1)[1])
        model = int(row["model"].rsplit("-", 1)[1])
        day = int(row["day"][-1]) - 1
        group = day * 4 + project * 2 + model
        require(row["observations"]["app_events"] == rows // 8, "benchmark_group_count_mismatch")
        for metric, values in row["metrics"].items():
            if metric != "requests" and model == 0:
                require(values["status"] == "withheld" and values["difference_provider_minus_app"] is None,
                        "benchmark_unknown_mismatch")
            else:
                expected = 3 if group == 1 and metric == "input_tokens" else 0
                require(values["status"] == "conditional_comparison" and
                        values["difference_provider_minus_app"] == expected, "benchmark_difference_mismatch")


def benchmark(rows=800, repeats=3):
    validate_bounds(rows, repeats)  # Before file creation or fixture allocation.
    payloads = _fixture(rows)
    hashes = {name: digest(raw) for name, raw in sorted(payloads.items())}
    samples = []
    def measured(operation):
        started = time.perf_counter()
        value = operation()
        return value, (time.perf_counter() - started) * 1000
    with tempfile.TemporaryDirectory(prefix="usage-benchmark-") as temporary:
        root = Path(temporary)
        for name, raw in payloads.items():
            with (root / name).open("xb") as handle: handle.write(raw)
            (root / name).chmod(0o600)
        for index in range(repeats):
            db = root / f"repeat-{index}.duckdb"
            app, app_ms = measured(lambda: ingest(root / "app.jsonl", root / "app-manifest.json", db))
            provider, provider_ms = measured(lambda: ingest(root / "provider.json", root / "provider-manifest.json", db))
            again, reimport_ms = measured(lambda: ingest(root / "app.jsonl", root / "app-manifest.json", db))
            report, comparison_ms = measured(lambda: compare(db, app["snapshot_id"], provider["snapshot_id"]))
            _check(app, provider, again, report, rows)  # Correctness assertions excluded from timers.
            samples.append(dict(repetition=index + 1, app_import_ms=app_ms, provider_import_ms=provider_ms,
                                app_reimport_ms=reimport_ms, comparison_ms=comparison_ms))
    metrics = ("app_import_ms", "provider_import_ms", "app_reimport_ms", "comparison_ms")
    return dict(benchmark_version=1, source="synthetic", completed=True,
                recorded_at=datetime.now(timezone.utc).isoformat(),
                workload=dict(unique_app_events=rows, input_app_records=rows + rows // 20,
                    exact_duplicates=rows // 20, unknown_usage_events=rows // 10,
                    provider_groups=8, days=2, projects=2, models=2, repetitions=repeats,
                    expected_summary=dict(groups=8, comparable_metrics=20, withheld_metrics=12),
                    file_bytes={name: len(raw) for name, raw in payloads.items()}, file_sha256=hashes,
                    dataset_sha256=digest(canonical(hashes).encode())),
                environment=dict(python=platform.python_version(), duckdb=duckdb.__version__, pytz=pytz.__version__,
                    package=__version__, platform=platform.platform(), logical_cpus=os.cpu_count(),
                    database_threads=2, database_memory_limit="256MB", storage="temporary directory; filesystem not characterized"),
                method=dict(clock="perf_counter wall-clock milliseconds", fresh_database_each_repetition=True,
                    order=list(metrics), included="input reads/parsing/validation/dedup, database open/close, provenance and transaction; comparison includes metadata, SQL and report assembly",
                    excluded="fixture generation/writes, assertions, report serialization/exports, browser, CLI startup, network/TLS",
                    cache="no explicit warmup or cache clearing; repeated inputs may benefit from OS caches",
                    baseline="none; not a throughput, production capacity or improvement comparison"),
                samples=samples, median_ms={key: statistics.median(sample[key] for sample in samples) for key in metrics})
