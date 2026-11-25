# Claims Platform

A Python 3.10+ reference implementation for hospital claims intake, payer-edit evaluation, dispatch, and reconciliation. The reference scope uses SQLite, one synthetic claim per outbound file, and a simulated clearinghouse. Use synthetic data only.

## Run the scenarios

```sh
python -m unittest -v
```

The scenarios cover duplicate intake, a lost receipt after gateway delivery, out-of-order and repeated acknowledgment events, unmatched remittance, effective-dated edits, and rollback with staged claims.

## How it works

- **Intake** stores an immutable canonical JSON revision. A conflicting replay raises an exception.
- **Rules** are immutable JSON bundles. Activating a bundle increments the payer's generation. Dispatch checks that generation transactionally at the point it records a send as potentially in flight. Rollback reactivates an earlier bundle and stale staged deliveries require another scrub.
- **Dispatch** uses a durable row as the outbox. Original submissions are unique by hospital, source claim and payer. Exact synthetic outbound bytes are stored before the network boundary. A timeout leaves delivery unknown. Reconciliation compares the gateway receipt hash with the stored bytes. No blind resend occurs.
- **Inbound** retains normalized 999, 277CA and 835 event envelopes and matches their synthetic reference to a delivery in the payer's scope. Identical event replays are idempotent and conflicting replays are rejected. Unmatched events are retained for review.
- **AI integration** starts with the read-only `Ledger.explain()` evidence surface. A future agent can summarize evidence and route internal work. An authorized human approves coding changes, ambiguous matches, rule publication and resubmissions. No model dependency is required to run the reference.

## Production design targets

| Measure | Target |
| --- | --- |
| Volume | 1.5 million claims/month from 40 hospital EHRs |
| Intake, scrub and staging capacity | 50 claims/sec sustained, 150 claims/sec for 15 minutes |
| Intake availability | 99.95% monthly |
| Durable intake to scrub decision | p95 ≤30 seconds, p99 ≤2 minutes |
| Clean claim to clearinghouse receipt | p95 ≤5 minutes on healthy routes with sufficient partner capacity |
| Inbound processing | p95 ≤60 seconds to match or queue for review |
| Rollback | Within five minutes of approval, with stale staged work fenced |
| First-pass payer acceptance | ≥98% on covered routes within two business days. Missing and rejected first submissions count as misses. |

These are engineering targets for a pilot, not measured performance. Report payer-acknowledgment coverage, held claim age, and straight-through acceptance across all complete claims received alongside the 98% figure.

## Production integration scope

Replace the synthetic envelope and normalized response dictionaries with licensed 5010 translators and trading-partner companion guides. Add real EHR adapters, gateway deduplication and status contracts, effective-dated authoritative edit imports, replacement and void workflows, authorization and audit, durable worker scheduling, operational metrics, a reconciliation work queue, load testing, and controls for PHI. A lost send on a route without verified gateway guarantees requires reconciliation.

