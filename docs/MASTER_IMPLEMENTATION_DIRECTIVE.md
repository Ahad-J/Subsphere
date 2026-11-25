# SUBSPHERE 4.0 — GREENFIELD MASTER IMPLEMENTATION DIRECTIVE

**Status:** Greenfield specification and starter repository; *not a completed production system*.  
**Objective:** Build a startup-ready, self-hostable subscription, usage and financial intelligence platform with **two user-facing views**: Individual (free) and Provider (business).  
**Approach:** API-first, multi-tenant modular monolith; asynchronous event pipelines and independently deployed open-source integrations.  
**Deliverable:** Working source code, database migrations, frontend, APIs, adapters, container deployment, tests, demonstration data, documentation and benchmarks.

> **Instruction for implementation agents:** Treat this document and its linked companion documents as the authoritative project definition. Start with the checked-in greenfield starter, implement incrementally and verify each phase. Do not treat this specification, illustrative JSON, diagrams or wireframes as already implemented. Do not request clarification on choices covered here. Log unanticipated decisions in `docs/DECISIONS.md`. Never invent data, API capabilities, test results or delivery states.

## 1. Mission and user promise

**Individual view**: Sign up free without a card; add SaaS, AI, software and gaming subscriptions; see monthly and annual spending, a calendar of renewals and upcoming obligations; opt into supported usage tracking from official APIs/exports, a LiteLLM gateway, a browser extension or a desktop foreground-activity helper; receive email reminders; download personal statements. Incomplete or estimated usage must be visibly labeled.

**Provider view**: A business creates an organization, defines plans and services, adds subscriber contacts without requiring them to create accounts, sends invitations to claim accounts, tracks subscription dues and usage, manages purchased enterprise seats and employees, compares receivables to payables, records collections and operating expenses, sends email and opted-in WhatsApp notices, and generates branded PDF receipts/invoices and KPI reports. The interface adapts for a gym, SaaS company, school, agency or enterprise buyer without adding another top-level dashboard.

**Central user promise:** "Know what is due, what was used, what is owed, what was collected, and what you actually spent—in one trustworthy place."

### 1.1 Goals and non-goals

| Goal | Definition of success |
|---|---|
| Convenience | Individual manually adds a subscription in <60 seconds in usability testing; provider adds a subscriber in <90 seconds. These are goals to measure, not achieved results. |
| Financial correctness | Decimal/minor-unit amounts, immutable financial entries, separate *projected* and *realized* metrics, no false payment confirmations. |
| Data accuracy | Request counts and tokens are source-tagged; unobserved app traffic is not called measured token use; duplicate events not double counted. |
| Architectural depth | PostgreSQL + outbox + Kafka + Spark + MinIO, OpenMeter/ClickHouse and LiteLLM integrations demonstrated by measured tests. |
| Portability | Core starts by Compose independently; profiles/independent stacks for AI, metering, analytics, observability, mail. |
| Privacy | Individual browser/app activity never visible to an employer merely through organization membership. |

**Out of scope for first public release:** acquiring or charging customer cards, automatic third-party cancellation, unauthorized account resale, blanket packet sniffing/TLS decryption, WhatsApp Web unattended bulk automation, full mail inbox hosting, tax compliance across jurisdictions, complex proration, Kubernetes, and ML trained recommenders. Commercial integrations may follow via adapters after explicit validation.

### 1.2 Two views, flexible roles

An authenticated user always has a personal Individual workspace; organization owners/admins additionally switch to a Provider workspace. The Provider view handles **selling** and **buying**: for example, a gym sells memberships while a software company buys enterprise licenses and/or sells SaaS subscriptions. A subscriber contact need not have a login; linking to a login requires an accepted invitation.

Accounts may belong to several organizations. Account context is selected by the user but **server-side permissions**, not UI flags, determine access. Organization roles: owner, admin, billing, developer, staff, member. System administrators are a separate platform role and their access is audited.

## 2. Explicit product behavior

