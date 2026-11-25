# SubSphere 4.0 Verification & Test Results

> All test outcomes and benchmark measurements below reflect actual commands executed in this workspace. No test results, delivery confirmations, or metrics were fabricated.

## 1. Automated Backend & Integration Test Suite (`M0`–`M7`)

- **Command**: `cd backend && python3 -m pytest -v`
- **Outcome**: **30 passed, 0 failed, 0 skipped in 13.92s**
- **Test Modules Covered**:
  - `tests/test_health.py` (`4` tests): Liveness, metadata implemented module inventory, readiness DB failure & success.
  - `tests/unit/test_domain_rules.py` (`3` tests): `T009`, `T011`, `T013` currency-segregated equivalents & one-time exclusion; `T014` anchored calendar month `Jan 31 -> Feb 28/29 -> Mar 31`; `T015` half-open `[start, end)` boundary.
  - `tests/integration/test_m0_auth_tenancy.py` (`6` tests): `T001`–`T007`, `T078`, `alembic` upgrade/downgrade cycle.
  - `tests/integration/test_m1_individual.py` (`3` tests): `T008`, `T010`, `T012`, `T016`–`T020`, `T057`, `T060`, `T071`, `T075` (personal CRUD, opt-in activity `tokens=null`, forbidden privacy field rejection, history deletion, export, idempotent renewal reminders).
  - `tests/integration/test_m2_provider_finance.py` (`4` tests): `T021`–`T035` (immutable plan versions, CRM contact claim invites, RBAC payment checks, idempotent manual payments, overpayment rejection, overdue transitions, FitZone 40-member KPI reconciliation).
  - `tests/integration/test_m3_seats_metering.py` (`2` tests): `T036`–`T046`, `T074`, `B-09` (enterprise seats `10 seats x $12 = $120`, 50-request concurrency cap, wrong-org denial, seat revocation, scoped service API keys, atomic quota consume + dedup + `EVENT_ID_CONFLICT` + `QUOTA_EXCEEDED` + `monitor_only` alert, Python SDK).
  - `tests/integration/test_m4_reports_comms.py` (`2` tests): `T052`–`T056`, `T058`–`T064`, `T070`, `T072`, `T073` (WeasyPrint/PyCairo `%PDF` receipts on paid invoices only, SSRF HTML/webhook blocking, immutable snapshot hash, cross-tenant report denial, single-use expiring share tokens, SMTP `accepted_by_relay` + Postal webhook `delivered`, sender domain DNS gate, WhatsApp consent & `wa.me` fallback).
  - `tests/integration/test_m5_ai_activity.py` (`2` tests): `T047`–`T051`, `Output O5` (LiteLLM connection & capability matrix, credential masking, SSRF endpoint check, `lit-1001` 3x replay dedup, Decimal pricing `$8.00` separated from `$20.00` subscription & `null` reported cost, missing tokens preserved as `null`, desktop helper privacy payload, org identity mapping).
  - `tests/integration/test_m6_data_platform.py` (`1` test): `T065`–`T069`, `T076`, `Output O10` (independent Kafka/OpenMeter outbox outage isolation, Postgres hard-limit continuity during outage, CloudEvents 1.0 schema, Bronze->Silver->Gold Medallion dedup & malformed quarantine, checkpoint replay, freshness lag banner, zero-discrepancy O10 reconciliation).
  - `tests/integration/test_m7_security_backup.py` (`3` tests): `T077` (Compose pinned tags & zero core `profiles`), `T079` (tar.gz logical DB + object backup & clean-target restore SHA-256 parity across all tables and PDF reports), `T080` (built frontend bundle, view switcher accessibility roles, `Unavailable` vs `0` token display, `Recorded Cash Surplus`).

## 2. Frontend Type-Check & Production Build Verification

- **Command**: `cd frontend && npm install --offline --cache /home/abdul-ahad/.npm && npm run build` (`tsc --noEmit && vite build`)
- **Outcome**: Exit code `0`, `built in 275ms`:
  - `dist/index.html` (`0.44 kB`)
  - `dist/assets/index-Br8jONHA.css` (`6.12 kB`)
  - `dist/assets/index-DU29nUGS.js` (`241.85 kB`)

## 3. Modular Docker Compose Validation (`T077`)

- **Command**:
  `POSTGRES_PASSWORD=test_pass POSTAL_SMTP_HOST=postal.example.com POSTAL_API_KEY=postal_test_key SUBSPHERE_DOMAIN=subsphere.example.com docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml -f infra/compose/compose.ai.yaml -f infra/compose/compose.metering.yaml -f infra/compose/compose.analytics.yaml -f infra/compose/compose.observability.yaml -f infra/compose/compose.production.yaml config --quiet`
- **Outcome**: Exit code `0` (`ALL_COMPOSE_VALID`).

## 4. Benchmark Suite (`B-01` through `B-10`)

- **Command**: `python3 data-platform/benchmarks/run_benchmarks.py`
- **Raw Output Artifact**: [latest_results.json](../data-platform/benchmarks/latest_results.json)
- **Machine Specs**: `Linux-7.0.0-42-generic-x86_64-with-glibc2.39`, Python `3.12.3`, `4` logical CPUs, `15.49 GB` RAM, SQLite WAL serialized transactions (`BEGIN IMMEDIATE`).

| Benchmark | Workload | Observed Measurement | Invariant |
|---|---|---|---|
| **B-01** | 10,000 events Bronze→Silver→Gold | `131.05 ms` (`76,305.6 events/s`) | Zero discrepancy: `True` |
| **B-02** | 100,000 events Bronze→Silver→Gold | `1,207.48 ms` (`82,817.1 events/s`) | Zero discrepancy: `True` |
| **B-03** | 1,000,000 events Bronze→Silver→Gold | `10,992.29 ms` (`90,972.9 events/s`) | Zero discrepancy: `True` |
| **B-04** | 100,000 events w/ 10% duplicate storm | `1,307.19 ms` (`76,500.2 events/s`), `10,000` dupes removed | Zero discrepancy: `True` |
| **B-05..B-08** | Outbox OpenMeter outage/recovery + checkpoint replay | `45.28 ms` (`11` Kafka delivered during outage, `11` recovered, `0` replay dupes) | Zero duplicate replay: `True` |
| **B-09** | 20 concurrent quota requests (10 remaining) | `345.95 ms` wall, `p50=238.84 ms`, `p95=277.54 ms` | `10` accepted (`201`), `10` rejected (`409`), `0` overshoot |
| **B-10** | 50 concurrent threads / 200 dashboard queries | `1,764.20 ms` wall, `p50=292.16 ms`, `p95=743.70 ms`, `p99=941.19 ms` | `200` HTTP 200, `0` errors |

## 5. Sandbox Environment Notes & Limitations

- The build/test sandbox had **no running Docker daemon** (`/var/run/docker.sock` absent) and **no outbound internet access**. Consequently:
  - Docker Compose configurations were validated statically via `docker compose ... config`, while live multi-container startup (`docker compose up`) against PostgreSQL 16, Apache Kafka 3.8.1, OpenMeter, Spark 3.5.3, MinIO, LiteLLM, Mailpit, and Postal was not run inside this daemonless sandbox.
  - External provider webhooks and SMTP relays were verified against deterministic in-process adapters and failure injectors without claiming external network delivery.
