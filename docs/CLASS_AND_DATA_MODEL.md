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
