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
