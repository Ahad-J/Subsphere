import React, { useEffect, useState } from "react";
import { apiRequest, formatMinorCurrency, formatNullableMetric } from "../api";
import type {
  AiUsageResponse,
  CalendarEvent,
  PersonalDashboardResponse,
  PersonalSubscription,
} from "../types";

type IndividualTab =
  | "overview"
  | "subscriptions"
  | "ai_usage"
  | "calendar"
  | "reports"
  | "notifications"
  | "settings";

interface Props {
  onNotify: (msg: string) => void;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function monthBounds(ym: string): { from: string; to: string; daysInMonth: number; startDow: number } {
  const [yStr, mStr] = ym.split("-");
  const y = Number(yStr);
  const m = Number(mStr);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const last = new Date(Date.UTC(y, m, 0));
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    from: `${y}-${pad(m)}-01`,
    to: `${y}-${pad(m)}-${pad(last.getUTCDate())}`,
    daysInMonth: last.getUTCDate(),
    startDow: first.getUTCDay(),
  };
}

function shiftMonth(ym: string, delta: number): string {
  const [yStr, mStr] = ym.split("-");
  const d = new Date(Date.UTC(Number(yStr), Number(mStr) - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export const IndividualView: React.FC<Props> = ({ onNotify }) => {
  const [tab, setTab] = useState<IndividualTab>("overview");
  const [dashboard, setDashboard] = useState<PersonalDashboardResponse | null>(null);
  const [subs, setSubs] = useState<PersonalSubscription[]>([]);
  const [aiUsage, setAiUsage] = useState<AiUsageResponse | null>(null);

  // Calendar state
  const [calendarMonth, setCalendarMonth] = useState("2026-12");
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [calendarAgendaMode, setCalendarAgendaMode] = useState(false);
  const [selectedCalEvent, setSelectedCalEvent] = useState<CalendarEvent | null>(null);

  // Filters & Add/Edit modal state
  const [filterText, setFilterText] = useState("");
  const [filterTracking, setFilterTracking] = useState("all");
  const [showSubModal, setShowSubModal] = useState(false);
  const [editingSub, setEditingSub] = useState<PersonalSubscription | null>(null);

  // Subscription form fields
  const [svcName, setSvcName] = useState("");
  const [svcUrl, setSvcUrl] = useState("");
  const [category, setCategory] = useState("AI");
  const [planName, setPlanName] = useState("Pro");
  const [productType, setProductType] = useState<"recurring_subscription" | "one_time_purchase" | "trial">("recurring_subscription");
  const [priceMajor, setPriceMajor] = useState("20.00");
  const [currency, setCurrency] = useState("USD");
  const [interval, setInterval] = useState<PersonalSubscription["billing_interval"]>("monthly");
  const [startDate, setStartDate] = useState("2026-11-01");
  const [renewalDate, setRenewalDate] = useState("2026-12-01");
  const [timezone, setTimezone] = useState("UTC");
  const [trackingStatus, setTrackingStatus] = useState<"tracking_active" | "stopped_tracking" | "canceled_at_provider">("tracking_active");
  const [offsetsInput, setOffsetsInput] = useState("7, 3, 1");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [notes, setNotes] = useState("");

  // Universal Usage Connector & AI Usage Intelligence state
  const [intelligence, setIntelligence] = useState<any>(null);
  const [connectorCatalog, setConnectorCatalog] = useState<any>(null);
  const [connectorBusy, setConnectorBusy] = useState<boolean>(false);
  const [connectResult, setConnectResult] = useState<any>(null);
  const [cursorPlanTier, setCursorPlanTier] = useState<string>("personal");
  const [cursorApiKey, setCursorApiKey] = useState<string>("");
  const [litellmVirtualKey, setLitellmVirtualKey] = useState<any>(null);
  const [connectorEventApp, setConnectorEventApp] = useState<string>("claude_code");
  const [connectorEventType, setConnectorEventType] = useState<string>("document_read");
  const [connectorEventDetail, setConnectorEventDetail] = useState<string>("pdf");
  const [connectorEventQty, setConnectorEventQty] = useState<string>("1");
  const [connectorEventTokensIn, setConnectorEventTokensIn] = useState<string>("0");
  const [connectorEventTokensOut, setConnectorEventTokensOut] = useState<string>("0");

  // SubSphere MCP Server state
  const [mcpStatus, setMcpStatus] = useState<any>(null);
  const [mcpTokenResult, setMcpTokenResult] = useState<any>(null);
  const [mcpClientName, setMcpClientName] = useState<string>("huggingchat");
  const [mcpBusy, setMcpBusy] = useState<boolean>(false);
  const [mcpTestModel, setMcpTestModel] = useState<string>("claude-3-5-sonnet");
  const [mcpTestLane, setMcpTestLane] = useState<"claude_subscription" | "api_key">("claude_subscription");
  const [mcpTestInTokens, setMcpTestInTokens] = useState<string>("320");
  const [mcpTestOutTokens, setMcpTestOutTokens] = useState<string>("140");
  const [mcpLastTestResult, setMcpLastTestResult] = useState<any>(null);

  // Reports state
  const [repFrom, setRepFrom] = useState("2026-11-01");
  const [repTo, setRepTo] = useState("2026-12-31");
  const [repCategory, setRepCategory] = useState("");
  const [repSource, setRepSource] = useState("");
  const [reports, setReports] = useState<any[]>([]);
  const [activeReportId, setActiveReportId] = useState("");
  const [previewHtml, setPreviewHtml] = useState("");

  // Notifications state
  const [emailStatus, setEmailStatus] = useState<any>(null);
  const [notifPrefs, setNotifPrefs] = useState<any>(null);
  const [prefEnabled, setPrefEnabled] = useState(true);
  const [prefEmail, setPrefEmail] = useState("");
  const [prefOffsets, setPrefOffsets] = useState("7, 3, 1");
  const [prefHour, setPrefHour] = useState("9");
  const [prefTz, setPrefTz] = useState("UTC");
  const [prefMonthly, setPrefMonthly] = useState(false);
  const [notifHistory, setNotifHistory] = useState<any[]>([]);
  const [testEmailOutcome, setTestEmailOutcome] = useState<any>(null);
  const [maskedInbox, setMaskedInbox] = useState<{ masked_email: string; relay_mask_email: string; messages: any[] } | null>(null);
  const [selectedInboxMsg, setSelectedInboxMsg] = useState<any | null>(null);
  const [recvSender, setRecvSender] = useState("billing@claude.ai");
  const [recvSubject, setRecvSubject] = useState("Monthly AI Subscription Receipt");

  const loadCalendarForMonth = async (ym: string) => {
    const { from, to } = monthBounds(ym);
    try {
      const cal = await apiRequest<{ events: CalendarEvent[] }>(
        `/api/v1/calendar/events?view=individual&from=${from}&to=${to}`
      );
      setCalendarEvents(cal.events || []);
    } catch {
      // Fallback to month legacy query if needed
    }
  };

  const loadAll = async () => {
    try {
      const [d, s, u, intel, cat, mcpSt, reps, emlStat, prefs, nhist, inbox] = await Promise.all([
        apiRequest<PersonalDashboardResponse>("/api/v1/personal/dashboard"),
        apiRequest<{ subscriptions: PersonalSubscription[] }>("/api/v1/personal/subscriptions"),
        apiRequest<AiUsageResponse>("/api/v1/ai/usage"),
        apiRequest<any>("/api/v1/connectors/intelligence").catch(() => null),
        apiRequest<any>("/api/v1/connectors/catalog").catch(() => null),
        apiRequest<any>("/api/v1/mcp/status").catch(() => null),
        apiRequest<{ reports: any[] }>("/api/v1/reports").catch(() => ({ reports: [] })),
        apiRequest<any>("/api/v1/integrations/email/status").catch(() => null),
        apiRequest<any>("/api/v1/notifications/preferences").catch(() => null),
        apiRequest<{ notifications: any[] }>("/api/v1/notifications/history").catch(() => ({ notifications: [] })),
        apiRequest<any>("/api/v1/notifications/masked-inbox").catch(() => null),
      ]);
      setDashboard(d);
      setSubs(s.subscriptions || []);
      setAiUsage(u);
      setIntelligence(intel || (u as any)?.intelligence || null);
      setConnectorCatalog(cat);
      setMcpStatus(mcpSt);
      setReports(reps.reports || []);
      setEmailStatus(emlStat);
      if (inbox) {
        setMaskedInbox(inbox);
      }
      if (prefs) {
        setNotifPrefs(prefs);
        setPrefEnabled(Boolean(prefs.email_enabled));
        setPrefEmail(prefs.recipient_email || prefs.primary_email || "");
        setPrefOffsets((prefs.default_offsets_days || [7, 3, 1]).join(", "));
        setPrefHour(String(prefs.send_hour_local ?? 9));
        setPrefTz(prefs.timezone || "UTC");
        setPrefMonthly(Boolean(prefs.monthly_report_opt_in));
      }
      setNotifHistory(nhist.notifications || []);
      await loadCalendarForMonth(calendarMonth);
    } catch (err: any) {
      onNotify(err?.message || "Failed to load Individual View data.");
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    void loadCalendarForMonth(calendarMonth);
  }, [calendarMonth]);

  const openAddModal = () => {
    setEditingSub(null);
    setSvcName("");
    setSvcUrl("");
    setCategory("AI");
    setPlanName("Pro");
    setProductType("recurring_subscription");
    setPriceMajor("20.00");
    setCurrency("USD");
    setInterval("monthly");
    setStartDate("2026-11-01");
    setRenewalDate("2026-12-01");
    setTimezone("UTC");
    setTrackingStatus("tracking_active");
    setOffsetsInput("7, 3, 1");
    setRecipientEmail(notifPrefs?.recipient_email || "");
    setNotes("");
    setShowSubModal(true);
  };

  const openEditModal = (sub: PersonalSubscription) => {
    setEditingSub(sub);
    setSvcName(sub.service_name);
    setSvcUrl(sub.service_url || "");
    setCategory(sub.category || "AI");
    setPlanName(sub.plan_name || "");
    setProductType(sub.product_type);
    setPriceMajor((sub.price_minor / 100).toFixed(2));
    setCurrency(sub.currency);
    setInterval(sub.billing_interval);
    setStartDate(sub.start_date || "");
    setRenewalDate(sub.next_renewal_date);
    setTimezone(sub.timezone || "UTC");
    setTrackingStatus(sub.tracking_status || "tracking_active");
    setOffsetsInput((sub.reminder_offsets_days || [7, 3, 1]).join(", "));
    setRecipientEmail(sub.recipient_email || "");
    setNotes(sub.notes || "");
    setShowSubModal(true);
  };

  const handleSaveSubscription = async (e: React.FormEvent) => {
    e.preventDefault();
    const minor = Math.round(parseFloat(priceMajor || "0") * 100);
    const parsedOffsets = offsetsInput
      .split(",")
      .map((x) => parseInt(x.trim(), 10))
      .filter((n) => !Number.isNaN(n) && n >= 0);

    try {
      if (editingSub) {
        await apiRequest(`/api/v1/personal/subscriptions/${editingSub.id}`, {
          method: "PATCH",
          body: {
            version: editingSub.version ?? 1,
            service_name: svcName,
            service_url: svcUrl,
            category,
            plan_name: planName,
            product_type: productType,
            price_minor: minor,
            currency,
            billing_interval: interval,
            start_date: startDate || undefined,
            next_renewal_date: renewalDate,
            timezone,
            tracking_status: trackingStatus,
            reminder_offsets_days: parsedOffsets,
            recipient_email: recipientEmail || undefined,
            notes,
          },
        });
        onNotify(`Updated ${svcName} — dashboard, calendar & reminders synchronized.`);
      } else {
        await apiRequest("/api/v1/personal/subscriptions", {
          method: "POST",
          body: {
            service_name: svcName,
            service_url: svcUrl,
            category,
            plan_name: planName,
            product_type: productType,
            price_minor: minor,
            currency,
            billing_interval: interval,
            start_date: startDate || undefined,
            next_renewal_date: renewalDate,
            timezone,
            tracking_status: trackingStatus,
            reminder_offsets_days: parsedOffsets,
            recipient_email: recipientEmail || undefined,
            notes,
          },
        });
        onNotify(`Saved ${svcName}.`);
      }
      setShowSubModal(false);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to save subscription");
    }
  };

  const handleDeleteSub = async (subId: string) => {
    try {
      await apiRequest(`/api/v1/personal/subscriptions/${subId}`, { method: "DELETE" });
      onNotify("Subscription removed.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Delete failed");
    }
  };

  const handleGenerateMcpToken = async () => {
    try {
      setMcpBusy(true);
      const res = await apiRequest<any>("/api/v1/mcp/token", {
        method: "POST",
        body: { client_name: mcpClientName },
      });
      setMcpTokenResult(res);
      onNotify(`SubSphere MCP token generated for ${mcpClientName}.`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "MCP token generation failed");
    } finally {
      setMcpBusy(false);
    }
  };

  const handleDownloadMcpScript = () => {
    const url = mcpTokenResult?.script_download_url || "/api/v1/mcp/install.py";
    window.open(url, "_blank", "noopener");
    onNotify("Downloading subsphere_mcp_server.py for your AI agent.");
  };

  const handleRevokeMcp = async () => {
    try {
      setMcpBusy(true);
      await apiRequest("/api/v1/mcp/revoke", { method: "POST" });
      setMcpTokenResult(null);
      setMcpLastTestResult(null);
      onNotify("SubSphere MCP tokens revoked.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "MCP revoke failed");
    } finally {
      setMcpBusy(false);
    }
  };

  const handleSendMcpUsageTest = async () => {
    try {
      setMcpBusy(true);
      const inTok = Math.max(0, parseInt(mcpTestInTokens || "0", 10) || 0);
      const outTok = Math.max(0, parseInt(mcpTestOutTokens || "0", 10) || 0);
      const rpcRes = await apiRequest<any>("/api/v1/mcp", {
        method: "POST",
        body: {
          jsonrpc: "2.0",
          id: Date.now(),
          method: "tools/call",
          params: {
            name: "subsphere_report_usage",
            arguments: {
              model_name: mcpTestModel,
              agent_name: mcpClientName,
              input_tokens: inTok,
              output_tokens: outTok,
              billing_lane: mcpTestLane,
            },
          },
        },
      });
      const rawText = rpcRes?.result?.content?.[0]?.text;
      const parsed = rawText ? JSON.parse(rawText) : rpcRes;
      setMcpLastTestResult(parsed?.recorded || parsed);
      onNotify(`MCP Server recorded ${inTok + outTok} tokens from ${mcpClientName} (${mcpTestModel}).`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "MCP usage test call failed");
    } finally {
      setMcpBusy(false);
    }
  };

  const handleCopyText = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      onNotify(`Copied ${label} to clipboard.`);
    } catch {
      onNotify(`Copy ${label} from the code block below.`);
    }
  };

  const handleConnectApp = async (
    application: string,
    extra: { plan_tier?: string; api_key?: string; allowlist?: string[] } = {}
  ) => {
    try {
      setConnectorBusy(true);
      const res = await apiRequest<any>("/api/v1/connectors/connect", {
        method: "POST",
        body: {
          application,
          plan_tier: extra.plan_tier || "personal",
          api_key: extra.api_key || undefined,
          allowlist: extra.allowlist,
        },
      });
      setConnectResult(res);
      if (res?.config?.pair_code && typeof window !== "undefined") {
        window.postMessage(
          {
            source: "subsphere-web",
            type: "SUBSPHERE_ONE_CLICK_PAIR",
            apiBaseUrl: window.location.origin,
            pairCode: res.config.pair_code,
            allowlist: res.config.allowlist || ["chatgpt.com", "claude.ai", "huggingface.co"],
          },
          window.location.origin
        );
      }
      onNotify(`${res.display_name} connected via ${res.source_badge} (${res.selected_method}).`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || `Failed to connect ${application}`);
    } finally {
      setConnectorBusy(false);
    }
  };

  const handleDownloadClaudeCodeHelper = () => {
    const url = connectResult?.config?.helper_script_url || "/api/v1/connectors/claude-code-helper.sh";
    window.open(url, "_blank", "noopener");
    onNotify("Downloading claude_code_connect.sh configuration helper.");
  };

  const handleCreateLiteLLMVirtualKey = async () => {
    try {
      setConnectorBusy(true);
      const res = await apiRequest<any>("/api/v1/integrations/litellm/virtual-key", {
        method: "POST",
        body: { key_alias: "subsphere-universal", billing_lane_default: "api_key" },
      });
      setLitellmVirtualKey(res);
      onNotify("LiteLLM Gateway virtual key generated.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to create LiteLLM virtual key");
    } finally {
      setConnectorBusy(false);
    }
  };

  const handleRecordConnectorEvent = async () => {
    try {
      setConnectorBusy(true);
      const qty = Math.max(1, parseInt(connectorEventQty || "1", 10) || 1);
      const inTok = parseInt(connectorEventTokensIn || "0", 10) || 0;
      const outTok = parseInt(connectorEventTokensOut || "0", 10) || 0;
      const ev: Record<string, any> = {
        event_type: connectorEventType,
        application: connectorEventApp,
        quantity: qty,
      };
      if (connectorEventType === "document_read" || connectorEventType === "source_code_read" || connectorEventType === "document_generated") {
        ev.file_type = connectorEventDetail || "pdf";
      } else if (connectorEventType === "image_generated" || connectorEventType === "image_uploaded") {
        ev.media_type = connectorEventDetail || "png";
      } else if (connectorEventType === "application_activity") {
        ev.active_seconds = qty * 60;
        ev.quantity = 1;
      } else if (connectorEventType === "model_request") {
        ev.model_name = "claude-3-5-sonnet";
        if (inTok > 0 || outTok > 0) {
          ev.input_tokens = inTok;
          ev.output_tokens = outTok;
          ev.total_tokens = inTok + outTok;
        }
      }
      const res = await apiRequest<any>("/api/v1/connectors/events", {
        method: "POST",
        body: ev,
      });
      if (res.intelligence) setIntelligence(res.intelligence);
      onNotify(`Recorded ${qty}x ${connectorEventType} for ${connectorEventApp}.`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to record connector event");
    } finally {
      setConnectorBusy(false);
    }
  };

  const handleLoadSection4Example = async () => {
    try {
      setConnectorBusy(true);
      const events = [
        { event_type: "source_code_read", application: "claude_code", source: "agent_tool_hook", file_type: "ts", quantity: 20, verified: true },
        { event_type: "document_read", application: "claude_code", source: "agent_tool_hook", file_type: "pdf", quantity: 3, verified: true },
        { event_type: "image_uploaded", application: "claude_code", source: "agent_tool_hook", media_type: "png", quantity: 2, verified: true },
        { event_type: "image_generated", application: "claude_code", source: "agent_tool_hook", media_type: "png", quantity: 4, verified: true },
        {
          event_type: "model_request",
          application: "claude_code",
          source: "opentelemetry",
          model_name: "claude-3-5-sonnet",
          quantity: 40,
          input_tokens: 380000,
          output_tokens: 120000,
          total_tokens: 500000,
          verified: true,
        },
        { event_type: "application_activity", application: "claude_web", source: "browser", active_seconds: 2700, quantity: 1, verified: false },
        { event_type: "prompt_submitted", application: "claude_web", source: "browser", quantity: 15, verified: false },
      ];
      const res = await apiRequest<any>("/api/v1/connectors/events", {
        method: "POST",
        body: { events },
      });
      if (res.intelligence) setIntelligence(res.intelligence);
      onNotify("Loaded multi-source Claude Code (OTel + hooks) & Claude Web (browser) telemetry.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to load example workload");
    } finally {
      setConnectorBusy(false);
    }
  };

  const handleGenerateHistoryReport = async () => {
    try {
      const filters: Record<string, string> = {};
      if (repCategory.trim()) filters.category = repCategory.trim();
      if (repSource.trim()) filters.source = repSource.trim();
      const rep = await apiRequest<{ id: string }>("/api/v1/reports", {
        method: "POST",
        body: {
          report_type: "statement",
          kind: "history_report",
          view: "individual",
          period_start: repFrom,
          period_end: repTo,
          filters,
        },
      });
      setActiveReportId(rep.id);
      const prev = await apiRequest<{ html: string }>(`/api/v1/reports/${rep.id}/preview?format=json`);
      setPreviewHtml(prev.html || "");
      onNotify("History report generated with live HTML preview & PDF ready.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Report generation failed");
    }
  };

  const handlePreviewExisting = async (repId: string) => {
    try {
      setActiveReportId(repId);
      const prev = await apiRequest<{ html: string }>(`/api/v1/reports/${repId}/preview?format=json`);
      setPreviewHtml(prev.html || "");
    } catch (err: any) {
      onNotify(err?.message || "Preview failed");
    }
  };

  const handleDownloadPdf = (repId: string) => {
    window.open(`/api/v1/reports/${repId}/download`, "_blank", "noopener");
  };

  const handleEmailReport = async (repId: string) => {
    try {
      const res = await apiRequest<{ status: string; recipient: string; last_error?: string }>(
        `/api/v1/reports/${repId}/send-email`,
        { method: "POST" }
      );
      onNotify(`Report email (${res.recipient}): ${res.status}${res.last_error ? ` — ${res.last_error}` : ""}`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Email report failed");
    }
  };

  const handleSecondaryExport = async (fmt: "csv" | "json") => {
    try {
      const data = await apiRequest<any>(`/api/v1/history?from=${repFrom}&to=${repTo}&view=individual`);
      let blob: Blob;
      let filename: string;
      if (fmt === "json") {
        blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        filename = `subsphere-history-${repFrom}-to-${repTo}.json`;
      } else {
        const rows = [["Service", "Category", "Plan", "Interval", "Currency", "Price", "NextDue", "Tracking"]];
        for (const s of data.subscriptions || []) {
          rows.push([
            s.service_name,
            s.category,
            s.plan_name,
            s.billing_interval,
            s.currency,
            (s.price_minor / 100).toFixed(2),
            s.next_renewal_date,
            s.tracking_status || "tracking_active",
          ]);
        }
        const csvText = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
        blob = new Blob([csvText], { type: "text/csv" });
        filename = `subsphere-history-${repFrom}-to-${repTo}.csv`;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      onNotify(`Exported ${fmt.toUpperCase()} snapshot.`);
    } catch (err: any) {
      onNotify(err?.message || "Export failed");
    }
  };

  const handleSaveNotifPrefs = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const offsets = prefOffsets.split(",").map((x) => parseInt(x.trim(), 10)).filter((n) => !Number.isNaN(n));
      await apiRequest("/api/v1/notifications/preferences", {
        method: "PATCH",
        body: {
          email_enabled: prefEnabled,
          recipient_email: prefEmail || undefined,
          default_offsets_days: offsets,
          send_hour_local: parseInt(prefHour || "9", 10),
          timezone: prefTz,
          monthly_report_opt_in: prefMonthly,
        },
      });
      onNotify("Notification preferences saved.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to update notification preferences");
    }
  };

  const handleSendTestEmail = async () => {
    try {
      const targetMask = maskedInbox?.relay_mask_email || notifPrefs?.relay_mask_email;
      const res = await apiRequest<any>("/api/v1/notifications/test-email", {
        method: "POST",
        body: { recipient: targetMask || prefEmail || undefined },
      });
      setTestEmailOutcome(res);
      onNotify(res.actionable_message || `Test email status: ${res.status}`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Test email failed");
    }
  };

  const handleReceiveViaMask = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    try {
      const msg = await apiRequest<any>("/api/v1/notifications/masked-inbox/receive", {
        method: "POST",
        body: { sender: recvSender || "billing@claude.ai", subject: recvSubject || "Subscription Receipt via Mask" },
      });
      setSelectedInboxMsg(msg);
      onNotify(`Received email to mask ${msg.masked_recipient} (routed to ${msg.masked_email}).`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Could not receive masked email");
    }
  };

  const handleRunRemindersNow = async () => {
    try {
      const res = await apiRequest<any>("/api/v1/personal/reminders/tick", { method: "POST" });
      onNotify(`Reminder check queued ${res.queued_count} email(s).`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Reminder check failed");
    }
  };

  const handleDeleteHistory = async () => {
    try {
      await apiRequest("/api/v1/personal/history", { method: "DELETE" });
      onNotify("Personal browser activity & report history deleted.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "History deletion failed");
    }
  };

  const filteredSubs = subs.filter((s) => {
    const matchesSearch =
      s.service_name.toLowerCase().includes(filterText.toLowerCase()) ||
      s.category.toLowerCase().includes(filterText.toLowerCase());
    const tStat = s.tracking_status || "tracking_active";
    const matchesTrack = filterTracking === "all" || tStat === filterTracking;
    return matchesSearch && matchesTrack;
  });

  const { daysInMonth, startDow } = monthBounds(calendarMonth);
  const eventsByDay: Record<string, CalendarEvent[]> = {};
  for (const ev of calendarEvents) {
    if (!eventsByDay[ev.date]) eventsByDay[ev.date] = [];
    eventsByDay[ev.date].push(ev);
  }
  const gridCells: Array<{ dayNum: number | null; dateStr: string }> = [];
  for (let i = 0; i < startDow; i += 1) gridCells.push({ dayNum: null, dateStr: "" });
  for (let d = 1; d <= daysInMonth; d += 1) {
    gridCells.push({ dayNum: d, dateStr: `${calendarMonth}-${String(d).padStart(2, "0")}` });
  }
  while (gridCells.length % 7 !== 0) gridCells.push({ dayNum: null, dateStr: "" });

  return (
    <div className="workspace">
      <nav className="sidebar" aria-label="Individual View Navigation">
        {(
          [
            ["overview", "Dashboard"],
            ["subscriptions", "Subscriptions"],
            ["ai_usage", "AI Usage Intelligence"],
            ["calendar", "Calendar"],
            ["reports", "Reports"],
            ["notifications", "Notifications"],
            ["settings", "Settings & Privacy"],
          ] as [IndividualTab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            className={`nav-item ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="main-content">
        {tab === "overview" && (
          <>
            <div className="page-header">
              <div>
                <h1>Personal Subscription &amp; AI Dashboard</h1>
                <p className="subtitle">
                  Private personal workspace — isolated from organizations.
                </p>
              </div>
              <button className="btn btn-primary" onClick={openAddModal} type="button">
                + Add Subscription
              </button>
            </div>

            <div className="alert alert-info" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
              <div>
                <strong>SubSphere Universal Usage Connector:</strong>{" "}
                <span className={`badge ${(intelligence?.active_sources_count || 0) > 0 ? "badge-exact" : "badge-unavailable"}`}>
                  {intelligence?.active_sources_count || 0} sources active
                </span>{" "}
                Automatically selects the most accurate method per AI app (Official API, OTel, LiteLLM, Agent Hooks, MCP, Browser) without guessing unavailable tokens.
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn btn-primary btn-sm"
                  type="button"
                  onClick={() => setTab("ai_usage")}
                >
                  Open AI Usage Intelligence
                </button>
              </div>
            </div>

            <div className="kpi-grid">
              {(dashboard?.currency_totals.length
                ? dashboard.currency_totals
                : [{ currency: "USD", monthly_equivalent_minor: 0, annual_equivalent_minor: 0, subscription_count: 0 }]
              ).map((c) => (
                <div className="card" key={c.currency}>
                  <div className="kpi-label">Monthly Spend ({c.currency})</div>
                  <div className="kpi-value">
                    {formatMinorCurrency(c.monthly_equivalent_minor, c.currency)}
                  </div>
                  <div className="kpi-meta">
                    Annual eq: {formatMinorCurrency(c.annual_equivalent_minor, c.currency)} ({c.subscription_count} active)
                  </div>
                </div>
              ))}
              <div className="card">
                <div className="kpi-label">Tokens Consumed (Reported)</div>
                <div className="kpi-value">
                  {formatNullableMetric(intelligence?.measurements?.tokens_consumed?.value ?? aiUsage?.tokens.total_tokens)}
                </div>
                <div className="kpi-meta">
                  {(intelligence?.measurements?.tokens_consumed?.value ?? aiUsage?.tokens.total_tokens) == null ? (
                    <span className="badge badge-unavailable">Unavailable — Connect telemetry source</span>
                  ) : (
                    <span className="badge badge-exact">
                      {formatNullableMetric(intelligence?.measurements?.model_requests?.value ?? aiUsage?.tokens.requests_count)} model reqs ·{" "}
                      {formatNullableMetric(aiUsage?.tokens.input_tokens ?? intelligence?.measurements?.tokens_consumed?.input_tokens)} IN
                    </span>
                  )}
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">Images &amp; Documents</div>
                <div className="kpi-value">
                  {intelligence?.measurements?.documents_accessed?.value != null ||
                  intelligence?.measurements?.images_generated?.value != null
                    ? `${intelligence?.measurements?.images_generated?.value ?? 0} img · ${intelligence?.measurements?.documents_accessed?.value ?? 0} docs`
                    : "Unavailable"}
                </div>
                <div className="kpi-meta">
                  {intelligence?.measurements?.application_activity?.active_minutes != null
                    ? `${intelligence.measurements.application_activity.active_minutes} min browser/desktop observed`
                    : "Instrumented tool reads & web uploads separated"}
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">AI Credits Remaining</div>
                <div className="kpi-value">
                  {aiUsage?.credits
                    ? formatMinorCurrency(aiUsage.credits.remaining_credits_minor, aiUsage.credits.currency)
                    : "Unavailable"}
                </div>
                <div className="kpi-meta">
                  {aiUsage?.credits
                    ? `Used: ${formatMinorCurrency(aiUsage.credits.used_credits_minor, aiUsage.credits.currency)} of ${formatMinorCurrency(aiUsage.credits.monthly_limit_minor, aiUsage.credits.currency)}`
                    : "Connect AI telemetry to monitor credits"}
                </div>
              </div>
            </div>

            <div className="card">
              <h2>Source Connection Status</h2>
              <div className="kpi-grid" style={{ marginTop: 10 }}>
                <div>
                  <strong>Universal Connector:</strong>{" "}
                  <span className={`badge ${(intelligence?.active_sources_count || 0) > 0 ? "badge-exact" : "badge-unavailable"}`}>
                    {(intelligence?.active_sources_count || 0) > 0
                      ? `${intelligence.active_sources_count} active (${(intelligence.active_sources || []).join(", ")})`
                      : "Not configured"}
                  </span>
                </div>
                <div>
                  <strong>SubSphere MCP Server:</strong>{" "}
                  <span className={`badge ${mcpStatus?.connected ? "badge-exact" : "badge-unavailable"}`}>
                    {mcpStatus?.connected
                      ? `Connected (${mcpStatus?.active_connection?.client_name || "agent"})`
                      : "Not configured"}
                  </span>
                </div>
                <div>
                  <strong>SMTP Email Relay:</strong>{" "}
                  <span className={`badge ${emailStatus?.reachable ? "badge-exact" : "badge-observed"}`}>
                    {emailStatus?.reachable ? `Connected (${emailStatus.smtp_host}:${emailStatus.smtp_port})` : "Unreachable"}
                  </span>
                </div>
              </div>
            </div>

            <div className="card">
              <h2>Upcoming Renewals</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Service</th>
                      <th>Category</th>
                      <th>Interval</th>
                      <th>Recorded Price</th>
                      <th>Next Renewal</th>
                      <th>Tracking</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(dashboard?.upcoming_renewals || []).length === 0 ? (
                      <tr>
                        <td colSpan={7}>No active subscriptions yet. Add one to populate calendar &amp; reminders.</td>
                      </tr>
                    ) : (
                      (dashboard?.upcoming_renewals || []).map((s) => (
                          <tr key={s.id}>
                            <td><strong>{s.service_name}</strong></td>
                            <td>{s.category}</td>
                            <td>{s.billing_interval}</td>
                            <td>{formatMinorCurrency(s.price_minor, s.currency)}</td>
                            <td>{s.next_renewal_date}</td>
                            <td><span className="badge badge-exact">{s.tracking_status || "tracking_active"}</span></td>
                            <td>
                              <button className="btn btn-sm" onClick={() => openEditModal(s)} type="button">Edit</button>
                            </td>
                          </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "subscriptions" && (
          <>
            <div className="page-header">
              <div>
                <h1>Personal Subscriptions</h1>
                <p className="subtitle">Add, edit, reschedule, or stop tracking personal recurring bills &amp; one-time purchases.</p>
              </div>
              <button className="btn btn-primary" onClick={openAddModal} type="button">+ Add Subscription</button>
            </div>
            <div className="card">
              <div className="form-grid" style={{ marginBottom: 14 }}>
                <div className="field">
                  <label htmlFor="sub-search">Search service or category</label>
                  <input
                    id="sub-search"
                    value={filterText}
                    onChange={(e) => setFilterText(e.target.value)}
                    placeholder="e.g. Claude, AI, Streaming"
                  />
                </div>
                <div className="field">
                  <label htmlFor="sub-track-filter">Filter by tracking status</label>
                  <select
                    id="sub-track-filter"
                    value={filterTracking}
                    onChange={(e) => setFilterTracking(e.target.value)}
                  >
                    <option value="all">All subscriptions</option>
                    <option value="tracking_active">Active Tracking</option>
                    <option value="stopped_tracking">Stopped Tracking</option>
                    <option value="canceled_at_provider">Canceled at Provider</option>
                  </select>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Service &amp; Plan</th>
                      <th>Category</th>
                      <th>Interval</th>
                      <th>Price</th>
                      <th>Next Due</th>
                      <th>TZ &amp; Offsets</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredSubs.map((s) => (
                        <tr key={s.id}>
                          <td>
                            <strong>{s.service_name}</strong>
                            {s.plan_name ? <div className="kpi-meta">{s.plan_name}</div> : null}
                          </td>
                          <td>{s.category}</td>
                          <td>{s.billing_interval}</td>
                          <td>{formatMinorCurrency(s.price_minor, s.currency)}</td>
                          <td>{s.next_renewal_date}</td>
                          <td>
                            <span className="badge">{s.timezone || "UTC"}</span>{" "}
                            <span className="kpi-meta">[{(s.reminder_offsets_days || []).join(",")}d]</span>
                          </td>
                          <td>
                            <span className={`badge ${(s.tracking_status || "tracking_active") === "tracking_active" ? "badge-exact" : "badge-danger"}`}>
                              {s.tracking_status || "tracking_active"}
                            </span>
                          </td>
                          <td>
                            <button className="btn btn-sm" onClick={() => openEditModal(s)} type="button" style={{ marginRight: 6 }}>
                              Edit
                            </button>
                            <button className="btn btn-danger btn-sm" onClick={() => handleDeleteSub(s.id)} type="button">
                              Delete
                            </button>
                          </td>
                        </tr>
                    ))}
                    {filteredSubs.length === 0 && (
                      <tr><td colSpan={8}>No matching subscriptions found.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "ai_usage" && (
          <>
            <div className="page-header">
              <div>
                <h1>AI Usage Intelligence</h1>
                <p className="subtitle">
                  {intelligence?.workspace_label || "Personal workspace"} · Connect your AI tools once; SubSphere automatically selects the most accurate source per app and labels exact vs. observed usage honestly.
                </p>
              </div>
              <span className={`badge ${(intelligence?.active_sources_count || 0) > 0 ? "badge-exact" : "badge-unavailable"}`}>
                {intelligence?.active_sources_count || 0} sources active
              </span>
            </div>

            <div className="alert alert-info">
              <strong>{aiUsage?.cost_separation.note || "Subscription fee, reported API cost, and estimated token value are separated strictly."}</strong>
              {" · "}
              {intelligence?.distinction_note ||
                "Model requests, web prompts, and coding-agent tool calls are counted separately."}
            </div>

            {/* Section A: Five Core Measurements */}
            <div className="kpi-grid">
              <div className="card">
                <div className="kpi-label">1. Model Requests</div>
                <div className="kpi-value">
                  {formatNullableMetric(intelligence?.measurements?.model_requests?.value)}
                </div>
                <div className="kpi-meta">
                  {intelligence?.measurements?.model_requests?.label || "Unavailable"} ·{" "}
                  {intelligence?.measurements?.model_requests?.tool_call_count ?? 0} tool calls ·{" "}
                  {intelligence?.measurements?.model_requests?.prompt_count_observed ?? 0} web prompts
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">2. Tokens Consumed</div>
                <div className="kpi-value">
                  {formatNullableMetric(intelligence?.measurements?.tokens_consumed?.value ?? aiUsage?.tokens.total_tokens)}
                </div>
                <div className="kpi-meta">
                  {(intelligence?.measurements?.tokens_consumed?.value ?? aiUsage?.tokens.total_tokens) == null ? (
                    <span className="badge badge-unavailable">Unavailable — Connect telemetry source</span>
                  ) : (
                    <span>
                      {formatNullableMetric(intelligence?.measurements?.tokens_consumed?.input_tokens ?? aiUsage?.tokens.input_tokens)} IN /{" "}
                      {formatNullableMetric(intelligence?.measurements?.tokens_consumed?.output_tokens ?? aiUsage?.tokens.output_tokens)} OUT
                    </span>
                  )}
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">3. Images Generated</div>
                <div className="kpi-value">
                  {formatNullableMetric(intelligence?.measurements?.images_generated?.value)}
                </div>
                <div className="kpi-meta">
                  {intelligence?.measurements?.images_generated?.label || "Unavailable"} ·{" "}
                  {intelligence?.measurements?.images_generated?.images_uploaded ?? 0} uploaded/examined
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">4. Documents Accessed</div>
                <div className="kpi-value">
                  {formatNullableMetric(intelligence?.measurements?.documents_accessed?.value)}
                </div>
                <div className="kpi-meta">
                  {intelligence?.measurements?.documents_accessed?.source_code_reads ?? 0} code files ·{" "}
                  {intelligence?.measurements?.documents_accessed?.document_reads ?? 0} PDFs/docs ·{" "}
                  {intelligence?.measurements?.documents_accessed?.documents_generated ?? 0} created
                </div>
              </div>
              <div className="card">
                <div className="kpi-label">5. Application Activity</div>
                <div className="kpi-value">
                  {intelligence?.measurements?.application_activity?.active_minutes != null
                    ? `${intelligence.measurements.application_activity.active_minutes} min`
                    : "Unavailable"}
                </div>
                <div className="kpi-meta">
                  Estimated via browser extension / desktop companion
                </div>
              </div>
            </div>

            {/* Strict Financial Separation Row */}
            <div className="kpi-grid">
              <div className="card">
                <div className="kpi-label">Subscription Fee ({aiUsage?.currency || "USD"})</div>
                <div className="kpi-value">{formatMinorCurrency(aiUsage?.cost_separation.subscription_fee_minor, aiUsage?.currency || "USD")}</div>
                <div className="kpi-meta">Fixed recurring fee</div>
              </div>
              <div className="card">
                <div className="kpi-label">Reported API Spend</div>
                <div className="kpi-value">{formatMinorCurrency(aiUsage?.cost_separation.reported_extra_usage_minor, aiUsage?.currency || "USD")}</div>
                <div className="kpi-meta">Direct metered charges only</div>
              </div>
              <div className="card">
                <div className="kpi-label">Estimated Equivalent Value</div>
                <div className="kpi-value">{formatMinorCurrency(aiUsage?.cost_separation.estimated_token_value_minor, aiUsage?.currency || "USD")}</div>
                <div className="kpi-meta">Not an invoice charge</div>
              </div>
            </div>

            {/* Section B: Usage by Application & Standardized Connector Records */}
            <div className="card">
              <h2>Usage by Application</h2>
              <p className="subtitle">
                {intelligence?.data_coverage_note ||
                  "Token totals cover connected telemetry sources only. Browser activity and tool-operation counts are displayed separately."}
              </p>
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Application</th>
                      <th>Tokens</th>
                      <th>Model Requests</th>
                      <th>Images (Gen / Up)</th>
                      <th>Docs (Code / PDF)</th>
                      <th>Activity</th>
                      <th>Source</th>
                      <th>Coverage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(intelligence?.usage_by_application || []).map((row: any) => (
                      <tr key={row.application}>
                        <td>
                          <strong>{row.display_name}</strong>
                          <div className="kpi-meta">{row.period}</div>
                        </td>
                        <td>
                          {row.metrics.total_tokens == null ? (
                            <span className="badge badge-unavailable">Unavailable</span>
                          ) : (
                            <strong>{formatNullableMetric(row.metrics.total_tokens)}</strong>
                          )}
                        </td>
                        <td>{row.metrics.model_requests != null ? formatNullableMetric(row.metrics.model_requests) : "Unavailable"}</td>
                        <td>
                          {row.metrics.images_generated != null || row.metrics.images_uploaded != null
                            ? `${row.metrics.images_generated ?? 0} / ${row.metrics.images_uploaded ?? 0}`
                            : "Unavailable"}
                        </td>
                        <td>
                          {row.metrics.source_code_reads != null || row.metrics.document_reads != null
                            ? `${row.metrics.source_code_reads ?? 0} code / ${row.metrics.document_reads ?? 0} docs`
                            : "Unavailable"}
                        </td>
                        <td>
                          {row.metrics.active_minutes != null
                            ? `${row.metrics.active_minutes} min`
                            : row.metrics.prompt_count_observed != null
                              ? `${row.metrics.prompt_count_observed} prompts`
                              : "-"}
                        </td>
                        <td>
                          <span className={`badge ${row.source === "browser" || row.source === "desktop" ? "badge-observed" : "badge-exact"}`}>
                            {row.source_badge}
                          </span>
                        </td>
                        <td className="kpi-meta">
                          tokens: <code>{row.coverage.tokens}</code> · docs: <code>{row.coverage.documents}</code>
                        </td>
                      </tr>
                    ))}
                    {(intelligence?.usage_by_application || []).length === 0 && (
                      <tr>
                        <td colSpan={8}>
                          No application telemetry recorded yet. Connect an AI tool below or load the 2-hour coding agent + web session example.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <details style={{ marginTop: 12 }}>
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                  Standardized Universal Connector Records (JSON)
                </summary>
                <pre className="code-block" style={{ marginTop: 8 }}>
                  {JSON.stringify(intelligence?.connector_records || [], null, 2)}
                </pre>
              </details>
            </div>

            {/* Section C: Connect Your AI Tools Once (Priority Order 1-5) */}
            <div className="card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
                <div>
                  <h2>Connect Your AI Tools Once (Auto-Selected Tracking Method)</h2>
                  <p className="subtitle">
                    Every supported tool uses its highest-accuracy connector instead of guessing across incompatible web and local environments.
                  </p>
                </div>
                <button
                  className="btn btn-primary btn-sm"
                  type="button"
                  disabled={connectorBusy}
                  onClick={() => void handleLoadSection4Example()}
                >
                  {connectorBusy ? "Loading..." : "Load 2-Hour Agent + Web Session Example"}
                </button>
              </div>

              <div className="kpi-grid" style={{ marginTop: 12 }}>
                {/* Priority 1: Claude Code OpenTelemetry */}
                <div className="card" style={{ margin: 0 }}>
                  <h3>1. Claude Code (OTel + Hooks)</h3>
                  <p className="kpi-meta">
                    Exports input/output/cache tokens, requests, sessions, and file-read tool hooks. No LiteLLM gateway required.
                  </p>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() => void handleConnectApp("claude_code")}
                    >
                      Connect Claude Code
                    </button>
                    <button
                      className="btn btn-sm"
                      type="button"
                      onClick={handleDownloadClaudeCodeHelper}
                    >
                      Download Config Helper (.sh)
                    </button>
                  </div>
                </div>

                {/* Priority 2: LiteLLM Gateway */}
                <div className="card" style={{ margin: 0 }}>
                  <h3>2. LiteLLM Local Gateway</h3>
                  <p className="kpi-meta">
                    Exact tokens and costs for explicitly routed API requests. Cannot passively monitor unrelated web apps.
                  </p>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() => void handleConnectApp("litellm_gateway")}
                    >
                      Connect LiteLLM
                    </button>
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() => void handleCreateLiteLLMVirtualKey()}
                    >
                      Generate Virtual Key
                    </button>
                  </div>
                  {litellmVirtualKey && (
                    <p className="kpi-meta" style={{ marginTop: 8 }}>
                      Virtual Key: <code>{litellmVirtualKey.virtual_key}</code>
                    </p>
                  )}
                </div>

                {/* Priority 3: Browser Extension */}
                <div className="card" style={{ margin: 0 }}>
                  <h3>3. Web Apps (Browser Extension)</h3>
                  <p className="kpi-meta">
                    Observes <code>claude.ai</code> / <code>chatgpt.com</code> / <code>huggingface.co</code> visits, active time, and upload/prompt actions. Labeled observed only — tokens remain <strong>Unavailable</strong>.
                  </p>
                  <div style={{ marginTop: 8, fontSize: "0.78rem", wordBreak: "break-all" }}>
                    <strong>Unpacked Chrome Extension Path:</strong>{" "}
                    <code>/home/abdul-ahad/Documents/SubSphere_Complete_Project_Package/SubSphere_Project/services/browser-extension</code>{" "}
                    <button
                      className="btn btn-sm"
                      type="button"
                      style={{ marginLeft: 4 }}
                      onClick={() =>
                        void handleCopyText(
                          "Extension Path",
                          "/home/abdul-ahad/Documents/SubSphere_Complete_Project_Package/SubSphere_Project/services/browser-extension"
                        )
                      }
                    >
                      Copy Extension Path
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() => void handleConnectApp("claude_web")}
                    >
                      Connect Claude Web
                    </button>
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() => void handleConnectApp("chatgpt_web")}
                    >
                      Connect ChatGPT Web
                    </button>
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() =>
                        void handleConnectApp("huggingchat", {
                          plan_tier: "browser",
                          allowlist: ["huggingface.co"],
                        })
                      }
                    >
                      Connect HuggingChat Web
                    </button>
                  </div>
                </div>

                {/* Priority 4: Cursor Auto-Selector */}
                <div className="card" style={{ margin: 0 }}>
                  <h3>4. Cursor (Plan-Aware Auto-Select)</h3>
                  <p className="kpi-meta">
                    Uses Admin API on Team/Business, OTel on Enterprise, and automatically falls back to Coding-Agent Hooks for Personal subscriptions.
                  </p>
                  <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                    <select value={cursorPlanTier} onChange={(e) => setCursorPlanTier(e.target.value)}>
                      <option value="personal">Personal Subscription (Hook / MCP Fallback)</option>
                      <option value="team">Team / Business Plan (Official Admin API)</option>
                      <option value="enterprise">Enterprise Plan (OTel Export)</option>
                    </select>
                    {cursorPlanTier === "team" && (
                      <input
                        type="password"
                        placeholder="Cursor Admin API key (cur_admin_...)"
                        value={cursorApiKey}
                        onChange={(e) => setCursorApiKey(e.target.value)}
                      />
                    )}
                    <button
                      className="btn btn-primary btn-sm"
                      type="button"
                      disabled={connectorBusy}
                      onClick={() =>
                        void handleConnectApp("cursor", {
                          plan_tier: cursorPlanTier,
                          api_key: cursorApiKey || undefined,
                        })
                      }
                    >
                      Connect Cursor
                    </button>
                  </div>
                </div>
              </div>

              {connectResult && (
                <div className="alert alert-info" style={{ marginTop: 14 }}>
                  <div>
                    <strong>{connectResult.display_name}</strong> → Selected{" "}
                    <span className="badge badge-exact">{connectResult.selected_method}</span>{" "}
                    ({connectResult.fallback_reason})
                  </div>
                  {connectResult.config?.pair_code && (
                    <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong>Extension Pair Code:</strong>{" "}
                      <code>{connectResult.config.pair_code}</code>
                      <button
                        className="btn btn-sm"
                        type="button"
                        onClick={() => void handleCopyText("Extension Pair Code", connectResult.config.pair_code)}
                      >
                        Copy Pair Code
                      </button>
                      <span className="kpi-meta">
                        (In Chrome: open <code>chrome://extensions</code> → enable Developer Mode → Load unpacked → select the folder above)
                      </span>
                    </div>
                  )}
                  {connectResult.config?.export_env_block && (
                    <pre className="code-block" style={{ marginTop: 8 }}>
                      {connectResult.config.export_env_block}
                    </pre>
                  )}
                </div>
              )}

              {connectorCatalog?.tracking_methods && (
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                    Six Practical Tracking Methods Catalog ({connectorCatalog.tracking_methods.length} supported)
                  </summary>
                  <div className="table-wrap" style={{ marginTop: 8 }}>
                    <table>
                      <thead>
                        <tr><th>Rank</th><th>Method</th><th>Accuracy</th><th>Limitations</th></tr>
                      </thead>
                      <tbody>
                        {connectorCatalog.tracking_methods.map((m: any) => (
                          <tr key={m.id}>
                            <td>#{m.rank}</td>
                            <td><strong>{m.name}</strong></td>
                            <td><code>{m.accuracy}</code></td>
                            <td className="kpi-meta">{m.limitations}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
            </div>

            {/* Priority 5: Coding-Agent Tool Hooks & Normalized Event Recorder */}
            <div className="card">
              <h2>5. Coding-Agent Tool Hooks &amp; Normalized Event Recorder</h2>
              <p className="subtitle">
                Record metadata-only file reads, PDF accesses, image generations, and model requests without ever uploading document bodies or prompts.
              </p>
              <div className="form-grid" style={{ marginTop: 10 }}>
                <div className="field">
                  <label>Application</label>
                  <select value={connectorEventApp} onChange={(e) => setConnectorEventApp(e.target.value)}>
                    <option value="claude_code">Claude Code</option>
                    <option value="cursor">Cursor</option>
                    <option value="codex">Codex</option>
                    <option value="claude_web">Claude Web (Observed)</option>
                  </select>
                </div>
                <div className="field">
                  <label>Event Type</label>
                  <select value={connectorEventType} onChange={(e) => setConnectorEventType(e.target.value)}>
                    <option value="document_read">document_read (PDF / doc accessed)</option>
                    <option value="source_code_read">source_code_read (code file read)</option>
                    <option value="document_generated">document_generated (doc/artifact created)</option>
                    <option value="image_generated">image_generated</option>
                    <option value="image_uploaded">image_uploaded</option>
                    <option value="model_request">model_request (API / OTel call)</option>
                    <option value="prompt_submitted">prompt_submitted (observed web prompt)</option>
                    <option value="application_activity">application_activity (minutes)</option>
                  </select>
                </div>
                <div className="field">
                  <label>File / Media Type</label>
                  <input value={connectorEventDetail} onChange={(e) => setConnectorEventDetail(e.target.value)} placeholder="pdf / py / png" />
                </div>
                <div className="field">
                  <label>Quantity (or Minutes)</label>
                  <input type="number" min={1} value={connectorEventQty} onChange={(e) => setConnectorEventQty(e.target.value)} />
                </div>
                {connectorEventType === "model_request" && (
                  <>
                    <div className="field">
                      <label>Input Tokens</label>
                      <input type="number" min={0} value={connectorEventTokensIn} onChange={(e) => setConnectorEventTokensIn(e.target.value)} />
                    </div>
                    <div className="field">
                      <label>Output Tokens</label>
                      <input type="number" min={0} value={connectorEventTokensOut} onChange={(e) => setConnectorEventTokensOut(e.target.value)} />
                    </div>
                  </>
                )}
                <div style={{ display: "flex", alignItems: "flex-end" }}>
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={connectorBusy}
                    onClick={() => void handleRecordConnectorEvent()}
                  >
                    Record Normalized Event
                  </button>
                </div>
              </div>
            </div>

            {/* SubSphere MCP Server Connector (Tool Execution & Usage Reporting) */}
            <details className="card" open>
              <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: "1rem" }}>
                SubSphere MCP Server Setup (HuggingChat, Claude Desktop, Cursor &amp; Web MCP Clients)
              </summary>
              <div style={{ marginTop: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
                  <p className="kpi-meta">
                    Status:{" "}
                    <span className={`badge ${mcpStatus?.connected ? "badge-exact" : "badge-unavailable"}`}>
                      {mcpStatus?.connected
                        ? `Connected (${mcpStatus?.active_connection?.client_name || "agent"})`
                        : "Not configured"}
                    </span>
                    {mcpStatus && (
                      <span> · Total MCP Tokens: {formatNullableMetric(mcpStatus.total_tokens)} · Requests: {mcpStatus.requests_count ?? 0}</span>
                    )}
                  </p>
                </div>

                <div className="form-grid" style={{ marginTop: 10 }}>
                  <div className="field">
                    <label>AI Agent / Web Client</label>
                    <select value={mcpClientName} onChange={(e) => setMcpClientName(e.target.value)}>
                      <option value="huggingchat">HuggingChat (huggingface.co/chat — HTTP MCP)</option>
                      <option value="claude-desktop">Claude Desktop (stdio MCP)</option>
                      <option value="cursor">Cursor (stdio MCP)</option>
                      <option value="claude-code">Claude Code CLI</option>
                      <option value="web-mcp-agent">Web / HTTP MCP Agent</option>
                    </select>
                  </div>
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
                    <button className="btn btn-primary" type="button" disabled={mcpBusy} onClick={() => void handleGenerateMcpToken()}>
                      {mcpBusy ? "Generating..." : "Generate MCP Install Config"}
                    </button>
                    <button className="btn" type="button" disabled={mcpBusy} onClick={handleDownloadMcpScript}>
                      Download subsphere_mcp_server.py
                    </button>
                    {(mcpStatus?.connected || mcpTokenResult) && (
                      <button className="btn btn-danger" type="button" disabled={mcpBusy} onClick={() => void handleRevokeMcp()}>
                        Revoke MCP
                      </button>
                    )}
                  </div>
                </div>

                {mcpTokenResult && (
                  <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
                    <div className="alert alert-info" style={{ display: "grid", gap: 6 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                        <div>
                          <strong>HuggingChat (<code>huggingface.co/chat</code>) MCP Server URL:</strong>{" "}
                          <code>{mcpTokenResult.http_mcp_url}</code>
                        </div>
                        <button
                          className="btn btn-primary btn-sm"
                          type="button"
                          onClick={() => void handleCopyText("HuggingChat MCP URL", mcpTokenResult.http_mcp_url)}
                        >
                          Copy HuggingChat MCP URL
                        </button>
                      </div>
                      <ol style={{ margin: "4px 0 0 18px", fontSize: "0.85rem" }}>
                        <li>Open <strong>https://huggingface.co/chat</strong> → Settings / <strong>MCP Servers</strong> → <strong>Add Server</strong>.</li>
                        <li>Set Name to <code>SubSphere</code> and Server URL to the copied URL (or <code>http://127.0.0.1:8080/api/v1/mcp</code>).</li>
                        <li>Click <strong>Save</strong> to enable SubSphere tools inside HuggingChat (if HuggingChat supports custom Authorization headers, optionally pass the MCP Bearer token below).</li>
                      </ol>
                    </div>
                    <div className="alert alert-info" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                      <div><strong>MCP Token:</strong> <code>{mcpTokenResult.mcp_token}</code></div>
                      <button className="btn btn-sm" type="button" onClick={() => void handleCopyText("MCP Token", mcpTokenResult.mcp_token)}>
                        Copy Token
                      </button>
                    </div>
                    <pre className="code-block">{JSON.stringify(mcpTokenResult.claude_desktop_config, null, 2)}</pre>
                    <pre className="code-block">{mcpTokenResult.claude_code_command}</pre>
                  </div>
                )}

                <div className="form-grid" style={{ marginTop: 12 }}>
                  <div className="field"><label>Test Model</label><input value={mcpTestModel} onChange={(e) => setMcpTestModel(e.target.value)} /></div>
                  <div className="field">
                    <label>Billing Lane</label>
                    <select value={mcpTestLane} onChange={(e) => setMcpTestLane(e.target.value as any)}>
                      <option value="claude_subscription">Subscription Plan</option>
                      <option value="api_key">Direct API Key</option>
                    </select>
                  </div>
                  <div className="field"><label>Input Tokens</label><input type="number" min={0} value={mcpTestInTokens} onChange={(e) => setMcpTestInTokens(e.target.value)} /></div>
                  <div className="field"><label>Output Tokens</label><input type="number" min={0} value={mcpTestOutTokens} onChange={(e) => setMcpTestOutTokens(e.target.value)} /></div>
                  <div style={{ display: "flex", alignItems: "flex-end" }}>
                    <button className="btn" type="button" disabled={mcpBusy} onClick={() => void handleSendMcpUsageTest()}>
                      Send MCP Test Report
                    </button>
                  </div>
                </div>
                {mcpLastTestResult && (
                  <div className="alert alert-info" style={{ marginTop: 8 }}>
                    Recorded via <code>{mcpLastTestResult.source || "MCP_SERVER"}</code>: {mcpLastTestResult.total_tokens ?? ""} tokens
                  </div>
                )}
              </div>
            </details>

            {/* Telemetry Ledger */}
            <div className="card">
              <h2>Verified Telemetry Ledger</h2>
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Application / Source</th>
                      <th>Model / Provenance</th>
                      <th>Tokens</th>
                      <th>API Cost</th>
                      <th>Est. Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(aiUsage?.records || []).map((r) => (
                      <tr key={r.id}>
                        <td>
                          <strong>{(r as any).application || r.connection_name || r.provider_code}</strong>{" "}
                          <span className="badge badge-exact">{r.source}</span> {(r as any).billing_lane || ""}
                        </td>
                        <td>{r.model_name || "-"} · <span className="kpi-meta">{r.quality_note}</span></td>
                        <td>{formatNullableMetric(r.total_tokens)}</td>
                        <td>{r.reported_cost_minor != null ? formatMinorCurrency(r.reported_cost_minor, r.currency) : "Not billed per token"}</td>
                        <td>{r.estimated_value_minor != null ? formatMinorCurrency(r.estimated_value_minor, r.currency) : "-"}</td>
                      </tr>
                    ))}
                    {(aiUsage?.records || []).length === 0 && (
                      <tr>
                        <td colSpan={5}>No verified token records ingested yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "calendar" && (
          <>
            <div className="page-header">
              <div>
                <h1>Personal Renewal Calendar ({calendarMonth})</h1>
                <p className="subtitle">True 7-column month view backed by timezone-aware recurrence projection.</p>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, -1))}>&lt; Prev</button>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth("2026-11")}>Today</button>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, 1))}>Next &gt;</button>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarAgendaMode(!calendarAgendaMode)}>
                  {calendarAgendaMode ? "Month Grid View" : "Agenda List View"}
                </button>
                <button className="btn btn-primary btn-sm" type="button" onClick={openAddModal}>+ Add</button>
              </div>
            </div>

            {!calendarAgendaMode ? (
              <div className="card">
                <div className="calendar-grid" style={{ marginBottom: 6 }}>
                  {WEEKDAYS.map((w) => (
                    <div key={w} className="calendar-weekday-hdr">{w}</div>
                  ))}
                </div>
                <div className="calendar-grid">
                  {gridCells.map((cell, idx) => {
                    if (!cell.dayNum) {
                      return <div key={`pad-${idx}`} className="calendar-cell calendar-cell-muted" />;
                    }
                    const dayEvents = eventsByDay[cell.dateStr] || [];
                    return (
                      <div key={cell.dateStr} className="calendar-cell">
                        <div className="calendar-day-num">{cell.dayNum}</div>
                        {dayEvents.map((ev) => {
                          const chipClass =
                            ev.status === "stopped_tracking" || ev.status === "canceled_at_provider"
                              ? "calendar-chip calendar-chip-muted"
                              : "calendar-chip";
                          return (
                            <button
                              key={ev.event_id}
                              type="button"
                              className={chipClass}
                              onClick={() => setSelectedCalEvent(ev)}
                              title={`${ev.title} (${formatMinorCurrency(ev.amount_minor, ev.currency)})`}
                            >
                              {ev.title} · {formatMinorCurrency(ev.amount_minor, ev.currency)}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="card">
                <table>
                  <thead><tr><th>Date</th><th>Subscription</th><th>Amount</th><th>Status</th><th>Action</th></tr></thead>
                  <tbody>
                    {calendarEvents.length === 0 ? (
                      <tr><td colSpan={5}>No renewals scheduled in {calendarMonth}.</td></tr>
                    ) : (
                      calendarEvents.map((ev) => (
                        <tr key={ev.event_id}>
                          <td>{ev.date}</td>
                          <td><strong>{ev.title}</strong> ({ev.plan_name || ev.billing_interval})</td>
                          <td>{formatMinorCurrency(ev.amount_minor, ev.currency)}</td>
                          <td><span className="badge">{ev.status}</span></td>
                          <td><button className="btn btn-sm" type="button" onClick={() => setSelectedCalEvent(ev)}>Inspect</button></td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {tab === "reports" && (
          <>
            <div className="page-header">
              <div>
                <h1>Personal History Reports &amp; PDFs</h1>
                <p className="subtitle">Styled HTML preview matching printable multi-section PDF with SVG category charts.</p>
              </div>
            </div>
            <div className="card">
              <div className="form-grid">
                <div className="field"><label>From Date</label><input type="date" value={repFrom} onChange={(e) => setRepFrom(e.target.value)} /></div>
                <div className="field"><label>To Date</label><input type="date" value={repTo} onChange={(e) => setRepTo(e.target.value)} /></div>
                <div className="field"><label>Category Filter</label><input value={repCategory} onChange={(e) => setRepCategory(e.target.value)} placeholder="optional e.g. AI" /></div>
                <div className="field"><label>AI Source Filter</label><input value={repSource} onChange={(e) => setRepSource(e.target.value)} placeholder="optional" /></div>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                <button className="btn btn-primary" type="button" onClick={handleGenerateHistoryReport}>Generate &amp; Preview Report</button>
                {activeReportId && (
                  <>
                    <button className="btn" type="button" onClick={() => handleDownloadPdf(activeReportId)}>Download PDF</button>
                    <button className="btn" type="button" onClick={() => handleEmailReport(activeReportId)}>Email Report</button>
                  </>
                )}
                <button className="btn" type="button" onClick={() => handleSecondaryExport("csv")}>Export CSV</button>
                <button className="btn" type="button" onClick={() => handleSecondaryExport("json")}>Export JSON</button>
              </div>
            </div>

            {previewHtml && (
              <div className="card">
                <h2>Styled HTML Preview</h2>
                <iframe className="preview-frame" sandbox="" srcDoc={previewHtml} title="History Report Preview" />
              </div>
            )}

            <div className="card">
              <h2>Saved Reports</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Document</th><th>Kind</th><th>Period</th><th>Status</th><th>Actions</th></tr></thead>
                  <tbody>
                    {reports.length === 0 ? (
                      <tr><td colSpan={5}>No reports generated yet.</td></tr>
                    ) : (
                      reports.map((r) => (
                        <tr key={r.id}>
                          <td><strong>{r.document_number}</strong></td>
                          <td>{r.kind || r.report_type}</td>
                          <td>{r.period_start ? `${r.period_start} .. ${r.period_end}` : "-"}</td>
                          <td><span className="badge badge-exact">{r.status}</span></td>
                          <td>
                            <button className="btn btn-sm" type="button" onClick={() => handlePreviewExisting(r.id)} style={{ marginRight: 6 }}>Preview</button>
                            <button className="btn btn-sm" type="button" onClick={() => handleDownloadPdf(r.id)} style={{ marginRight: 6 }}>PDF</button>
                            <button className="btn btn-sm" type="button" onClick={() => handleEmailReport(r.id)}>Email</button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "notifications" && (
          <>
            <div className="page-header">
              <div>
                <h1>Masked Email Router &amp; Reminders</h1>
                <p className="subtitle">Your login email is saved automatically and protected behind a private send/receive SubSphere mask.</p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" type="button" onClick={handleRunRemindersNow}>Check Due Reminders Now</button>
                <button className="btn btn-primary" type="button" onClick={handleSendTestEmail}>Send Email via Mask</button>
              </div>
            </div>

            <div className="card">
              <h2>Your Private Email Mask (Send &amp; Receive)</h2>
              <div className="kpi-grid" style={{ marginTop: 10 }}>
                <div>
                  <div className="kpi-label">Saved Login Email (Masked Display)</div>
                  <div style={{ fontWeight: 700, fontSize: "1.05rem" }}>
                    {maskedInbox?.masked_email || notifPrefs?.masked_email || prefEmail || "Saved on login"}
                  </div>
                </div>
                <div>
                  <div className="kpi-label">Active Relay Mask (Send &amp; Receive Address)</div>
                  <div>
                    <code>{maskedInbox?.relay_mask_email || notifPrefs?.relay_mask_email || "Generating mask..."}</code>
                  </div>
                </div>
              </div>
              <form onSubmit={handleReceiveViaMask} className="form-grid" style={{ marginTop: 14 }}>
                <div className="field">
                  <label>Receive Email From (External Provider)</label>
                  <input value={recvSender} onChange={(e) => setRecvSender(e.target.value)} placeholder="billing@claude.ai" />
                </div>
                <div className="field">
                  <label>Subject</label>
                  <input value={recvSubject} onChange={(e) => setRecvSubject(e.target.value)} placeholder="Monthly AI Receipt" />
                </div>
                <div style={{ display: "flex", alignItems: "flex-end", gap: 8 }}>
                  <button className="btn btn-primary" type="submit">Receive Sample Email to Mask</button>
                </div>
              </form>
              {selectedInboxMsg && (
                <div className="alert alert-info" style={{ marginTop: 12 }}>
                  <div>
                    <strong>{selectedInboxMsg.subject}</strong> ({selectedInboxMsg.direction === "received" ? "Received" : "Sent"})
                  </div>
                  <div className="kpi-meta">
                    From: <code>{selectedInboxMsg.masked_sender}</code> → To: <code>{selectedInboxMsg.masked_recipient}</code>
                  </div>
                  <pre style={{ whiteSpace: "pre-wrap", marginTop: 8, fontFamily: "inherit", fontSize: "0.9rem" }}>
                    {selectedInboxMsg.body}
                  </pre>
                </div>
              )}
              <div className="table-wrap" style={{ marginTop: 12 }}>
                <table>
                  <thead>
                    <tr><th>Direction</th><th>From</th><th>To (Mask)</th><th>Subject</th><th>Status</th><th>View</th></tr>
                  </thead>
                  <tbody>
                    {(maskedInbox?.messages || []).length === 0 ? (
                      <tr><td colSpan={6}>No masked emails sent or received yet. Click &ldquo;Send Email via Mask&rdquo; or &ldquo;Receive Sample Email to Mask&rdquo; above.</td></tr>
                    ) : (
                      (maskedInbox?.messages || []).map((m) => (
                        <tr key={m.id}>
                          <td><span className={`badge ${m.direction === "received" ? "badge-exact" : "badge-observed"}`}>{m.direction}</span></td>
                          <td><code>{m.masked_sender}</code></td>
                          <td><code>{m.masked_recipient}</code></td>
                          <td>{m.subject}</td>
                          <td>{m.status}</td>
                          <td>
                            <button className="btn btn-sm" type="button" onClick={() => setSelectedInboxMsg(m)}>
                              Open
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="card">
              <h2>SMTP Relay Health</h2>
              <p>
                Relay: <code>{emailStatus?.smtp_host || "mailpit"}:{emailStatus?.smtp_port || 1025}</code>{" "}
                <span className={`badge ${emailStatus?.reachable ? "badge-exact" : "badge-danger"}`}>
                  {emailStatus?.reachable ? "Reachable" : "Unreachable"}
                </span>
                {emailStatus?.error && <span className="kpi-meta"> · {emailStatus.error}</span>}
              </p>
              {testEmailOutcome && (
                <div className={`alert ${testEmailOutcome.ok ? "alert-info" : "alert-warn"}`}>
                  Email via mask <code>{testEmailOutcome.relay_mask_email || ""}</code> to <strong>{testEmailOutcome.masked_email || testEmailOutcome.recipient}</strong>: status=<strong>{testEmailOutcome.status}</strong>. {testEmailOutcome.actionable_message}
                </div>
              )}
            </div>

            <div className="card">
              <h2>Notification Preferences</h2>
              <form onSubmit={handleSaveNotifPrefs} className="form-grid">
                <div className="field">
                  <label>Email Reminders Enabled</label>
                  <select value={prefEnabled ? "yes" : "no"} onChange={(e) => setPrefEnabled(e.target.value === "yes")}>
                    <option value="yes">Enabled</option>
                    <option value="no">Paused / Disabled</option>
                  </select>
                </div>
                <div className="field">
                  <label>Recipient Email (account or configured email)</label>
                  <input type="email" value={prefEmail} onChange={(e) => setPrefEmail(e.target.value)} />
                </div>
                <div className="field">
                  <label>Default Reminder Offsets (days before)</label>
                  <input value={prefOffsets} onChange={(e) => setPrefOffsets(e.target.value)} />
                </div>
                <div className="field">
                  <label>Preferred Send Hour (0–23 local)</label>
                  <input type="number" min="0" max="23" value={prefHour} onChange={(e) => setPrefHour(e.target.value)} />
                </div>
                <div className="field">
                  <label>Timezone</label>
                  <input value={prefTz} onChange={(e) => setPrefTz(e.target.value)} placeholder="UTC or Asia/Karachi" />
                </div>
                <div className="field">
                  <label>Monthly Summary Report Opt-In</label>
                  <select value={prefMonthly ? "yes" : "no"} onChange={(e) => setPrefMonthly(e.target.value === "yes")}>
                    <option value="no">Off</option>
                    <option value="yes">On</option>
                  </select>
                </div>
                <div style={{ display: "flex", alignItems: "flex-end" }}>
                  <button className="btn btn-primary" type="submit">Save Preferences</button>
                </div>
              </form>
            </div>

            <div className="card">
              <h2>Notification Delivery History</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Recipient</th><th>Subject</th><th>Status</th><th>Attempts</th><th>Detail / Error</th><th>Updated</th></tr></thead>
                  <tbody>
                    {notifHistory.length === 0 ? (
                      <tr><td colSpan={6}>No notifications queued or sent yet.</td></tr>
                    ) : (
                      notifHistory.map((n) => (
                        <tr key={n.id}>
                          <td>{n.recipient}</td>
                          <td>{n.subject}</td>
                          <td><span className={`badge ${n.status === "accepted_by_relay" || n.status === "delivered" ? "badge-exact" : "badge-observed"}`}>{n.status}</span></td>
                          <td>{n.attempts}</td>
                          <td>{n.last_error || n.attempts_history?.[0]?.detail || "-"}</td>
                          <td>{String(n.updated_at).slice(0, 19)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "settings" && (
          <>
            <div className="page-header">
              <div>
                <h1>Privacy &amp; Data Controls</h1>
                <p className="subtitle">Personal data belongs strictly to your account and can be exported or purged at any time.</p>
              </div>
            </div>
            <div className="card">
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button className="btn" type="button" onClick={() => window.open("/api/v1/personal/export", "_blank")}>Export Personal JSON</button>
                <button className="btn btn-danger" type="button" onClick={handleDeleteHistory}>Delete Activity &amp; Report History</button>
              </div>
            </div>
          </>
        )}
      </main>

      {/* Add / Edit Subscription Dialog */}
      {showSubModal && (
        <div className="modal-backdrop" role="presentation" onClick={() => setShowSubModal(false)}>
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="sub-dialog-title" onClick={(e) => e.stopPropagation()}>
            <h2 id="sub-dialog-title">{editingSub ? `Edit ${editingSub.service_name}` : "Add Personal Subscription"}</h2>
            <form onSubmit={handleSaveSubscription}>
              <div className="form-grid">
                <div className="field"><label>Service Name</label><input value={svcName} onChange={(e) => setSvcName(e.target.value)} required /></div>
                <div className="field"><label>Service URL</label><input value={svcUrl} onChange={(e) => setSvcUrl(e.target.value)} placeholder="https://claude.ai" /></div>
                <div className="field"><label>Category</label><input value={category} onChange={(e) => setCategory(e.target.value)} /></div>
                <div className="field"><label>Plan Name</label><input value={planName} onChange={(e) => setPlanName(e.target.value)} /></div>
                <div className="field">
                  <label>Product Type</label>
                  <select value={productType} onChange={(e) => setProductType(e.target.value as any)}>
                    <option value="recurring_subscription">Recurring Subscription</option>
                    <option value="trial">Trial</option>
                    <option value="one_time_purchase">One-Time Purchase</option>
                  </select>
                </div>
                <div className="field"><label>Price (Major units)</label><input value={priceMajor} onChange={(e) => setPriceMajor(e.target.value)} required /></div>
                <div className="field"><label>Currency</label><input value={currency} maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></div>
                <div className="field">
                  <label>Billing Interval</label>
                  <select value={interval} onChange={(e) => setInterval(e.target.value as any)}>
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                    <option value="annual">Annual</option>
                    <option value="yearly">Yearly</option>
                    <option value="weekly">Weekly</option>
                    <option value="one_time">One-Time</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
                <div className="field"><label>Start Date</label><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></div>
                <div className="field"><label>Next Renewal Date</label><input type="date" value={renewalDate} onChange={(e) => setRenewalDate(e.target.value)} required /></div>
                <div className="field"><label>Timezone</label><input value={timezone} onChange={(e) => setTimezone(e.target.value)} /></div>
                <div className="field">
                  <label>Tracking Status</label>
                  <select value={trackingStatus} onChange={(e) => setTrackingStatus(e.target.value as any)}>
                    <option value="tracking_active">Active Tracking</option>
                    <option value="stopped_tracking">Stopped Tracking</option>
                    <option value="canceled_at_provider">Canceled at Provider</option>
                  </select>
                </div>
                <div className="field"><label>Reminder Offsets (days)</label><input value={offsetsInput} onChange={(e) => setOffsetsInput(e.target.value)} /></div>
                <div className="field"><label>Reminder Recipient Email</label><input type="email" value={recipientEmail} onChange={(e) => setRecipientEmail(e.target.value)} /></div>
              </div>
              <div className="field" style={{ marginTop: 10 }}>
                <label>Notes</label>
                <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
                <button className="btn" type="button" onClick={() => setShowSubModal(false)}>Cancel</button>
                <button className="btn btn-primary" type="submit">{editingSub ? "Save Changes" : "Save Subscription"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Calendar Event Drawer / Modal */}
      {selectedCalEvent && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSelectedCalEvent(null)}>
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-label="Calendar Event Details" onClick={(e) => e.stopPropagation()}>
            <h2>{selectedCalEvent.title}</h2>
            <p className="subtitle">Date: {selectedCalEvent.date} · Status: {selectedCalEvent.status}</p>
            <p>
              <strong>Amount:</strong> {formatMinorCurrency(selectedCalEvent.amount_minor, selectedCalEvent.currency)} ({selectedCalEvent.billing_interval})<br />
              <strong>Timezone:</strong> {selectedCalEvent.timezone || "UTC"}<br />
              <strong>Reminders:</strong> [{(selectedCalEvent.reminder_offsets_days || [7, 3, 1]).join(", ")} days before]
            </p>
            {selectedCalEvent.notes && <p className="kpi-meta">Notes: {selectedCalEvent.notes}</p>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              {selectedCalEvent.subscription_id && (
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() => {
                    const matched = subs.find((x) => x.id === selectedCalEvent.subscription_id);
                    setSelectedCalEvent(null);
                    if (matched) openEditModal(matched);
                  }}
                >
                  Edit Subscription / Reminders
                </button>
              )}
              <button className="btn" type="button" onClick={() => setSelectedCalEvent(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
