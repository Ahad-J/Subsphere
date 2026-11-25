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
