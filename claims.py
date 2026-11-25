import hashlib
import json
import sqlite3
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS bundle (
    version TEXT PRIMARY KEY, rules TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS route (
    payer TEXT PRIMARY KEY, version TEXT NOT NULL REFERENCES bundle(version),
    generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS claim (
    id INTEGER PRIMARY KEY, hospital TEXT NOT NULL, source TEXT NOT NULL,
    revision INTEGER NOT NULL, payer TEXT NOT NULL, body TEXT NOT NULL,
    UNIQUE(hospital, source, revision)
);
CREATE TABLE IF NOT EXISTS scrub (
    id INTEGER PRIMARY KEY, claim INTEGER NOT NULL REFERENCES claim(id),
    version TEXT NOT NULL, generation INTEGER NOT NULL, findings TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery (
    id INTEGER PRIMARY KEY, claim INTEGER NOT NULL UNIQUE REFERENCES claim(id),
    hospital TEXT NOT NULL, source TEXT NOT NULL, payer TEXT NOT NULL,
    scrub INTEGER NOT NULL REFERENCES scrub(id), body TEXT,
    state TEXT NOT NULL,
    UNIQUE(hospital, source, payer)
);
CREATE TABLE IF NOT EXISTS response (
    sender TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
    body TEXT NOT NULL, delivery INTEGER REFERENCES delivery(id),
    PRIMARY KEY(sender, event_id)
);
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def publish(self, version, rules):
        with self.transaction():
            self.db.execute("INSERT INTO bundle VALUES (?, ?)", (version, encode(rules)))

    def activate(self, payer, version):
        with self.transaction():
            self.db.execute(
                """INSERT INTO route VALUES (?, ?, 1)
                ON CONFLICT(payer) DO UPDATE
                SET version=excluded.version, generation=route.generation+1""",
                (payer, version),
            )

    def intake(self, claim):
        key = (claim["hospital"], claim["source"], claim["revision"])
        body = encode(claim)
        with self.transaction():
            self.db.execute(
                """INSERT INTO claim(hospital,source,revision,payer,body)
                VALUES (?,?,?,?,?) ON CONFLICT(hospital,source,revision) DO NOTHING""",
                (*key, claim["payer"], body),
            )
            row = self.db.execute(
                "SELECT id,body FROM claim WHERE hospital=? AND source=? AND revision=?", key
            ).fetchone()
            if row["body"] != body:
                raise ValueError("Conflicting payload for existing source revision")
            return row["id"]

    def stage(self, claim_id):
        with self.transaction():
            row = self.db.execute(
                """SELECT claim.*, route.version, route.generation, bundle.rules
                FROM claim JOIN route USING(payer) JOIN bundle USING(version)
                WHERE claim.id=?""", (claim_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Claim or active payer bundle missing")
            claim = json.loads(row["body"])
            findings = evaluate(claim, json.loads(row["rules"]))
            run = self.db.execute(
                "INSERT INTO scrub(claim,version,generation,findings) VALUES (?,?,?,?)",
                (claim_id, row["version"], row["generation"], encode(findings)),
            ).lastrowid
            previous = self.db.execute(
                "SELECT * FROM delivery WHERE hospital=? AND source=? AND payer=?",
                (row["hospital"], row["source"], row["payer"]),
            ).fetchone()
            if previous and previous["state"] not in ("staged", "held"):
                return findings, previous["id"]
            if previous:
                staged_revision = self.db.execute(
                    "SELECT revision FROM claim WHERE id=?", (previous["claim"],)
                ).fetchone()[0]
                if row["revision"] < staged_revision:
                    raise ValueError("Cannot stage an older revision")
            state = "held" if findings else "staged"
            self.db.execute(
                """INSERT INTO delivery(claim,hospital,source,payer,scrub,state)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(hospital,source,payer) DO UPDATE
                SET claim=excluded.claim,scrub=excluded.scrub,state=excluded.state""",
                (claim_id, row["hospital"], row["source"], row["payer"], run, state),
            )
            delivery_id = self.db.execute(
                "SELECT id FROM delivery WHERE claim=?", (claim_id,)
            ).fetchone()[0]
            return findings, delivery_id

    def dispatch(self, delivery_id, gateway, *, lose_receipt=False):
        with self.transaction():
            row = self.db.execute(
                """SELECT delivery.*, claim.body AS claim_body,
                scrub.generation AS checked, route.generation AS active
                FROM delivery JOIN claim ON claim.id=delivery.claim
                JOIN scrub ON scrub.id=delivery.scrub
                JOIN route ON route.payer=delivery.payer WHERE delivery.id=?""",
                (delivery_id,),
            ).fetchone()
            if row is None or row["state"] != "staged" or row["checked"] != row["active"]:
                raise ValueError("Delivery must be staged under the active rule bundle")
            body = format_demo_837(delivery_id, json.loads(row["claim_body"]))
            self.db.execute(
                "UPDATE delivery SET body=?, state='unknown' WHERE id=?",
                (body, delivery_id),
            )
        gateway.send(delivery_id, body, lose_receipt=lose_receipt)
        self.reconcile(delivery_id, gateway)

    def reconcile(self, delivery_id, gateway):
        receipt = gateway.status(delivery_id)
        with self.transaction():
            row = self.db.execute(
                "SELECT body,state FROM delivery WHERE id=?", (delivery_id,)
            ).fetchone()
            if row and row["state"] == "unknown" and receipt and receipt == digest(row["body"]):
                self.db.execute(
                    "UPDATE delivery SET state='received' WHERE id=?", (delivery_id,)
                )
                return True
            return bool(row and row["state"] == "received")

    def receive(self, event):
        kind = event["kind"]
        if kind not in {"999", "277CA", "835"}:
            raise ValueError("Unsupported response kind")
        key = (event["sender"], event["id"])
        body = encode(event)
        with self.transaction():
            existing = self.db.execute(
                "SELECT * FROM response WHERE sender=? AND event_id=?", key
            ).fetchone()
            if existing and existing["body"] != body:
                raise ValueError("Conflicting response under existing event ID")
            candidate = self.db.execute(
                """SELECT id FROM delivery WHERE id=? AND payer=?
                AND state IN ('unknown','received')""", (event["reference"], event["payer"])
            ).fetchone()
            match = candidate["id"] if candidate else None
            self.db.execute(
                """INSERT INTO response VALUES (?,?,?,?,?)
                ON CONFLICT(sender,event_id) DO UPDATE SET delivery=excluded.delivery""",
                (*key, kind, body, match),
            )
            return match

    def explain(self, delivery_id):
        row = self.db.execute(
            """SELECT scrub.version, scrub.findings FROM delivery
            JOIN scrub ON scrub.id=delivery.scrub WHERE delivery.id=?""", (delivery_id,)
        ).fetchone()
        return {"rule_version": row["version"], "findings": json.loads(row["findings"]),
                "permission": "read-only evidence for exception triage"}


def evaluate(claim, rules):
    findings = []
    for field in rules.get("required", []):
        if not claim.get(field):
            findings.append(f"Missing payer-required field: {field}")
    codes = {line["code"] for line in claim["lines"]}
    for rule in rules.get("edits", []):
        if not rule["start"] <= claim["service_date"] <= rule["end"]:
            continue
        if rule["type"] == "pair" and set(rule["codes"]) <= codes:
            findings.append(f'{rule["id"]}: code pair requires review')
        if rule["type"] == "units":
            units = sum(line["units"] for line in claim["lines"] if line["code"] == rule["code"])
            if units > rule["limit"]:
                findings.append(f'{rule["id"]}: units exceed {rule["limit"]}')
    return findings


def format_demo_837(delivery_id, claim):
    # Synthetic subset only. The simulator accepts this exact representation.
    guide = {"I": "005010X223A2", "P": "005010X222A1"}[claim["type"]]
    segments = [
        f"ST*837*{delivery_id:04d}*{guide}",
        f"BHT*0019*00*{delivery_id}*{claim['service_date'].replace('-', '')}*0000*CH",
        f"CLM*{delivery_id}*{sum(line['cents'] for line in claim['lines']) / 100:.2f}",
    ]
    return "~".join([*segments, f"SE*{len(segments) + 1}*{delivery_id:04d}"]) + "~"


def digest(body):
    return hashlib.sha256(body.encode()).hexdigest()


class SimulatedClearinghouse:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS receipt (id INTEGER PRIMARY KEY, hash TEXT)")

    def send(self, delivery_id, body, *, lose_receipt=False):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO receipt VALUES (?,?)", (delivery_id, digest(body)))
            if self.status(delivery_id) != digest(body):
                raise ValueError("Different payload under same clearinghouse key")
        if lose_receipt:
            raise TimeoutError("Clearinghouse stored file but receipt was lost")

    def status(self, delivery_id):
        row = self.db.execute("SELECT hash FROM receipt WHERE id=?", (delivery_id,)).fetchone()
        return row[0] if row else None

