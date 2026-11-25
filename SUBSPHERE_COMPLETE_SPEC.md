# SUBSPHERE 4.0 — COMPLETE GREENFIELD IMPLEMENTATION SPECIFICATION

Single-file consolidation of the project directive and its companion documents. Editable diagram source files and starter code are included in the ZIP package.

**Status:** Engineering specification + basic scaffold; not a completed application.

## Contents
1. MASTER IMPLEMENTATION DIRECTIVE — `docs/MASTER_IMPLEMENTATION_DIRECTIVE.md`
2. CLASS AND DATA MODEL — `docs/CLASS_AND_DATA_MODEL.md`
3. WIREFRAMES — `docs/WIREFRAMES.md`
4. API CONTRACTS — `docs/API_CONTRACTS.md`
5. OPEN SOURCE INTEGRATION CONTRACTS — `docs/OPEN_SOURCE_INTEGRATIONS.md`
6. DOCKER DEPLOYMENT — `docs/DEPLOYMENT.md`
7. TEST PLAN — `docs/TEST_PLAN.md`
8. SAMPLE OUTPUTS — `docs/SAMPLE_OUTPUTS.md`
9. ROADMAP — `docs/ROADMAP.md`
10. ARCHITECTURE DECISIONS — `docs/DECISIONS.md`

---

# PART 1: MASTER IMPLEMENTATION DIRECTIVE

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


---

# PART 2: CLASS AND DATA MODEL

# Class diagram, entity relations and database invariants

**Editable diagrams:** `diagrams/04_domain_class.mmd` (domain classes), `diagrams/05_core_erd.mmd` (ER relationships). Render Mermaid in GitHub or a Mermaid-enabled editor. The classes describe the target implementation, not classes already written in the starter.

## Class responsibilities

| Class / interface | Responsibility | Key invariant |
|---|---|---|
| `User` / `Account` | Authentication principal / subscription-owning identity | Each user has one personal account, can have multiple organization memberships |
| `Organization` / `Membership` | Provider/buyer tenancy and permissions | Access scoped to authenticated organization context |
| `SubscriberContact` | Provider CRM record without a login | May link to User only through accepted verified invitation |
| `Service` / `PlanVersion` | Sellable service with immutable published pricing and limits | Active subscription retains accepted plan version |
| `Subscription` / `SubscriptionPeriod` | Lifecycle and half-open billing periods | No overlapping periods; renewal idempotent |
| `FinancialObligation` / `Invoice` / `Payment` | Receivable/payable, invoice snapshot, confirmed settlement | Outstanding = issued amount - confirmed payments - adjustments |
| `SeatAllocation` | Binds licensed org member to a seat | Active assignments <= purchased seat quantity |
| `MetricDefinition` / `UsageEvent` / `UsageCounter` | Generic metric, accepted atomic events, current limit | One event ID within service never counted twice |
| `PersonalSubscription` / `ActivityObservation` | User-recorded costs and observational telemetry | Observed usage cannot become provider-billable event |
| `NotificationRule` / `NotificationJob` | Reminder policy and durable dispatch | Logical notification key prevents duplicate sends |
| `Report` / `ReportRenderer` | Immutable report snapshots + PDF serialization | Paid receipt requires confirmed payment |
| `IntegrationConnection` / `UsageSourceAdapter` | Provider/API/gateway/CSV integrations | Unknown metrics remain null; source ID dedup |
| `OutboxEvent` | Transaction-linked event awaiting delivery | Destinations tracked separately; at-least-once semantics |

## Key tables and constraints

**Identity and catalog:** `users(email unique)`, `accounts(owner_user_id,kind)`, `organizations(account_id unique,slug unique)`, `memberships(organization_id,user_id unique)`, `subscriber_contacts(organization_id,email nullable)`, `invitations(token_hash unique,expires_at)`, `services(organization_id,slug unique pair)`, `plans(service_id)`, `plan_versions(plan_id,version unique pair)`, `metrics(service_id,key unique pair)`, `plan_metric_limits(plan_version_id,metric_id unique pair)`.

**Subscriptions:** `subscriptions(buyer_account_id,provider_org_id,plan_version_id,quantity,status,anchor,period_start,end)`, `subscription_periods(subscription_id,start,end, unique)`, `seat_allocations(subscription_id,membership_id,assigned_at,revoked_at)` with partial unique active assignment. Also distinct `external_subscriptions` for purchases outside platform and `personal_subscriptions` for individual manually tracked services.

**Finance:** `financial_obligations(organization_id,direction,amount_minor,currency,due_at,status)`, `invoices(obligation_id,document_number,total_minor,currency,snapshot_json)`, `invoice_lines(invoice_id,qty,unit_price_minor,total_minor)`, `payments(invoice_id,amount_minor,status,external_ref,recorded_by)`, `expenses(organization_id,currency,amount_minor,paid_at,category)`, `financial_adjustments(reference,amount_minor,reason)`.

**Telemetry:** `usage_events(service_id,external_event_id unique pair,subscription_period_id,metric_id,quantity,source,occurred_at)`, `usage_counters(subscription_period_id,metric_id unique pair,consumed)`, `integration_connections(account_id,provider_code,credential_ref)`, `external_usage_records(connection_id,external_record_id unique pair,counts nullable,price nullable,coverage)`, `personal_activity(account_id,personal_subscription_id,source,observed_active_seconds nullable,visit_count nullable)`.

**Automation:** `notification_rules(owner_scope,rule_type,channel,schedule_offset)`, `notification_jobs(logical_key unique,recipient,scheduled_at,status)`, `notification_attempts(job_id,provider_msg_id,result)`, `consents(recipient,channel,scope,granted_at,revoked_at)`, `reports(report_type,owner_scope,snapshot_hash,object_key,status)`, `audit_logs(actor,tenant,action,resource_id,ts)`, `outbox_events(id,event_type,payload)`, `outbox_deliveries(event_id,destination unique pair,status,attempts)`.

**Financial amounts:** PostgreSQL `BIGINT` minor units for USD/PKR, amounts nonnegative for invoice/payment/expense records; use signed adjustments for reversals. Prices per token use fixed-precision `NUMERIC` and dated pricing versions. Currency normalization requires an exchange-rate table with source+timestamp or display by currency.

**DB-side rules:** Always use foreign keys, unique constraints and appropriate indexes. Domain service changes occur in database transactions. DB rejects duplicated event identity. Use row-lock/conditional update for concurrent quota. Immutable historical payment entries are audited and reversed, not overwritten.


---

# PART 3: WIREFRAMES

# Screen designs and wireframes — desktop and mobile

**Design language:** Clean financial SaaS, off-white/slate backgrounds, high-contrast navy text, one primary accent, card layout, 8-point spacing grid; 14–16px body text, 24–30px section titles, clear status chips. User can switch Individual ↔ Provider in header only if they are authorized. Charts need screen-reader descriptions, downloadable tabular equivalents and visible "data updated" timestamps. No fake data in production screens.

## WF-01 — Sign up / workspace

```text
┌─────────────────────────────────────────────────────────────┐
│  ◈ SUBSPHERE                                    Sign In     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│          YOUR SUBSCRIPTIONS. ONE CLEAR PICTURE.            │
│                                                             │
│          Email      [________________________]             │
│          Password   [________________________] [show]      │
│          [ Create free personal account ]                  │
│                                                             │
│       No card needed. Personal plan is free.                │
│                                                             │
│      Already registered? Sign in                            │
└─────────────────────────────────────────────────────────────┘
```

**Interactions:** email/password validation, verification link, accessible errors, no organization creation or credit card prompts. After login, `/individual` unless user explicitly selected Provider and has permission.