### 2.1 Individual journey
1. Register, verify email, personal workspace is provisioned free (no card).
2. Enter subscription name, category, billing amount/currency/cycle, trial or plan, next due date and reminder preferences.
3. Dashboard shows calendar, category spending, monthly equivalent costs by currency and due-soon statuses; one-time game purchases are excluded from recurring totals.
4. Optionally connect LiteLLM, a provider's **authorized** usage API, a permitted export, Chrome extension (allowlisted domains) or desktop companion (allowlisted foreground processes). Any local gateway works only for clients explicitly configured to send requests through it.
5. View source-qualified request, token, session and cost trends. When tokens/costs are unavailable, display `Unavailable`, **not** zero.
6. Receive an SMTP renewal reminder and optionally monthly personal statement; can delete activity, disconnect sources, export data.

### 2.2 Provider — gym membership
1. Business owner creates organization and service `Membership`; plans `Basic PKR 3000/month`, `Premium PKR 5000/month`.
2. Adds a contact subscriber with name/email/phone; optionally sends a claim invitation.
3. Creates membership obligation and schedule. Until confirmed, payment remains unpaid.
4. On due date, unpaid obligation becomes overdue; owner sees aging and receivables; member receives opted-in notification.
5. Staff records gym check-in manually or through permitted SDK event with metric `checkins` (monitor-only or configured cap).
6. Owner confirms payment, which creates immutable payment ledger record, clears outstanding amount and generates a unique receipt PDF.
7. Owner sends email or WhatsApp link to approved recipient; report delivery is tracked honestly (queued/accepted/delivered if verified).

### 2.3 Provider — SaaS application
1. Provider creates API service, versioned plan with 1,000 API requests per billing period (hard limit) and/or token monitor metric.
2. Developer obtains scoped key or SDK; client calls `POST /api/v1/usage/consume` with idempotency key and metric quantity.
3. PostgreSQL transaction authoritatively checks subscription status, quota and period, inserts accepted usage, updates counter and writes outbox event. Retry with identical event ID is a no-op; conflicting payload raises 409.
4. Dispatcher delivers to Kafka and OpenMeter independently; Spark writes Bronze/Silver/Gold data; dashboards show operational counters and freshness-tagged analytics.
5. Overuse is possible only for `monitor_only` metrics. `hard_limit` must reject an over-quota use before committing the usage event.

### 2.4 Provider — enterprise seats
1. Organization buyer records a vendor subscription: 10 seats at $12 each monthly, resulting in $120/month payable. The provider may be external, so create an *externally managed purchase* record, not an internally sold subscription.
2. Invite employees; assign seven seats; show three **unassigned** seats, not "unused employees".
3. Import authorized organization usage keyed by vendor user/team IDs; display observed or verified usage with coverage and last sync date.
4. Business displays renewals, budgets, per-employee attributed spend only where the data source supports attribution. Never harvest employees' private browsing history.

### 2.5 Finance semantics (important)
- `receivable` = a customer owes the business; `payable` = the business owes someone else.
- `invoice` = claim/request, **not** proof of collection.
- `payment` = confirmed settlement with method, actor, timestamp and immutable audit trail.
- `MRR/ARR` = normalized contractual recurring amounts, not cash received.
- `cash_collected` and `cash_paid` = actual confirmed transactions.
- `operating_surplus` = cash collected - recorded cash expenses; not true net profit unless all relevant costs are present. Call it `recorded cash surplus` in the interface.
- Do not convert different currencies without a rate source+timestamp; use integer minor units, Decimal arithmetic.
- `fixed subscription fee`, `reported extra usage charge`, `estimated token value` are three separate amounts; never automatically sum estimated token value into amount due.

## 3. Project-wide architecture choices

### 3.1 Technical baseline
- Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16, Pytest.
- React + TypeScript + Vite + Tailwind + React Router + TanStack Query + Recharts, Playwright/Vitest.
- Kafka (KRaft), PySpark Structured Streaming, MinIO/Parquet, OpenMeter + ClickHouse (required metering profile), LiteLLM proxy (required AI profile), WeasyPrint + Jinja2 (report worker), Mailpit dev SMTP and Postal separate production mail stack.
- Optional browser extension Manifest V3 and desktop helper (Python + psutil, OS-dependent activity adapters); both opt-in and independent of the web app.
- Optional Langfuse (not default: additional infrastructure, privacy considerations). Optional WhatsApp official Business Platform adapter; manual click-to-chat fallback. No default Selenium mass sender.
- Configure component versions via reviewed, pinned locks or digests; do not deploy using mutable `latest` tags. License audit required for production.

