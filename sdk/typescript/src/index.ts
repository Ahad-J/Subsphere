/**
 * SubSphere 4.0 TypeScript Developer SDK for usage metering and entitlements.
 */

export interface ConsumeUsageInput {
  subscriptionId: string;
  metricKey: string;
  quantity: number;
  externalEventId?: string;
  occurredAt?: string;
  seatAllocationId?: string;
}

export interface ConsumeUsageResult {
  accepted: boolean;
  duplicate: boolean;
  metric_key: string;
  used: number;
  limit: number | null;
  remaining: number | null;
  event_id: string;
  source: string;
}

export class SubSphereApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details: Record<string, unknown> = {}
  ) {
    super(message);
    this.name = 'SubSphereApiError';
  }
}

export class SubSphereClient {
  constructor(
    private readonly apiKey: string,
    private readonly baseUrl = 'http://localhost:8000'
  ) {}

  async consumeUsage(input: ConsumeUsageInput): Promise<ConsumeUsageResult> {
    const eventId = input.externalEventId ?? `sdk_${crypto.randomUUID()}`;
    const res = await fetch(`${this.baseUrl.replace(/\/$/, '')}/api/v1/usage/consume`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': this.apiKey,
        'Idempotency-Key': eventId,
      },
      body: JSON.stringify({
        external_event_id: eventId,
        subscription_id: input.subscriptionId,
        metric_key: input.metricKey,
        quantity: input.quantity,
        occurred_at: input.occurredAt,
        seat_allocation_id: input.seatAllocationId,
        source: 'PROVIDER_SDK',
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new SubSphereApiError(res.status, data.code ?? 'API_ERROR', data.message ?? 'Request failed', data.details);
    }
    return data as ConsumeUsageResult;
  }
}
