export type ViewMode = "individual" | "provider";

export interface UserInfo {
  id: string;
  email: string;
  masked_email?: string;
  relay_mask_email?: string;
  full_name: string;
  email_verified?: boolean;
}

export interface OrganizationMembership {
  organization_id: string;
  organization_name: string;
  slug: string;
  role: string;
  default_currency: string;
}

export interface AuthMeResponse {
  user: UserInfo;
  personal_account_id: string;
  active_org_id: string | null;
  memberships: OrganizationMembership[];
}

export interface PersonalSubscription {
  id: string;
  service_name: string;
  service_url?: string;
  category: string;
  product_type: "recurring_subscription" | "one_time_purchase" | "trial";
  plan_name: string;
  price_minor: number;
  currency: string;
  billing_interval: "monthly" | "quarterly" | "annual" | "yearly" | "weekly" | "one_time" | "custom";
  next_renewal_date: string;
  start_date?: string | null;
  timezone?: string;
  notes?: string;
  recipient_email?: string | null;
  tracking_status?: "tracking_active" | "stopped_tracking" | "canceled_at_provider";
  version?: number;
  auto_renew: boolean;
  reminder_offsets_days: number[];
  monitoring_source: string;
  status: string;
  monthly_equivalent_minor: number;
  annual_equivalent_minor: number;
}

export interface CalendarEvent {
  event_id: string;
  date: string;
  title: string;
  kind: string;
  status: string;
  amount_minor: number;
  currency: string;
  subscription_id?: string;
  invoice_id?: string;
  subscriber_contact_id?: string;
  billing_interval?: string;
  plan_name?: string;
  tracking_status?: string;
  timezone?: string;
  reminder_offsets_days?: number[];
  notes?: string;
}

export interface CurrencyTotal {
  currency: string;
  monthly_equivalent_minor: number;
  annual_equivalent_minor: number;
  one_time_spend_minor?: number;
  subscription_count: number;
}

export interface PersonalDashboardResponse {
  currency_totals: CurrencyTotal[];
  upcoming_renewals: PersonalSubscription[];
  category_breakdown: Record<string, Record<string, number>>;
  active_subscription_count: number;
  monitoring_sources: {
    source_type: string;
    enabled: boolean;
    allowlist: string[];
    updated_at: string;
  }[];
}

export interface AiUsageRecord {
  id: string;
  external_record_id: string;
  provider_code: string;
  connection_name: string;
  model_name: string | null;
  requests_count: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  reported_cost_minor: number | null;
  estimated_value_minor: number | null;
  currency: string;
  source: string;
  reliability: string;
  quality_note: string;
  occurred_at: string;
}

export interface AiUsageResponse {
  currency: string;
  authorized?: boolean;
  credits?: {
    monthly_limit_minor: number;
    used_credits_minor: number;
    remaining_credits_minor: number;
    currency: string;
    last_ping_at?: string | null;
  };
  cost_separation: {
    subscription_fee_minor: number | null;
    reported_extra_usage_minor: number | null;
    estimated_token_value_minor: number | null;
    note: string;
  };
  tokens: {
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
    requests_count: number | null;
    unavailable_fields: string[];
  };
  records: AiUsageRecord[];
  observed_activity: {
    id: string;
    domain_or_app: string;
    source: string;
    observed_active_seconds: number | null;
    visit_count: number | null;
    tokens: number | null;
    reliability: string;
    quality_note: string;
    occurred_at: string;
  }[];
  record_count: number;
}

export interface FreshnessStatus {
  has_materialized_run: boolean;
  last_refreshed_at: string | null;
  staleness_seconds: number | null;
  is_stale: boolean;
  banner: string | null;
  pending_or_failed_deliveries: number;
}

export interface ReconciliationReport {
  organization_id: string | null;
  reconciled_at: string;
  postgres_ledger: { accepted_events: number; total_quantity: number };
  openmeter: { distinct_cloudevents: number; total_quantity: number };
  spark_gold: { distinct_events: number; total_quantity: number };
  discrepancies: {
    postgres_vs_openmeter_events: number;
    postgres_vs_openmeter_quantity: number;
    postgres_vs_spark_gold_events: number;
    postgres_vs_spark_gold_quantity: number;
  };
  reconciled_zero_discrepancy: boolean;
}