### 3.2 Source of truth and two-plane design
**Operational plane:** PostgreSQL owns identities, organizations, service catalog, subscribers, seats, subscription states, balances, payables/receivables, invoices, payments, authoritative hard-limit counters, notification jobs and audit logs. FastAPI controls state transitions; workers implement scheduled renewals/alerts and transactional outbox.

**Analytical plane:** LiteLLM is source for requests **routed through its gateway**; OpenMeter aggregates custom usage events asynchronously in ClickHouse; Kafka transports domain events; Spark normalizes/deduplicates and writes MinIO Parquet Bronze/Silver/Gold; reporting jobs publish cached analytical aggregates to PostgreSQL with refresh timestamps. Analytics may lag and must not enforce hard quota or mark invoices paid.

### 3.3 Integration reliability rules
1. Every accepted authoritative mutation and outbox event are written in the **same** SQL transaction.
2. Outbox delivery is at-least-once: stable event IDs and destination-specific delivery receipts; retries are expected; Kafka and OpenMeter delivery statuses are independent.
3. Normalize all external source records; preserve `source_system`, `source_record_id`, `source_scope`, timestamps, data coverage and reliability; put unique constraints on normalized source IDs.
4. **LiteLLM → SubSphere → OpenMeter** is the default for gateway spend events. No parallel path that double-counts the same request.
5. Separate personal activity observations from provider billable consumption.
6. Internal canonical quantities are integers; currency values integer minor units; per-million-token pricing stored Decimal.
7. Use event-time and processing-time separately; late events have documented watermark/backfill rules. Do not rewrite past invoices silently.

### 3.4 Profiles and physical deployment
- **Core (default)**: backend, frontend, PostgreSQL, scheduler/worker, report-worker (WeasyPrint), Mailpit (dev), reverse proxy.
- **AI**: LiteLLM and a separately scoped PostgreSQL database.
- **Metering**: OpenMeter runtime + ClickHouse + necessary Kafka and optional Redis according to pinned upstream release; follow official stack/worker configuration.
- **Analytics**: Kafka, Spark master/worker as appropriate, MinIO.
- **Observability**: Prometheus/Grafana optional.
- **Mail production**: Postal via its officially managed independent Compose project, reachable over SMTP/TLS; not bundled into core startup.
- Support a single-host Compose demo and later split-host Compose deployments with real DNS/HTTPS/service credentials; Compose service names are not assumed to resolve across hosts.

See `docs/DEPLOYMENT.md` for exact layouts, commands and service responsibility.

## 4. Functionality and requirements catalog