## WF-02 — Individual dashboard `/individual`

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ ◈ SubSphere            [ Individual ▾ ]                   🔔    Search   Profile ▾       │
├──────────────────┬──────────────────────────────────────────────────────────────────────┤
│ Overview         │  GOOD EVENING                                      [+ Add Subscription]│
│ Subscriptions    │  Overview · November 2026                                                │
│ Usage & AI       │  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐ ┌───────────────┐     │
│ Calendar         │  │ ACTIVE      │ │ MONTHLY EQ. │ │ DUE SOON     │ │ AI USAGE      │     │
│ Reports          │  │ 5           │ │ $64 / PKR.. │ │ 2 renewals   │ │ Partial data  │     │
│ Settings         │  └─────────────┘ └─────────────┘ └──────────────┘ └───────────────┘     │
│                  │  ┌───────────────────────────┐ ┌─────────────────────────────────┐     │
│                  │  │ SPENDING BY MONTH         │ │ NEXT RENEWALS                   │     │
│                  │  │  line chart               │ │ Cursor   Nov 22    $20          │     │
│                  │  │                           │ │ Game Pass Dec 01    $12          │     │
│                  │  └───────────────────────────┘ └─────────────────────────────────┘     │
│                  │  ┌────────────────────────────────────────────────────────────────┐     │
│                  │  │ MY SERVICES                     Source / Coverage  Next Due   │     │
│                  │  │ Claude    Monthly $20   Active   Official API      Dec 12     │     │
│                  │  │ Cursor    Monthly $20   Active   Desktop observed  Nov 22     │     │
│                  │  │ Game Pass Monthly $12   Active   Manual            Dec 01     │     │
│                  │  └────────────────────────────────────────────────────────────────┘     │
└──────────────────┴──────────────────────────────────────────────────────────────────────┘
```

**Behaviors:** values separated by currency; monthly equivalent distinct from actual cash charges. Status `Unknown` for missing usage, not `0`. Due-soon card opens filtered calendar. View has a meaningful empty state: "Add your first subscription".

## WF-03 — Add/edit subscription drawer `/individual/subscriptions/new`

```text
┌────────────────────────────────────────────┐
│ Add Subscription                       ✕  │
│ Service name* [Cursor______________]       │
│ Category      [AI / Developer ▾]          │
│ Plan          [Pro_________________]       │
│ Price*        [20.00____] [USD ▾]          │
│ Billing*      [Monthly ▾]                 │
│ Next due*     [2026-12-12 📅]             │
│ [▸ Additional details]                     │
│   Auto-renew  [on/off]                     │
│   Monitoring  [Not connected ▾]           │
│   Reminder    [7][3][1] days              │
│                                            │
│ [Cancel]             [Save Subscription]   │
└────────────────────────────────────────────┘
```

Four hard-required inputs: service name, price, interval, next due date; currency is defaulted but editable. Free accounts can set price to 0. Trial type reveals trial ending date. Existing subscriptions retain their own pricing history on edits.

## WF-04 — AI and activity usage `/individual/usage`

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ AI & SOFTWARE USAGE                                  [Connect a source]      │
│ Time window [Last 30 days ▾]   Provider [All ▾]   Source [All ▾]            │
├──────────────────────────────────────────────────────────────────────────────┤
│ Requests:  842 (Verified)      Tokens: 2.3M (Verified)                     │
│ Observed active hours: 14.5 (Estimated from foreground activity)          │
│ Reported usage charges: $3.20      Subscription fees: $40.00               │
│ Estimated token value: $6.10      NOT additional amount due                │
├────────────────────────────────┬─────────────────────────────────────────────┤
│ Requests / tokens chart        │ Source coverage                            │
│ per day and per model          │ LiteLLM     Connected / 2 min ago          │
│                                │ Browser     Partial coverage              │
│                                │ Cursor app  Foreground only, no tokens    │
├────────────────────────────────┴─────────────────────────────────────────────┤
│ ACTIVITY RECORDS: Model / time / tokens / reported cost / source / quality │
│ ...                                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Each number has source tooltip; unknown is `—`, not zero. Personal browser and desktop monitoring opt in via per-source controls. No raw prompt, response, keystroke or full URL capture.

## WF-05 — Calendar and notifications `/individual/calendar`

```text
┌─────────────────────────────────────────────────────────────────────┐
│ NOVEMBER 2026                      [‹] November [›]    [+ Reminder] │
│ Sun    Mon    Tue    Wed    Thu    Fri    Sat                         │
│                          1      2      3      4                     │
│ 5      6      7      8      9     10     11                          │
│ 12     13     14     15     16     17     18                          │
│ 19     20     21  [22 Cursor $20] ...                                 │
│                                                                     │
│ SELECTED EVENT  Cursor Pro                                            │
│ Next charge: Nov 22 | Reminder: 7 days, 3 days, 1 day | Email On     │
│ [Edit] [Send test reminder to verified email]                       │
└─────────────────────────────────────────────────────────────────────┘
```

Calendar is visual projection, not payment ledger. Mark externally managed renewals as user-recorded. Do not mark payments complete on calendar event passage.

## WF-06 — Provider overview `/provider/:orgId`

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ◈ SubSphere     [ FitZone Gym · Provider ▾ ]          🔔   Export    Owner ▾           │
├──────────────────────┬───────────────────────────────────────────────────────────────────┤
│ Dashboard            │ PERFORMANCE                            [Add Subscriber] [Invoice]│
│ Subscribers          │ ┌────────────┐┌────────────┐┌────────────┐┌────────────┐          │
│ Services & Plans     │ │ COLLECTED  ││ OUTSTANDING││ PAYABLES   ││ CASH SURPLUS│          │
│ Usage                │ │ PKR 90,000 ││ PKR 30,000 ││ PKR 15,000 ││ PKR 75,000 │          │
│ Finance              │ └────────────┘└────────────┘└────────────┘└────────────┘          │
│ Calendar             │ ┌────────────────────────────┐┌──────────────────────────────┐     │
│ Documents            │ │ COLLECTION TREND           ││ NEEDS ATTENTION              │     │
│ Notifications        │ │ bar/line chart             ││ 10 overdue members           │     │
│ Settings             │ └────────────────────────────┘│ 3 subscriptions due soon     │     │
│                      │                               └──────────────────────────────┘     │
│                      │ OVERDUE SUBSCRIBERS                                      [See all]│
│                      │ Name      Plan      Due      Amount     Action                  │
│                      │ Ali       Basic     Nov 10   PKR 3,000  [Remind][Record Paid]  │
│                      │ Sara      Basic     Nov 10   PKR 3,000  [Remind][Record Paid]  │
└──────────────────────┴───────────────────────────────────────────────────────────────────┘
```

**Adaptive cards**: SaaS highlights quota, tokens and subscription ARR; enterprise buyer highlights seats, unused allocations and payables. Still one Provider view and one route family.

## WF-07 — Subscriber detail and payment `/provider/:orgId/subscribers/:id`

```text
┌────────────────────────────────────────────────────────────────────────────────────┐
│ Ali Ahmed  [ACTIVE MEMBERSHIP]                          [Send Notice] [New Receipt] │
│ Email: ali@example.com  Phone: (verified/opted-in)   Member since: 2026-08-10      │
├────────────────────────────────────────────────────────────────────────────────────┤
│ Basic Plan | PKR 3,000 monthly | Next due: 2026-11-10 | Outstanding: PKR 3,000       │
│ Usage: Check-ins  8 this period  [Source: staff entry]                            │
├────────────────────────────────────────────────────────────────────────────────────┤
│ FINANCIAL HISTORY                                                                  │
│ Invoice INV-2026-0041     PKR 3,000    OVERDUE     [Record Payment] [View Invoice]│
│ Invoice INV-2026-0021     PKR 3,000    PAID        [Download Receipt]              │
├────────────────────────────────────────────────────────────────────────────────────┤
│ Record Payment                                    Generate Receipt after Confirm?  │
│ Amount [3,000]  Date [2026-11-12] Method [Cash ▾] Ref [optional]  [Confirm]       │
└────────────────────────────────────────────────────────────────────────────────────┘
```

