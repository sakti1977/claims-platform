import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from claims import digest, format_demo_837


SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_claim (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hospital text NOT NULL,
    source text NOT NULL,
    payer text NOT NULL,
    revision integer NOT NULL,
    body jsonb NOT NULL,
    UNIQUE (hospital, source, payer, revision)
);
CREATE TABLE IF NOT EXISTS learning_delivery (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id bigint NOT NULL REFERENCES learning_claim(id),
    hospital text NOT NULL,
    source text NOT NULL,
    payer text NOT NULL,
    partner_key text NOT NULL UNIQUE,
    payload text NOT NULL,
    state text NOT NULL CHECK (state IN ('ready','unknown','received')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (hospital, source, payer)
);
CREATE INDEX IF NOT EXISTS learning_delivery_work
    ON learning_delivery (state, id);
"""


class Conflict(ValueError):
    pass


class Ledger:
    def connect(self):
        url = os.environ.get(
            "CLAIMS_DATABASE_URL", "postgresql://claims:claims@127.0.0.1:5432/claims"
        )
        return psycopg.connect(url, row_factory=dict_row)

    def initialize(self):
        with self.connect() as db:
            db.execute(SCHEMA)

    def accept(self, body):
        key = (body["hospital"], body["source"], body["payer"])
        with self.connect() as db:
            claim = db.execute(
                """INSERT INTO learning_claim(hospital,source,payer,revision,body)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (hospital,source,payer,revision) DO NOTHING
                RETURNING id,body""",
                (*key, body["revision"], Jsonb(body)),
            ).fetchone()
            if claim is None:
                claim = db.execute(
                    """SELECT id,body FROM learning_claim
                    WHERE hospital=%s AND source=%s AND payer=%s AND revision=%s""",
                    (*key, body["revision"]),
                ).fetchone()
            if claim["body"] != body:
                raise Conflict("Existing source revision has different claim contents")
            delivery = db.execute(
                """INSERT INTO learning_delivery
                    (claim_id,hospital,source,payer,partner_key,payload,state)
                VALUES (%s,%s,%s,%s,%s,%s,'ready')
                ON CONFLICT (hospital,source,payer) DO NOTHING
                RETURNING id,claim_id,state""",
                (claim["id"], *key, uuid4().hex, format_demo_837(claim["id"], body)),
            ).fetchone()
            if delivery is None:
                delivery = db.execute(
                    """SELECT id,claim_id,state FROM learning_delivery
                    WHERE hospital=%s AND source=%s AND payer=%s""", key
                ).fetchone()
            if delivery["claim_id"] != claim["id"]:
                raise Conflict("An original submission already exists for this claim and payer")
            return delivery

    def get(self, delivery_id):
        with self.connect() as db:
            return db.execute(
                "SELECT id,claim_id,state FROM learning_delivery WHERE id=%s",
                (delivery_id,),
            ).fetchone()

    def reserve(self):
        # Commit the unknown state before invoking transport.
        with self.connect() as db:
            return db.execute(
                """WITH next AS (
                    SELECT id FROM learning_delivery WHERE state='ready'
                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE learning_delivery AS d SET state='unknown'
                FROM next WHERE d.id=next.id
                RETURNING d.id,d.partner_key,d.payload"""
            ).fetchone()

    def unknown(self):
        with self.connect() as db:
            return db.execute(
                """SELECT id,partner_key,payload FROM learning_delivery
                WHERE state='unknown' ORDER BY id"""
            ).fetchall()

    def reconcile(self, delivery, gateway):
        receipt = gateway.status(delivery["partner_key"])
        if receipt is None or receipt != digest(delivery["payload"]):
            return False
        with self.connect() as db:
            db.execute(
                "UPDATE learning_delivery SET state='received' WHERE id=%s AND state='unknown'",
                (delivery["id"],),
            )
        return True
