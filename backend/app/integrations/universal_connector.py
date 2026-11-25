"""SubSphere Universal Usage Connector & AI Usage Intelligence Engine.

Normalizes telemetry across the six practical tracking methods:
1. Official Provider APIs (Cursor Team Admin API, OpenAI Usage/Image API, Anthropic Admin API)
2. OpenTelemetry (Claude Code, Codex, Cursor Enterprise)
3. Local AI Gateway (LiteLLM routed traffic)
4. Coding-Agent Hooks & MCP (document reads, source-code reads, generated docs/images)
5. Browser Extension (observed web app time, visits, and prompt/upload interactions; tokens unavailable)
6. Desktop Companion (observed process focus time; tokens unavailable)

Strictly maintains privacy (rejects raw prompts, document bodies, file contents, and cookies)
and never fabricates or zeroes out unavailable token/cost telemetry.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from app.audit.service import record_audit_log
from app.core.clock import get_clock
from app.core.errors import DomainError
from app.integrations.litellm_adapter import (
    compute_estimated_token_value_minor,
    mask_credential,
    parse_decimal,
)


TRACKING_METHODS_CATALOG: list[dict[str, Any]] = [
    {
        "id": "official_provider_api",
        "rank": 1,
        "name": "Official Provider APIs",
        "accuracy": "most_accurate",
        "supports": ["tokens", "requests", "model_details", "reported_cost", "images_generated"],
        "limitations": "Requires vendor admin/organization API credentials (e.g., Cursor Team Admin API).",
    },
    {
        "id": "opentelemetry",
        "rank": 2,
        "name": "OpenTelemetry (OTel)",
        "accuracy": "exact_telemetry",
        "supports": ["tokens", "cache_tokens", "requests", "sessions", "estimated_cost", "tool_events"],
        "limitations": "Tool must support OTLP telemetry export (e.g., Claude Code, Cursor Enterprise, Codex). No LiteLLM gateway required.",
    },
    {
        "id": "litellm_gateway",
        "rank": 3,
        "name": "Local AI Gateway (LiteLLM)",
        "accuracy": "exact_if_routed",
        "supports": ["tokens", "requests", "model_details", "billing_lanes", "reported_cost"],
        "limitations": "Client must explicitly route requests through the gateway; cannot passively monitor unrelated apps.",
    },
    {
        "id": "agent_tool_hook",
        "rank": 4,
        "name": "Coding-Agent Hooks & MCP",
        "accuracy": "observed_tool_calls",
        "supports": ["source_code_reads", "document_reads", "documents_generated", "images_generated", "tool_calls"],
        "limitations": "Instruments tool execution metadata only (never file contents or prompts).",
    },
    {
        "id": "browser_extension",
        "rank": 5,
        "name": "Browser Extension",
        "accuracy": "observed_interactions",
        "supports": ["application_activity", "visits", "prompt_interactions", "upload_interactions"],
        "limitations": "Observes approved sites only; server-side token consumption remains Unavailable.",
    },
    {
        "id": "desktop_companion",
        "rank": 6,
        "name": "Desktop Companion",
        "accuracy": "observed_activity",
        "supports": ["application_activity", "focus_time"],
        "limitations": "Focus time only; cannot recover exact token counts from encrypted desktop app traffic.",
    },
]

APPLICATION_CONNECTOR_MATRIX: dict[str, dict[str, Any]] = {
    "claude_code": {
        "application": "claude_code",
        "display_name": "Claude Code",
        "primary_method": "opentelemetry",
        "secondary_methods": ["agent_tool_hook"],
        "default_source": "opentelemetry",
        "source_badge": "OTel",
        "notes": "Exports OTel tokens (input/output/cache), API requests, sessions, and tool-hook file reads. No LiteLLM required.",
    },
    "cursor": {
        "application": "cursor",
        "display_name": "Cursor",
        "primary_method": "official_provider_api",
        "secondary_methods": ["opentelemetry", "agent_tool_hook"],
        "default_source": "api",
        "source_badge": "API",
        "notes": "Auto-selects Admin API for Team/Business plans, OTel for Enterprise plans, or Coding-Agent Hook / MCP for Personal plans.",
    },
    "codex": {
        "application": "codex",
        "display_name": "Codex",
        "primary_method": "opentelemetry",
        "secondary_methods": ["litellm_gateway", "agent_tool_hook"],
        "default_source": "opentelemetry",
        "source_badge": "OTel",
        "notes": "Exports OTel metrics, routed gateway counts, or agent hook file operations.",
    },
    "litellm_gateway": {
        "application": "litellm_gateway",
        "display_name": "LiteLLM Gateway",
        "primary_method": "litellm_gateway",
        "secondary_methods": [],
        "default_source": "litellm",
        "source_badge": "Gateway",
        "notes": "Captures exact tokens and costs for explicitly routed API traffic.",
    },
    "claude_web": {
        "application": "claude_web",
        "display_name": "Claude Web",
        "primary_method": "browser_extension",
        "secondary_methods": [],
        "default_source": "browser",
        "source_badge": "Browser",
        "notes": "Observes claude.ai active time, visits, and upload/prompt interactions only; server-side tokens are Unavailable.",
    },
    "chatgpt_web": {
        "application": "chatgpt_web",
        "display_name": "ChatGPT Web",
        "primary_method": "browser_extension",
        "secondary_methods": [],
        "default_source": "browser",
        "source_badge": "Browser",
        "notes": "Observes chatgpt.com active time and upload/prompt interactions only; server-side tokens are Unavailable.",
    },
    "huggingchat": {
        "application": "huggingchat",
        "display_name": "HuggingChat",
        "primary_method": "agent_tool_hook",
        "secondary_methods": ["browser_extension"],
        "default_source": "mcp_server",
        "source_badge": "MCP",
        "notes": "Connects via SubSphere HTTP MCP Server (/api/v1/mcp) on huggingface.co/chat and optionally observes tab time via Browser Extension.",
    },
}

FIVE_MEASUREMENTS_SPEC: list[dict[str, str]] = [
    {"key": "model_requests", "label": "Model Requests", "example": "75 AI model calls today", "accuracy": "Exact via API/OTel/Gateway telemetry (distinct from prompts & tool calls)"},
    {"key": "tokens_consumed", "label": "Tokens Consumed", "example": "45,000 input + 12,000 output tokens", "accuracy": "Exact when reported by telemetry; otherwise Unavailable"},
    {"key": "images_generated", "label": "Images", "example": "8 images generated, 3 uploaded", "accuracy": "Exact for generation API/tool events; observed for web uploads"},
    {"key": "documents_accessed", "label": "Documents Accessed", "example": "4 PDFs uploaded, 12 source files read", "accuracy": "Exact via instrumented agent file-read hooks; otherwise partial"},
    {"key": "application_activity", "label": "Application Activity", "example": "Claude Web used for 45 minutes", "accuracy": "Observed via browser extension or desktop companion"},
]

_FORBIDDEN_PRIVACY_KEYS = {
    "content",
    "raw_text",
    "document_body",
    "file_content",
    "document_content",
    "file_body",
    "prompt",
    "prompts",
    "messages",
    "completion",
    "cookie",
    "cookies",
    "password",
}

_SOURCE_BADGE_MAP: dict[str, str] = {
    "official_provider_api": "API",
    "OFFICIAL_PROVIDER_API": "API",
    "api": "API",
    "opentelemetry": "OTel",
    "CLAUDE_CODE_OTEL": "OTel",
    "otel": "OTel",
    "litellm_gateway": "Gateway",
    "GATEWAY_ROUTED": "Gateway",
    "litellm": "Gateway",
    "agent_tool_hook": "Hook",
    "AGENT_TOOL_HOOK": "Hook",
    "mcp_server": "MCP",
    "MCP_SERVER": "MCP",
    "browser_extension": "Browser",
    "ALLOWED_BROWSER_ACTIVITY": "Browser",
    "browser": "Browser",
    "desktop_companion": "Desktop",
    "ALLOWED_DESKTOP_ACTIVITY": "Desktop",
    "desktop": "Desktop",
}

_SOURCE_CANONICAL_MAP: dict[str, str] = {
    "OFFICIAL_PROVIDER_API": "api",
    "official_provider_api": "api",
    "api": "api",
    "CLAUDE_CODE_OTEL": "opentelemetry",
    "opentelemetry": "opentelemetry",
    "otel": "opentelemetry",
    "GATEWAY_ROUTED": "litellm",
    "litellm_gateway": "litellm",
    "litellm": "litellm",
    "AGENT_TOOL_HOOK": "agent_tool_hook",
    "agent_tool_hook": "agent_tool_hook",
    "MCP_SERVER": "mcp_server",
    "mcp_server": "mcp_server",
    "ALLOWED_BROWSER_ACTIVITY": "browser",
    "browser_extension": "browser",
    "browser": "browser",
    "ALLOWED_DESKTOP_ACTIVITY": "desktop",
    "desktop_companion": "desktop",
    "desktop": "desktop",
}

_SOURCE_PRIORITY: dict[str, int] = {
    "api": 1,
    "opentelemetry": 2,
    "litellm": 3,
    "mcp_server": 4,
    "agent_tool_hook": 5,
    "browser": 6,
    "desktop": 7,
}

_APP_DISPLAY_NAMES: dict[str, str] = {k: v["display_name"] for k, v in APPLICATION_CONNECTOR_MATRIX.items()}
_APP_DISPLAY_NAMES.update({
    "claude.ai": "Claude Web",
    "chatgpt.com": "ChatGPT Web",
    "chat.openai.com": "ChatGPT Web",
    "huggingface.co": "HuggingChat",
    "mcp_agent": "MCP Agent",
    "claude_desktop": "Claude Desktop",
})


def normalize_application_slug(app: Optional[str]) -> str:
    raw = (app or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "claude.ai": "claude_web",
        "claude_ai": "claude_web",
        "claudeweb": "claude_web",
        "chatgpt.com": "chatgpt_web",
        "chat.openai.com": "chatgpt_web",
        "chatgpt": "chatgpt_web",
        "huggingface.co": "huggingchat",
        "huggingface_chat": "huggingchat",
        "huggingchat": "huggingchat",
        "claudecode": "claude_code",
        "litellm": "litellm_gateway",
    }
    return aliases.get(raw, raw or "claude_code")


def select_best_connector(
    application: str,
    plan_tier: str = "personal",
    has_admin_api_key: bool = False,
    has_otel: bool = False,
) -> dict[str, Any]:
    """Select the highest-accuracy tracking method legitimately supported for the app + plan."""
    app_key = normalize_application_slug(application)
    tier = (plan_tier or "personal").strip().lower()

    if app_key == "cursor":
        if tier in ("team", "business") or has_admin_api_key:
            return {
                "application": "cursor",
                "display_name": "Cursor",
                "selected_method": "official_provider_api",
                "provider_code": "cursor_admin_api",
                "source": "api",
                "source_badge": "API",
                "fallback_reason": "Team/Business plan with Admin API uses official Cursor usage & cost endpoints.",
                "coverage": {
                    "tokens": "reported",
                    "requests": "reported",
                    "images": "unavailable",
                    "documents": "observed_tool_calls",
                    "activity": "unavailable",
                },
            }
        if tier == "enterprise":
            return {
                "application": "cursor",
                "display_name": "Cursor",
                "selected_method": "opentelemetry",
                "provider_code": "claude_code_otel",
                "source": "opentelemetry",
                "source_badge": "OTel",
                "fallback_reason": "Enterprise Cursor plan uses OTel export for model usage and agent tool events.",
                "coverage": {
                    "tokens": "reported",
                    "requests": "reported",
                    "images": "unavailable",
                    "documents": "observed_tool_calls",
                    "activity": "unavailable",
                },
            }
        return {
            "application": "cursor",
            "display_name": "Cursor",
            "selected_method": "agent_tool_hook",
            "provider_code": "agent_tool_hook",
            "source": "agent_tool_hook",
            "source_badge": "Hook",
            "fallback_reason": (
                "Personal Cursor subscriptions do not expose the Team Admin API; automatically fell back "
                "to Coding-Agent Hooks & MCP for file/tool telemetry (tokens remain Unavailable unless OTel/LiteLLM is connected)."
            ),
            "coverage": {
                "tokens": "unavailable",
                "requests": "observed_tool_calls",
                "images": "unavailable",
                "documents": "observed_tool_calls",
                "activity": "unavailable",
            },
        }

    if app_key in ("claude_code", "codex"):
        display = "Claude Code" if app_key == "claude_code" else "Codex"
        return {
            "application": app_key,
            "display_name": display,
            "selected_method": "opentelemetry",
            "provider_code": "claude_code_otel",
            "source": "opentelemetry",
            "source_badge": "OTel",
            "secondary_methods": ["agent_tool_hook"],
            "fallback_reason": f"{display} natively exports OpenTelemetry metrics (tokens, requests, cache, sessions) paired with coding-agent file hooks. No LiteLLM required.",
            "coverage": {
                "tokens": "reported",
                "requests": "reported",
                "images": "unavailable",
                "documents": "observed_tool_calls",
                "activity": "unavailable",
            },
        }

    if app_key == "litellm_gateway":
        return {
            "application": "litellm_gateway",
            "display_name": "LiteLLM Gateway",
            "selected_method": "litellm_gateway",
            "provider_code": "litellm",
            "source": "litellm",
            "source_badge": "Gateway",
            "fallback_reason": "Routes supported API requests through local LiteLLM proxy for exact token and spend accounting.",
            "coverage": {
                "tokens": "reported",
                "requests": "reported",
                "images": "unavailable",
                "documents": "unavailable",
                "activity": "unavailable",
            },
        }

    if app_key in ("claude_web", "chatgpt_web") or (app_key == "huggingchat" and tier == "browser"):
        display = (
            "HuggingChat"
            if app_key == "huggingchat"
            else ("Claude Web" if app_key == "claude_web" else "ChatGPT Web")
        )
        return {
            "application": app_key,
            "display_name": display,
            "selected_method": "browser_extension",
            "provider_code": "browser_extension",
            "source": "browser",
            "source_badge": "Browser",
            "fallback_reason": (
                f"{display} does not expose a consumer token API; Browser Extension observes approved tab time "
                "and upload/prompt interactions only (tokens remain Unavailable)."
            ),
            "coverage": {
                "tokens": "unavailable",
                "requests": "unavailable",
                "images": "observed_interactions",
                "documents": "observed_interactions",
                "activity": "observed_interactions",
            },
        }

    if app_key == "huggingchat":
        return {
            "application": "huggingchat",
            "display_name": "HuggingChat",
            "selected_method": "agent_tool_hook",
            "provider_code": "mcp_server",
            "source": "mcp_server",
            "source_badge": "MCP",
            "fallback_reason": "HuggingChat connects directly to SubSphere's HTTP MCP Server (/api/v1/mcp) plus optional Browser Extension tab tracking.",
            "coverage": {
                "tokens": "reported",
                "requests": "reported",
                "images": "observed_tool_calls",
                "documents": "observed_tool_calls",
                "activity": "observed_interactions",
            },
        }

    if has_otel:
        return {
            "application": app_key,
            "display_name": _APP_DISPLAY_NAMES.get(app_key, app_key.replace("_", " ").title()),
            "selected_method": "opentelemetry",
            "provider_code": "claude_code_otel",
            "source": "opentelemetry",
            "source_badge": "OTel",
            "fallback_reason": "OpenTelemetry selected as highest-accuracy source.",
            "coverage": {
                "tokens": "reported",
                "requests": "reported",
                "images": "unavailable",
                "documents": "observed_tool_calls",
                "activity": "unavailable",
            },
        }

    return {
        "application": app_key,
        "display_name": _APP_DISPLAY_NAMES.get(app_key, app_key.replace("_", " ").title()),
        "selected_method": "agent_tool_hook",
        "provider_code": "agent_tool_hook",
        "source": "agent_tool_hook",
        "source_badge": "Hook",
        "fallback_reason": "Defaulted to coding-agent tool hook / MCP metadata reporting.",
        "coverage": {
            "tokens": "unavailable",
            "requests": "observed_tool_calls",
            "images": "unavailable",
            "documents": "observed_tool_calls",
            "activity": "unavailable",
        },
    }


def validate_connector_event_privacy(payload: Any) -> None:
    """Reject events attempting to transmit raw prompts, document bodies, file contents, or secrets."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k).strip().lower() in _FORBIDDEN_PRIVACY_KEYS:
                raise DomainError(
                    400,
                    "PRIVACY_VIOLATION",
                    "SubSphere Universal Connector stores minimal event metadata only; never upload document contents or prompts.",
                )
            validate_connector_event_privacy(v)
    elif isinstance(payload, list):
        for item in payload:
            validate_connector_event_privacy(item)