`Confirm` opens a verification dialog and writes a real immutable payment entry. Never send a paid receipt for an unconfirmed invoice.

## WF-08 — Business finance `/provider/:orgId/finance`

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│ FINANCE               Period [Nov 2026] Currency [PKR] [Record Expense] [Export]│
│ Invoiced:120,000 | Collected:90,000 | Outstanding:30,000 | Paid expenses:15,000 │
│ Recorded cash surplus: 75,000    [ⓘ Excludes unrecorded expenses]              │
│                                                                                 │
│ [Receivables] [Payables] [Payments] [Expenses] [Reports]                       │
│ Name         Direction   Amount    Due date      State         Actions           │
│ Ali Ahmed    Receivable  3,000     Nov 10        Overdue       [Remind]          │
│ Hosting      Payable     8,000     Nov 18        Due soon      [Record Paid]     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

Never display a synthetic `Profit` value when expense coverage is incomplete; show recorded cash surplus and assumptions.

## WF-09 — Invoice preview / sending

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│ DOCUMENT PREVIEW                         [Download PDF] [Email] [WhatsApp]        │
│ ┌────────────────────────────────────────────────────┐ Recipient: ali@example.com│
│ │  [Business logo] FitZone Gym                       │ Status: CONFIRMED PAYMENT │
│ │  RECEIPT #R-2026-00041              Nov 12, 2026   │ Email [on]               │
│ │  Customer Ali Ahmed                               │ WhatsApp [opt-in required]│
│ │  Basic membership         PKR 3,000              │ [Send]                   │
│ │  Paid by cash             PKR 3,000              │ Last attempt: accepted   │
│ │  Balance                  PKR 0                  │                         │
│ └────────────────────────────────────────────────────┘                         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Render PDF from immutable report snapshot, not from browser DOM. File private in object storage; URL authorized and expiring.

## WF-10 — Integrations / custom-domain sender

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ ADVANCED INTEGRATIONS                                                   │
│ LiteLLM Gateway   [Configured]   [Usage mappings] [Rotate key]         │
│ OpenMeter Metering [Connected]   [Create meter] [Test event]            │
│ SMTP               [Connected]   [Send test to verified address]       │
│ Sender domain      business.com [Pending DNS verification] [Verify]    │
│ WhatsApp           [Not connected] [Connect official Business API]     │
│                                                                         │
│ Last successful AI sync: 2026-11-12 09:00 UTC                          │
│ Last analytical refresh: 2026-11-12 09:05 UTC                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## WF-11 — Mobile layout (<768px)

```text
┌───────────────────────────┐
│ ◈ SubSphere       🔔  ☰   │
├───────────────────────────┤
│ Individual ▾              │
│ SPENDING THIS MONTH       │
│ $64   [currencies toggle] │
├───────────────────────────┤
│ Due soon: 2              │
│ [ Cursor   Nov 22  $20 ] │
│ [ Game Pass Dec 1  $12 ] │
├───────────────────────────┤
│ Usage trend               │
│ [compact accessible chart]│
├───────────────────────────┤
│ [+ Add Subscription]      │
├───────────────────────────┤
│ Home | Plans | Calendar |…│
└───────────────────────────┘
```

Mobile forms use full-screen sheets, buttons large enough for touch and no horizontal scrolling data table. For Provider view, analogous tabs Home, People, Finance, More.

## Screen acceptance criteria

Each screen must use actual backend data and visibly distinguish `loading`, `empty`, `error`, `disabled integration`, `stale analytics`, and `success` states. Must be keyboard navigable, support reduced-motion preference, and have E2E tests for two desktop widths and at least one mobile viewport. All chart numbers must be drillable to their underlying tenant-authorized records. Screenshot snapshots belong in `docs/ui/screenshots/` only after real implementation.


---

# PART 4: API CONTRACTS

# SubSphere 4.0 — API contracts and example payloads

**Version:** `/api/v1` | **Format:** JSON UTF-8 | **Pagination:** cursor for usage/logs, offset or cursor for small admin lists | **Auth:** secure browser session cookie + CSRF, service-scoped API key for usage/SDK, internal service tokens for workers. Every tenant-owned resource requires scope checks.

## Error contract

```json
{
  "code": "QUOTA_EXCEEDED",
  "message": "Requested usage exceeds the current subscription allowance.",
  "details": {"metric":"api_requests","allowed_remaining":0},
  "request_id": "req_example_123"
}
```

Status conventions: 200 read/idempotent replay, 201 resource created, 202 asynchronously accepted, 204 deleted/revoked, 400 malformed request, 401 unauthenticated, 403 forbidden, 404 not found/hidden cross-tenant, 409 domain conflict, 422 validation failure, 429 rate limit, 503 required dependency unavailable. Document domain-specific error codes in OpenAPI.

## Authentication + accounts

| Method | Path | Auth | Note |
|---|---|---|---|
| POST | `/auth/register` | public | Email/password, auto-create personal free account; verify email |
| POST | `/auth/login` | public | Secure cookies, rate limiting |
| POST | `/auth/refresh` | session | Rotate refresh token |
| POST | `/auth/logout` | session | Revoke session |
| POST | `/auth/verify-email` | token | One-time, expiring |
| GET | `/me` | session | User and authorized organizations only |
| GET | `/accounts` | session | Own personal and org account contexts |
| POST | `/organizations` | session | Create business, owner role assigned |
| POST | `/organizations/{id}/invites` | owner/admin | Invite member with scoped role |

## Personal subscriptions

`POST /personal/subscriptions`:

```json
{
  "service_name":"Cursor",
  "category":"AI & Developer Tools",
  "product_type":"recurring_subscription",
  "plan_name":"Pro",
  "price_minor":2000,
  "currency":"USD",
  "billing_interval":"monthly",
  "next_renewal_date":"2026-12-12",
  "auto_renew":true,
  "reminder_offsets_days":[7,3,1]
}
```

Response 201:

```json
{
  "id":"9e2f7c28-a7eb-40c0-b0a2-b6642f25c27e",
  "status":"active_user_recorded",
  "monthly_equivalent_minor":2000,
  "currency":"USD",
  "next_renewal_date":"2026-12-12",
  "usage_status":"not_connected"
}
```

Also implement `GET /personal/subscriptions`, `GET/PATCH/DELETE /personal/subscriptions/{id}`, `GET /personal/calendar?month=2026-12`, `GET /personal/analytics/spending`, `GET /personal/usage`, `POST /personal/monitoring/connect`, `POST /personal/monitoring/revoke`, `POST /personal/activity/import`, `DELETE /personal/activity/{id}`. User-recorded `status` does not claim payment verification.

## Provider services and contacts

- `POST /services` `{organization_id,name,slug,type,category,currency}`; verify owner/admin.
- `POST /services/{id}/plans` `{name,description}`.
- `POST /plans/{id}/versions` `{price_minor,currency,billing_interval,pricing_model,included_seats,max_seats,metrics:[...]}`; published versions immutable.
- `POST /subscribers` `{organization_id,name,email?,phone_e164?,notes?}`; contact is not automatically a User.
- `POST /subscribers/{id}/invite` `{email,role:"subscriber"}`; accepted invitation associates contact with identity.
- `POST /subscriptions` `{buyer_account_id?,subscriber_contact_id?,plan_version_id,seats:1,starts_at,auto_renew}`; provider-managed or buyer-initiated with actor checks. Creates invoice if paid plan.
- `POST /subscriptions/{id}/cancel` `{effective:"now"|"end_of_period",reason?}`.
- `POST /subscriptions/{id}/seat-allocations` `{membership_id}`; atomic capacity check.
- `DELETE /subscriptions/{id}/seat-allocations/{allocation_id}`; historical association retained, `revoked_at` set.

