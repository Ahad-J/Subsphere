# Architecture Decision Log

## ADR-001 — Two-view UX
Individual (free) and Provider (business). Enterprise purchasing, memberships and SaaS selling are contextual functions of Provider, not additional top-level products.

## ADR-002 — Modular monolith operational core
FastAPI + PostgreSQL handles auth, money, subscriptions and quotas. Kafka/Spark/OpenMeter/LiteLLM remain independent analytical/integration systems. Avoid distributed transactions for paid state.

## ADR-003 — Source-qualified usage
Official/SDK/gateway data may contain verified tokens; browser/desktop tools only observe activity. Unsupported metrics remain null, not zero.

## ADR-004 — OpenMeter is not invoice authority
PostgreSQL enforces hard limit and financial state; accepted events go through outbox to OpenMeter for asynchronous aggregation.

## ADR-005 — Email development vs production
Mailpit is local development. Postal is an independent production SMTP stack with sender domain verification, not an inbox-hosting replacement.

## ADR-006 — Reporting
WeasyPrint converts immutable database report snapshots to PDFs with locked-down asset fetching. Payment receipts only for verified confirmed payments.

## ADR-007 — WhatsApp
Use official Business Platform for automation and verified recipient consent; manual click-to-chat fallback. No unattended Selenium mass sender.

## ADR-008 — Offline Sandbox Compatibility & Production Parity
For air-gapped CI/testing without external PyPI packages (`fastapi`, `pydantic`, `httpx`, `pytest`, `uvicorn`, `alembic`) or WeasyPrint system shared libraries, `backend/` provides drop-in pure-Python interface layers (`backend/pydantic`, `backend/fastapi`, `backend/httpx`, `backend/pytest`, `backend/uvicorn`, `backend/alembic`) plus a `PyCairo` fallback inside `app/reports/renderer.py` that still generates real `%PDF-1.4` documents. All domain logic, SQL queries, cryptographic primitives (`bcrypt`, `PyJWT`, `cryptography`), Decimal math, and relational constraints remain identical, and standard PyPI packages install directly via `backend/requirements.txt` in Docker builds.

## ADR-009 — Dual PostgreSQL / Serialized SQLite Engine
`backend/app/db/engine.py` supports both PostgreSQL (`postgresql://`) and local deterministic `sqlite3` (`sqlite:///`) using identical SQL semantics (`%s` -> `?` parameter binding, `PRAGMA foreign_keys = ON`, `WAL` journal mode, and a thread-serialized `BEGIN IMMEDIATE` write lock) so concurrency stress tests (`B-09`, `T044`) enforce strict zero-overshoot atomicity identically in test sandboxes and production PostgreSQL.

## ADR-010 — Non-Additive AI Cost Separation & Null Token Integrity
`GET /api/v1/ai/usage` computes token value using `decimal.Decimal` (`ROUND_HALF_UP`) against effective-dated `pricing_versions` and returns `subscription_fee_minor`, `reported_extra_usage_minor`, and `estimated_token_value_minor` strictly separated. When any token category (`input_tokens`, `output_tokens`) is omitted by a source or unavailable from activity observers, both database rows and JSON responses preserve `null` rather than coercing missing data to `0`.

## ADR-011 — Logical JSONL/Tarball Backup + Deterministic Medallion Local Engine
`infra/backups/backup_restore.py` exports every database table in deterministic key-sorted order plus object/analytics artifacts into a `.tar.gz` archive with a per-table/per-object SHA-256 `manifest.json` (`T079`). `data-platform/spark/streaming_medallion.py` executes PySpark DataFrame Bronze->Silver->Gold jobs when `pyspark` is available and delegates cleanly to `MedallionPipeline` for single-node local materialization and CI verification.
