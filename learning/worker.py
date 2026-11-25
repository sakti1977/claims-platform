import argparse
import os
import sqlite3
from contextlib import closing

from claims import digest
from learning.ledger import Ledger


class LearningGateway:
    """Local durable gateway keyed by a stable, externally scoped submission ID."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS receipt (id TEXT PRIMARY KEY, hash TEXT)")

    def send(self, key, body):
        checksum = digest(body)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO receipt VALUES (?,?)", (key, checksum))
            if self.status(key) != checksum:
                raise ValueError("Different payload under the same gateway key")

    def status(self, key):
        row = self.db.execute("SELECT hash FROM receipt WHERE id=?", (key,)).fetchone()
        return row[0] if row else None


def run_once(ledger, gateway, *, lose_receipt=False):
    for item in ledger.unknown():
        if ledger.reconcile(item, gateway):
            print(f"Recovered delivery {item['id']} from its existing receipt")
    item = ledger.reserve()
    if item is None:
        return
    gateway.send(item["partner_key"], item["payload"])
    if lose_receipt:
        print(f"Simulated lost receipt for delivery {item['id']}")
        return
    if ledger.reconcile(item, gateway):
        print(f"Received delivery {item['id']}")


def main():
    parser = argparse.ArgumentParser(description="Run one synthetic dispatch/recovery cycle.")
    parser.add_argument("--lose-receipt", action="store_true")
    args = parser.parse_args()
    ledger = Ledger()
    ledger.initialize()
    path = os.environ.get("CLAIMS_GATEWAY_DB", "learning-gateway.db")
    gateway = LearningGateway(path)
    with closing(gateway.db):
        run_once(ledger, gateway, lose_receipt=args.lose_receipt)


if __name__ == "__main__":
    main()