## Finance

`POST /finance/obligations`:

```json
{
  "organization_id":"<authorized-org-id>",
  "direction":"RECEIVABLE",
  "party_ref":"<subscriber-id>",
  "amount_minor":300000,
  "currency":"PKR",
  "due_at":"2026-11-10T23:59:59+05:00",
  "description":"Basic gym membership — November 2026"
}
```

For a payable substitute `direction=PAYABLE` and the vendor reference. Never treat payable/receivable as a bank account debit/credit balance by itself.

`POST /invoices/{id}/payments` with `Idempotency-Key: payment-2026-00041`:

```json
{"amount_minor":300000,"currency":"PKR","method":"cash","paid_at":"2026-11-12T10:00:00+05:00","external_reference":"counter-register-41"}
```

Response 201:

```json
{
  "payment_id":"<uuid>",
  "invoice_id":"<uuid>",
  "invoice_status":"paid",
  "amount_paid_minor":300000,
  "balance_due_minor":0,
  "receipt_job_status":"queued"
}
```

`GET /finance/kpis?period=2026-11&currency=PKR` returns namespaced metrics:

```json
{
  "invoiced_receivables_minor":12000000,
  "confirmed_collections_minor":9000000,
  "outstanding_receivables_minor":3000000,
  "confirmed_paid_expenses_minor":1500000,
  "recorded_cash_surplus_minor":7500000,
  "currency":"PKR",
  "expense_coverage":"user_recorded_only",
  "calculated_at":"2026-11-12T09:00:00Z"
}
```

## Generic usage consumption

`POST /usage/consume` **API key auth** with `Idempotency-Key` header (same as external_event_id if given):

```json
{
  "external_event_id":"evt_cust5_req001",
  "subscription_id":"<uuid>",
  "metric_key":"api_requests",
  "quantity":1,
  "occurred_at":"2026-11-12T08:59:59Z",
  "seat_allocation_id":null
}
```

Response 201:

```json
{
  "accepted":true,
  "duplicate":false,
  "metric_key":"api_requests",
  "used":1000,
  "limit":1000,
  "remaining":0,
  "event_id":"<uuid>",
  "source":"PROVIDER_SDK"
}
```

Repeat identical request -> HTTP 200 with `duplicate=true`, original event ID and usage result; change quantity for same ID -> 409 `EVENT_ID_CONFLICT`; if `hard_limit` exceeded -> 409 `QUOTA_EXCEEDED`; no increment or event. A separate `GET /entitlements/check` is **advisory** and cannot reserve quota.

## External AI usage sources

- `POST /connections` to authorize LiteLLM / provider-admin APIs / import; never accept raw credential into log streams.
- `POST /connections/{id}/sync` returns `202 {sync_job_id,status:"queued"}`.
- `GET /connections/{id}/status` returns capability matrix (requests, tokens, cost, period, last verified time).
- `GET /ai/usage?scope=personal|organization&start&end` requires tenant membership and separates `reported_cost_minor`, `estimated_value_minor`, `subscription_fee_minor`.
- `POST /personal/activity/aggregate` authorized extension credential only, allowlisted metric with no raw URLs or content. Returned coverage label `OBSERVED`.

## Notifications, messaging and reports

- `GET/PATCH /notification-rules` own/organization-scoped.
- `GET /notifications` with recipient/tenant filter.
- `POST /notifications/test-email` only to verified own address or configured test recipient; rate limited.
- `POST /reports` `{report_type,subscription_id?,period_start,period_end}` -> 202 queued.
- `GET /reports/{id}` -> status: queued/rendering/ready/failed, document hash and legal recipient.
- `GET /reports/{id}/download` -> signed short-lived URL or streamed PDF with authorization.
- `POST /reports/{id}/deliver` `{channel:"email"|"whatsapp",recipient_id}` -> 202 queued if verified and consented.
- `POST /domains` -> DNS verification pending; `POST /domains/{id}/verify` -> verified only after actual checks.
- `POST /contacts/{id}/consents` -> authorized recipient consent workflow; not a provider-only arbitrary opt-in toggle.

## Webhook envelope

```json
{
  "id":"event-uuid",
  "type":"invoice.paid",
  "version":1,
  "occurred_at":"2026-11-12T09:00:00Z",
  "data":{"invoice_id":"<uuid>","subscription_id":"<uuid>","amount_minor":300000,"currency":"PKR"}
}
```

Sign timestamp + payload with HMAC; safe delivery URL validation, retries with bounded backoff, idempotency for receivers, logs without raw secrets.

## OpenMeter CloudEvent adapter sample

```json
{
  "specversion":"1.0",
  "type":"api_request",
  "id":"<same-canonical-event-id>",
  "source":"subsphere/tenant/<opaque-tenant-id>/service/<opaque-service-id>",
  "subject":"<tenant-scoped-subscriber-id>",
  "time":"2026-11-12T08:59:59Z",
  "data":{"quantity":1,"metric":"api_requests","subscription_ref":"<uuid>"}
}
```

Define OpenMeter meter selector and aggregation for `data.quantity`. Do not assume event ingestion immediately implies query availability; poll in integration tests with bounded timeout.


---

# PART 5: OPEN SOURCE INTEGRATION CONTRACTS

# Open-source integration contracts, ownership, tradeoffs

**Status:** Design specifications; adapters must be implemented and tested in milestones M4–M6.

| Component | Why chosen | SubSphere data flow | Production considerations |
|---|---|---|---|
| LiteLLM | Gateway for clients explicitly routed through it; usage / virtual-key attribution | LiteLLM spend logs → normalized external usage → analytics | Dedicated DB, keys, no raw prompts; upstream docs provide scoped spend routes; some enterprise features separately licensed |
| OpenMeter | High-volume event metering, flexible metric definitions | Accepted SubSphere usage event → CloudEvent → OpenMeter API → Kafka/ClickHouse → read-only aggregate | PostgreSQL/ClickHouse and workers; Redis optional for dedup; separate from authoritative quota/cash ledger |
| Kafka | Durable asynchronous transport/replay | PostgreSQL outbox → Kafka → Spark | At-least-once, partition ordering only, consumer idempotency |
| Spark | Stream and batch engineering demonstration | Kafka → validated/dedup Silver → Gold aggregates | Checkpoints, backfill, expensive resources only when analytics profile enabled |
| MinIO | Private reports and historical Parquet object store | Report PDFs; Bronze/Silver/Gold; checkpoint | Private buckets, presigned short-lived URLs, secure retention and backup |
| WeasyPrint | HTML/CSS to PDF receipts | Immutable report snapshot → sandboxed Jinja2 HTML → PDF | Security of untrusted HTML/CSS and external URL fetching; install native deps |
| Mailpit | Local dev message capture | SMTP adapter → Mailpit | Never claim production delivery |
| Postal | Self-hosted transactional mail, custom sender domains and delivery tracking | Notification worker → SMTP/TLS → Postal with webhooks | Independent official deployment, public DNS SPF/DKIM/DMARC, port 25 and reputation |
| Langfuse (optional) | LLM tracing/evaluation beyond spend | Opted-in application traces | Higher complexity; optional and feature/license review; redact prompts by default |
| WhatsApp Business Platform (not OSS) | Official compliant message channel | Notification worker → approved template/consented recipient | Requires external official account, opt-in and current policy compliance |

## Integration precedence

