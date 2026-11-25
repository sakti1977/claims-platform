import tempfile
import unittest
from pathlib import Path

from claims import Ledger, SimulatedClearinghouse


class ClaimsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.ledger = Ledger(self.path / "ledger.db")
        self.gateway = SimulatedClearinghouse(self.path / "gateway.db")
        self.addCleanup(self.gateway.db.close)
        self.addCleanup(self.ledger.db.close)
        self.ledger.publish("v1", {})
        self.ledger.publish("v2", {"required": ["authorization"]})
        self.ledger.activate("demo-payer", "v1")
        self.claim = {
            "hospital": "demo-hospital", "source": "claim-001", "revision": 1,
            "payer": "demo-payer", "type": "I", "service_date": "2026-11-01",
            "lines": [{"code": "DEMO1", "units": 1, "cents": 12000}],
        }

    def test_timeout_after_forwarding_and_responses(self):
        claim = self.ledger.intake(self.claim)
        self.assertEqual(claim, self.ledger.intake(self.claim))
        with self.assertRaises(ValueError):
            self.ledger.intake(dict(self.claim, type="P"))
        findings, delivery = self.ledger.stage(claim)
        self.assertEqual(findings, [])
        with self.assertRaises(TimeoutError):
            self.ledger.dispatch(delivery, self.gateway, lose_receipt=True)
        with self.assertRaises(ValueError):
            self.ledger.dispatch(delivery, self.gateway)
        self.assertTrue(self.ledger.reconcile(delivery, self.gateway))
        for kind in ("835", "999", "277CA"):
            event = {"sender": "demo-gateway", "id": kind, "kind": kind,
                     "payer": "demo-payer", "reference": delivery, "status": "accepted"}
            self.assertEqual(delivery, self.ledger.receive(event))
            self.assertEqual(delivery, self.ledger.receive(event))
        self.assertEqual(3, self.ledger.db.execute("SELECT count(*) FROM response").fetchone()[0])
        self.assertEqual(1, self.gateway.db.execute("SELECT count(*) FROM receipt").fetchone()[0])

    def test_rollback_fences_staged_work_and_rescrubs(self):
        claim = self.ledger.intake(self.claim)
        _, delivery = self.ledger.stage(claim)
        self.ledger.activate("demo-payer", "v2")
        with self.assertRaises(ValueError):
            self.ledger.dispatch(delivery, self.gateway)
        self.assertEqual(["Missing payer-required field: authorization"],
                         self.ledger.stage(claim)[0])
        self.assertEqual("v2", self.ledger.explain(delivery)["rule_version"])
        self.ledger.activate("demo-payer", "v1")
        self.assertEqual(([], delivery), self.ledger.stage(claim))
        self.ledger.dispatch(delivery, self.gateway)

    def test_unmatched_response_is_saved(self):
        event = {"sender": "demo-gateway", "id": "unmatched", "kind": "835",
                 "payer": "demo-payer", "reference": 99}
        self.assertIsNone(self.ledger.receive(event))
        self.assertEqual(1, self.ledger.db.execute("SELECT count(*) FROM response").fetchone()[0])

    def test_effective_dated_edits(self):
        self.ledger.publish("edits", {"edits": [
            {"id": "units-demo", "type": "units", "code": "DEMO1", "limit": 1,
             "start": "2026-11-01", "end": "2026-11-30"}
        ]})
        self.ledger.activate("demo-payer", "edits")
        self.claim["lines"].append(self.claim["lines"][0])
        findings, _ = self.ledger.stage(self.ledger.intake(self.claim))
        self.assertEqual(["units-demo: units exceed 1"], findings)


if __name__ == "__main__":
    unittest.main(verbosity=2)

