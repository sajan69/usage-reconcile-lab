import copy
import json
import unittest
from usage_reconcile.contract import (Manifest, InputError, app_events, provider_pages,
                                      parse_json, utc, count, MAX_BYTES)


def manifest(source="app_events_v1", **changes):
    return Manifest.parse(dict(schema_version=1, source=source,
        start_utc="2026-10-01T00:00:00Z", end_utc="2026-10-03T00:00:00Z",
        acquired_at="2026-10-04T00:00:00Z", coverage="complete",
        group_by=["project_id", "model"], batch=False, modalities=["text"], **changes))


def event(**changes):
    return dict(event_id="e1", response_id="r1", occurred_at="2026-10-01T00:00:00Z",
                project_id="demo", model="model-text", usage=dict(input_tokens=10,
                output_tokens=2, input_cached_tokens=4), **changes)


def raw_events(rows):
    return ("\n".join(json.dumps(x) for x in rows)).encode()


def bundle():
    result = dict(object="organization.usage.completions.result", input_tokens=10,
                  output_tokens=2, input_cached_tokens=4, num_model_requests=1,
                  project_id="demo", model="model-text", batch=None)
    return dict(pages=[dict(object="page", has_more=False, next_page=None, data=[
        dict(object="bucket", start_time=1790812800, end_time=1790899200, results=[result]),
        dict(object="bucket", start_time=1790899200, end_time=1790985600, results=[])])])