1. A request is routed through LiteLLM **only if the client configures the gateway**. Report `routed_request_count`; do not claim to observe native desktop application private API sessions.
2. SubSphere imports LiteLLM spend logs with a stable unique `(integration_id, source_request_id)`. If LiteLLM cost includes extra guardrail/routing fees, don't double-add them.
3. Canonical provider SDK usage is stored transactionally in PostgreSQL; a single normalized event is delivered through outbox. A LiteLLM event is not also submitted through unrelated SDK metering unless linked and deduped.
4. OpenMeter receives CloudEvents with stable `id`, tenant-scoped `subject`, numeric `data.quantity`; query aggregations are **not** used for hard enforcement.
5. Spark may process the same source event with at-least-once delivery. Silver/Gold dedup relies on stable IDs, not on magical exactly-once transport.
6. Payment receipts rely on PostgreSQL invoice/payment snapshots, **never** LiteLLM estimates or OpenMeter aggregate alone.
7. The provider's API credential, domain sender, and WhatsApp communication permissions are individually scoped; one doesn't authorize the others.

## Connection status and capability matrix

Every connection exposes `provider`, `connection_scope` (personal / organization), `status`, `supports_requests`, `supports_input_tokens`, `supports_output_tokens`, `supports_costs`, `supports_attribution`, `last_sync_success`, `coverage_start`, `coverage_end`, `privacy_scope` and `error_code`. If unsupported, show `Unavailable` rather than 0.

## Usage source taxonomy

`OFFICIAL_PROVIDER_API`, `GATEWAY_ROUTED`, `APP_SDK`, `ALLOWED_BROWSER_ACTIVITY`, `ALLOWED_DESKTOP_ACTIVITY`, `CSV_IMPORT`, `MANUAL`. Each record has `reliability` = `VERIFIED`, `OBSERVED`, `IMPORTED`, `MANUAL`, `ESTIMATED`, plus explicit `quality_note`. Cost is `PROVIDER_REPORTED` or `CALCULATED_ESTIMATE`, never ambiguously just `cost`.

## Client-side monitoring boundaries

- Browser extension may collect allowlisted domain visits/selected tab active time under user consent. Must strip query/path and all content; no unrelated history.
- Desktop helper may track approved foreground process presence/time with user consent; no passwords, prompts, windows contents, screenshots or traffic interception.
- Local proxy may count requests and response token metadata for clients that **intentionally point to it**. It cannot magically decrypt HTTPS app traffic.
- Official provider API access depends on account plan and API authorization. Build capability detection and honest disconnected states.

## Licensing and operations

Check official SPDX/license files at exact locked source version before redistribution/commercialization; components may mix OSS and enterprise-only features. Avoid silent upgrades. Reference docs:

- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://docs.litellm.ai/docs/proxy/cost_tracking
- https://openmeter.io/docs/open-source/architecture
- https://openmeter.io/docs/metering/events/usage-events
- https://docs.postalserver.io/
- https://doc.courtbouillon.org/weasyprint/latest/first_steps.html
- https://langfuse.com/self-hosting
- https://whatsappbusiness.com/policy/


---

# PART 6: DOCKER DEPLOYMENT

# Modular Docker Compose architecture and deployment guide

## What is actually in the starter?

The checked-in starter implements a **minimal greenfield boot skeleton only**: React/Vite landing screen, FastAPI health/info endpoints, PostgreSQL service and optional Mailpit. Business modules, migrations, OpenMeter/LiteLLM/Postal integration, PDF worker, Kafka/Spark data jobs are **planned components**, not yet built. An implementation agent must complete milestones M0–M7 and add actual runtime Compose services as they are implemented. Never represent a placeholder as a working integration.

## Deployment layout — target

```text
infra/compose/
  compose.yaml                 # Core API + frontend + PostgreSQL (starter)
  compose.dev.yaml             # Mailpit and developer tooling (starter)
  compose.ai.yaml              # LiteLLM gateway (M5)
  compose.metering.yaml        # OpenMeter + workers, ClickHouse, dependencies (M6)
  compose.analytics.yaml       # Kafka + Spark + MinIO (M6)
  compose.observability.yaml   # Prometheus + Grafana (M7)
  compose.production.yaml      # Hardened ingress, replicas, override (M7)
infra/postal/
  README.md                    # Separate official Postal deployment stack (M4)
```

**Do not use `profiles` for the core API/database**: those must always work. For optional stacks use Compose `-f` files and/or profiles; pin image digests and exact Python/Node package locks before production. `docker compose config` must validate every documented combination. Never hand-maintain a partial copy of OpenMeter/Postal upstream Compose stack as if production-ready; vendor a reviewed, pinned release or use documented image services and their required runtime workers.

## Working starter commands (from repository root)

```bash
cp .env.example .env
# Change the POSTGRES_PASSWORD and APP_SECRET in .env
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml config
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml up -d --build
```

After startup, the intended starter addresses are `http://localhost:8080` (landing screen), `http://localhost:8000/health/live` and `/health/ready`, and Mailpit `http://localhost:8025`. These are configured starter endpoints, not proof of runtime validation: this environment does not have a Docker engine. Health readiness must report 503 if the database cannot be queried.

## Target module enablement after implementation

```bash
# AI gateway; requires pinned image + provider configuration
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.ai.yaml up -d

# Metering: use pinned compatible OpenMeter API and worker release, scoped databases
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.metering.yaml up -d

# Big Data: Kafka/Spark/MinIO, checkpoint/object persistence
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.analytics.yaml up -d

# Full local demonstration after all modules are implemented
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml \
#   -f infra/compose/compose.ai.yaml -f infra/compose/compose.metering.yaml \
#   -f infra/compose/compose.analytics.yaml up -d
```

Mailpit is development-only and not a general outgoing SMTP relay. Postal is a separate officially installed Compose deployment and connects to SubSphere via DNS/TLS/SMTP credentials.

## Component allocation and dependencies

| Module | Services | Critical storage | Depends on | Should survive failure of |
|---|---|---|---|---|
| Core | API, frontend, Postgres, scheduler | Postgres named volume | Postgres | Kafka, Spark, OpenMeter, LiteLLM, Postal |
| Reports | WeasyPrint worker, private file store | MinIO/private objects + report snapshots in PG | PG, object store | Postal, LiteLLM |
| AI | LiteLLM, its isolated database | Separate Postgres database | AI provider credentials for live requests | Spark, OpenMeter |
| Metering | OpenMeter API/worker, ClickHouse, suitable broker, optional Redis | ClickHouse + scoped PostgreSQL | Kafka depending on pinned runtime | Postal |
| Analytics | Kafka, Spark, MinIO | Kafka log, MinIO datasets/checkpoints | Kafka/object store | Frontend (jobs asynchronous) |
| Email development | Mailpit | Ephemeral mailbox dev only | None | Entire production platform |
| Production email | Postal independently | Postal's own state per official release | DNS, SMTP connectivity, sender verification | AI data plane |

## Cross-host production topology

**Host A:** reverse proxy, frontend, backend, worker, PostgreSQL, report worker.  
**Host B:** Kafka, Spark, MinIO, OpenMeter, ClickHouse, LiteLLM (size and isolate appropriately).  
**Host C:** independent Postal installation with correct hostname, DNS, valid TLS and outbound port availability.

These are logical groups, **not a mandate for three machines**. A startup could begin on one adequately provisioned host and migrate modules later. Define `KAFKA_BOOTSTRAP_SERVERS`, `MINIO_ENDPOINT`, `OPENMETER_URL`, `LITELLM_URL`, `SMTP_HOST` as actual reachable DNS/HTTPS addresses across hosts. Docker service names work only on the appropriate Docker network. Configure TLS/private networks and firewall allowlists. Never publish Postgres/ClickHouse/Kafka administration ports to the public internet.

