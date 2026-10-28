import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from usage_reconcile.cli import main
from usage_reconcile.demo import demo, generate
from usage_reconcile.contract import read_bytes, InputError


class CliTests(unittest.TestCase):
    def test_demo_exact_counts(self):
        result = demo()
        self.assertEqual(result["source"], "synthetic")
        self.assertEqual(result["app"]["quality"]["records"], 6)
        self.assertEqual(result["provider"]["quality"]["records"], 2)
        self.assertEqual(result["reimport"]["inserted"], 0)

    def test_generate_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "new"
            generate(directory); before = (directory / "app.jsonl").read_bytes()
            with self.assertRaises(FileExistsError): generate(directory)
            self.assertEqual((directory / "app.jsonl").read_bytes(), before)

    def test_cli_ingest_and_inspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / "fixture"; generate(folder)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["ingest", str(folder / "app.jsonl"), "--manifest",
                             str(folder / "app-manifest.json"), "--db", str(root / "lab.duckdb")])
            self.assertEqual(code, 0)
            sid = json.loads(output.getvalue())["snapshot_id"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["inspect", "--db", str(root / "lab.duckdb"), "--snapshot", sid]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["source"], "app_events_v1")
            self.assertNotIn("rows", report)

    def test_error_does_not_expose_private_path(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["inspect", "--db", "/missing/PRIVATE_MARKER.duckdb", "--snapshot", "a" * 64])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(json.loads(errors.getvalue()), {"error": "database_missing"})

    def test_read_cap_rejects_before_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small"; path.write_bytes(b"12345")
            with self.assertRaisesRegex(InputError, "input_too_large"): read_bytes(path, cap=4)

    def test_installed_cli_demo(self):
        process = subprocess.run([sys.executable, "-m", "usage_reconcile.cli", "demo"],
                                 text=True, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["provider"]["inserted"], 2)
