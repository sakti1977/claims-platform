import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.environ.get("CLAIMS_RUN_POSTGRES_TEST") == "1",
                     "requires the learning PostgreSQL database")
class SubmissionLabTest(unittest.TestCase):
    def test_recovers_original_delivery_after_receipt_loss(self):
        from learning.ledger import Conflict, Ledger
        from learning.worker import LearningGateway, run_once

        ledger = Ledger()
        ledger.initialize()
        body = {
            "hospital": "demo", "source": uuid4().hex, "payer": "demo", "revision": 1,
            "type": "I", "service_date": "2026-11-01",
            "lines": [{"code": "DEMO1", "units": 1, "cents": 100}],
        }
        delivery = ledger.accept(body)
        self.assertEqual(delivery, ledger.accept(body))
        with self.assertRaises(Conflict):
            ledger.accept(dict(body, type="P"))
        # Use a dedicated fresh database for this worker test. It reserves the oldest ready item.
        with tempfile.TemporaryDirectory() as folder:
            gateway = LearningGateway(Path(folder) / "gateway.db")
            with closing(gateway.db):
                run_once(ledger, gateway, lose_receipt=True)
                self.assertEqual("unknown", ledger.get(delivery["id"])["state"])
                run_once(ledger, gateway)
                self.assertEqual("received", ledger.get(delivery["id"])["state"])
                self.assertEqual(1, gateway.db.execute("SELECT count(*) FROM receipt").fetchone()[0])