## Operations, resilience and failure-mode behavior

- PostgreSQL operation is required for critical API writes; `503` and no committed mutation if unavailable.
- Kafka down: operational commits continue with pending transactional outbox; do not drop accepted usage events.
- Spark down: operational pages work; historical analytics display stale timestamp.
- OpenMeter down: durable retry queues; hard quota still enforced by PostgreSQL.
- LiteLLM down: explicitly configured gateway requests may fail; personal manual tracking still works.
- Postal down: email delivery jobs retry; billing changes remain committed.
- Report worker down: document job queued/failed; do not falsely mark report sent.
- Use `docker compose stop`, `restart`, `down` to test recovery; never use `down -v` for operational environments.

## Deploy/security checklist

1. Pin Docker image tags and ideally digests; pin Python/npm lockfiles; produce SBOM.
2. Create isolated database users and databases: `subsphere`, `litellm`, `openmeter` (separate DB credentials).
3. Use Docker secrets or locked-down secret storage for JWT, API, LiteLLM, SMTP and integration keys. Frontend never receives server credentials.
4. Use non-root app containers and resource/CPU/memory limits where supported.
5. Implement readiness/liveness probes and correct startup/migration ordering.
6. Add a true reverse-proxy/TLS setup, secure cookies, CORS and CSRF protection, consistent public URL.
7. Scheduled database backups, private MinIO object backups, ClickHouse/OpenMeter backups according to RPO/RTO, and verified restore drills.
8. Add migrations with forward/backward compatibility, isolate migration job, no resetting DB on startup.
9. Preserve Kafka topic policies and Spark checkpoint state; choose retention aligned to replay/backfill requirements.
10. Email sender domains must complete DNS identity verification; block spoofed/unverified From addresses.
11. Rate limit signup, login, test messages, exports, webhook attempts and metering endpoints.
12. Run on a mail-capable network if Postal is used; upstream recommends a dedicated server and adequate storage/RAM, and outbound port 25 may be blocked.
13. Review upstream project license and editions before enabling proprietary/enterprise-only capabilities.

## Upstream reference links

- Docker Compose profiles: https://docs.docker.com/compose/how-tos/profiles/
- Docker Compose includes: https://docs.docker.com/compose/how-tos/multiple-compose-files/include/
- Docker secrets: https://docs.docker.com/compose/how-tos/use-secrets/
- LiteLLM proxy deployment: https://docs.litellm.ai/docs/proxy/deploy
- OpenMeter architecture: https://openmeter.io/docs/open-source/architecture
- Postal prerequisites: https://docs.postalserver.io/getting-started/prerequisites/
- Postal helper commands: https://docs.postalserver.io/getting-started/postal-command/


---

# PART 7: TEST PLAN

# Verification Matrix, Integration Test Requirements and Expected Outputs

**Important:** All rows below define **tests to implement**, not tests already executed. The starter repository contains only a basic live/ready smoke test. Record actual outcomes with commands, environment and logs in `docs/TEST_RESULTS.md` when implemented.

## 1. Test data and fixed clocks

Use deterministic fixtures, a frozen clock (`2026-11-12T09:00:00Z` for time-sensitive scenarios), seeded UUIDs, isolated PostgreSQL test DBs and clean Kafka/OpenMeter namespaces.

- `user_personal_A`: verified account; `user_personal_B`: another unrelated account.
- `org_fit`: FitZone Gym (PKR); owner and staff roles; 40 basic memberships at PKR 3,000/month each; 30 invoices paid and ten overdue as of frozen clock; PKR 15,000 confirmed paid expense.
- `org_tech`: TechNova Solutions (USD); enterprise purchase of ten seats × $12/month; seven employees actively allocated.
- `org_ai`: NovaAI service; Basic hard-limit requests=1000 per period; one subscription at `consumed=999`.
- `personal_subs`: AI assistant $20 monthly, game subscription $12 monthly, next due dates in later calendar month.
- `ai_tokens`: input 2,000,000 with rate $2/M; output 500,000 with rate $8/M, no cache charges -> $8 estimated value.
- `gateway_log`: real or fixture LiteLLM request with source-specific ID `lit-1001`; same log replayed 3 times.

## 2. Functional / unit / API test cases

