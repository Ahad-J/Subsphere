-- SubSphere 4.0 Authoritative Relational Schema (PostgreSQL 16 & SQLite compatible)

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    is_verified INTEGER NOT NULL DEFAULT 0,
    is_platform_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'organization')),
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    default_currency TEXT NOT NULL DEFAULT 'USD',
    business_type TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'billing', 'developer', 'staff', 'member')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, user_id)
);

CREATE TABLE IF NOT EXISTS invitations (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subscriber_contact_id TEXT,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    accepted_by_user_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_verifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    verified_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    active_org_id TEXT REFERENCES organizations(id) ON DELETE SET NULL,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    service_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL DEFAULT 'metering:write',
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    service_type TEXT NOT NULL DEFAULT 'saas',
    category TEXT NOT NULL DEFAULT 'general',
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'archived')),
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, slug)
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_versions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    price_minor BIGINT NOT NULL CHECK (price_minor >= 0),
    currency TEXT NOT NULL,
    billing_interval TEXT NOT NULL CHECK (billing_interval IN ('monthly', 'annual', 'weekly', 'one_time')),
    pricing_model TEXT NOT NULL DEFAULT 'flat',
    included_seats INTEGER NOT NULL DEFAULT 1 CHECK (included_seats >= 1),
    max_seats INTEGER NOT NULL DEFAULT 1 CHECK (max_seats >= 1),
    status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('draft', 'published', 'archived')),
    published_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, version)
);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'units',
    enforcement TEXT NOT NULL DEFAULT 'hard_limit' CHECK (enforcement IN ('hard_limit', 'monitor_only')),
    created_at TEXT NOT NULL,
    UNIQUE(service_id, key)
);

