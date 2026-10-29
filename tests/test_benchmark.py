import contextlib
import io
import json
from unittest.mock import patch
import unittest
from usage_reconcile.benchmark import benchmark, _check
from usage_reconcile.cli import main
from usage_reconcile.contract import InputError


class BenchmarkTests(unittest.TestCase):
    def test_bounds_before_allocating_files_or_fixture(self):
        for rows, repeats in ((True, 1), (0, 1), (81, 1), (8080, 1), (80, False), (80, 0), (80, 6)):
            with self.subTest(rows=rows, repeats=repeats), patch("usage_reconcile.benchmark._fixture") as fixture:
                with self.assertRaises(InputError): benchmark(rows, repeats)
                fixture.assert_not_called()

    def test_small_workload_records_verified_results_and_timings(self):
        result = benchmark(rows=80, repeats=1)
        self.assertTrue(result["completed"])
        self.assertEqual(result["workload"]["input_app_records"], 84)
        self.assertEqual(result["workload"]["unknown_usage_events"], 8)
        self.assertEqual(result["workload"]["expected_summary"], {"groups": 8, "comparable_metrics": 20, "withheld_metrics": 12})
        self.assertEqual(len(result["workload"]["dataset_sha256"]), 64)
        for key, median in result["median_ms"].items():
            self.assertGreaterEqual(median, 0)
            self.assertEqual(median, result["samples"][0][key])

    def test_incorrect_measured_result_fails_instead_of_recording_success(self):
        with patch("usage_reconcile.benchmark.compare", return_value={"blockers": ["unexpected"], "summary": {}}):
            with self.assertRaisesRegex(InputError, "benchmark_comparison_mismatch"):
                benchmark(80, 1)

    def test_cli_reports_actual_workload(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(main(["benchmark", "--rows", "80", "--repeats", "1"]), 0)
        self.assertEqual(json.loads(output.getvalue())["workload"]["unique_app_events"], 80)
