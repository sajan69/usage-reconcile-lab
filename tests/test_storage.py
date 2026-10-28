import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from usage_reconcile import storage
from usage_reconcile.contract import InputError
from tests.test_contract import manifest, event, raw_events, bundle


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "lab.duckdb"
        self.data = self.root / "input.jsonl"
        self.meta = self.root / "manifest.json"
        self.data.write_bytes(raw_events([event()]))
        self.meta.write_text(json.dumps(manifest().value))

    def load(self): return storage.ingest(self.data, self.meta, self.db)

    def test_reimport_and_provenance(self):
        first = self.load(); second = self.load()
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertTrue(second["reused"])
        report = storage.inspect(self.db, first["snapshot_id"])
        self.assertEqual(len(report["raw_sha256"]), 64)
        self.assertEqual(report["manifest"], manifest().value)

    def test_changed_observation_immutable(self):
        first = self.load()
        row = event(); row["usage"]["input_tokens"] = 20
        self.data.write_bytes(raw_events([row])); second = self.load()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM snapshots").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT input_tokens FROM app_events WHERE snapshot_id=?",
                                        [first["snapshot_id"]]).fetchone()[0], 10)

    def test_acquisition_context_changes_identity(self):
        first = self.load()
        self.meta.write_text(json.dumps({**manifest().value, "acquired_at": "2026-10-05T00:00:00Z"}))
        self.assertNotEqual(first["snapshot_id"], self.load()["snapshot_id"])

    def test_validation_before_database_creation(self):
        self.data.write_bytes(b'{"secret":"not leaked", "usage":')
        with self.assertRaises(InputError) as error: self.load()
        self.assertNotIn("secret", str(error.exception))
        self.assertFalse(self.db.exists())

    def test_fault_rolls_back_new_observation(self):
        first = self.load(); self.data.write_bytes(raw_events([event(), {**event(), "event_id": "e2"}]))
        real = storage._insert_rows
        def fail(con, sid, parsed):
            real(con, sid, parsed)
            raise RuntimeError("injected write failure")
        with patch.object(storage, "_insert_rows", fail), self.assertRaises(RuntimeError): self.load()
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT id FROM snapshots").fetchall(), [(first["snapshot_id"],)])
            self.assertEqual(con.execute("SELECT count(*) FROM app_events").fetchone()[0], 1)

    def test_provider_empty_buckets_retained(self):
        self.data.write_text(json.dumps(bundle()))
        self.meta.write_text(json.dumps(manifest("openai_completions_v1").value))
        self.load()
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM provider_buckets").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT count(*) FROM provider_usage").fetchone()[0], 1)

    def test_sensitive_extras_not_stored(self):
        row = event(); row["prompt"] = "SENSITIVE_MARKER"; row["usage"]["private"] = "SENSITIVE_MARKER"
        self.data.write_bytes(raw_events([row])); result = self.load()
        self.assertNotIn("SENSITIVE_MARKER", json.dumps(storage.inspect(self.db, result["snapshot_id"])))
        with storage.connect(self.db, read_only=True) as con:
            self.assertNotIn("SENSITIVE_MARKER", str(con.execute("SELECT * FROM app_events").fetchall()))

    def test_external_sql_file_access_disabled(self):
        self.load(); secret = self.root / "private.csv"; secret.write_text("private\n42\n")
        with storage.connect(self.db, read_only=True) as con, self.assertRaises(Exception):
            con.execute("SELECT * FROM read_csv(?)", [str(secret)]).fetchall()

    def test_missing_readonly_database_not_created(self):
        with self.assertRaisesRegex(InputError, "database_missing"):
            storage.inspect(self.db, "a" * 64)
        self.assertFalse(self.db.exists())

    def test_readonly_inspection_does_not_change_database(self):
        result = self.load(); before = self.db.read_bytes()
        storage.inspect(self.db, result["snapshot_id"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_unknown_database_refused(self):
        with storage.connect(self.db, create=True) as con: con.execute("CREATE TABLE other(x int)")
        with self.assertRaisesRegex(InputError, "unsupported_database"): self.load()
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM other").fetchone()[0], 0)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_database_mode_symlink_and_permissive_files(self):
        self.load(); self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        link = self.root / "link"; link.symlink_to(self.db)
        with self.assertRaisesRegex(InputError, "unsafe_database_path"):
            storage.ingest(self.data, self.meta, link)
        self.db.chmod(0o644)
        with self.assertRaisesRegex(InputError, "unsafe_database_permissions"): self.load()

    def test_null_counts_stored_as_sql_null(self):
        self.data.write_bytes(raw_events([{**event(), "usage": None}, {**event(), "event_id": "zero",
                               "usage": {"input_tokens": 0, "output_tokens": 0}}]))
        self.load()
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT count(input_tokens), count(*) FROM app_events").fetchone(), (1, 2))

    def test_empty_existing_file_never_replaced(self):
        self.db.touch(mode=0o600)
        with self.assertRaisesRegex(InputError, "empty_database_file"): self.load()
        self.assertEqual(self.db.read_bytes(), b"")

    def test_schema_initialization_rolls_back_with_first_import(self):
        with patch.object(storage, "_insert_rows", side_effect=RuntimeError("injected")), self.assertRaises(RuntimeError):
            self.load()
        with storage.connect(self.db, read_only=True) as con:
            self.assertEqual(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall(), [])
        self.assertEqual(self.load()["inserted"], 1)

    def test_unknown_snapshot_and_invalid_identifier(self):
        self.load()
        for identifier, code in (("x' OR TRUE--", "invalid_snapshot_id"), ("0" * 64, "snapshot_missing")):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(InputError, code):
                storage.inspect(self.db, identifier)
