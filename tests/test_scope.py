import json
import unittest
from usage_reconcile.contract import Manifest, InputError, app_events, provider_pages, canonical, digest
from tests.test_contract import manifest, event, raw_events, bundle


def scoped(source="app_events_v1", **changes):
    context = dict(organization_id="demo-org", project_ids=["demo"], models=["model-text"],
                   time_basis="provider_utc_bucket")
    context.update(changes)
    return Manifest.parse({**manifest(source).value, "reconciliation": context})


class ScopeTests(unittest.TestCase):
    def test_old_identity_preserved(self):
        from usage_reconcile.demo import demo
        result = demo()
        self.assertEqual(result["app"]["snapshot_id"],
                         "77565b81d6fabc63298a9481259916d7d4cac89e5e11e8ace7ab4c7020443d34")
        self.assertNotIn("reconciliation", manifest().value)

    def test_scope_lists_canonicalized(self):
        m = scoped(project_ids=["z", "a"])
        self.assertEqual(m.value["reconciliation"]["project_ids"], ["a", "z"])

    def test_scope_fields_strict(self):
        for changes in ({"project_ids": []}, {"models": ["x", "x"]}, {"project_ids": ["x"] * 21},
                        {"time_basis": "guess"}, {"organization_id": "<script>"}, {"extra": 1}):
            with self.subTest(changes=changes), self.assertRaises(InputError): scoped(**changes)

    def test_app_scope_enforced(self):
        with self.assertRaisesRegex(InputError, "record_outside_declared_scope"):
            app_events(raw_events([event()]), scoped(project_ids=["other"]))

    def test_provider_scope_enforced(self):
        with self.assertRaisesRegex(InputError, "record_outside_declared_scope"):
            provider_pages(json.dumps(bundle()).encode(), scoped("openai_completions_v1", models=["other"]))
