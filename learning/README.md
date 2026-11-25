# PostgreSQL submission lab

Run one synthetic claim through FastAPI, a PostgreSQL ledger and outbox, and a separate dispatch worker. The worker uses a local clearinghouse simulator with durable receipts. Use synthetic data only.

## Run the lab

From the repository root, using Python 3.10+ and Docker Compose:

```sh
python -m pip install -r learning/requirements.txt
docker compose -f learning/compose.yaml up -d --wait
python -m uvicorn learning.api:app
```

Open [Swagger UI](http://127.0.0.1:8000/docs). Submit this body to **POST /claims** and note the returned delivery ID.

```json
{
  "hospital": "demo-hospital",
  "source": "learning-001",
  "revision": 1,
  "payer": "demo-payer",
  "type": "I",
  "service_date": "2026-11-01",
  "lines": [{"code": "DEMO1", "units": 1, "cents": 12000}]
}
```

In another terminal, run the worker and inspect **GET /deliveries/{delivery_id}** after each pass.

```sh
python -m learning.worker --lose-receipt
python -m learning.worker
```

The first pass stores a gateway receipt and leaves the ledger at **unknown**. The next pass reconciles the existing receipt and moves the ledger to **received**. Submitting the same claim JSON returns the existing delivery. A conflicting body for the same revision returns HTTP 409. Change the source ID to run another experiment. Stop the database with `docker compose -f learning/compose.yaml down`.

## Design lessons and scope

- Intake persists the claim revision and dispatch record in a single database transaction. A unique key allows one original submission per hospital, source claim and payer.
- Workers reserve queued work with row locking and record unknown delivery before transport. A worker interruption after reservation can leave no partner receipt. Such items remain unknown for review. The lab only advances an unknown delivery when the matching receipt hash exists.
- A verified receipt means the simulated gateway stored the transmission. Payer acceptance is a separate event. The lab uses a one-claim synthetic 837 envelope and does not implement production EDI, payer scrubbing, EHR adapters, or live AI. The original SQLite prototype covers rule rollback and response matching with synthetic events.
- PostgreSQL itself is the queue for this lab so you can inspect the transaction boundary. An external broker and AI triage can be added as separate lessons after this flow.

## Integration test

With a dedicated empty lab database, set `CLAIMS_RUN_POSTGRES_TEST=1` in your shell and run `python -m unittest -v learning.test_submission`. The test checks replay, conflict handling, and receipt recovery across two worker cycles.
