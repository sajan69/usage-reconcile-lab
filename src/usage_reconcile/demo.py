"""Credential-free synthetic inputs and an exact ingestion smoke demonstration."""
from pathlib import Path
import tempfile
from .contract import canonical
from .storage import ingest, inspect


def generate(out):
    out = Path(out)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    context = dict(schema_version=1, start_utc="2026-10-01T00:00:00Z",
                   end_utc="2026-10-03T00:00:00Z", acquired_at="2026-10-04T00:00:00Z",
                   coverage="complete", group_by=["project_id", "model"], batch=False, modalities=["text"])
    def event(i, at, response, usage):
        return dict(event_id=f"event-{i}", occurred_at=at, response_id=response,
                    project_id="demo-project", model="synthetic-text-model", usage=usage)
    rows = [
        event(1, "2026-10-01T08:00:00Z", "response-1",
              dict(input_tokens=10, output_tokens=2, input_cached_tokens=4)),
        event(2, "2026-10-01T09:00:00Z", "response-2", None),
        event(3, "2026-10-01T10:00:00Z", "response-3", dict(input_tokens=0, output_tokens=0)),
        event(4, "2026-10-03T01:00:00+02:00", "response-4", dict(input_tokens=7, output_tokens=3)),
        event(5, "2026-10-02T11:00:00Z", "response-5",
              dict(input_tokens=5, output_tokens=1, input_cached_tokens=0)),
        event(6, "2026-10-02T11:00:00Z", "response-5",
              dict(input_tokens=5, output_tokens=1, input_cached_tokens=0)),
    ]
    rows.append(dict(rows[0]))
    buckets = []
    for start, input_tokens, output_tokens, requests, cached in (
            (1790812800, 20, 6, 3, 4), (1790899200, 12, 4, 2, None)):
        result = dict(object="organization.usage.completions.result", project_id="demo-project",
                      model="synthetic-text-model", batch=None, input_tokens=input_tokens,
                      output_tokens=output_tokens, num_model_requests=requests, input_cached_tokens=cached,
                      input_audio_tokens=0, output_audio_tokens=0)
        buckets.append(dict(object="bucket", start_time=start, end_time=start + 86400, results=[result]))
    files = {
        "app.jsonl": "\n".join(canonical(row) for row in rows) + "\n",
        "app-manifest.json": canonical(dict(context, source="app_events_v1")) + "\n",
        "provider.json": canonical(dict(pages=[dict(object="page", has_more=False,
                                                    next_page=None, data=buckets)])) + "\n",
        "provider-manifest.json": canonical(dict(context, source="openai_completions_v1")) + "\n",
    }
    for name, content in files.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(content)
        (out / name).chmod(0o600)
    return dict(source="synthetic", files=sorted(files), app_input_records=7)


def demo():
    with tempfile.TemporaryDirectory(prefix="usage-reconcile-") as root:
        root = Path(root); folder = root / "fixture"; generate(folder)
        db = root / "lab.duckdb"
        app = ingest(folder / "app.jsonl", folder / "app-manifest.json", db)
        provider = ingest(folder / "provider.json", folder / "provider-manifest.json", db)
        repeat = ingest(folder / "app.jsonl", folder / "app-manifest.json", db)
        assert app["inserted"] == 6 and app["quality"]["exact_projected_duplicates"] == 1
        assert app["quality"]["unknown_usage_records"] == 1
        assert app["quality"]["repeated_response_ids"] == 1
        assert provider["inserted"] == 2 and provider["quality"]["missing_daily_buckets"] == 0
        assert repeat["reused"] and repeat["inserted"] == 0
        assert inspect(db, app["snapshot_id"])["quality"] == app["quality"]
        return dict(source="synthetic", app=app, provider=provider, reimport=repeat,
                    interpretation="Ingestion checks only; no usage reconciliation or accuracy claim.")


def comparison_demo(out=None):
    """End-to-end synthetic comparison with independently specified expected totals."""
    import json
    from .analysis import compare
    from .reports import export
    with tempfile.TemporaryDirectory(prefix="usage-comparison-") as tmp:
        root = Path(tmp); folder = root / "fixture"; generate(folder)
        models = ["synthetic-text-model", "matched-text-model", "zero-text-model"]
        scope = dict(organization_id="synthetic-org", project_ids=["demo-project"], models=models,
                     time_basis="provider_utc_bucket")
        for name in ("app-manifest.json", "provider-manifest.json"):
            path = folder / name; obj = json.loads(path.read_text()); obj["reconciliation"] = scope
            path.write_text(canonical(obj) + "\n")
        rows = [json.loads(line) for line in (folder / "app.jsonl").read_text().splitlines()]
        for day in (1, 2):
            rows.append(dict(event_id=f"matched-{day}", response_id=f"matched-response-{day}",
                             occurred_at=f"2026-10-0{day}T12:00:00Z", project_id="demo-project",
                             model="matched-text-model", usage=dict(input_tokens=25, output_tokens=5,
                                                                    input_cached_tokens=10)))
        rows.append(dict(event_id="zero", response_id="zero-response", occurred_at="2026-10-01T13:00:00Z",
                         project_id="demo-project", model="zero-text-model",
                         usage=dict(input_tokens=0, output_tokens=0, input_cached_tokens=0)))
        (folder / "app.jsonl").write_text("\n".join(canonical(row) for row in rows) + "\n")
        path = folder / "provider.json"; provider = json.loads(path.read_text())
        for i, bucket in enumerate(provider["pages"][0]["data"]):
            bucket["results"].append(dict(object="organization.usage.completions.result", project_id="demo-project",
                model="matched-text-model", input_tokens=25 + i * 2, output_tokens=5,
                input_cached_tokens=10, num_model_requests=1))
        provider["pages"][0]["data"][0]["results"].append(dict(object="organization.usage.completions.result",
            project_id="demo-project", model="zero-text-model", input_tokens=0, output_tokens=0,
            input_cached_tokens=0, num_model_requests=1))
        path.write_text(canonical(provider) + "\n")
        db = root / "lab.duckdb"
        app = ingest(folder / "app.jsonl", folder / "app-manifest.json", db)
        provider = ingest(folder / "provider.json", folder / "provider-manifest.json", db)
        report = compare(db, app["snapshot_id"], provider["snapshot_id"])
        assert report["summary"] == dict(groups=6, comparable_metrics=13, withheld_metrics=11)
        lookup = {(r["day"], r["model"]): r for r in report["rows"]}
        assert lookup["2026-10-02", "matched-text-model"]["metrics"]["input_tokens"]["difference_provider_minus_app"] == 2
        assert lookup["2026-10-01", "zero-text-model"]["metrics"]["input_tokens"]["difference_provider_minus_app"] == 0
        assert lookup["2026-10-01", "synthetic-text-model"]["observations"]["app_known_input"] == 10
        assert lookup["2026-10-01", "synthetic-text-model"]["metrics"]["input_tokens"]["app"] is None
        assert app["inserted"] == 9 and provider["inserted"] == 5
        result = dict(source="synthetic", app_snapshot=app["snapshot_id"], provider_snapshot=provider["snapshot_id"],
                      app_events=9, app_projected_duplicates=1, provider_groups=5, summary=report["summary"],
                      checked_input_difference=2, checked_zero_difference=0)
        if out is not None:
            target = Path(out); target.mkdir(mode=0o700, exist_ok=False)
            result["exports"] = {fmt: export(report, target / ("comparison." + fmt), fmt)
                                 for fmt in ("json", "csv", "html")}
        return result
