import copy
import json
from pathlib import Path
import tempfile
import unittest
from usage_reconcile.analysis import compare
from usage_reconcile.contract import InputError
from usage_reconcile.storage import ingest
from tests.test_contract import event, raw_events, bundle, manifest
from tests.test_scope import scoped


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = self.root / "lab.duckdb"
        self.index = 0

    def load(self, rows=None, provider=None, app_manifest=None, provider_manifest=None):
        self.index += 1; prefix = str(self.index)
        a = self.root / (prefix + "app.jsonl"); p = self.root / (prefix + "provider.json")
        am = self.root / (prefix + "a.json"); pm = self.root / (prefix + "p.json")
        a.write_bytes(raw_events(rows if rows is not None else [event()]))
        p.write_text(json.dumps(provider if provider is not None else bundle()))
        am.write_text(json.dumps((app_manifest or scoped()).value))
        pm.write_text(json.dumps((provider_manifest or scoped("openai_completions_v1")).value))
        return ingest(a, am, self.db)["snapshot_id"], ingest(p, pm, self.db)["snapshot_id"]

    def report(self, **kwargs):
        return compare(self.db, *self.load(**kwargs))

    def test_matching_and_empty_groups_not_zero_filled(self):
        report = self.report()
        self.assertEqual(report["blockers"], [])
        first, second = report["rows"]
        self.assertEqual(first["metrics"]["input_tokens"]["difference_provider_minus_app"], 0)
        self.assertTrue(second["provider_bucket_present"])
        self.assertIsNone(second["metrics"]["input_tokens"]["provider"])
        self.assertIn("provider_group_unobserved", second["metrics"]["requests"]["reasons"])
        self.assertEqual(report["summary"], {"groups": 2, "comparable_metrics": 4, "withheld_metrics": 4})

    def test_missing_usage_keeps_subtotal_and_withholds_delta(self):
        rows = [event(), {**event(), "event_id": "e2", "response_id": "r2", "usage": None}]
        first = self.report(rows=rows)["rows"][0]
        self.assertEqual(first["observations"]["app_known_input"], 10)
        self.assertEqual(first["observations"]["unknown_input"], 1)
        self.assertIsNone(first["metrics"]["input_tokens"]["app"])
        self.assertIsNone(first["metrics"]["input_tokens"]["difference_provider_minus_app"])
        self.assertEqual(first["metrics"]["requests"]["difference_provider_minus_app"], -1)

    def test_explicit_zero_is_compared(self):
        row = event(); row["usage"] = dict(input_tokens=0, output_tokens=0, input_cached_tokens=0)
        b = bundle(); b["pages"][0]["data"][0]["results"][0].update(input_tokens=0, output_tokens=0, input_cached_tokens=0)
        metric = self.report(rows=[row], provider=b)["rows"][0]["metrics"]["input_tokens"]
        self.assertEqual(metric["app"], 0)
        self.assertEqual(metric["difference_provider_minus_app"], 0)
        self.assertEqual(metric["status"], "conditional_comparison")

    def test_cached_unknown_does_not_block_total_input(self):
        row = event(); row["usage"]["input_cached_tokens"] = None
        metrics = self.report(rows=[row])["rows"][0]["metrics"]
        self.assertEqual(metrics["input_tokens"]["difference_provider_minus_app"], 0)
        self.assertEqual(metrics["cached_input_tokens"]["status"], "withheld")

    def test_repeated_response_across_days_blocks_both_groups(self):
        rows = [event(), {**event(), "event_id": "e2", "occurred_at": "2026-10-02T00:00:00Z"}]
        report = self.report(rows=rows)
        for row in report["rows"]:
            self.assertIn("repeated_response_identity", row["metrics"]["requests"]["reasons"])
            self.assertIsNone(row["metrics"]["requests"]["difference_provider_minus_app"])

    def test_missing_response_identity_is_explicit(self):
        first = self.report(rows=[{**event(), "response_id": None}])["rows"][0]
        self.assertIn("missing_response_identity", first["metrics"]["requests"]["reasons"])

    def test_legitimate_retry_responses_preserved(self):
        rows = [event(), {**event(), "event_id": "retry", "response_id": "new-response"}]
        first = self.report(rows=rows)["rows"][0]
        self.assertEqual(first["observations"]["app_events"], 2)
        self.assertEqual(first["metrics"]["input_tokens"]["app"], 20)
        self.assertEqual(first["metrics"]["input_tokens"]["difference_provider_minus_app"], -10)

    def test_missing_scope_and_aggregate_grouping_blocked(self):
        report = self.report(app_manifest=manifest())
        self.assertIn("missing_reconciliation_scope", report["blockers"])
        self.assertEqual(report["rows"], [])
        b = bundle(); b["pages"][0]["data"][0]["results"][0].update(project_id=None, model=None)
        pm = scoped("openai_completions_v1")
        pm = type(pm).parse({**pm.value, "group_by": []})
        report = self.report(provider=b, provider_manifest=pm)
        self.assertIn("grouping_mismatch", report["blockers"])

    def test_scope_and_clock_mismatches(self):
        pm = scoped("openai_completions_v1", organization_id="another-org")
        self.assertIn("scope_mismatch", self.report(provider_manifest=pm)["blockers"])
        am = scoped(time_basis="application_clock"); pm = scoped("openai_completions_v1", time_basis="application_clock")
        self.assertIn("unmatched_timestamp_basis", self.report(app_manifest=am, provider_manifest=pm)["blockers"])

    def test_partial_coverage_withholds_present_groups(self):
        pm = scoped("openai_completions_v1")
        pm = type(pm).parse({**pm.value, "coverage": "partial"})
        b = bundle(); b["pages"][0]["data"].pop()
        report = self.report(provider=b, provider_manifest=pm)
        self.assertIn("provider_coverage_not_complete", report["rows"][0]["metrics"]["input_tokens"]["reasons"])
        self.assertIn("provider_bucket_missing", report["rows"][1]["metrics"]["requests"]["reasons"])
        self.assertEqual(report["summary"]["comparable_metrics"], 0)

    def test_late_correction_keeps_old_snapshot(self):
        ids = self.load(); initial = compare(self.db, *ids)
        rows = [event()]; rows[0]["usage"]["input_tokens"] = 12
        later = compare(self.db, *self.load(rows=rows))
        self.assertEqual(initial["rows"][0]["metrics"]["input_tokens"]["difference_provider_minus_app"], 0)
        self.assertEqual(later["rows"][0]["metrics"]["input_tokens"]["difference_provider_minus_app"], -2)
        self.assertEqual(compare(self.db, *ids), initial)

    def test_comparison_readonly_and_invalid_source(self):
        ids = self.load(); before = self.db.read_bytes()
        compare(self.db, *ids); self.assertEqual(self.db.read_bytes(), before)
        with self.assertRaisesRegex(InputError, "wrong_snapshot_source"): compare(self.db, *reversed(ids))
        with self.assertRaisesRegex(InputError, "invalid_snapshot_id"): compare(self.db, "' OR 1=1", ids[1])

    def test_group_cap_before_query_result(self):
        projects = ["demo"] + [f"p{i}" for i in range(19)]
        models = ["model-text"] + [f"m{i}" for i in range(19)]
        # Six days * 400 declared pairs exceeds 2000; use unknown coverage for the shorter provided capture.
        from usage_reconcile.contract import Manifest
        a = scoped(project_ids=projects, models=models); p = scoped("openai_completions_v1", project_ids=projects, models=models)
        a = Manifest.parse({**a.value, "end_utc": "2026-10-07T00:00:00Z", "acquired_at": "2026-10-08T00:00:00Z"})
        p = Manifest.parse({**p.value, "end_utc": "2026-10-07T00:00:00Z", "acquired_at": "2026-10-08T00:00:00Z", "coverage": "partial"})
        with self.assertRaisesRegex(InputError, "comparison_group_limit"):
            self.report(app_manifest=a, provider_manifest=p)

    def test_window_mismatch_never_joins(self):
        from usage_reconcile.contract import Manifest
        pm = scoped("openai_completions_v1")
        pm = Manifest.parse({**pm.value, "end_utc": "2026-10-04T00:00:00Z", "coverage": "partial"})
        report = self.report(provider_manifest=pm)
        self.assertIn("window_mismatch", report["blockers"])
        self.assertEqual(report["rows"], [])

    def test_provider_unknown_cache_and_app_unknown_coverage(self):
        b = bundle(); b["pages"][0]["data"][0]["results"][0]["input_cached_tokens"] = None
        report = self.report(provider=b)
        self.assertEqual(report["rows"][0]["metrics"]["cached_input_tokens"]["status"], "withheld")
        self.assertEqual(report["rows"][0]["metrics"]["input_tokens"]["status"], "conditional_comparison")
        am = scoped(); am = type(am).parse({**am.value, "coverage": "unknown"})
        report = self.report(app_manifest=am)
        self.assertEqual(report["summary"]["comparable_metrics"], 0)
        self.assertIn("app_coverage_not_complete", report["rows"][0]["metrics"]["requests"]["reasons"])