| ID | Requirement | Priority | Acceptance |
|---|---|---|---|
| FR-01 | Registration, login, email verification, password reset | P0 | Validates sessions and rate limits; no card needed |
| FR-02 | Individual free workspace and subscription CRUD | P0 | Subscription persists; upcoming due date computed correctly |
| FR-03 | Monthly-equivalent spending and calendar | P0 | Does not add currencies or one-time purchase to MRR |
| FR-04 | Multi-organization Provider workspaces and RBAC | P0 | Tenant-isolation tests pass |
| FR-05 | Service and immutable plan-version management | P0 | Existing contracts retain accepted pricing |
| FR-06 | Contact subscribers + claim-invitation workflow | P0 | Contacts need no login; linking requires acceptance |
| FR-07 | Provider-sold subscription and state machine | P0 | Explicit allowed transitions and audit trail |
| FR-08 | Payables, receivables, manual confirmed payments | P0 | No auto-success; no duplicate invoice or payment |
| FR-09 | Multi-seat purchase/allocation | P0 | No over-allocation under concurrency |
| FR-10 | Generic metric definitions and usage events | P0 | Works for check-ins, requests, tokens |
| FR-11 | Hard quota atomic enforcement and retries | P0 | No overspend in concurrency test |
| FR-12 | Renewal/inactivity/quota email rules via SMTP | P0 | Mailpit delivery in dev; retry logic |
| FR-13 | WeasyPrint PDF receipts and statement generation | P0 | Amounts reconcile; private expiring links |
| FR-14 | Individual AI gateway (LiteLLM) | P1 | Routed requests yield real token/cost metadata |
| FR-15 | Personal browser and desktop activity | P1 | Opt-in allowlist; no content collection |
| FR-16 | OpenMeter custom aggregate meters | P1 | CloudEvents ingestion; aggregate reconciled |
| FR-17 | Kafka/Spark/MinIO medallion pipeline | P1 | Duplicate/recovery benchmarks pass |
| FR-18 | Verified custom-domain sending with Postal | P1 | Sender verified; delivery evidence preserved |
| FR-19 | Opted-in WhatsApp notices/secure receipt links | P1 | Official API adapter; no unapproved bulk automation |
| FR-20 | Business KPI & projected income/expenditure | P0 | Defined formulas and evidence provenance |
| FR-21 | Public developer API/SDKs | P1 | Python and TypeScript client contract tests |
| FR-22 | Exports, account privacy/delete, audit logs | P0 | Cross-tenant data and secret leakage blocked |

### 4.1 Nonfunctional requirements
- **NFR-01 Security:** least-privilege RBAC, Argon2id, short-lived sessions, secure secrets, webhook HMAC, CSRF protections with cookies, rate limiting, SSRF-safe webhook and PDF asset fetching.
- **NFR-02 Reliability:** subscription operations survive analytics outages, outbox replay, idempotent renewals and notification jobs, periodic reconciliation.
- **NFR-03 Performance targets:** p95 operational read <500 ms in small local fixture, simple mutation <1 s, 10k/100k/1M event workloads benchmarked. Targets, not promises; report hardware and measured results.
- **NFR-04 Privacy:** browser/desktop local observation opt-in; raw URLs/prompts/content/keystrokes/screenshots never collected; explicit source, coverage, retention controls; deletion propagates to derived datasets.
- **NFR-05 Deployment:** fresh clone boots core; profiles independently start/stop; data survives container restart; Postgres backups and verified restores.
- **NFR-06 UX:** Individual add-subscription flow <=4 fields required; Provider overdue list one click away; WCAG-inspired keyboard/tab/contrast testing; mobile-first responsive views.
- **NFR-07 Compliance:** branded sender domain verification, SMTP anti-abuse, WhatsApp consent & approved template route. Do not claim tax or other legal compliance without jurisdiction-specific implementation.

## 5. Use cases and expected outcomes

