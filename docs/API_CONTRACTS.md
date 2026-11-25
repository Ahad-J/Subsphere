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
