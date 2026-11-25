# Implementation backlog and milestone completion gates

This is a **new** repository. The product is not implemented simply because the documents and Docker starter exist.

| Milestone | Required deliverables | Acceptance gate |
|---|---|---|
| M0 Foundation | FastAPI, React, Postgres, Docker Compose core; migrations, auth, CI, clean env, health | Fresh install; auth and tenant test suite |
| M1 Individual | Free account, CRUD, recurring dates, category/currency analytics, calendar, personal notification rules | UC-01–05 including Mailpit SMTP notifications |
| M2 Provider finance | Organizations, roles, contacts, plans, membership, receivables/payables, invoices, manual payments, overdue aging, KPIs | Gym example exactly reconciles; no false paid status |
| M3 Seats + quotas | Enterprise purchase, employee mapping, seats, custom metrics, atomic UsageEvent + counters + idempotency | 10 seat cap; concurrent quota test; provider SDK |
| M4 Documents + comms | Report worker Jinja2/WeasyPrint, MinIO private docs, email, official WhatsApp adapter, Postal config | Verified receipt/tenant control, correct notification states |
| M5 AI and activity | LiteLLM gateway, adapter, capability matrix, optional browser/desktop helpers | Routed request tokens; honest unsupported sources |
| M6 Big Data | PostgreSQL outbox, Kafka, OpenMeter/ClickHouse, Spark/MinIO, reconciled Gold | 10k/100k/1M load + restart validation |
| M7 Production hardening | Encryption/secrets, rate limits, audit, CI/CD, monitoring, backups, responsive UI, usability | Full P0 suite + security tests + verified restore |

## Work packages per milestone

**M0:** set up base packages, Alembic, core migrations, login, secure cookie sessions/CSRF, refresh rotation, scoped context, lint/test pipeline. Replace starter health/info landing with real authentication screens. Seed demo admin only in dev.

**M1:** implement subscription entity+calculator; single-screen create/edit; timeline calendar and reminders; data export/deletion; no activity tracking requirement for using the free tier.

**M2:** invoice states and immutable pricing snapshots; organization/subscriber contacts, email invitations, payment confirmation and finance KPIs; provider-focused responsive screens and test data.

**M3:** externally bought subscriptions, seat pools and mappings; custom gym check-ins and API requests; synchronous row-lock quota enforcement, duplicate event response, service scoped keys, API docs.

**M4:** report templates and renderer sandbox, PDF metadata and private objects, SMTP mailer with durable notification jobs, provider domain verification, official WhatsApp channel adapter with opt-in + secure links. Default all messaging channels to email only until configured.

**M5:** LiteLLM in dedicated optional Compose stack, import by source log ID with replay dedup; optional browser allowlist and desktop foreground app helper; no packet sniffing or unconsented telemetry.

**M6:** outbox worker with per-destination status, Kafka topics, OpenMeter CloudEvents contract and ClickHouse, Spark Bronze/Silver/Gold, checkpoint and backfill, dashboards with freshness stamps, large generated workloads.

**M7:** penetration test focused on tenant/finance/privacy, security scan of images/deps, verified backup restore, SMTP bounce handling, analytics reconciliation alerts, documented production deployment, accessible UX.

## Project structure convention

```text
backend/app/{auth,accounts,organizations,personal,providers,catalog,subscribers,subscriptions,finance,entitlements,metering,integrations,notifications,reports,analytics,audit,db}
backend/tests/{unit,integration,contracts,e2e_fixtures}
frontend/src/{app,pages,features,components,api,styles}
services/{worker,report-worker,desktop-helper,browser-extension}
data-platform/{kafka,spark,openmeter,fixtures,benchmarks}
infra/{compose,config,postal,backups,monitoring,scripts}
docs/{diagrams,ui}
```

Each implementation PR must include domain checks, migration (where applicable), API, frontend, automated tests, API docs and a progress entry. Avoid empty modules or stub routes presented as complete.

## Definition of done for release

**Functional:** both views usable, correct service/plan/subscriber relationships, fee due/overdue, payment confirmation, receipt generation and sending, usage/event/seat tracking, accurate KPI definitions, source-qualified AI usage.

**Architectural:** outbox delivery and replay, OpenMeter+ClickHouse aggregation, Spark/MinIO historical processing, LiteLLM gateway and Postal integration tests, verified split-stack Compose configuration, persistent backups.

**Safety:** reliable permission checks, tenant isolation, no prompt/URL capture, consent-based communication, verified sender domain, safe PDF rendering and secure link expiry.

**Quality evidence:** Playwright user journeys, Pytest API/domain coverage, PDF checks, SQL concurrency tests, performance report, successful disaster restore and implementation limitations disclosed.
