import contextlib
import copy
import csv
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from usage_reconcile.analysis import compare
from usage_reconcile.cli import main
from usage_reconcile.contract import InputError
from usage_reconcile.reports import export, render, _cell
from tests import test_analysis


class Tags(HTMLParser):
    def __init__(self): super().__init__(); self.tags = []; self.attrs = []
    def handle_starttag(self, tag, attrs): self.tags.append(tag); self.attrs.extend(attrs)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_analysis.AnalysisTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ids = self.fixture.load()
        self.report = compare(self.fixture.db, *self.ids)

    def test_formats_preserve_unknown_zero_context(self):
        self.assertEqual(json.loads(render(self.report, "json"))["rows"], self.report["rows"])
        rows = list(csv.DictReader(io.StringIO(render(self.report, "csv").decode())))
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[1]["difference_provider_minus_app"], "0")
        self.assertEqual(rows[5]["difference_provider_minus_app"], "")
        self.assertIn("provider_group_unobserved", rows[5]["reasons"])
        self.assertEqual(rows[0]["app_snapshot"], self.ids[0])
        self.assertIn("not provider attestation", rows[0]["warnings"])
        self.assertIn("Unknown / unobserved", render(self.report, "html").decode())

    def test_html_payload_is_escaped_and_no_active_content(self):
        report = copy.deepcopy(self.report)
        report["rows"][0]["project_id"] = '<img src=x onerror="alert(1)">'
        report["warnings"].append('</pre><script>alert(1)</script>')
        raw = render(report, "html").decode(); parser = Tags(); parser.feed(raw)
        self.assertIn("&lt;img", raw)
        self.assertNotIn("img", parser.tags)
        self.assertNotIn("script", parser.tags)
        self.assertFalse(any(key.startswith("on") for key, value in parser.attrs))
        self.assertIn("default-src &#x27;none&#x27;", raw.replace("'", "&#x27;"))

    def test_csv_formula_guard_including_unicode_and_whitespace(self):
        for value in ("=1+1", " +SUM(A1)", "-1+2", "@SUM(A1)", "\tplain", "＝1+1"):
            with self.subTest(value=value): self.assertEqual(_cell(value), "'" + value)
        report = copy.deepcopy(self.report); report["rows"][0]["project_id"] = "=1+1"
        rows = list(csv.DictReader(io.StringIO(render(report, "csv").decode())))
        self.assertEqual(rows[0]["project_id"], "'=1+1")
        self.assertEqual(_cell(-2), -2)  # numeric difference, not an untrusted label

    def test_exclusive_writes_symlinks_and_mode(self):
        root = self.fixture.root; out = root / "report.json"
        result = export(self.report, out, "json")
        self.assertEqual(result["bytes"], len(out.read_bytes()))
        if os.name == "posix": self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): export(self.report, out, "json")
        if os.name == "posix":
            link = root / "link"; link.symlink_to(out)
            with self.assertRaises(FileExistsError): export(self.report, link, "json")

    def test_report_caps_before_file_creation(self):
        out = self.fixture.root / "too-large.html"
        with patch("usage_reconcile.reports.MAX_REPORT_BYTES", 10), self.assertRaisesRegex(InputError, "report_too_large"):
            export(self.report, out, "html")
        self.assertFalse(out.exists())
        report = copy.deepcopy(self.report); report["rows"] *= 1001
        with self.assertRaisesRegex(InputError, "comparison_group_limit"): render(report, "csv")

    def test_blocked_report_still_explains_context(self):
        report = self.fixture.report(app_manifest=__import__('tests.test_contract', fromlist=['manifest']).manifest())
        rows = list(csv.DictReader(io.StringIO(render(report, "csv").decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "blocked")
        self.assertIn("missing_reconciliation_scope", render(report, "html").decode())

    def test_cli_compare_and_export(self):
        args = ["--db", str(self.fixture.db), "--app", self.ids[0], "--provider", self.ids[1]]
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(main(["compare", *args]), 0)
        self.assertEqual(json.loads(out.getvalue())["summary"]["groups"], 2)
        for fmt in ("json", "csv", "html"):
            output = self.fixture.root / ("output." + fmt)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["export", *args, "--format", fmt, "--out", str(output)]), 0)
            self.assertGreater(output.stat().st_size, 100)