class ContractTests(unittest.TestCase):
    def test_null_and_zero_remain_different(self):
        a, b = event(), event()
        a.update(event_id="missing", usage=None)
        b.update(event_id="zero", usage={"input_tokens": 0, "output_tokens": 0})
        parsed = app_events(raw_events([a, b]), manifest())
        self.assertIsNone(parsed.rows[0]["input_tokens"])
        self.assertEqual(parsed.rows[1]["input_tokens"], 0)
        self.assertIsNone(parsed.rows[1]["input_cached_tokens"])
        self.assertEqual(parsed.quality["unknown_usage_records"], 1)

    def test_exact_projected_duplicate_and_conflict(self):
        a, b = event(), event()
        b["prompt"] = "not retained"
        parsed = app_events(raw_events([a, b]), manifest())
        self.assertEqual(parsed.quality["exact_projected_duplicates"], 1)
        self.assertNotIn("prompt", str(parsed.rows))
        b["usage"]["input_tokens"] = 11
        with self.assertRaisesRegex(InputError, "conflicting_event_id"):
            app_events(raw_events([a, b]), manifest())

    def test_retry_ids_preserved_repeated_response_flagged(self):
        rows = [event() for _ in range(3)]
        rows[1].update(event_id="e2", response_id="r2")
        rows[2]["event_id"] = "e3"
        parsed = app_events(raw_events(rows), manifest())
        self.assertEqual(len(parsed.rows), 3)
        self.assertEqual(parsed.quality["repeated_response_ids"], 1)

    def test_utc_boundary_and_offset(self):
        row = event()
        row["occurred_at"] = "2026-10-03T01:00:00+02:00"
        self.assertIn("2026-10-02T23", app_events(raw_events([row]), manifest()).rows[0]["occurred_at"])
        row["occurred_at"] = "2026-10-03T00:00:00Z"
        with self.assertRaisesRegex(InputError, "outside_window"):
            app_events(raw_events([row]), manifest())

    def test_bad_timestamps(self):
        for value in ("2026-10-01", "2026-10-01T00:00:00", "2026-02-30T00:00:00Z", "1900-01-01T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(InputError): utc(value)

    def test_bad_counts(self):
        for value in (True, -1, 1.0, "5", 10**12 + 1):
            with self.subTest(value=value), self.assertRaises(InputError): count(value)

    def test_cached_subset(self):
        row = event()
        row["usage"]["input_cached_tokens"] = 11
        with self.assertRaisesRegex(InputError, "cached_exceeds"):
            app_events(raw_events([row]), manifest())

    def test_strict_json_entire_document(self):
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff', b'{} trailing'):
            with self.subTest(raw=raw), self.assertRaises(InputError): parse_json(raw)

    def test_size_and_line_caps(self):
        with self.assertRaisesRegex(InputError, "input_too_large"):
            app_events(b" " * (MAX_BYTES + 1), manifest())
        with self.assertRaisesRegex(InputError, "record_too_large"):
            app_events(b'{"x":"' + b"a" * 65536 + b'"}', manifest())
        with self.assertRaisesRegex(InputError, "too_many_records"):
            app_events(b"\n" * 20001, manifest())

    def test_manifest_rejects_unknown_and_unsupported(self):
        base = manifest().value
        for key, value in (("schema_version", True), ("batch", True), ("modalities", ["audio"]),
                           ("group_by", ["model", "model"]), ("coverage", "probably"),
                           ("extra", 1), ("acquired_at", "2026-10-01T00:00:00Z")):
            with self.subTest(key=key), self.assertRaises(InputError):
                Manifest.parse({**base, key: value})

    def test_missing_usage_and_bad_labels(self):
        for change in ({"model": "<script>"}, {"event_id": ""}, {"project_id": "bad\x00"}, {"usage": []}):
            row = event(); row.update(change)
            with self.subTest(change=change), self.assertRaises(InputError):
                app_events(raw_events([row]), manifest())
        row = event(); del row["usage"]
        with self.assertRaisesRegex(InputError, "missing_usage_field"):
            app_events(raw_events([row]), manifest())

    def test_provider_text_totals_and_empty_bucket(self):
        parsed = provider_pages(json.dumps(bundle()).encode(), manifest("openai_completions_v1"))
        self.assertEqual(parsed.rows[0]["input_tokens"], 10)  # cached 4 is already included
        self.assertEqual(parsed.quality["buckets"], 2)
        self.assertEqual(parsed.quality["missing_daily_buckets"], 0)
        self.assertFalse(parsed.quality["capture_attested"])

    def test_provider_null_means_aggregate(self):
        b = bundle(); result = b["pages"][0]["data"][0]["results"][0]
        result.update(project_id=None, model=None)
        m = Manifest.parse({**manifest("openai_completions_v1").value, "group_by": []})
        parsed = provider_pages(json.dumps(b).encode(), m)
        self.assertIsNone(parsed.rows[0]["model"])
        with self.assertRaisesRegex(InputError, "invalid_label"):
            provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))

    def test_provider_partial_and_gaps(self):
        b = bundle(); b["pages"][0].update(has_more=True, next_page="cursor")
        with self.assertRaisesRegex(InputError, "incomplete_provider"):
            provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))
        m = Manifest.parse({**manifest("openai_completions_v1").value, "coverage": "partial"})
        self.assertTrue(provider_pages(json.dumps(b).encode(), m).quality["has_more"])
        b["pages"][0]["data"].pop()
        self.assertEqual(provider_pages(json.dumps(b).encode(), m).quality["missing_daily_buckets"], 1)

    def test_provider_duplicate_and_overlap(self):
        for duplicate_bucket in (True, False):
            b = bundle(); bucket = b["pages"][0]["data"][0]
            if duplicate_bucket: b["pages"][0]["data"].append(copy.deepcopy(bucket))
            else: bucket["results"].append(copy.deepcopy(bucket["results"][0]))
            with self.assertRaises(InputError):
                provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))

    def test_provider_unsupported_media_dimensions_and_missing_totals(self):
        changes = ({"input_audio_tokens": 1}, {"batch": True}, {"service_tier": "flex"},
                   {"user_id": "sensitive"}, {"input_tokens": None}, {"num_model_requests": False},
                   {"object": "organization.usage.images.result"})
        for change in changes:
            b = bundle(); b["pages"][0]["data"][0]["results"][0].update(change)
            with self.subTest(change=change), self.assertRaises(InputError):
                provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))

    def test_provider_subday_and_page_after_final(self):
        b = bundle(); b["pages"][0]["data"][0]["end_time"] -= 1
        with self.assertRaisesRegex(InputError, "unsupported_bucket"):
            provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))
        b = bundle(); b["pages"].append(copy.deepcopy(b["pages"][0]))
        with self.assertRaisesRegex(InputError, "page_after_final"):
            provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))

    def test_cursor_cycle_rejected(self):
        b = bundle()
        b["pages"] = [dict(object="page", data=[], has_more=True, next_page=cursor)
                      for cursor in ("a", "b", "a")]
        m = Manifest.parse({**manifest("openai_completions_v1").value, "coverage": "partial"})
        with self.assertRaisesRegex(InputError, "repeated_cursor"):
            provider_pages(json.dumps(b).encode(), m)

    def test_unknown_provider_cache_stays_null(self):
        b = bundle(); del b["pages"][0]["data"][0]["results"][0]["input_cached_tokens"]
        parsed = provider_pages(json.dumps(b).encode(), manifest("openai_completions_v1"))
        self.assertIsNone(parsed.rows[0]["input_cached_tokens"])
