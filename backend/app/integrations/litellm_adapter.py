"""LiteLLM and provider usage integration adapter with capability matrix and Decimal pricing."""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "litellm": {
        "provider_code": "litellm",
        "supports_tokens": True,
        "supports_reported_cost": False,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": True,
        "confidence_class": "GATEWAY_METERED",
        "default_source": "GATEWAY_ROUTED",
        "default_reliability": "EXACT_IF_ROUTED",
        "notes": "Exact token and request counts for requests routed through LiteLLM Proxy.",
    },
    "openai_organization_api": {
        "provider_code": "openai_organization_api",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "EXACT_API",
        "default_source": "OFFICIAL_PROVIDER_API",
        "default_reliability": "EXACT_API",
        "notes": "Official OpenAI organization usage & costs API.",
    },
    "anthropic_admin_api": {
        "provider_code": "anthropic_admin_api",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "EXACT_API",
        "default_source": "OFFICIAL_PROVIDER_API",
        "default_reliability": "EXACT_API",
        "notes": "Official Anthropic organization usage & cost reports.",
    },
    "claude_code_otel": {
        "provider_code": "claude_code_otel",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "VERIFIED_TELEMETRY",
        "default_source": "CLAUDE_CODE_OTEL",
        "default_reliability": "VERIFIED_TELEMETRY",
        "notes": "Verified Claude Code OpenTelemetry counter/gauge metrics (privacy-filtered, no prompts).",
    },
    "openrouter_api": {
        "provider_code": "openrouter_api",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "EXACT_API",
        "default_source": "OFFICIAL_PROVIDER_API",
        "default_reliability": "EXACT_API",
        "notes": "Official OpenRouter generation usage and billed credit breakdown.",
    },
    "browser_extension": {
        "provider_code": "browser_extension",
        "supports_tokens": False,
        "supports_reported_cost": False,
        "supports_requests": False,
        "supports_activity_duration": True,
        "requires_proxy_routing": False,
        "confidence_class": "OBSERVED",
        "default_source": "ALLOWED_BROWSER_ACTIVITY",
        "default_reliability": "OBSERVED",
        "notes": "Opt-in domain active duration & visit counts only; never captures tokens, prompts, URLs, or cookies.",
    },
    "desktop_helper": {
        "provider_code": "desktop_helper",
        "supports_tokens": False,
        "supports_reported_cost": False,
        "supports_requests": False,
        "supports_activity_duration": True,
        "requires_proxy_routing": False,
        "confidence_class": "OBSERVED",
        "default_source": "ALLOWED_DESKTOP_ACTIVITY",
        "default_reliability": "OBSERVED",
        "notes": "Opt-in allowlisted foreground app duration only; never captures prompts or tokens.",
    },
    "csv_import": {
        "provider_code": "csv_import",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "ESTIMATED",
        "default_source": "CSV_IMPORT",
        "default_reliability": "ESTIMATED",
        "notes": "User-imported CSV statement records; accuracy depends on exported file.",
    },
    "manual": {
        "provider_code": "manual",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "ESTIMATED",
        "default_source": "MANUAL",
        "default_reliability": "ESTIMATED",
        "notes": "Manual user entry.",
    },
    "mcp_server": {
        "provider_code": "mcp_server",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": True,
        "requires_proxy_routing": False,
        "confidence_class": "MCP_AGENT_REPORTED",
        "default_source": "MCP_SERVER",
        "default_reliability": "EXACT_AGENT_MCP",
        "notes": "Direct Model Context Protocol (MCP) server installed in the user's AI agent or web client.",
    },
    "cursor_admin_api": {
        "provider_code": "cursor_admin_api",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "EXACT_API",
        "default_source": "OFFICIAL_PROVIDER_API",
        "default_reliability": "EXACT_API",
        "notes": "Official Cursor Team/Business Admin API for usage events, token counts, and charges.",
    },
    "agent_tool_hook": {
        "provider_code": "agent_tool_hook",
        "supports_tokens": False,
        "supports_reported_cost": False,
        "supports_requests": True,
        "supports_activity_duration": False,
        "requires_proxy_routing": False,
        "confidence_class": "OBSERVED_TOOL_CALLS",
        "default_source": "AGENT_TOOL_HOOK",
        "default_reliability": "OBSERVED_TOOL_CALLS",
        "notes": "Coding-agent tool execution hooks recording file reads, PDF opens, doc creation, and image operations.",
    },
    "universal_connector": {
        "provider_code": "universal_connector",
        "supports_tokens": True,
        "supports_reported_cost": True,
        "supports_requests": True,
        "supports_activity_duration": True,
        "requires_proxy_routing": False,
        "confidence_class": "AUTO_SELECTED",
        "default_source": "UNIVERSAL_CONNECTOR",
        "default_reliability": "SOURCE_QUALIFIED",
        "notes": "SubSphere Universal Usage Connector automatically selects the highest-accuracy source per AI app.",
    },
}


def get_provider_capabilities(provider_code: str) -> dict[str, Any]:
    return dict(
        PROVIDER_CAPABILITIES.get(
            provider_code,
            {
                "provider_code": provider_code,
                "supports_tokens": False,
                "supports_reported_cost": False,
                "supports_requests": False,
                "supports_activity_duration": False,
                "requires_proxy_routing": False,
                "confidence_class": "ESTIMATED",
                "default_source": "MANUAL",
                "default_reliability": "ESTIMATED",
                "notes": "Unknown provider capability defaults to conservative non-verified.",
            },
        )
    )


def mask_credential(secret: str) -> str:
    """Create a non-reversible masked credential reference for storage and display."""
    cleaned = secret.strip()
    if not cleaned:
        return ""
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]
    suffix = cleaned[-4:] if len(cleaned) >= 4 else "****"
    return f"enc_sha256:{digest}:***{suffix}"


def parse_decimal(value: Any, field_name: str = "rate") -> Decimal:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid decimal for {field_name}: {value}") from exc
    if dec < Decimal("0"):
        raise ValueError(f"{field_name} must be non-negative")
    return dec


def compute_estimated_token_value_minor(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    input_per_million_usd: str,
    output_per_million_usd: str,
) -> tuple[int | None, str | None]:
    """Compute token valuation in minor USD cents using Decimal arithmetic.

    Never coerces unavailable token categories to zero.
    Returns (estimated_value_minor, quality_note).
    """
    if input_tokens is None and output_tokens is None:
        return None, "Token counts unavailable from source; estimated token value left null."

    in_rate = Decimal(input_per_million_usd)
    out_rate = Decimal(output_per_million_usd)
    million = Decimal("1000000")

    missing_parts: list[str] = []
    if input_tokens is None:
        missing_parts.append("input_tokens")
    if output_tokens is None:
        missing_parts.append("output_tokens")

    if missing_parts:
        # Partial token telemetry: do not fabricate missing side as zero value
        return None, (
            f"Unavailable token category ({', '.join(missing_parts)}); "
            "estimated token value withheld rather than coerced to zero."
        )

    assert input_tokens is not None and output_tokens is not None
    usd_value = (Decimal(input_tokens) / million) * in_rate + (
        Decimal(output_tokens) / million
    ) * out_rate
    minor = int((usd_value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor, None