| ID | Actor | Trigger | Primary flow / successful output | Alternate/failure path |
|---|---|---|---|---|
| UC-01 | Individual | Register | Personal workspace + free entitlement; welcome email queued | Duplicate email: 409; invalid email: 422 |
| UC-02 | Individual | Add SaaS subscription | Appears in list/calendar; monthly equivalent calculated | Invalid date/negative amount: 422 |
| UC-03 | Individual | Connect optional AI tracking | Routed/API/imported usage appears with source/coverage | Unsupported provider: show unavailable; never guess tokens |
| UC-04 | Individual | Renewal approaches | Single queued email at 7/3/1 days by preference | SMTP down: retry; no false `delivered` |
| UC-05 | Individual | Delete personal history | Removes account-scoped activity and derived views | Immutable payment records handled via retention policy |
| UC-06 | Provider owner | Create organization | Provider workspace with appropriate role | Duplicate slug: 409 |
| UC-07 | Provider owner | Create service+plan | Public plan visible; immutable published plan version | Invalid pricing or inactive service: 422/409 |
| UC-08 | Provider staff | Add subscriber contact | Contact and subscription invitation created | Claim requires explicit acceptance; no forced account |
| UC-09 | Subscriber | Subscribe to paid plan | Pending record and invoice; paid only on confirmation | Unpaid: no activation |
| UC-10 | Billing staff | Confirm payment | Paid invoice, payment entry, PDF receipt job, notification | Duplicate confirmation idempotent; conflicting amount 409 |
| UC-11 | Scheduler | Unpaid obligation past due | Overdue receivable, owner alert, opted-in subscriber notice | Paid before scheduler: no overdue change |
| UC-12 | Provider SDK | Consume API units | Atomic usage counter, remaining quota, outbox event | Limit exceeded: 409 or 429 per documented domain error |
| UC-13 | Provider staff | Record gym attendance | Check-in usage and monitor aggregate | Invalid/unowned subscriber: deny |
| UC-14 | Organization buyer | Buy 10 seats | $120 payable at $12 each; 10 available allocations | 11th assignment denied |
| UC-15 | Organization admin | Assign employee | 7 assigned / 3 unassigned dashboard | Wrong-org employee denied |
| UC-16 | Provider owner | Generate report | Accurate branded PDF in private storage | Report worker down: queued status, retry |
| UC-17 | Provider owner | Send WhatsApp receipt | Authorized opted-in recipient receives secure link | Consent missing or provider disabled: fail safely |
| UC-18 | Admin/developer | Configure LiteLLM gateway | Real gateway request attributed to correct account | Gateway outage does not stop core |
| UC-19 | Analytics worker | Replay events after crash | Historical totals reconcile to authoritative unique events | Late/out-of-order events quarantined/backfilled |
| UC-20 | User with multiple orgs | Switch context | Only correct tenant's records shown | Forged resource ID: 403/404 |

**Mandatory expansions for every P0 use case:** in `docs/TEST_PLAN.md`, include setup, authentication scope, test inputs, expected database changes, expected UI output, notification/report events, failure variant and rollback behavior.

## 6. Role permissions (deny by default)

| Action | Individual | Provider owner/admin | Provider billing | Provider staff | Organization member | Platform admin |
|---|---:|---:|---:|---:|---:|---:|
| Manage own subscriptions/history | Own only | Own only | Own only | Own only | Own only | No routine access |
| Manage service catalog/plans | No | Yes | No | No | No | Abuse moderation only |
| Add subscriber contact | No | Yes | Scoped | Scoped | No | No |
| Record manual payment | No | Yes | Yes | No | No | No |
| Allocate organization seats | No | Yes | Scoped | No | No | No |
| View provider financial KPIs | No | Yes | Yes | Limited | No | Aggregates only |
| Submit API metering | Service-key only | Service-key only | No | No | No | No |
| View assigned work subscription | No | Org if authorized | Org if authorized | Own assignment | Own only | No |
| View employee private browser history | Never | Never | Never | Never | Own only | Never |
| Change SMTP sending domain | No | Yes with DNS verification | No | No | No | Ops authorized only |

## 7. Main domain rules and state machines

### 7.1 Subscription
`pending -> trialing|active|canceled`, `trialing -> active|expired|canceled`, `active -> past_due|suspended|canceled|expired`, `past_due -> active|suspended|canceled`, `suspended -> active|canceled`. Only explicit domain services perform transitions. Preserve plan snapshot and history. Paid plans require confirmation; trials may activate limited access without payment.

### 7.2 Billing and obligations
`draft -> issued -> partially_paid|paid|overdue|void`; `overdue -> partially_paid|paid|void`. Multiple partial payments may be supported if database and remaining-balance invariant are correctly implemented; otherwise first release accepts full payment only and rejects partials explicitly. All money is stored as integer minor units with immutable event history; use signed adjustments rather than silent edits.

### 7.3 Renewal anchor
UTC for stored instants; business timezone for presentation/schedule; anchored calendar-month behavior (Jan 31 -> Feb 28 non-leap -> Mar 31), half-open `[start,end)` periods. Do not add fixed 30-day intervals to model monthly renewals.