CREATE TABLE IF NOT EXISTS plan_metric_limits (
    id TEXT PRIMARY KEY,
    plan_version_id TEXT NOT NULL REFERENCES plan_versions(id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    limit_value BIGINT NOT NULL CHECK (limit_value >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(plan_version_id, metric_id)
);

CREATE TABLE IF NOT EXISTS subscriber_contacts (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT,
    phone_e164 TEXT,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    plan_name TEXT NOT NULL DEFAULT '',
    fee_minor BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    next_due_date TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_links (
    id TEXT PRIMARY KEY,
    subscriber_contact_id TEXT NOT NULL UNIQUE REFERENCES subscriber_contacts(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    linked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    buyer_account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
    provider_org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subscriber_contact_id TEXT REFERENCES subscriber_contacts(id) ON DELETE SET NULL,
    plan_version_id TEXT NOT NULL REFERENCES plan_versions(id),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    status TEXT NOT NULL CHECK (status IN ('pending', 'trialing', 'active', 'past_due', 'suspended', 'canceled', 'expired')),
    anchor_day INTEGER NOT NULL CHECK (anchor_day >= 1 AND anchor_day <= 31),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    auto_renew INTEGER NOT NULL DEFAULT 1,
    canceled_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_periods (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE(subscription_id, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS external_subscriptions (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    vendor_name TEXT NOT NULL,
    service_name TEXT NOT NULL,
    seats_purchased INTEGER NOT NULL CHECK (seats_purchased >= 1),
    unit_price_minor BIGINT NOT NULL CHECK (unit_price_minor >= 0),
    currency TEXT NOT NULL,
    billing_interval TEXT NOT NULL DEFAULT 'monthly',
    next_renewal_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_allocations (
    id TEXT PRIMARY KEY,
    subscription_id TEXT REFERENCES subscriptions(id) ON DELETE CASCADE,
    external_subscription_id TEXT REFERENCES external_subscriptions(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    membership_id TEXT NOT NULL REFERENCES memberships(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_seat_alloc_sub_active
    ON seat_allocations(subscription_id, membership_id)
    WHERE revoked_at IS NULL AND subscription_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_seat_alloc_ext_active
    ON seat_allocations(external_subscription_id, membership_id)
    WHERE revoked_at IS NULL AND external_subscription_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS employee_identity_mappings (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    membership_id TEXT NOT NULL REFERENCES memberships(id) ON DELETE CASCADE,
    provider_code TEXT NOT NULL,
    external_user_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, provider_code, external_user_ref)
);

CREATE TABLE IF NOT EXISTS financial_obligations (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subscription_id TEXT REFERENCES subscriptions(id) ON DELETE SET NULL,
    external_subscription_id TEXT REFERENCES external_subscriptions(id) ON DELETE SET NULL,
    subscriber_contact_id TEXT REFERENCES subscriber_contacts(id) ON DELETE SET NULL,
    direction TEXT NOT NULL CHECK (direction IN ('RECEIVABLE', 'PAYABLE')),
    party_ref TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'issued', 'partially_paid', 'paid', 'overdue', 'void')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    obligation_id TEXT NOT NULL UNIQUE REFERENCES financial_obligations(id) ON DELETE CASCADE,
    subscription_period_id TEXT REFERENCES subscription_periods(id) ON DELETE SET NULL,
    document_number TEXT NOT NULL UNIQUE,
    invoice_type TEXT NOT NULL DEFAULT 'standard',
    total_minor BIGINT NOT NULL CHECK (total_minor >= 0),
    currency TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'issued', 'partially_paid', 'paid', 'overdue', 'void')),
    issued_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_period_type
    ON invoices(subscription_period_id, invoice_type)
    WHERE subscription_period_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS invoice_lines (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price_minor BIGINT NOT NULL CHECK (unit_price_minor >= 0),
    total_minor BIGINT NOT NULL CHECK (total_minor >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    invoice_id TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    method TEXT NOT NULL,
    external_ref TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'reversed')),
    recorded_by_user_id TEXT NOT NULL REFERENCES users(id),
    paid_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    paid_at TEXT NOT NULL,
    recorded_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS financial_adjustments (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    invoice_id TEXT REFERENCES invoices(id),
    payment_id TEXT REFERENCES payments(id),
    amount_minor BIGINT NOT NULL,
    currency TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    external_event_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    subscription_period_id TEXT NOT NULL REFERENCES subscription_periods(id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    seat_allocation_id TEXT REFERENCES seat_allocations(id) ON DELETE SET NULL,
    quantity BIGINT NOT NULL CHECK (quantity > 0),
    source TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(service_id, external_event_id)
);

CREATE TABLE IF NOT EXISTS usage_counters (
    id TEXT PRIMARY KEY,
    subscription_period_id TEXT NOT NULL REFERENCES subscription_periods(id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    consumed BIGINT NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    updated_at TEXT NOT NULL,
    UNIQUE(subscription_period_id, metric_id)
);

CREATE TABLE IF NOT EXISTS integration_connections (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    provider_code TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_ref TEXT NOT NULL DEFAULT '',
    privacy_scope TEXT NOT NULL DEFAULT 'metadata_only',
    status TEXT NOT NULL DEFAULT 'connected',
    config_json TEXT NOT NULL DEFAULT '{}',
    last_sync_success TEXT,
    coverage_start TEXT,
    coverage_end TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_usage_records (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
    external_record_id TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    membership_id TEXT REFERENCES memberships(id) ON DELETE SET NULL,
    model_name TEXT,
    requests_count INTEGER,
    input_tokens BIGINT,
    output_tokens BIGINT,
    total_tokens BIGINT,
    cache_read_tokens BIGINT,
    cache_creation_tokens BIGINT,
    reported_cost_minor BIGINT,
    estimated_value_minor BIGINT,
    currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL,
    reliability TEXT NOT NULL,
    billing_lane TEXT NOT NULL DEFAULT '',
    application TEXT NOT NULL DEFAULT '',
    images_generated BIGINT,
    images_uploaded BIGINT,
    document_reads BIGINT,
    documents_generated BIGINT,
    source_code_reads BIGINT,
    request_ref TEXT,
    session_id TEXT,
    quality_note TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(connection_id, external_record_id)
);

CREATE TABLE IF NOT EXISTS personal_subscriptions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    service_name TEXT NOT NULL,
    service_url TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'General',
    product_type TEXT NOT NULL DEFAULT 'recurring_subscription' CHECK (product_type IN ('recurring_subscription', 'one_time_purchase', 'trial')),
    plan_name TEXT NOT NULL DEFAULT '',
    price_minor BIGINT NOT NULL CHECK (price_minor >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    billing_interval TEXT NOT NULL CHECK (billing_interval IN ('monthly', 'quarterly', 'annual', 'yearly', 'weekly', 'one_time', 'custom')),
    next_renewal_date TEXT NOT NULL,
    start_date TEXT,
    trial_end_date TEXT,
    anchor_day INTEGER NOT NULL CHECK (anchor_day >= 1 AND anchor_day <= 31),
    auto_renew INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    notes TEXT NOT NULL DEFAULT '',
    recipient_email TEXT,
    reminder_offsets_json TEXT NOT NULL DEFAULT '[7,3,1]',
    monitoring_source TEXT NOT NULL DEFAULT 'not_connected',
    tracking_status TEXT NOT NULL DEFAULT 'tracking_active',
    status TEXT NOT NULL DEFAULT 'active_user_recorded',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personal_monitoring_sources (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('browser_extension', 'desktop_helper', 'litellm', 'official_api')),
    enabled INTEGER NOT NULL DEFAULT 0,
    allowlist_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, source_type)
);

CREATE TABLE IF NOT EXISTS personal_activity (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    personal_subscription_id TEXT REFERENCES personal_subscriptions(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    domain_or_app TEXT NOT NULL,
    observed_active_seconds INTEGER,
    visit_count INTEGER,
    tokens BIGINT,
    reliability TEXT NOT NULL,
    quality_note TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_versions (
    id TEXT PRIMARY KEY,
    provider_code TEXT NOT NULL,
    model_name TEXT NOT NULL,
    input_per_million_usd TEXT NOT NULL,
    output_per_million_usd TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_rules (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    rule_type TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    schedule_offset_days INTEGER NOT NULL DEFAULT 3,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_jobs (
    id TEXT PRIMARY KEY,
    logical_key TEXT NOT NULL UNIQUE,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    rule_id TEXT REFERENCES notification_rules(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'retrying', 'accepted_by_relay', 'delivered', 'failed', 'suppressed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES notification_jobs(id) ON DELETE CASCADE,
    provider_msg_id TEXT,
    result_status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    attempted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subscriber_contact_id TEXT NOT NULL REFERENCES subscriber_contacts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'whatsapp')),
    scope TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('granted', 'revoked')),
    granted_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, subscriber_contact_id, channel, scope)
);

CREATE TABLE IF NOT EXISTS sender_domains (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    spf_verified INTEGER NOT NULL DEFAULT 0,
    dkim_verified INTEGER NOT NULL DEFAULT 0,
    dmarc_verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending_dns' CHECK (status IN ('pending_dns', 'verified', 'failed')),
    verification_token TEXT NOT NULL,
    verified_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(organization_id, domain)
);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    events_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    invoice_id TEXT REFERENCES invoices(id) ON DELETE SET NULL,
    report_type TEXT NOT NULL CHECK (report_type IN ('payment_receipt', 'invoice', 'statement')),
    kind TEXT NOT NULL DEFAULT '',
    period_start TEXT,
    period_end TEXT,
    filters_json TEXT NOT NULL DEFAULT '{}',
    document_number TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    object_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('queued', 'rendering', 'ready', 'failed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_download_tokens (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES outbox_events(id) ON DELETE CASCADE,
    destination TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    delivered_at TEXT,
    UNIQUE(event_id, destination)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT,
    tenant_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materialized_analytics_runs (
    id TEXT PRIMARY KEY,
    pipeline_name TEXT NOT NULL,
    status TEXT NOT NULL,
    bronze_count BIGINT NOT NULL DEFAULT 0,
    silver_count BIGINT NOT NULL DEFAULT 0,
    gold_count BIGINT NOT NULL DEFAULT 0,
    duplicates_removed BIGINT NOT NULL DEFAULT 0,
    rejected_count BIGINT NOT NULL DEFAULT 0,
    refreshed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS daily_usage_aggregates (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    day TEXT NOT NULL,
    total_quantity BIGINT NOT NULL,
    distinct_events BIGINT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, service_id, metric_key, day)
);

CREATE TABLE IF NOT EXISTS daily_finance_aggregates (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    day TEXT NOT NULL,
    invoiced_minor BIGINT NOT NULL,
    collected_minor BIGINT NOT NULL,
    expenses_minor BIGINT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, currency, day)
);


-- SubSphere v4.1 Additive Tables (Preserves all v4.0 data)

CREATE TABLE IF NOT EXISTS notification_preferences (
    id TEXT PRIMARY KEY,
    account_id TEXT UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    email_enabled INTEGER NOT NULL DEFAULT 1,
    primary_email TEXT NOT NULL DEFAULT '',
    default_offsets_json TEXT NOT NULL DEFAULT '[7,3,1]',
    send_hour_local INTEGER NOT NULL DEFAULT 9,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    monthly_report_opt_in INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_rules (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    subscription_id TEXT NOT NULL,
    recipient TEXT NOT NULL,
    days_before INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    channel TEXT NOT NULL DEFAULT 'email',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    send_hour_local INTEGER NOT NULL DEFAULT 9,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(subscription_id, recipient, days_before, channel)
);

CREATE TABLE IF NOT EXISTS tracking_connections (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paired',
    device_name TEXT NOT NULL DEFAULT 'browser',
    pair_code_hash TEXT,
    pair_expires_at TEXT,
    token_hash TEXT UNIQUE,
    permissions_json TEXT NOT NULL DEFAULT '[]',
    allowlist_json TEXT NOT NULL DEFAULT '[]',
    last_seen TEXT,
    last_synced TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_daily (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    personal_subscription_id TEXT REFERENCES personal_subscriptions(id) ON DELETE SET NULL,
    source_id TEXT,
    source_device TEXT NOT NULL DEFAULT 'default',
    record_id TEXT NOT NULL,
    domain_or_app TEXT NOT NULL,
    day TEXT NOT NULL,
    visits INTEGER NOT NULL DEFAULT 0,
    active_seconds INTEGER,
    coverage TEXT NOT NULL DEFAULT 'live_focused',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, source_device, record_id)
);

CREATE TABLE IF NOT EXISTS otel_metric_checkpoints (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    token_type TEXT NOT NULL DEFAULT '',
    temporality TEXT NOT NULL DEFAULT 'delta',
    last_cumulative_value BIGINT NOT NULL DEFAULT 0,
    last_timestamp TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(account_id, session_id, metric_name, model_name, token_type)
);

CREATE TABLE IF NOT EXISTS calendar_event_overrides (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'custom',
    amount_minor BIGINT,
    currency TEXT NOT NULL DEFAULT 'USD',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universal_connector_events (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    organization_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
    connection_id TEXT REFERENCES integration_connections(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    application TEXT NOT NULL,
    source TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT '',
    model_name TEXT,
    quantity BIGINT NOT NULL DEFAULT 1,
    input_tokens BIGINT,
    output_tokens BIGINT,
    cache_tokens BIGINT,
    total_tokens BIGINT,
    reported_cost_minor BIGINT,
    active_seconds INTEGER,
    verified INTEGER NOT NULL DEFAULT 1,
    request_ref TEXT,
    session_id TEXT,
    period_day TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
