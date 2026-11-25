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