| ID | Level | Action and input | Exact expected output / assertion |
|---|---|---|---|
| T001 | API | Register new verified personal email | 201 account, personal workspace created, `personal_free`, no payment requirement |
| T002 | API | Register duplicate email | 409 conflict; no second identity |
| T003 | API | Login with wrong password | 401, rate-limited on repeated attempts; no token |
| T004 | API | Access protected endpoint without auth | 401, no data exposure |
| T005 | API | Change authenticated tenant query ID to unrelated org | 403/404; zero write/read side effects |
| T006 | API | Invite employee to organization | Pending invite with expiring token hash; not yet active membership |
| T007 | API | Reuse accepted invitation | Reject; no duplicate membership |
| T008 | UI/API | Create personal $20 monthly subscription | Persists, appears on list/calendar, next due preserved |
| T009 | Unit | Add $20 monthly + $12 monthly USD | Monthly equivalent USD $32.00 |
| T010 | Unit | Add PKR record to USD records | Currency totals separate; no silent arithmetic conversion |
| T011 | Unit | Enter one-time game purchase $60 | Excluded from monthly recurring total |
| T012 | API | Negative price, missing date, invalid recurrence | 422; no row created |
| T013 | Unit | Annual $240 USD | Monthly equivalent $20; annual equivalent $240 |
| T014 | Unit | Monthly plan Jan 31, 2027 (non-leap) | Feb 28 then Mar 31 under anchored logic |
| T015 | Unit | Subscription period boundary `end` instant | Prior period excludes event; next valid period includes |
| T016 | API | Personal A requests personal B activity | 403/404; no data in response |
| T017 | Browser | User approves only `exampleai.com` | Other domains never synchronized |
| T018 | Browser | Disable monitoring while focused | No further activity from disabled source |
| T019 | Activity | 40 min observed foreground usage | `active_seconds=2400`, `tokens=null`, label OBSERVED |
| T020 | API | Unsupported provider token query | `null/unavailable`; never zero or fabricated |
| T021 | Catalog | Publish service with Basic $10 plan v1 | Public plan accessible; immutable v1 preserved |
| T022 | Catalog | Modify published plan price | Creates v2; old subscriptions retain v1 price |
| T023 | Contacts | Provider creates subscriber without account | Valid contact/subscription; no user login created |
| T024 | Contacts | Contact claims invitation with verified identity | Explicit link established; original provider data preserved |
| T025 | Subscription | Paid subscription requested, no payment | State pending, invoice issued; no paid receipt |
| T026 | Subscription | Confirm full payment with finance role | Payment persisted, invoice paid, subscription active, receipt job queued |
| T027 | Subscription | Unauthorized staff confirms payment | 403; status unchanged |
| T028 | Subscription | Same payment confirmation idempotency key twice | One payment, one receipt logical job, same result |
| T029 | Billing | Overpayment not supported in v1 | 409/422, outstanding unchanged |
| T030 | Billing | Mark invoice overdue after due date and unpaid | Overdue state; once-per-rule notification; owner count updated |
| T031 | Billing | Payment confirmed just before overdue worker executes | No overdue reminder; no invalid state transition |
| T032 | Finance | Gym 40 × PKR 3000 invoices | Total invoiced PKR 120,000 |
| T033 | Finance | 30 paid × PKR 3000 | Confirmed collected PKR 90,000 |
| T034 | Finance | 10 unpaid × PKR 3000 | Outstanding PKR 30,000, overdue count 10 |
| T035 | Finance | Confirmed expenses PKR 15,000 | Recorded cash surplus PKR 75,000; not labeled net profit |
| T036 | Seats | Purchase 10 seats, allocate seven | 7 assigned, 3 unassigned, recurring payable $120 |
| T037 | Seats | Concurrent 11th allocation attempt | Max 10 active seat assignments; excess denied |
| T038 | Seats | Assign member from different org | 403; no allocation |
| T039 | Seats | Revoke seat and reassign | Historical usage remains; capacity reopens once |
| T040 | Metering | Current 999, submit new qty=1 | 201; `consumed=1000`, remaining 0 |
| T041 | Metering | Current 1000, submit new qty=1 hard limit | 409 QUOTA_EXCEEDED, counter stays 1000 |
| T042 | Metering | Retry exact accepted ID 3× | Return earlier accepted result, counter not incremented |
| T043 | Metering | Same event ID conflicting quantity | 409 EVENT_ID_CONFLICT |
| T044 | Metering | 50 concurrent qty=1 requests with 5 remaining | Exactly five accepted unique events; no quota overshoot |
| T045 | Metering | Monitor-only quota exceeded | Accepted usage + alert; never extra invoice unless contract rules permit |
| T046 | Metering | Gym check-in for wrong service | 403/404; no usage event |
| T047 | Gateway | LiteLLM sourced input/output token fields | Canonical record matches provider log and provenance |
| T048 | Gateway | Import `lit-1001` thrice | One unique external usage record; aggregates not inflated |
| T049 | Pricing | 2M×$2/M + 0.5M×$8/M | Estimated usage value $8.00 (not actual bill) |
| T050 | Pricing | Fixed subscription $20 plus estimated token value $8 | Amount due remains $20 unless verified extra charges exist |
| T051 | Pricing | Missing token category | Null unknown; do not assume zero |
| T052 | Reports | Unpaid invoice -> generate `payment_receipt` | Denied; only `invoice` or `statement` allowed |
| T053 | Reports | Confirm payment -> receipt PDF | Correct number, amount, status, owner, checksum; valid PDF bytes |
| T054 | Reports | Re-render historical receipt after plan price edit | Same snapshot monetary totals as original |
| T055 | Reports | Cross-tenant secure report URL | Denied; no leaked PDF |
| T056 | Reports | Expired single-use link | 401/403/410 according to contract; never serves document |
| T057 | SMTP | Trigger personal renewal reminder | Exactly one notification job per rule and period |
| T058 | SMTP | Send to Mailpit | Message appears in local test mailbox; status accepted_by_relay |
| T059 | SMTP | SMTP unavailable | Retry with backoff, subscription/payment operations unaffected |
| T060 | SMTP | Duplicate scheduled tick | No duplicate logical email jobs |
| T061 | Email Domain | Unverified business sending domain | Sending blocked; use verified platform sender |
| T062 | WhatsApp | Phone saved but no consent | No WhatsApp send; email behavior independent |
| T063 | WhatsApp | Consent verified, approved template via fake provider | One outbound request with expected recipient/payload |
| T064 | WhatsApp | Recipient opts out | Subsequent sends suppressed; audit stored |
| T065 | Outbox | DB commit while Kafka unavailable | Payment/usage persists; outbox pending |
| T066 | Outbox | Kafka restored | Eventually published with same event ID, marked destination delivered |
| T067 | OpenMeter | Valid CloudEvent exported | Meter query returns exactly the expected quantity after catch-up |
| T068 | OpenMeter | Duplicate event delivery | No inflated *trusted* meter after dedup/reconciliation |
| T069 | Spark | Stop process mid-stream, restart from checkpoint | Gold distinct count equals authoritative accepted event count |
| T070 | Storage | Restart Compose without deleting volumes | Records and stored PDF survive |
| T071 | Security | Browser payload with prompts, cookies, full URLs | Reject or strip content; raw secrets never stored |
| T072 | Security | Webhook URL to private/loopback address | Reject request and connection attempt |
| T073 | Security | Crafted HTML with remote asset `file://` / internal IP | PDF worker rejects fetch; no file or metadata exposure |
| T074 | Security | API key for service A sends metric to service B | 403; no usage counted |
| T075 | Data deletion | Personal deletes activity + rerun aggregates | Deleted personal records do not resurface in UI or reprocessed outputs |
| T076 | Observability | Spark offline and analytics stale | UI shows stale timestamp; core read/update still works |
| T077 | Deployment | `docker compose config` in each documented mode | Valid merged YAML with declared services/volumes/ports |
| T078 | Deployment | Clean core install and `GET /health/ready` | 200 if real Postgres reachable; 503 otherwise |
| T079 | Deployment | Backup and restore into clean DB | User+invoice counts and hashes match snapshot |
| T080 | Accessibility | Keyboard-only navigate personal and provider workflows | Every actionable element reachable, properly labeled |

## 3. Integration-test environment matrix

| Test suite | Required containers/services | Expected signal | Fail gate |
|---|---|---|---|
| Core API | backend, PostgreSQL | health, auth, subscription CRUD | Any P0 failure |
| Finance | backend, PostgreSQL, worker | accurate invoices and payments | Any inconsistent financial amount |
| Notification | backend, worker, Mailpit | verified test message, no dup | Missed or unverified send |
| Reporting | backend, report-worker, MinIO (or private local dev bucket), PostgreSQL | PDF stored and downloadable with auth | Wrong money or cross-tenant access |
| AI Gateway | LiteLLM, isolated Postgres, mock upstream/provider test account, adapter | canonical token records, no prompt storage | Missing/duplicated counts |
| OpenMeter | OpenMeter API/workers, Kafka, ClickHouse, scoped Postgres; Redis if selected | CloudEvents counted correctly | Incorrect reconciliation |
| Analytics | Kafka, Spark, MinIO, backend/PG | Bronze/Silver/Gold and checkpoint | Data corruption, duplicate inflation |
| Postal | Dedicated Postal test stack with approved sender, or SMTP contract mock | accepted, delivery callback when available | False delivery claim |
| WhatsApp | Official API test credentials or mocked adapter | consent enforced, templated payload | Unconsented send |
| Full E2E | all enabled test modules + Playwright | complete user journeys | Any critical security or financial failure |

## 4. Contracts and fixtures

- Freeze time using dependency-injected clock, never patch random global clock in domain layer.
- Use `testcontainers` or a dedicated Compose integration test project for real PostgreSQL; do not use SQLite as sole integration DB.
- Mock provider APIs via WireMock or local FastAPI provider stub; fixture recording must omit real secrets and personal data.
- LiteLLM: test routed request metadata from gateway, not fabricated token counts; include source IDs, timestamps, model, reported cost.
- OpenMeter: contract test `CloudEvents` schema and meter definition; query eventually consistent usage with bounded retries before assert.
- Postal: local Mailpit tests and a **separate** optional live sender/recipient verification gate; SMTP acceptance != inbox delivery.
- WhatsApp: mock official API for CI; opt-in/approved template integration can be verified with test account only.
- WeasyPrint: verify PDF header `%PDF`, text/layout snapshot or PDF extraction for totals, and blocked external asset requests.
- Kafka/Spark: use deterministic 10k, 100k and 1M unique event generators, inject duplicates, compare distinct IDs with PG accepted events.
- Scope every fixture to tenants A and B; test that database joins, aggregates, objects and external integration mappings do not cross boundaries.

## 5. Performance / fault injection

