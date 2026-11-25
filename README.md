# Claims Platform

A Python reference implementation for hospital claims intake, payer-rule scrubbing, clearinghouse dispatch, and response reconciliation.

The project demonstrates durable claim ingestion, effective-dated rule bundles, stale-work safeguards, idempotent response processing, and recovery from lost clearinghouse receipts using SQLite and synthetic data.

## Features

- Durable, canonical claim revisions with duplicate and conflict detection
- Versioned payer-rule bundles with payer-specific route generations
- Required-field and effective-dated edit evaluation
- Transactional staging and dispatch fencing
- Synthetic 837 payload generation
- Lost-receipt reconciliation through a simulated clearinghouse
- Idempotent processing of 999, 277CA, and 835 responses
- Read-only evidence explanations for exception triage
- An interactive browser-based workflow replay

## Requirements

- Python 3.10 or newer

This implementation has no third-party runtime dependencies.

## Run the tests

```sh
python -m unittest -v
```

The test suite covers:

- Duplicate and conflicting claim intake
- Lost clearinghouse receipts and reconciliation
- Repeated and unmatched response events
- Payer-rule changes and staged-work rollback
- Effective-dated edit rules

## Example

```python
from claims import Ledger, SimulatedClearinghouse

ledger = Ledger("ledger.db")
clearinghouse = SimulatedClearinghouse("gateway.db")

ledger.publish("v1", {})
ledger.activate("demo-payer", "v1")

claim_id = ledger.intake({
    "hospital": "demo-hospital",
    "source": "claim-001",
    "revision": 1,
    "payer": "demo-payer",
    "type": "I",
    "service_date": "2026-11-01",
    "lines": [{"code": "DEMO1", "units": 1, "cents": 12000}],
})

findings, delivery_id = ledger.stage(claim_id)
if not findings:
    ledger.dispatch(delivery_id, clearinghouse)
```

## Repository layout

- `claims.py` — SQLite ledger, scrubber, dispatcher, response processor, and clearinghouse simulator
- `test_claims.py` — scenario-based unit tests
- `docs/design.md` — implementation and production design details
- `docs/claims-workflow.html` — interactive workflow replay

Open `docs/claims-workflow.html` directly in a browser to replay clean, timeout, and rollback scenarios.

## Documentation

- [Design and production targets](docs/design.md)
- [Claims workflow replay](docs/claims-workflow.html)

## Scope

This repository is a reference and simulation. It does not contain a production EHR integration or a complete X12 5010 implementation. Real deployments should replace the synthetic format and response dictionaries with licensed translators and trading-partner companion guides.
