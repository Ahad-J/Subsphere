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
