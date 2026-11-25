# SubSphere v4.1 Implementation Progress (`v4.1`)

**Directive:** SubSphere v4.1 In-Place Repair & Feature Completion (`R0`–`R8`)  
**Started:** 2026-11-14  

## Gate Status Overview

| Gate | Description | Status | Notes |
|---|---|---|---|
| **R0** | Codebase & Runtime Audit + `docs/audits/v4.1-before.md` | **Done** | Baseline 30/30 v4.0 pytest tests verified passing before edits. |
| **R1** | Additive Migrations, Editable Subscriptions, Calendar Month Grid API | **Done** | Schema migrations, editable personal & provider subscriptions (`PATCH` + optimistic `version`), month calendar `/api/v1/calendar/events`. |
| **R2** | SMTP Preferences, Test Email, Reminder Rules & Worker Scheduler | **Done** | `/api/v1/notifications/preferences|test-email|history`, `/api/v1/subscriptions/{id}/reminder-rules`, `/api/v1/integrations/email/status`, worker reminder scheduling + SMTP dispatch in `outbox_dispatcher.py`. |
| **R3** | Styled HTML/PDF History Report, Preview & Email Send | **Done** | `/api/v1/history`, `POST /api/v1/reports` (`kind=history_report`), `/reports/{id}/preview` (styled HTML + inline SVG bar chart), multi-page pure-Python PDF fallback, `/reports/{id}/send-email`. |
| **R4** | Chrome MV3 Extension Pairing, Focus/Idle Guards & History Import | **Done** | Upgraded `manifest.json`, `background.js`, `popup.html/.js`, `options.html/.js` with pairing, `windows.onFocusChanged`, `idle.onStateChanged`, persistent retry queue, and opt-in local domain history import. |
| **R5** | Claude Code OpenTelemetry Collector & Ingest Adapter | **Done** | `ClaudeCodeOtelIngestAdapter` (`otel_adapter.py`) with cumulative/delta checkpointing & prompt-content rejection (`422`), `/integrations/otel/pair-token|claude-code|status`, `/v1/metrics`, `/integrations/mcp/tools`, and `compose.telemetry.yaml`. |
| **R6** | LiteLLM Gateway Wizard, Virtual Keys & Cross-Source Dedupe | **Done** | `/integrations/litellm/health|virtual-key|test-request` (`api_key` vs `claude_subscription` billing lanes), `request_ref` deduplication in `GET /api/v1/ai/usage`, and `service_runner.py` `/key/generate`. |
| **R7** | Provider Overdue Workflow, Employee AI Usage & Full v4.1 React UI | **Done** | `POST /api/v1/invoices/{id}/send-reminder`, `GET /api/v1/provider/ai/usage`, 7-tab `IndividualView.tsx`, 9-tab `ProviderView.tsx`, and clean `tsc && vite build` (`dist/index.html` built). |
| **R8** | Modular Compose Telemetry Profile, `Makefile` & Full Test Suite | **Done** | Validated `compose.telemetry.yaml`, `Makefile` (`test-*`, `up-*`, `smoke`), `run-all.sh config`, full 36/36 backend pytest matrix, and `tsc && vite build`. |

---

## Gate Log
- **R0**: Audited existing backend routers, DB schema, Alembic runner, frontend components, browser extension, workers, and Compose files. Saved findings in `docs/audits/v4.1-before.md`.
- **R1–R7**: Implemented additive DB schema migrations, optimistic-locking subscription `PATCH` + `calendar/events` month projection, SMTP preferences/test-email/reminder rules & worker scheduler, styled HTML/SVG/PDF history reports + preview + send-email dispatch, Chrome MV3 extension pairing/focus/idle/history-import helpers, Claude Code OpenTelemetry cumulative/delta ingest + privacy rejection (`422`), LiteLLM virtual key & billing-lane separation (`api_key` vs `claude_subscription`) + cross-source `request_ref` deduplication, Provider overdue invoice reminder & employee AI usage views, and full 7-tab `IndividualView.tsx` + 9-tab `ProviderView.tsx` UI.
- **R8**: Completed `Makefile`, `compose.telemetry.yaml`, updated `run-all.sh`, fixed calendar `to_date` inclusive range handling, `event_id`/`kind` payload fields, `pair_extension` reuse `409` status, `test-email` rate-limit ordering, `compute_estimated_token_value_minor` keyword unpacking, and `build_history_dataset` date parsing; verified all 36 tests and frontend production build.

## Verification Evidence & Runtime Boundaries

### Verified Commands & Observed Results (after final code edit)
- `cd backend && python3 -m pytest -v`: **36 passed, 0 failed, 0 skipped in 28.69s** (30 baseline M0–M7 unit/integration tests + 6 comprehensive v4.1 end-to-end tests in `tests/integration/test_v41_complete_matrix.py`).
- `cd frontend && npm run build` (`tsc --noEmit && vite build`): **exit code 0, built in 1.18s** (`dist/index.html 0.44 kB`, `dist/assets/index-BslOAMTn.css 7.16 kB`, `dist/assets/index-CH9E3y4a.js 277.10 kB`).
- `bash run-all.sh config`: **exit code 0**, `All Compose configurations are valid.`
- `make test-calendar`: **1 passed, 0 failed, 0 skipped in 1.07s**.
- `node -e "...extractAllowlistedDomain / summarizeHistoryItems..."`: **exit code 0**, verified domain-only extraction (`claude.ai`, `null` for non-allowlisted domain) and non-reconstructed `active_seconds: null` for `history_import_may_include_synced`.

### Test Mode vs Live Container / Browser Runtime Distinction
- **In-Process Real Protocol Sinks (Executed in Automated Tests)**:
  - Email delivery (`MAIL-01..05`, `PDF-03`, `PROV-01`) was exercised over live loopback RFC 5321 TCP SMTP sockets (`smtplib`) using temporary SQLite isolation.
  - LiteLLM health, `/key/generate`, and chat completions gateway forwarding (`LLM-01..04`) were exercised over a live loopback Python `HTTPServer` speaking LiteLLM REST JSON.
- **Static & Unit Helper Verification Only (Not Live Container / Browser Runtime in This Session)**:
  - Full Docker Compose container runtime startup (`docker compose up` across Mailpit, Postgres, LiteLLM proxy, OpenTelemetry collector, frontend Nginx), real Chrome MV3 unpacked extension loading/UI clicking, and interactive browser rendering against localhost were **not** executed live in this terminal session (`run-all.sh config` validated Compose syntax only).
  - Test email rate limits remain process-local in-memory for dev/single-instance FastAPI processes and `SQLite` was used as the test database engine in `pytest` while PostgreSQL DDL & additive column guards in `schema.sql` / `engine.py` remain untested against a live PostgreSQL instance in this session.