### 7.4 Source-qualified usage
- `VERIFIED` = official provider report or trusted SDK application record.
- `OBSERVED` = allowlisted browser/desktop activity.
- `IMPORTED` = imported export with provenance.
- `MANUAL` = user/staff entry.
- `ESTIMATED` = derived calculation.
- `UNKNOWN` metric = null, not zero.
Do not merge overlapping data streams unless the deduplication relationship is established.

## 8. Domain data model contract

**Identity**: User, Account, Organization, Membership, Invitation, Role.  
**Sales**: Service, Plan, PlanVersion, MetricDefinition, SubscriberContact, CustomerLink, Subscription, SubscriptionPeriod.  
**Enterprise buying**: ExternalSubscription, SeatPool, SeatAllocation, EmployeeIdentityMapping.  
**Financial**: FinancialObligation(direction RECEIVABLE|PAYABLE), Invoice, InvoiceLine, Payment, Expense, FinancialAdjustment.  
**Usage**: UsageEvent (authoritative), UsageCounter, IntegrationConnection, ExternalUsageRecord, PersonalActivityObservation, PricingVersion.  
**Automation**: NotificationRule, NotificationJob, DeliveryAttempt, Consent, Report, ReportSnapshot, OutboxEvent, OutboxDelivery, AuditLog.  
**Analytics**: MaterializedAnalyticsRun, DailyUsageAggregate, DailyFinanceAggregate.

DB requirements: UUID keys, `tenant_id`/owner relationship for all tenant-owned data, foreign keys, indexes, uniqueness `(service_id, external_event_id)` for authoritative usage and `(source_id, external_record_id)` for imports; partial unique indexes for active seat allocation, unique invoice `(subscription_period_id, invoice_type)`, unique notification logical key. Use Alembic migrations from the start; no schema creation on production web startup.

**Never equate contact person = registered user.** Invitation acceptance is the only account-linking workflow. See `docs/CLASS_AND_DATA_MODEL.md` and diagram files for exact entities and associations.

## 9. API design contract

`/api/v1` JSON; `GET /api/v1/health/live`, `GET /api/v1/health/ready`; OpenAPI docs in development, protect administration in production. Auth via same-site HTTP-only secure cookies and CSRF protection for web, scoped API keys for providers. Every mutation validates actor+tenant+resource. Errors structured as `{code,message,details,request_id}`.

| Family | Essential routes |
|---|---|
| Identity | `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/verify-email`, `/me` |
| Individual | `/personal/subscriptions`, `/personal/usage`, `/personal/calendar`, `/personal/reports`, `/personal/monitoring` |
| Organizations | `/organizations`, `/{id}/members`, `/{id}/invites`, `/{id}/switch-context` (context server validated) |
| Services | `/services`, `/services/{id}/plans`, `/plans/{id}/versions`, `/services/{id}/metrics` |
| Subscribers | `/subscribers`, `/subscribers/{id}`, `/subscribers/{id}/invite`, `/subscribers/{id}/subscriptions` |
| Subscriptions | `/subscriptions`, `/subscriptions/{id}/cancel`, `/{id}/renew`, `/{id}/seats` |
| Usage | `POST /usage/consume`, `GET /usage`, `/entitlements/check`, `/integrations/litellm/sync` |
| Finance | `/finance/obligations`, `/finance/expenses`, `/invoices`, `/invoices/{id}/payments`, `/finance/kpis` |
| Notices | `/notification-rules`, `/notifications`, `/notifications/test`, `/consents` |
| Reports | `/reports`, `/reports/{id}/download`, `/reports/{id}/deliver` |
| Integrations | `/api-keys`, `/connections`, `/domains`, `/domains/{id}/verify`, `/webhooks` |

Explicit service-owned API keys for meter events; idempotency key for payment confirmation, usage consume, subscriber creation, invoice issue, notification scheduling. See `docs/API_CONTRACTS.md` for payloads, sample responses, auth and status codes.

## 10. GUI specification

**Individual screen:** left sidebar (Overview, Subscriptions, Usage, Calendar, Reports, Settings), top summary cards (Spend, Active plans, Due this month, AI token coverage), spending line chart + upcoming renewals, subscription table, monitor source status chips. Add subscription modal with five core required fields (name, amount, currency, billing frequency, next date); advanced optional.