| Benchmark | Input | Measure | Required report |
|---|---|---|---|
| B-01 | 10,000 unique usage events | throughput, p50/p95 ingest latency | unique accepted vs normalized count |
| B-02 | 100,000 unique events | same + CPU/memory | processing lag and errors |
| B-03 | 1,000,000 unique events | same + Spark batch time | hardware, resource use, limits |
| B-04 | 10% repeated IDs | dedup ratio | no overcount, detected duplicates |
| B-05 | stop outbox dispatcher 60s | backlog, recovery | processing resumes after restart |
| B-06 | stop Spark 60s | Kafka lag, catch-up time | Gold count reconciles |
| B-07 | stop OpenMeter 60s | delayed aggregate/freshness | core enforcement unchanged |
| B-08 | stop SMTP 60s | job retry counts | no false delivery states |
| B-09 | 50 simultaneous quota requests | acceptance count, row locks | never exceeds quota |
| B-10 | 100 API dashboard requests | p95 response, DB performance | no N+1 or cross-tenant leakage |

**Never claim horizontal scale from one physical host.** Record actual machine CPU, memory, disk, Compose versions, image digests, random seed, date and configuration.

## 6. CI/CD and quality gates

1. Lint/format backend (`ruff`); type check critical modules (`mypy`); unit tests (`pytest`).
2. Frontend `npm ci`, TypeScript strict compile, Vitest, ESLint.
3. Build containers; run Compose config validation.
4. Start PostgreSQL integration environment; run Alembic migration head and reverse compatibility check.
5. Run API and domain integration suites.
6. Start Mailpit/report-worker; run PDF/SMTP tests.
7. Run LiteLLM/OpenMeter contract tests with deterministic stubs; separate optional live-credential tests.
8. Run Playwright personal/gym/enterprise flows and accessibility smoke tests.
9. Run security and IDOR tests; fail on high-severity issues.
10. Run Big Data micro-benchmark on demand (not every PR); full 1M test for release candidate.
11. Record evidence and artifacts; publish `docs/TEST_RESULTS.md` with **actual** pass/fail/skip data, not expectations.

**Release blockers:** money misstatements, unauthorized access, hard limit overshoot, duplicate payment, incorrect recipient, private data leak, undeployable core, unverifiable DB restore, fake delivery status. All P0 scenarios must pass before MVP can be called complete.


---

# PART 8: SAMPLE OUTPUTS

# Sample outputs and final demonstration script

These are **expected illustrative outputs**, not generated production records and not measured benchmark results.

## Output O1 — Individual dashboard response

```json
{
 "account_type":"individual",
 "currencies":[{"currency":"USD","monthly_equivalent_minor":3200}],
 "active_recurring_subscriptions":2,
 "due_in_next_30_days":2,
 "usage_sources":{"liteLLM":"not_connected","desktop":"observed_partial"},
 "observed_active_minutes":40,
 "token_count":null,
 "token_count_quality":"unavailable"
}
```

UI copy: `Monthly equivalent $32.00 • 2 renewals upcoming • 40 min observed activity • Tokens unavailable`. Do not label active minutes as token consumption.

## Output O2 — Gym finance response

```json
{
 "org":"FitZone Gym",
 "period":"2026-11",
 "currency":"PKR",
 "members_invoiced":40,
 "members_overdue":10,
 "invoiced_minor":12000000,
 "collected_minor":9000000,
 "outstanding_minor":3000000,
 "recorded_paid_expenses_minor":1500000,
 "recorded_cash_surplus_minor":7500000,
 "surplus_disclaimer":"Excludes expenses not recorded in SubSphere"
}
```

## Output O3 — Ten-seat enterprise purchase

`Purchased: 10 | Assigned: 7 | Unassigned: 3 | Per-seat price: $12.00/month | Normalized payable: $120.00/month`. Per employee usage is shown only when a verified admin source maps usage to the employee identity.

## Output O4 — Usage event accepted/rejected

```text
[before] used=999, hard_limit=1000
POST event req-0001 quantity=1 → 201 accepted, used=1000, remaining=0
POST event req-0002 quantity=1 → 409 QUOTA_EXCEEDED, used=1000
POST event req-0001 quantity=1 → 200 duplicate=true, used=1000
```

## Output O5 — Personal usage cost

`Input 2M @ $2/M = $4.00; Output 0.5M @ $8/M = $4.00; Estimated token value = $8.00; Fixed plan fee = $20.00; Confirmed extra charge = Unknown; Amount owed = based on actual contractual invoice, NOT $28.00 by default.`

## Output O6 — Subscription reminder email

**Subject:** Upcoming renewal — Cursor on December 12

```text
Hello,

Your tracked Cursor Pro subscription is due to renew on December 12, 2026.
Your recorded price is $20.00 per month.

This reminder is based on your saved subscription details.
SubSphere has not charged your account or confirmed payment.

Open your renewal calendar: [authenticated app link]

Manage reminders: [authenticated settings link]
```

## Output O7 — Business overdue notice

**Subject:** FitZone Gym membership fee reminder

```text
Hello Ali,

Your FitZone Gym Basic membership fee of PKR 3,000.00 was due on November 10, 2026.
Our records currently show it as unpaid.

If you have already paid, contact FitZone Gym so your records can be updated.
You may review your invoice at this secure link: [expiring link]
```

## Output O8 — Receipt PDF page structure

```text
┌────────────────────────────────────────────────────────┐
│ FITZONE GYM                         RECEIPT R-2026-041 │
│ Membership Services                            PAID   │
│ Issue Date: 12 November 2026                          │
├────────────────────────────────────────────────────────┤
│ Billed to: Ali Ahmed                                  │
│ Plan: Basic         Period: Nov 10 – Dec 10            │
│                                                        │
│ Item                                  Amount           │
│ Basic monthly membership             PKR 3,000.00       │
│                                                        │
│ Total                                PKR 3,000.00       │
│ Confirmed payment (cash)             PKR 3,000.00       │
│ Balance due                                 PKR 0.00    │
│                                                        │
│ Payment reference: counter-register-41                │
│ Issued by SubSphere for FitZone Gym                     │
└────────────────────────────────────────────────────────┘
```

## Output O9 — Delivery history

`Notification #N-123 | email ali@example.com | queued 09:01 | accepted_by_relay 09:02 | delivered: unknown (no delivery webhook)`. Do not fabricate received/read status.

## Output O10 — Analytics recovery report

```text
Test label: Spark restart recovery
Input accepted unique events: [measured]
Kafka published events: [measured]
Silver unique events: [measured]
Gold aggregate count: [measured]
Rejected invalid events: [measured]
Duplicates removed: [measured]
Recovery time: [measured]
Reconciliation: PASS only if accepted unique == Silver/Gold corresponding scope
Hardware/versions: [record exact]
```

Never replace `[measured]` with guessed values. Reconciliation scope must exclude rejected records and account for defined late-event/backfill rules.

## 10-minute live evaluation demo

1. Create free user; add AI tool and Game Pass subscription; show calendar and total $32 monthly equivalent.
2. Connect a mock or authorized API usage source and show token source status; show unsupported browser app tokens as unavailable.
3. Create FitZone Gym provider; import 40 members; issue invoices; confirm 30 payments; see 10 overdue, PKR 30,000 outstanding.
4. Record PKR 15,000 expense and inspect `recorded cash surplus` of PKR 75,000.
5. Confirm one outstanding payment; show immutable receipt job, generated PDF and Mailpit message.
6. Create enterprise license purchase and assign 7 of 10 seats.
7. Use service SDK to consume 1000th API unit, then show hard-limit rejection and retry idempotency.
8. Turn off Spark, submit events, restart and show backfill reconciliation with timestamps.
9. Show Compose services and module isolation, cross-tenant IDOR rejection and an audit-log entry.


---

# PART 9: ROADMAP

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


---

# PART 10: ARCHITECTURE DECISIONS

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


---

# DIAGRAM SOURCE INDEX

For editable Mermaid use-case, UML class, database ERD, HLD, LLD and sequence diagrams, open `docs/diagrams/*.mmd` in the project ZIP. Rendered PNG and SVG files are also included.