def ensure_connector_connection(
    db: Any,
    *,
    account_id: str,
    organization_id: Optional[str],
    provider_code: str,
    display_name: str,
    credential_secret: Optional[str] = None,
    config_obj: Optional[dict[str, Any]] = None,
) -> str:
    import json as _json

    now_iso = get_clock().now_iso()
    if organization_id:
        row = db.fetchone(
            """
            SELECT id FROM integration_connections
            WHERE organization_id = %s AND provider_code = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (organization_id, provider_code),
        )
    else:
        row = db.fetchone(
            """
            SELECT id FROM integration_connections
            WHERE account_id = %s AND organization_id IS NULL AND provider_code = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (account_id, provider_code),
        )
    cred_ref = mask_credential(credential_secret) if credential_secret else "universal_connector"
    cfg_json = _json.dumps(config_obj or {}, separators=(",", ":"))
    if row:
        db.execute(
            """
            UPDATE integration_connections
            SET status = 'connected', display_name = %s,
                credential_ref = CASE WHEN %s != 'universal_connector' THEN %s ELSE credential_ref END,
                config_json = %s, last_sync_success = %s
            WHERE id = %s
            """,
            (display_name, cred_ref, cred_ref, cfg_json, now_iso, row["id"]),
        )
        return row["id"]

    conn_id = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO integration_connections (
            id, account_id, organization_id, provider_code, display_name,
            credential_ref, privacy_scope, status, config_json, last_sync_success, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'metadata_only', 'connected', %s, %s, %s)
        """,
        (conn_id, account_id, organization_id, provider_code, display_name, cred_ref, cfg_json, now_iso, now_iso),
    )
    return conn_id


_VALID_EVENT_TYPES = {
    "document_read",
    "source_code_read",
    "document_generated",
    "image_generated",
    "image_uploaded",
    "model_request",
    "prompt_submitted",
    "application_activity",
    "tool_call",
}


def _default_source_for_event(event_type: str, app_slug: str, raw_source: Optional[str]) -> tuple[str, str, bool]:
    """Return (canonical_source, external_record_source, default_verified)."""
    if raw_source:
        canon = _SOURCE_CANONICAL_MAP.get(raw_source, raw_source.strip().lower())
    elif event_type in ("document_read", "source_code_read", "document_generated", "tool_call"):
        canon = "agent_tool_hook"
    elif event_type in ("application_activity", "prompt_submitted") or app_slug in ("claude_web", "chatgpt_web"):
        canon = "browser"
    elif app_slug in ("claude_code", "codex"):
        canon = "opentelemetry"
    elif app_slug == "litellm_gateway":
        canon = "litellm"
    elif app_slug == "cursor":
        canon = "api"
    else:
        canon = "agent_tool_hook"

    ext_src_map = {
        "api": "OFFICIAL_PROVIDER_API",
        "opentelemetry": "CLAUDE_CODE_OTEL",
        "litellm": "GATEWAY_ROUTED",
        "agent_tool_hook": "AGENT_TOOL_HOOK",
        "mcp_server": "MCP_SERVER",
        "browser": "ALLOWED_BROWSER_ACTIVITY",
        "desktop": "ALLOWED_DESKTOP_ACTIVITY",
    }
    ext_src = ext_src_map.get(canon, "AGENT_TOOL_HOOK")
    default_verified = canon not in ("browser", "desktop") and event_type not in ("prompt_submitted", "application_activity")
    return canon, ext_src, default_verified


def _pricing_lookup(db: Any, provider_code: str, model_name: Optional[str], occurred_at: str) -> dict[str, Any] | None:
    if not model_name:
        return None
    row = db.fetchone(
        """
        SELECT * FROM pricing_versions
        WHERE model_name = %s AND effective_from <= %s
          AND (provider_code = %s OR provider_code = '*')
        ORDER BY CASE WHEN provider_code = %s THEN 0 ELSE 1 END ASC,
                 effective_from DESC, created_at DESC
        LIMIT 1
        """,
        (model_name, occurred_at, provider_code, provider_code),
    )
    if row is not None:
        return row
    return db.fetchone(
        """
        SELECT * FROM pricing_versions
        WHERE model_name = %s AND effective_from <= %s
        ORDER BY effective_from DESC, created_at DESC
        LIMIT 1
        """,
        (model_name, occurred_at),
    )


def ingest_universal_events(
    db: Any,
    *,
    account_id: str,
    organization_id: Optional[str],
    actor_user_id: Optional[str],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate privacy, store universal connector events, and mirror verified token/cost events."""
    now_iso = get_clock().now_iso()
    today = now_iso[:10]
    stored: list[dict[str, Any]] = []

    for ev in events:
        validate_connector_event_privacy(ev)
        ev_type = str(ev.get("event_type") or "").strip().lower()
        if ev_type not in _VALID_EVENT_TYPES:
            raise DomainError(400, "INVALID_EVENT_TYPE", f"Unsupported event_type '{ev_type}'.")

        app_slug = normalize_application_slug(ev.get("application"))
        canon_source, ext_source, default_verified = _default_source_for_event(ev_type, app_slug, ev.get("source"))
        verified = 1 if (ev.get("verified") if ev.get("verified") is not None else default_verified) else 0
        # Browser/desktop activity & prompt observations can never claim verified server-side token usage
        if canon_source in ("browser", "desktop") or ev_type in ("prompt_submitted", "application_activity"):
            verified = 0

        qty = max(1, int(ev.get("quantity") if ev.get("quantity") is not None else 1))
        file_type = str(ev.get("file_type") or "").strip().lower()
        media_type = str(ev.get("media_type") or "").strip().lower()
        model_name = str(ev.get("model_name") or ev.get("model") or "").strip() or None

        in_tok = int(ev["input_tokens"]) if ev.get("input_tokens") is not None else None
        out_tok = int(ev["output_tokens"]) if ev.get("output_tokens") is not None else None
        cache_tok = int(ev["cache_tokens"]) if ev.get("cache_tokens") is not None else None
        tot_tok = int(ev["total_tokens"]) if ev.get("total_tokens") is not None else None
        if tot_tok is None and in_tok is not None and out_tok is not None:
            tot_tok = in_tok + out_tok
        elif tot_tok is None and (in_tok is not None or out_tok is not None):
            tot_tok = (in_tok or 0) + (out_tok or 0)

        # Strict accuracy: browser & desktop sources cannot report verified token counts
        if canon_source in ("browser", "desktop") or ev_type in ("prompt_submitted", "application_activity"):
            in_tok = None
            out_tok = None
            cache_tok = None
            tot_tok = None

        rep_cost = int(ev["reported_cost_minor"]) if ev.get("reported_cost_minor") is not None else None
        active_secs = int(ev["active_seconds"]) if ev.get("active_seconds") is not None else None
        req_ref = (str(ev["request_ref"]).strip() if ev.get("request_ref") else None)
        session_id = (str(ev["session_id"]).strip() if ev.get("session_id") else None)
        occurred_at = str(ev.get("occurred_at") or now_iso)
        period_day = str(ev.get("period_day") or occurred_at[:10] or today)[:10]

        provider_code_for_conn = {
            "api": "cursor_admin_api",
            "opentelemetry": "claude_code_otel",
            "litellm": "litellm",
            "mcp_server": "mcp_server",
            "agent_tool_hook": "agent_tool_hook",
            "browser": "browser_extension",
            "desktop": "desktop_helper",
        }.get(canon_source, "universal_connector")
        conn_id = ensure_connector_connection(
            db,
            account_id=account_id,
            organization_id=organization_id,
            provider_code=provider_code_for_conn,
            display_name=_APP_DISPLAY_NAMES.get(app_slug, app_slug),
        )

        ev_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO universal_connector_events (
                id, account_id, organization_id, connection_id, event_type, application, source,
                file_type, media_type, model_name, quantity,
                input_tokens, output_tokens, cache_tokens, total_tokens, reported_cost_minor,
                active_seconds, verified, request_ref, session_id, period_day, occurred_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ev_id,
                account_id,
                organization_id,
                conn_id,
                ev_type,
                app_slug,
                canon_source,
                file_type,
                media_type,
                model_name,
                qty,
                in_tok,
                out_tok,
                cache_tok,
                tot_tok,
                rep_cost,
                active_secs,
                verified,
                req_ref,
                session_id,
                period_day,
                occurred_at,
                now_iso,
            ),
        )

        # Mirror verified token / model_request telemetry into external_usage_records for unified /ai/usage ledger
        if verified == 1 and (ev_type == "model_request" or tot_tok is not None or rep_cost is not None):
            est_minor: Optional[int] = None
            if in_tok is not None or out_tok is not None:
                pricing = _pricing_lookup(db, provider_code_for_conn, model_name, occurred_at)
                in_rate = parse_decimal(pricing["input_per_million_usd"]) if pricing else Decimal("3.00")
                out_rate = parse_decimal(pricing["output_per_million_usd"]) if pricing else Decimal("15.00")
                est_minor, _ = compute_estimated_token_value_minor(
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    input_per_million_usd=str(in_rate),
                    output_per_million_usd=str(out_rate),
                )
            lane = str(ev.get("billing_lane") or ("api_key" if rep_cost is not None else "claude_subscription"))
            rel = "EXACT_API" if canon_source == "api" else ("VERIFIED_TELEMETRY" if canon_source == "opentelemetry" else "EXACT_IF_ROUTED")
            q_note = f"Universal Connector ({app_slug} via {canon_source}, {ev_type})."
            img_gen = qty if ev_type == "image_generated" else None
            doc_reads = qty if ev_type == "document_read" else None
            code_reads = qty if ev_type == "source_code_read" else None
            docs_gen = qty if ev_type == "document_generated" else None

            db.execute(
                """
                INSERT INTO external_usage_records (
                    id, connection_id, external_record_id, account_id, organization_id, membership_id,
                    model_name, requests_count, input_tokens, output_tokens, total_tokens,
                    cache_read_tokens, cache_creation_tokens,
                    reported_cost_minor, estimated_value_minor, currency, source, reliability,
                    billing_lane, application, images_generated, images_uploaded,
                    document_reads, documents_generated, source_code_reads,
                    request_ref, session_id, quality_note, occurred_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, 0, %s, %s, 'USD', %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (connection_id, external_record_id) DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    conn_id,
                    f"uc-{ev_id}",
                    account_id,
                    organization_id,
                    model_name,
                    qty if ev_type == "model_request" else None,
                    in_tok,
                    out_tok,
                    tot_tok,
                    cache_tok,
                    rep_cost,
                    est_minor,
                    ext_source,
                    rel,
                    lane,
                    app_slug,
                    img_gen,
                    doc_reads,
                    docs_gen,
                    code_reads,
                    req_ref or f"uc-{ev_id}",
                    session_id,
                    q_note,
                    occurred_at,
                    now_iso,
                ),
            )

        stored.append(
            {
                "id": ev_id,
                "event_type": ev_type,
                "application": app_slug,
                "source": canon_source,
                "file_type": file_type,
                "media_type": media_type,
                "quantity": qty,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": tot_tok,
                "active_seconds": active_secs,
                "verified": bool(verified),
                "period_day": period_day,
            }
        )

    if actor_user_id and stored:
        record_audit_log(
            db,
            actor_user_id=actor_user_id,
            tenant_id=organization_id or account_id,
            action="universal_connector.events_ingested",
            resource_type="universal_connector_event",
            resource_id=stored[0]["id"],
            metadata={"count": len(stored), "applications": sorted({x["application"] for x in stored})},
        )
    return stored


def _infer_application_from_ext_record(rec: dict[str, Any]) -> str:
    explicit = (rec.get("application") or "").strip()
    if explicit:
        return normalize_application_slug(explicit)
    src = (rec.get("source") or "").upper()
    pcode = (rec.get("provider_code") or "").lower()
    note = f"{rec.get('connection_name') or ''} {rec.get('quality_note') or ''}".lower()
    if src == "CLAUDE_CODE_OTEL" or pcode == "claude_code_otel":
        return "claude_code"
    if "cursor" in pcode or "cursor" in note:
        return "cursor"
    if "codex" in pcode or "codex" in note:
        return "codex"
    if src == "GATEWAY_ROUTED" or pcode == "litellm":
        return "litellm_gateway"
    if src == "MCP_SERVER" or pcode == "mcp_server":
        return "claude_desktop" if "claude-desktop" in note else "claude_code"
    return normalize_application_slug(pcode or "ai_provider")


def build_usage_intelligence(
    db: Any,
    *,
    account_id: Optional[str],
    organization_id: Optional[str],
    currency: str = "USD",
) -> dict[str, Any]:
    """Assemble the 5 AI measurements, source-qualified app breakdown, and Section 6 connector records."""
    if organization_id:
        ext_raw = db.fetchall(
            """
            SELECT r.*, c.provider_code, c.display_name AS connection_name
            FROM external_usage_records r
            JOIN integration_connections c ON c.id = r.connection_id
            WHERE r.organization_id = %s
            ORDER BY r.occurred_at DESC, r.created_at DESC
            """,
            (organization_id,),
        )
        uc_events = db.fetchall(
            """
            SELECT * FROM universal_connector_events
            WHERE organization_id = %s
            ORDER BY occurred_at DESC, created_at DESC
            """,
            (organization_id,),
        )
        conns = db.fetchall(
            "SELECT * FROM integration_connections WHERE organization_id = %s AND status = 'connected'",
            (organization_id,),
        )
        act_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
    else:
        ext_raw = db.fetchall(
            """
            SELECT r.*, c.provider_code, c.display_name AS connection_name
            FROM external_usage_records r
            JOIN integration_connections c ON c.id = r.connection_id
            WHERE r.account_id = %s AND r.organization_id IS NULL
            ORDER BY r.occurred_at DESC, r.created_at DESC
            """,
            (account_id,),
        )
        uc_events = db.fetchall(
            """
            SELECT * FROM universal_connector_events
            WHERE account_id = %s AND organization_id IS NULL
            ORDER BY occurred_at DESC, created_at DESC
            """,
            (account_id,),
        )
        conns = db.fetchall(
            "SELECT * FROM integration_connections WHERE account_id = %s AND organization_id IS NULL AND status = 'connected'",
            (account_id,),
        )
        act_rows = db.fetchall(
            "SELECT * FROM personal_activity WHERE account_id = %s ORDER BY occurred_at DESC",
            (account_id,),
        )
        daily_rows = db.fetchall(
            "SELECT * FROM activity_daily WHERE account_id = %s ORDER BY day DESC",
            (account_id,),
        )

    # Deduplicate overlapping OTel / LiteLLM request_refs
    seen_refs: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for r in ext_raw:
        ref = (r.get("request_ref") or "").strip()
        if not ref:
            passthrough.append(r)
            continue
        if ref not in seen_refs:
            seen_refs[ref] = r
        elif seen_refs[ref].get("reported_cost_minor") is None and r.get("reported_cost_minor") is not None:
            seen_refs[ref] = r
    ext_records = list(seen_refs.values()) + passthrough

    mirrored_event_ids = {
        str(r["external_record_id"])[3:]
        for r in ext_records
        if str(r.get("external_record_id") or "").startswith("uc-")
    }

    # Global accumulators across the 5 measurements
    model_requests_total = 0
    has_model_requests = False
    tool_call_count = 0
    prompt_count_observed = 0

    in_tok_vals: list[int] = []
    out_tok_vals: list[int] = []
    cache_tok_vals: list[int] = []
    tot_tok_vals: list[int] = []

    images_gen_total: Optional[int] = None
    images_up_total: Optional[int] = None
    has_verified_images = False

    doc_reads_total: Optional[int] = None
    code_reads_total: Optional[int] = None
    docs_gen_total: Optional[int] = None
    has_doc_events = False

    active_seconds_total = 0
    visits_total = 0
    has_activity = False

    active_sources: set[str] = set()
    for c in conns:
        pcode = (c.get("provider_code") or "").lower()
        active_sources.add(_SOURCE_CANONICAL_MAP.get(pcode, pcode))

    # Per-application buckets matching Section 6 standardized connector schema
    app_buckets: dict[str, dict[str, Any]] = {}

    def _get_bucket(app_slug: str, day: str) -> dict[str, Any]:
        if app_slug not in app_buckets:
            app_buckets[app_slug] = {
                "application": app_slug,
                "display_name": _APP_DISPLAY_NAMES.get(app_slug, app_slug.replace("_", " ").title()),
                "sources_seen": set(),
                "period": day,
                "model_requests": None,
                "input_tokens": None,
                "output_tokens": None,
                "cache_tokens": None,
                "total_tokens": None,
                "images_generated": None,
                "images_uploaded": None,
                "document_reads": None,
                "source_code_reads": None,
                "documents_generated": None,
                "active_seconds": 0,
                "has_activity": False,
                "prompt_count_observed": 0,
                "tool_call_count": 0,
                "has_verified_images": False,
                "has_doc_tool_calls": False,
            }
        elif day and day > app_buckets[app_slug]["period"]:
            app_buckets[app_slug]["period"] = day
        return app_buckets[app_slug]

    def _add_opt(bucket: dict[str, Any], key: str, val: Optional[int]) -> None:
        if val is None:
            return
        bucket[key] = (bucket[key] or 0) + int(val)

    default_day = get_clock().now_iso()[:10]

    # 1. Process external_usage_records (API / OTel / Gateway / MCP / mirrored universal events)
    for r in ext_records:
        app_slug = _infer_application_from_ext_record(r)
        canon_src = _SOURCE_CANONICAL_MAP.get(str(r.get("source") or ""), "api")
        active_sources.add(canon_src)
        day = str(r.get("occurred_at") or default_day)[:10]
        b = _get_bucket(app_slug, day)
        b["sources_seen"].add(canon_src)

        if r.get("requests_count") is not None:
            req_n = int(r["requests_count"])
            model_requests_total += req_n
            has_model_requests = True
            _add_opt(b, "model_requests", req_n)

        if r.get("input_tokens") is not None:
            iv = int(r["input_tokens"])
            in_tok_vals.append(iv)
            _add_opt(b, "input_tokens", iv)
        if r.get("output_tokens") is not None:
            ov = int(r["output_tokens"])
            out_tok_vals.append(ov)
            _add_opt(b, "output_tokens", ov)
        if r.get("total_tokens") is not None:
            tv = int(r["total_tokens"])
            tot_tok_vals.append(tv)
            _add_opt(b, "total_tokens", tv)
        cache_sum = int(r.get("cache_read_tokens") or 0) + int(r.get("cache_creation_tokens") or 0)
        if cache_sum > 0:
            cache_tok_vals.append(cache_sum)
            _add_opt(b, "cache_tokens", cache_sum)

        # Standalone external_usage_records (not mirrored from uc_events) that carry media/doc counts
        if not str(r.get("external_record_id") or "").startswith("uc-"):
            if r.get("images_generated") is not None:
                ig = int(r["images_generated"])
                images_gen_total = (images_gen_total or 0) + ig
                has_verified_images = True
                b["has_verified_images"] = True
                _add_opt(b, "images_generated", ig)
            if r.get("document_reads") is not None:
                dr = int(r["document_reads"])
                doc_reads_total = (doc_reads_total or 0) + dr
                has_doc_events = True
                b["has_doc_tool_calls"] = True
                _add_opt(b, "document_reads", dr)
            if r.get("source_code_reads") is not None:
                cr = int(r["source_code_reads"])
                code_reads_total = (code_reads_total or 0) + cr
                has_doc_events = True
                b["has_doc_tool_calls"] = True
                _add_opt(b, "source_code_reads", cr)

    # 2. Process universal_connector_events (without double-counting tokens/requests already in ext_records)
    for ev in uc_events:
        app_slug = normalize_application_slug(ev.get("application"))
        canon_src = _SOURCE_CANONICAL_MAP.get(str(ev.get("source") or ""), str(ev.get("source") or "agent_tool_hook"))
        active_sources.add(canon_src)
        day = str(ev.get("period_day") or default_day)[:10]
        b = _get_bucket(app_slug, day)
        b["sources_seen"].add(canon_src)

        ev_type = str(ev.get("event_type") or "")
        qty = int(ev.get("quantity") or 0)
        already_mirrored = ev["id"] in mirrored_event_ids

        if ev_type == "model_request" and not already_mirrored:
            model_requests_total += qty
            has_model_requests = True
            _add_opt(b, "model_requests", qty)
            if ev.get("input_tokens") is not None:
                in_tok_vals.append(int(ev["input_tokens"]))
                _add_opt(b, "input_tokens", int(ev["input_tokens"]))
            if ev.get("output_tokens") is not None:
                out_tok_vals.append(int(ev["output_tokens"]))
                _add_opt(b, "output_tokens", int(ev["output_tokens"]))
            if ev.get("total_tokens") is not None:
                tot_tok_vals.append(int(ev["total_tokens"]))
                _add_opt(b, "total_tokens", int(ev["total_tokens"]))
            if ev.get("cache_tokens") is not None:
                cache_tok_vals.append(int(ev["cache_tokens"]))
                _add_opt(b, "cache_tokens", int(ev["cache_tokens"]))

        elif ev_type == "tool_call":
            tool_call_count += qty
            b["tool_call_count"] += qty

        elif ev_type == "prompt_submitted":
            prompt_count_observed += qty
            b["prompt_count_observed"] += qty
            has_activity = True
            b["has_activity"] = True

        elif ev_type == "document_read":
            doc_reads_total = (doc_reads_total or 0) + qty
            has_doc_events = True
            b["has_doc_tool_calls"] = bool(ev.get("verified")) or canon_src != "browser"
            _add_opt(b, "document_reads", qty)
            tool_call_count += qty
            b["tool_call_count"] += qty

        elif ev_type == "source_code_read":
            code_reads_total = (code_reads_total or 0) + qty
            has_doc_events = True
            b["has_doc_tool_calls"] = True
            _add_opt(b, "source_code_reads", qty)
            tool_call_count += qty
            b["tool_call_count"] += qty

        elif ev_type == "document_generated":
            docs_gen_total = (docs_gen_total or 0) + qty
            has_doc_events = True
            b["has_doc_tool_calls"] = bool(ev.get("verified")) or canon_src != "browser"
            _add_opt(b, "documents_generated", qty)
            tool_call_count += qty
            b["tool_call_count"] += qty

        elif ev_type == "image_generated":
            images_gen_total = (images_gen_total or 0) + qty
            if bool(ev.get("verified")):
                has_verified_images = True
                b["has_verified_images"] = True
            _add_opt(b, "images_generated", qty)

        elif ev_type == "image_uploaded":
            images_up_total = (images_up_total or 0) + qty
            _add_opt(b, "images_uploaded", qty)

        elif ev_type == "application_activity":
            secs = int(ev.get("active_seconds") or 0)
            active_seconds_total += secs
            visits_total += qty
            has_activity = True
            b["active_seconds"] += secs
            b["has_activity"] = True

    # 3. Process legacy browser/desktop activity tables (tokens remain null / Unavailable)
    for a in act_rows:
        app_slug = normalize_application_slug(a.get("domain_or_app"))
        canon_src = _SOURCE_CANONICAL_MAP.get(str(a.get("source") or ""), "browser")
        active_sources.add(canon_src)
        day = str(a.get("occurred_at") or default_day)[:10]
        b = _get_bucket(app_slug, day)
        b["sources_seen"].add(canon_src)
        secs = int(a.get("observed_active_seconds") or 0)
        vis = int(a.get("visit_count") or 0)
        active_seconds_total += secs
        visits_total += vis
        has_activity = True
        b["active_seconds"] += secs
        b["has_activity"] = True

    for d in daily_rows:
        app_slug = normalize_application_slug(d.get("domain_or_app"))
        canon_src = "browser"
        active_sources.add(canon_src)
        day = str(d.get("day") or default_day)[:10]
        b = _get_bucket(app_slug, day)
        b["sources_seen"].add(canon_src)
        secs = int(d.get("active_seconds") or 0)
        vis = int(d.get("visits") or 0)
        active_seconds_total += secs
        visits_total += vis
        has_activity = True
        b["active_seconds"] += secs
        b["has_activity"] = True

    # Assemble Section 6 standardized application records
    app_rows: list[dict[str, Any]] = []
    for app_slug, b in app_buckets.items():
        # Highest-accuracy active source for this application
        primary_src = min(b["sources_seen"], key=lambda s: _SOURCE_PRIORITY.get(s, 99)) if b["sources_seen"] else "agent_tool_hook"
        src_badge = _SOURCE_BADGE_MAP.get(primary_src, primary_src.title())

        # Strictly enforce that browser/desktop-only apps NEVER expose token counts
        is_non_token_only = b["sources_seen"].issubset({"browser", "desktop"})
        in_t = None if is_non_token_only else b["input_tokens"]
        out_t = None if is_non_token_only else b["output_tokens"]
        tot_t = None if is_non_token_only else b["total_tokens"]
        tok_cov = "reported" if tot_t is not None else "unavailable"

        img_cov = (
            "reported"
            if b["has_verified_images"]
            else ("observed_interactions" if (b["images_generated"] is not None or b["images_uploaded"] is not None) else "unavailable")
        )
        has_any_docs = any(
            x is not None for x in (b["document_reads"], b["source_code_reads"], b["documents_generated"])
        )
        doc_cov = (
            "observed_tool_calls"
            if b["has_doc_tool_calls"]
            else ("observed_interactions" if has_any_docs else "unavailable")
        )
        act_min = int(round(b["active_seconds"] / 60)) if b["has_activity"] and b["active_seconds"] > 0 else None

        app_rows.append(
            {
                "application": app_slug,
                "display_name": b["display_name"],
                "source": primary_src,
                "source_badge": src_badge,
                "period": b["period"],
                "metrics": {
                    "model_requests": b["model_requests"],
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                    "cache_tokens": None if is_non_token_only else b["cache_tokens"],
                    "total_tokens": tot_t,
                    "images_generated": b["images_generated"],
                    "images_uploaded": b["images_uploaded"],
                    "document_reads": b["document_reads"],
                    "source_code_reads": b["source_code_reads"],
                    "documents_generated": b["documents_generated"],
                    "active_minutes": act_min,
                    "prompt_count_observed": b["prompt_count_observed"] if b["prompt_count_observed"] > 0 else None,
                    "tool_call_count": b["tool_call_count"] if b["tool_call_count"] > 0 else None,
                },
                "coverage": {
                    "tokens": tok_cov,
                    "images": img_cov,
                    "documents": doc_cov,
                    "activity": "observed_interactions" if act_min is not None else "unavailable",
                },
            }
        )

    app_rows.sort(
        key=lambda row: (
            -(row["metrics"]["total_tokens"] or 0),
            _SOURCE_PRIORITY.get(row["source"], 99),
            row["display_name"],
        )
    )

    docs_accessed_total: Optional[int] = (
        (doc_reads_total or 0) + (code_reads_total or 0)
        if (doc_reads_total is not None or code_reads_total is not None)
        else (0 if has_doc_events else None)
    )
    active_minutes_val: Optional[int] = (
        int(round(active_seconds_total / 60)) if has_activity and active_seconds_total > 0 else (0 if has_activity else None)
    )
    tokens_total_val: Optional[int] = sum(tot_tok_vals) if tot_tok_vals else None
    input_total_val: Optional[int] = sum(in_tok_vals) if in_tok_vals else None
    output_total_val: Optional[int] = sum(out_tok_vals) if out_tok_vals else None
    cache_total_val: Optional[int] = sum(cache_tok_vals) if cache_tok_vals else None

    if has_verified_images or images_gen_total is not None:
        img_coverage = "reported" if has_verified_images else "observed_interactions"
    elif images_up_total is not None:
        img_coverage = "observed_interactions"
    else:
        img_coverage = "unavailable"

    total_images_val: Optional[int] = (
        (images_gen_total or 0) + (images_up_total or 0)
        if (images_gen_total is not None or images_up_total is not None)
        else None
    )

    return {
        "workspace_label": "Organization workspace" if organization_id else "Personal workspace",
        "currency": currency.upper(),
        "active_sources_count": len(active_sources),
        "active_sources": sorted(active_sources),
        "measurements": {
            "model_requests": {
                "value": model_requests_total if has_model_requests else None,
                "tool_call_count": tool_call_count,
                "prompt_count_observed": prompt_count_observed,
                "coverage": "reported" if has_model_requests else "unavailable",
                "label": "Verified sources" if has_model_requests else "Unavailable",
            },
            "tokens_consumed": {
                "value": tokens_total_val,
                "input_tokens": input_total_val,
                "output_tokens": output_total_val,
                "cache_tokens": cache_total_val,
                "coverage": "reported" if tokens_total_val is not None else "unavailable",
                "label": "Reported usage" if tokens_total_val is not None else "Unavailable",
            },
            "images_generated": {
                "value": images_gen_total,
                "images_uploaded": images_up_total,
                "total_images": total_images_val,
                "coverage": img_coverage,
                "label": "Instrumented tools" if has_verified_images else ("Observed interactions" if img_coverage == "observed_interactions" else "Unavailable"),
            },
            "documents_accessed": {
                "value": docs_accessed_total,
                "document_reads": doc_reads_total,
                "source_code_reads": code_reads_total,
                "documents_generated": docs_gen_total,
                "coverage": "observed_tool_calls" if has_doc_events else "unavailable",
                "label": "Recorded read operations" if has_doc_events else "Unavailable",
            },
            "application_activity": {
                "active_minutes": active_minutes_val,
                "active_seconds": active_seconds_total if has_activity else None,
                "visits": visits_total if has_activity else None,
                "coverage": "observed_interactions" if has_activity else "unavailable",
                "label": "Observed browser/desktop activity" if has_activity else "Unavailable",
            },
        },
        "usage_by_application": app_rows,
        "connector_records": app_rows,
        "data_coverage_note": (
            "Token totals cover connected telemetry sources only. "
            "Browser activity and tool-operation counts are displayed separately."
        ),
        "distinction_note": (
            "Model requests, web prompts, and agent tool calls are separated: one coding-agent prompt "
            "may trigger multiple model requests, file reads, and tool commands."
        ),
    }


def render_claude_code_helper_script(
    otel_token: str = "",
    api_url: str = "http://127.0.0.1:8080",
) -> str:
    """Render claude_code_connect.sh to enable OTel metrics + metadata-only tool hooks."""
    clean_url = (api_url or "http://127.0.0.1:8080").rstrip("/")
    tok = otel_token or "<YOUR_SUBSPHERE_OTEL_TOKEN>"
    return f"""#!/usr/bin/env bash
# SubSphere Claude Code Universal Connector Helper
# Enables privacy-filtered OpenTelemetry telemetry (no LiteLLM required) and tool-call metadata logging.
# Usage: source ./claude_code_connect.sh && claude

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT="{clean_url.replace(':8080', ':4317')}"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer {tok}"
export SUBSPHERE_API_URL="{clean_url}"
export SUBSPHERE_CONNECTOR_TOKEN="{tok}"

subsphere_record_event() {{
  local event_type="${{1:-source_code_read}}"
  local file_type="${{2:-py}}"
  local quantity="${{3:-1}}"
  curl -fsS -X POST "${{SUBSPHERE_API_URL}}/api/v1/connectors/events" \\
    -H "Authorization: Bearer ${{SUBSPHERE_CONNECTOR_TOKEN}}" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"event_type\\":\\"${{event_type}}\\",\\"application\\":\\"claude_code\\",\\"source\\":\\"agent_tool_hook\\",\\"file_type\\":\\"${{file_type}}\\",\\"quantity\\":${{quantity}},\\"verified\\":true}}" \\
    >/dev/null
}}

echo "SubSphere OTel + Hook helper loaded. Launch Claude Code in this shell: claude"
"""