**Provider screen:** context selector, sidebar (Overview, Subscribers & Employees, Services & Plans, Usage, Finance, Calendar, Notifications, Documents, Settings); summary row of Collected/Outstanding/Due/Recorded cash surplus, usage bar charts, overdue table, subscriber actions, "Send reminder" and "Generate receipt". In `Provider Settings` technical integrations are under collapsible Advanced area.

**Reporting:** branded invoices with preview, PDF, authorized recipient, delivery channel, status. **Mobile:** bottom navigation (Overview, Subscriptions/People, Calendar, More); responsive cards rather than horizontal tables.

Exact ASCII wireframes and field-level interactions are in `docs/WIREFRAMES.md`.

## 11. Email, WhatsApp and report safety

**SMTP:** Mailpit local default; Postal production standalone following upstream installer, not an ad hoc invented image. Templates for renewal 7/3/1 days, due/overdue, paid, quota, usage digest. SMTP acceptance is not inbox delivery; use Postal delivery/bounce webhook when configured. Per-business sending domain requires ownership and SPF/DKIM/DMARC verification. Sending-only does **not** create full email inboxes.

**WhatsApp:** official Business Platform through provider adapter and consent + appropriate templates. Manual click-to-chat fallback; no unsupervised Selenium mass sender. Never send unexpired reusable public receipt URLs; use authenticated or single-use short-lived links.

**PDF:** report-worker renders Jinja2 HTML through WeasyPrint, reads immutable snapshot, writes private object to MinIO. Restrict asset fetcher (no arbitrary remote URLs or filesystem leaks), sandbox resources, avoid dynamic remote CSS. `receipt` only for confirmed payment; `statement` may include user-reported/external obligations with clear disclaimer.

## 12. Exact expected demonstration outcomes

1. **Individual:** Add AI Assistant $20/month and Game Pass $12/month; total normalized spend **$32/month** for USD records; calendar shows both renewals. Browser observed 40 minutes activity -> "Observed active time 40 min", tokens "Unavailable".
2. **Gym:** 40 basic members at PKR 3,000/month: **PKR 120,000 invoiced**, 30 paid **PKR 90,000 collected**, 10 unpaid **PKR 30,000 outstanding**; record PKR 15,000 expenses -> **PKR 75,000 recorded cash surplus** (not net profit). PDFs for the 30 confirmed payments only.
3. **Enterprise:** 10 seats × $12 = **$120/month payable**; 7 allocated, 3 unassigned. Employee usage appears only for authorized verified integrations.
4. **SaaS hard quota:** allowance 1,000; counter 999 -> event of 1 succeeds (remaining 0); next event of 1 returns `QUOTA_EXCEEDED` and leaves count 1,000. Same event retry returns existing success without increment.
5. **AI cost:** 2M input tokens @ $2/million + 0.5M output tokens @ $8/million = **$8 estimated usage value**, shown separately from fixed subscription charge and provider-reported bills.
6. **SMTP outage:** payment commit still succeeds; notification `pending/retrying`, later SMTP acceptance marked `accepted_by_relay`, not delivered until evidence.
7. **Analytics outage:** Spark down while operational usage continues. Restart, replay, deduplicate, and match distinct accepted event IDs. Do not claim exactly-once delivery across all sinks without proof.

See `docs/TEST_PLAN.md` for machine-checkable test cases and target outputs.

## 13. Mandatory implementation process (agent instructions)

### Foundation (M0)
Scaffold repo, backend, frontend, Docker Compose core, env templates, migrations, authentication and CI. Gate: clean boot + `/health/ready` verifies database + frontend loads.

### Individual MVP (M1)
Personal free signup, subscription CRUD, calendar, normalized spending, email reminders. Gate: UC-01..04 end-to-end pass.

### Provider MVP (M2)
Organizations, service/plan, contacts, membership, receivable/payable invoices, manual payments, due/overdue, KPIs, simple business dashboard. Gate: gym scenario numbers correct and recipient notices queued.

### Enterprise + metering (M3)
Organization purchased seats, identity mapping, generic metric definitions, atomic hard counters and idempotency. Gate: race test + ten-seat limit pass.

### Communication + reports (M4)
PDF reports, branded receipts, Mailpit SMTP; then independently deployed Postal and sending domain; WhatsApp official adapter when credentials supplied. Gate: payments produce an immutable receipt and correct notification history.

### AI integrations (M5)
LiteLLM and scoped API key/workspace attribution; official provider export adapter; optional browser MV3 and desktop companion. Gate: routed request tokens reported; unrouteable desktop traffic labeled unknown.

### Data platform (M6)
Kafka + outbox, OpenMeter and ClickHouse, Spark/MinIO; Bronze/Silver/Gold, reconciliation, batch reporting. Gate: 10k /100k /1M event experiments with throughput, duplicates and restart results.

### Security & production quality (M7)
Cross-tenant testing, backup-restore, upgrade/rollback, email bounces, monitoring, accessibility, integration tests, playbooks, release evidence. Gate: all P0 cases pass; documented P1 limitations and measured outcomes.

**Every milestone**: (i) implement DB+service+API+UI+tests as a vertical slice, (ii) run checks, (iii) record result/limitations in `docs/PROGRESS.md`, (iv) commit migrations, (v) no claims of success without evidence. A smaller actually tested release is better than an extensive fake UI.

## 14. Integration tests and release gates

- `unit`: domain calculations; money, state transitions, due date recurrence, token pricing, permissions.
- `api`: authentication+RBAC, idempotency, validation, ownership, concurrency.
- `contract`: LiteLLM, OpenMeter CloudEvents and aggregate queries, Postal SMTP/webhooks, WeasyPrint PDF output, WhatsApp API adapter with provider fixture. External account live tests require credentials; mock success is not proof of live integration.
- `e2e`: Playwright individual, gym and enterprise journeys; all values from actual database and jobs, not hardcoded frontend fixtures.
- `chaos`: stop Kafka, Spark, OpenMeter, Postal, PDF worker independently; retry/catch-up and no false states.
- `security`: IDOR across tenants, role escalation, token leakage, arbitrary webhook URL/SSRF, PDF remote fetching, SMTP abuse, personal privacy.
- `load`: 10k, 100k, 1M unique events with duplicate injections; p50/p95/p99, messages/sec, CPU, memory, lag and reconciliation; record machine specs and dataset seed.
- `deploy`: fresh clone, `docker compose config`, `up`, migrations, seed, restart with data persistence, scheduled backups and test restore, distinct single-host and split-stack networking.

Test data, exact expected outputs and pass/fail gate definitions: `docs/TEST_PLAN.md`.

## 15. Deliverable inventory

- Source: backend, frontend, worker, optional extension/desktop helper, integrations, SDKs.
- Database: models, Alembic migrations, foreign keys/indexes and audited access.
- Diagrams: `docs/diagrams` (use cases, HLD, LLD, class, ERD, sequences).
- Wireframes: `docs/WIREFRAMES.md` and implemented responsive screens.
- Integration: LiteLLM, OpenMeter, Kafka/Spark/MinIO, Postal, WeasyPrint adapters.
- Docs: API, data model, deployment, security, ADRs, license inventory, integration contracts, testing, sample outputs.
- Docker: portable core Compose and optional modules + version locks + health checks + backups.
- Evidence: CI run links/logs, request samples, screenshot exports, tests results, performance reports, known limitations.

## 16. Final command to coding agent

**Build a NEW SubSphere project from zero.** Assume no previous implementation exists. Inspect this repository; treat any existing files as an initial scaffold, not completed product functionality. Implement milestones M0–M7 in order. Preserve a single simple two-view UX and isolate optional infrastructure. Honor the financial invariants and data provenance rules. Never make up integration capabilities, test results, tokens, payments or delivery statuses. Build working features and prove them with tests. Record changes in `docs/PROGRESS.md`, run the appropriate gates, and report the exact implemented status. Start with M0.
