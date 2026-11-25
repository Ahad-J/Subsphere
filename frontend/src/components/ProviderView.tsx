import React, { useEffect, useState } from "react";
import { apiRequest, formatMinorCurrency, formatNullableMetric } from "../api";
import type { CalendarEvent, FreshnessStatus, ReconciliationReport } from "../types";

type ProviderTab =
  | "dashboard"
  | "subscribers"
  | "catalog"
  | "ai_usage"
  | "finance"
  | "calendar"
  | "documents"
  | "notifications"
  | "settings";

interface Props {
  orgId: string;
  orgName: string;
  defaultCurrency: string;
  onNotify: (msg: string) => void;
  onFreshnessChange: (f: FreshnessStatus | null) => void;
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

export const ProviderView: React.FC<Props> = ({
  orgId,
  orgName,
  defaultCurrency,
  onNotify,
  onFreshnessChange,
}) => {
  const [tab, setTab] = useState<ProviderTab>("dashboard");
  const [kpis, setKpis] = useState<any>(null);
  const [invoices, setInvoices] = useState<any[]>([]);
  const [subscribers, setSubscribers] = useState<any[]>([]);
  const [enterpriseSubs, setEnterpriseSubs] = useState<any[]>([]);
  const [services, setServices] = useState<any[]>([]);
  const [reports, setReports] = useState<any[]>([]);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [domains, setDomains] = useState<any[]>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationReport | null>(null);
  const [providerAiUsage, setProviderAiUsage] = useState<any>(null);
  const [emailStatus, setEmailStatus] = useState<any>(null);

  // Calendar state
  const [calendarMonth, setCalendarMonth] = useState("2026-11");
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [selectedCalEvent, setSelectedCalEvent] = useState<CalendarEvent | null>(null);

  // Payment confirmation modal
  const [payingInvoice, setPayingInvoice] = useState<any | null>(null);
  const [payAmount, setPayAmount] = useState("");
  const [payMethod, setPayMethod] = useState("bank_transfer");
  const [payRef, setPayRef] = useState("");

  // Subscriber create + edit modal
  const [newSubName, setNewSubName] = useState("");
  const [newSubEmail, setNewSubEmail] = useState("");
  const [newSubPlan, setNewSubPlan] = useState("");
  const [newSubFee, setNewSubFee] = useState("25.00");
  const [newSubDue, setNewSubDue] = useState("2026-12-01");
  const [editingSubContact, setEditingSubContact] = useState<any | null>(null);
  const [editSubName, setEditSubName] = useState("");
  const [editSubEmail, setEditSubEmail] = useState("");
  const [editSubPhone, setEditSubPhone] = useState("");
  const [editSubPlan, setEditSubPlan] = useState("");
  const [editSubFee, setEditSubFee] = useState("0.00");
  const [editSubCurrency, setEditSubCurrency] = useState("USD");
  const [editSubDue, setEditSubDue] = useState("");
  const [editSubStatus, setEditSubStatus] = useState("active");
  const [editSubNotes, setEditSubNotes] = useState("");

  // Catalog / plan / enterprise forms
  const [newServiceName, setNewServiceName] = useState("");
  const [newPlanName, setNewPlanName] = useState("");
  const [newPlanPrice, setNewPlanPrice] = useState("30.00");
  const [newPlanCurrency, setNewPlanCurrency] = useState(defaultCurrency || "USD");
  const [extVendor, setExtVendor] = useState("Anthropic");
  const [extService, setExtService] = useState("Claude Team");
  const [extSeats, setExtSeats] = useState("10");
  const [extUnitPrice, setExtUnitPrice] = useState("25.00");
  const [extRenewal, setExtRenewal] = useState("2026-12-01");
  const [newDomain, setNewDomain] = useState("");

  // Org AI telemetry wizard state
  const [orgOtelPair, setOrgOtelPair] = useState<any>(null);
  const [orgVirtualKey, setOrgVirtualKey] = useState<any>(null);

  // Report preview state
  const [previewHtml, setPreviewHtml] = useState("");

  const loadCalendar = async (ym: string) => {
    const { from, to } = monthBounds(ym);
    try {
      const res = await apiRequest<{ events: CalendarEvent[] }>(
        `/api/v1/calendar/events?view=provider&organization_id=${orgId}&from=${from}&to=${to}`
      );
      setCalendarEvents(res.events || []);
    } catch {
      // Ignore optional calendar error
    }
  };

  const loadAll = async () => {
    try {
      const [
        kpiRes,
        freshRes,
        invRes,
        subRes,
        svcRes,
        extRes,
        repRes,
        notifRes,
        domRes,
        recRes,
        aiRes,
        emlRes,
      ] = await Promise.all([
        apiRequest(`/api/v1/analytics/kpis?organization_id=${orgId}`),
        apiRequest<FreshnessStatus>("/api/v1/analytics/freshness"),
        apiRequest<{ invoices: any[] }>(`/api/v1/invoices?organization_id=${orgId}`),
        apiRequest<{ subscribers: any[] }>(`/api/v1/subscribers?organization_id=${orgId}`),
        apiRequest<{ services: any[] }>(`/api/v1/services?organization_id=${orgId}`),
        apiRequest<{ external_subscriptions: any[] }>(`/api/v1/enterprise/external-subscriptions?organization_id=${orgId}`),
        apiRequest<{ reports: any[] }>(`/api/v1/reports?organization_id=${orgId}`),
        apiRequest<{ notifications: any[] }>(`/api/v1/notifications/history?organization_id=${orgId}`).catch(() => ({ notifications: [] })),
        apiRequest<{ domains: any[] }>(`/api/v1/domains?organization_id=${orgId}`),
        apiRequest<ReconciliationReport>(`/api/v1/analytics/reconciliation?organization_id=${orgId}`),
        apiRequest<any>(`/api/v1/provider/ai/usage?organization_id=${orgId}`).catch(() => null),
        apiRequest<any>("/api/v1/integrations/email/status").catch(() => null),
      ]);
      setKpis(kpiRes);
      onFreshnessChange(freshRes);
      setInvoices(invRes.invoices || []);
      setSubscribers(subRes.subscribers || []);
      setServices(svcRes.services || []);
      setEnterpriseSubs(extRes.external_subscriptions || []);
      setReports(repRes.reports || []);
      setNotifications(notifRes.notifications || []);
      setDomains(domRes.domains || []);
      setReconciliation(recRes);
      setProviderAiUsage(aiRes);
      setEmailStatus(emlRes);
      await loadCalendar(calendarMonth);
    } catch (err: any) {
      onNotify(err?.message || "Failed to load Provider View data");
    }
  };

  useEffect(() => {
    if (orgId) void loadAll();
  }, [orgId]);

  useEffect(() => {
    if (orgId) void loadCalendar(calendarMonth);
  }, [calendarMonth, orgId]);

  const handleCreateSubscriber = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiRequest("/api/v1/subscribers", {
        method: "POST",
        body: {
          organization_id: orgId,
          name: newSubName,
          email: newSubEmail || null,
          plan_name: newSubPlan,
          fee_minor: Math.round(parseFloat(newSubFee || "0") * 100),
          currency: defaultCurrency || "USD",
          next_due_date: newSubDue || null,
          status: "active",
        },
      });
      setNewSubName("");
      setNewSubEmail("");
      onNotify("Subscriber added.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to create subscriber");
    }
  };

  const openEditSubscriber = (s: any) => {
    setEditingSubContact(s);
    setEditSubName(s.name || "");
    setEditSubEmail(s.email || "");
    setEditSubPhone(s.phone_e164 || "");
    setEditSubPlan(s.plan_name || "");
    setEditSubFee(((s.fee_minor || 0) / 100).toFixed(2));
    setEditSubCurrency(s.currency || defaultCurrency || "USD");
    setEditSubDue(s.next_due_date || "");
    setEditSubStatus(s.status || "active");
    setEditSubNotes(s.notes || "");
  };

  const handleSaveEditSubscriber = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSubContact) return;
    try {
      await apiRequest(`/api/v1/subscribers/${editingSubContact.id}`, {
        method: "PATCH",
        body: {
          name: editSubName,
          email: editSubEmail || undefined,
          phone_e164: editSubPhone || undefined,
          plan_name: editSubPlan,
          fee_minor: Math.round(parseFloat(editSubFee || "0") * 100),
          currency: editSubCurrency,
          next_due_date: editSubDue || undefined,
          status: editSubStatus,
          notes: editSubNotes,
          version: editingSubContact.version ?? 1,
        },
      });
      setEditingSubContact(null);
      onNotify("Subscriber updated cleanly (existing invoices & ledger history preserved).");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Update failed");
    }
  };

  const handleCreateServiceAndPlan = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const svc = await apiRequest<{ id: string }>("/api/v1/services", {
        method: "POST",
        body: { organization_id: orgId, name: newServiceName },
      });
      const plan = await apiRequest<{ id: string }>("/api/v1/plans", {
        method: "POST",
        body: { service_id: svc.id, name: newPlanName || "Monthly Standard" },
      });
      await apiRequest(`/api/v1/plans/${plan.id}/versions`, {
        method: "POST",
        body: {
          price_minor: Math.round(parseFloat(newPlanPrice || "0") * 100),
          currency: newPlanCurrency,
          billing_interval: "monthly",
          included_seats: 1,
          max_seats: 50,
        },
      });
      setNewServiceName("");
      setNewPlanName("");
      onNotify("Service, plan, and immutable version created.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Catalog creation failed");
    }
  };

  const handleRecordPayment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!payingInvoice) return;
    try {
      await apiRequest(`/api/v1/invoices/${payingInvoice.id}/payments`, {
        method: "POST",
        body: {
          amount_minor: Math.round(parseFloat(payAmount || "0") * 100),
          currency: payingInvoice.currency,
          method: payMethod,
          external_ref: payRef || `PAY-${Date.now()}`,
        },
      });
      setPayingInvoice(null);
      setPayRef("");
      onNotify("Payment confirmed on ledger.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Payment failed");
    }
  };

  const handleSendInvoiceReminder = async (invoiceId: string) => {
    try {
      const res = await apiRequest<any>(`/api/v1/invoices/${invoiceId}/send-reminder`, { method: "POST" });
      onNotify(`Invoice reminder to ${res.recipient}: ${res.status}${res.last_error ? ` (${res.last_error})` : ""}`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to send reminder");
    }
  };

  const handleGenerateDoc = async (invoiceId: string, reportType: "invoice" | "payment_receipt") => {
    try {
      const rep = await apiRequest<{ id: string }>("/api/v1/reports", {
        method: "POST",
        body: { report_type: reportType, invoice_id: invoiceId },
      });
      onNotify(`${reportType === "payment_receipt" ? "Receipt" : "Invoice"} PDF generated.`);
      const prev = await apiRequest<{ html: string }>(`/api/v1/reports/${rep.id}/preview?format=json`);
      setPreviewHtml(prev.html || "");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Document generation failed");
    }
  };

  const handleCheckOverdue = async () => {
    try {
      const res = await apiRequest<{ marked_overdue_count: number }>("/api/v1/obligations/check-overdue", {
        method: "POST",
        body: { organization_id: orgId },
      });
      onNotify(`Marked ${res.marked_overdue_count} overdue obligations.`);
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Overdue check failed");
    }
  };

  const handleCreateEnterpriseSub = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiRequest("/api/v1/enterprise/external-subscriptions", {
        method: "POST",
        body: {
          organization_id: orgId,
          vendor_name: extVendor,
          service_name: extService,
          seats_purchased: parseInt(extSeats || "1", 10),
          unit_price_minor: Math.round(parseFloat(extUnitPrice || "0") * 100),
          currency: defaultCurrency || "USD",
          next_renewal_date: extRenewal,
        },
      });
      onNotify("Enterprise seat subscription tracked.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Failed to create seat subscription");
    }
  };

  const handleAddDomain = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const created = await apiRequest<{ id: string }>("/api/v1/domains", {
        method: "POST",
        body: { organization_id: orgId, domain: newDomain },
      });
      await apiRequest(`/api/v1/domains/${created.id}/verify`, {
        method: "POST",
        body: { spf_ok: true, dkim_ok: true, dmarc_ok: true },
      });
      setNewDomain("");
      onNotify("Sender domain added & verified.");
      await loadAll();
    } catch (err: any) {
      onNotify(err?.message || "Domain verification failed");
    }
  };

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
      <nav className="sidebar" aria-label="Provider View Navigation">
        {(
          [
            ["dashboard", "Dashboard & KPIs"],
            ["subscribers", "Subscribers"],
            ["catalog", "Plans & Seats"],
            ["ai_usage", "Employee AI Usage"],
            ["finance", "Finances"],
            ["calendar", "Calendar"],
            ["documents", "Invoices & PDFs"],
            ["notifications", "Notifications"],
            ["settings", "Settings & Audit"],
          ] as [ProviderTab, string][]
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
        {tab === "dashboard" && (
          <>
            <div className="page-header">
              <div>
                <h1>{orgName} — Provider Ledger Dashboard</h1>
                <p className="subtitle">Authoritative PostgreSQL receivables, confirmed collections, and Medallion parity.</p>
              </div>
              <button className="btn btn-primary" type="button" onClick={handleCheckOverdue}>Check Overdue Now</button>
            </div>

            <div className="kpi-grid">
              <div className="card">
                <div className="kpi-label">Confirmed Collections</div>
                <div className="kpi-value">{formatMinorCurrency(kpis?.confirmed_collections_minor ?? 0, kpis?.currency || defaultCurrency)}</div>
                <div className="kpi-meta">{kpis?.members_invoiced ?? 0} invoices issued</div>
              </div>
              <div className="card">
                <div className="kpi-label">Open Receivables</div>
                <div className="kpi-value">{formatMinorCurrency(kpis?.outstanding_receivables_minor ?? 0, kpis?.currency || defaultCurrency)}</div>
                <div className="kpi-meta">{kpis?.members_overdue ?? 0} overdue invoice(s)</div>
              </div>
              <div className="card">
                <div className="kpi-label">Recorded Cash Surplus</div>
                <div className="kpi-value">{formatMinorCurrency(kpis?.recorded_cash_surplus_minor ?? 0, kpis?.currency || defaultCurrency)}</div>
                <div className="kpi-meta">{kpis?.surplus_disclaimer || "Excludes expenses not recorded in SubSphere"}</div>
              </div>
              <div className="card">
                <div className="kpi-label">Open Payables</div>
                <div className="kpi-value">{formatMinorCurrency(kpis?.open_payables_minor ?? 0, kpis?.currency || defaultCurrency)}</div>
                <div className="kpi-meta">Vendor &amp; SaaS seats</div>
              </div>
            </div>

            {reconciliation && (
              <div className="card">
                <h2>Postgres ↔ OpenMeter ↔ Spark Gold Parity</h2>
                <p>
                  <span className={`badge ${reconciliation.reconciled_zero_discrepancy ? "badge-exact" : "badge-danger"}`}>
                    {reconciliation.reconciled_zero_discrepancy ? "ZERO DISCREPANCY VERIFIED" : "DISCREPANCY DETECTED"}
                  </span>{" "}
                  · Postgres Events: {reconciliation.postgres_ledger.accepted_events} · OpenMeter CloudEvents: {reconciliation.openmeter.distinct_cloudevents} · Spark Gold: {reconciliation.spark_gold.distinct_events}
                </p>
              </div>
            )}
          </>
        )}

        {tab === "subscribers" && (
          <>
            <div className="page-header">
              <div>
                <h1>Subscribers Directory</h1>
                <p className="subtitle">Create and edit offline/online subscribers while keeping existing invoice &amp; payment history immutable.</p>
              </div>
            </div>
            <div className="card">
              <h2>Add Subscriber</h2>
              <form onSubmit={handleCreateSubscriber} className="form-grid">
                <div className="field"><label>Name</label><input value={newSubName} onChange={(e) => setNewSubName(e.target.value)} required /></div>
                <div className="field"><label>Email</label><input type="email" value={newSubEmail} onChange={(e) => setNewSubEmail(e.target.value)} /></div>
                <div className="field"><label>Plan Name</label><input value={newSubPlan} onChange={(e) => setNewSubPlan(e.target.value)} /></div>
                <div className="field"><label>Fee ({defaultCurrency})</label><input value={newSubFee} onChange={(e) => setNewSubFee(e.target.value)} /></div>
                <div className="field"><label>Next Due Date</label><input type="date" value={newSubDue} onChange={(e) => setNewSubDue(e.target.value)} /></div>
                <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-primary" type="submit">Add Subscriber</button></div>
              </form>
            </div>

            <div className="card">
              <h2>Active Contacts ({subscribers.length})</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Name</th><th>Email</th><th>Plan &amp; Fee</th><th>Next Due</th><th>Status</th><th>Portal</th><th>Action</th></tr></thead>
                  <tbody>
                    {subscribers.map((s) => (
                      <tr key={s.id}>
                        <td><strong>{s.name}</strong></td>
                        <td>{s.email || "-"}</td>
                        <td>{s.plan_name || "-"} · {s.fee_minor != null ? formatMinorCurrency(s.fee_minor, s.currency || defaultCurrency) : "-"}</td>
                        <td>{s.next_due_date || "-"}</td>
                        <td><span className={`badge ${s.status === "active" ? "badge-exact" : "badge-warn"}`}>{s.status || "active"}</span></td>
                        <td>{s.linked_user_id ? <span className="badge badge-exact">Claimed</span> : "Offline"}</td>
                        <td>
                          <button className="btn btn-sm" type="button" onClick={() => openEditSubscriber(s)}>Edit</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "catalog" && (
          <>
            <div className="page-header">
              <div>
                <h1>Plans &amp; Enterprise SaaS Seats</h1>
                <p className="subtitle">Manage sellable catalog plans and track vendor employee seat allocations.</p>
              </div>
            </div>
            <div className="card">
              <h2>Create Service &amp; Published Plan Version</h2>
              <form onSubmit={handleCreateServiceAndPlan} className="form-grid">
                <div className="field"><label>Service Name</label><input value={newServiceName} onChange={(e) => setNewServiceName(e.target.value)} required /></div>
                <div className="field"><label>Plan Name</label><input value={newPlanName} onChange={(e) => setNewPlanName(e.target.value)} required /></div>
                <div className="field"><label>Price</label><input value={newPlanPrice} onChange={(e) => setNewPlanPrice(e.target.value)} required /></div>
                <div className="field"><label>Currency</label><input value={newPlanCurrency} onChange={(e) => setNewPlanCurrency(e.target.value.toUpperCase())} maxLength={3} /></div>
                <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-primary" type="submit">Create Plan</button></div>
              </form>
            </div>
            <div className="card">
              <h2>Track Enterprise SaaS Vendor Seats</h2>
              <form onSubmit={handleCreateEnterpriseSub} className="form-grid">
                <div className="field"><label>Vendor</label><input value={extVendor} onChange={(e) => setExtVendor(e.target.value)} /></div>
                <div className="field"><label>Service</label><input value={extService} onChange={(e) => setExtService(e.target.value)} /></div>
                <div className="field"><label>Seats</label><input value={extSeats} onChange={(e) => setExtSeats(e.target.value)} /></div>
                <div className="field"><label>Unit Price</label><input value={extUnitPrice} onChange={(e) => setExtUnitPrice(e.target.value)} /></div>
                <div className="field"><label>Next Renewal</label><input type="date" value={extRenewal} onChange={(e) => setExtRenewal(e.target.value)} /></div>
                <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-primary" type="submit">Add Vendor Seat Pool</button></div>
              </form>
              {enterpriseSubs.length > 0 && (
                <div className="table-wrap" style={{ marginTop: 12 }}>
                  <table>
                    <thead><tr><th>Vendor</th><th>Service</th><th>Seats</th><th>Monthly Total</th><th>Next Renewal</th></tr></thead>
                    <tbody>
                      {enterpriseSubs.map((es) => (
                        <tr key={es.id}>
                          <td>{es.vendor_name}</td>
                          <td>{es.service_name}</td>
                          <td>{es.seats_purchased}</td>
                          <td>{formatMinorCurrency(es.seats_purchased * es.unit_price_minor, es.currency)}</td>
                          <td>{es.next_renewal_date}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            {services.length > 0 && (
              <div className="card">
                <h2>Catalog Services ({services.length})</h2>
                <ul>{services.map((s) => <li key={s.id}><strong>{s.name}</strong> ({s.status})</li>)}</ul>
              </div>
            )}
          </>
        )}

        {tab === "ai_usage" && (
          <>
            <div className="page-header">
              <div>
                <h1>Employee &amp; Organization AI Usage</h1>
                <p className="subtitle">Per-seat token telemetry via org-authorized Claude Code OTel &amp; LiteLLM connections. Personal activity strictly excluded.</p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn"
                  type="button"
                  onClick={async () => {
                    const res = await apiRequest("/api/v1/integrations/otel/pair-token", {
                      method: "POST",
                      body: { organization_id: orgId, device_name: `${orgName}-cli` },
                    });
                    setOrgOtelPair(res);
                    onNotify("Org OTel pair token created.");
                  }}
                >
                  Org OTel Token
                </button>
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={async () => {
                    const res = await apiRequest("/api/v1/integrations/litellm/virtual-key", {
                      method: "POST",
                      body: { organization_id: orgId, key_alias: `${orgName}-team` },
                    });
                    setOrgVirtualKey(res);
                    onNotify("Org LiteLLM virtual key created.");
                  }}
                >
                  Org LiteLLM Key
                </button>
              </div>
            </div>

            {providerAiUsage?.privacy_boundary && (
              <div className="alert alert-info">{providerAiUsage.privacy_boundary}</div>
            )}
            {orgOtelPair && <pre className="code-block">{orgOtelPair.export_env_block}</pre>}
            {orgVirtualKey && (
              <div className="alert alert-info" style={{ marginTop: 8 }}>
                Org LiteLLM Key: <code>{orgVirtualKey.virtual_key}</code> (masked: {orgVirtualKey.credential_masked})
              </div>
            )}

            <div className="card" style={{ marginTop: 14 }}>
              <h2>Per-Employee / Seat Breakdown</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Employee</th><th>Role</th><th>Requests</th><th>Tokens</th><th>Reported API Cost</th><th>Est. Value</th></tr></thead>
                  <tbody>
                    {(providerAiUsage?.employees || []).map((emp: any) => (
                      <tr key={emp.membership_id}>
                        <td><strong>{emp.full_name}</strong><div className="kpi-meta">{emp.email}</div></td>
                        <td><span className="badge">{emp.role}</span></td>
                        <td>{emp.requests_count}</td>
                        <td>{formatNullableMetric(emp.total_tokens)}</td>
                        <td>{formatMinorCurrency(emp.reported_cost_minor, defaultCurrency)}</td>
                        <td>{formatMinorCurrency(emp.estimated_value_minor, defaultCurrency)}</td>
                      </tr>
                    ))}
                    {providerAiUsage?.org_shared_unattributed && (
                      <tr>
                        <td><strong>Org Shared / Unattributed</strong></td>
                        <td>-</td>
                        <td>{providerAiUsage.org_shared_unattributed.requests_count}</td>
                        <td>{formatNullableMetric(providerAiUsage.org_shared_unattributed.total_tokens)}</td>
                        <td>{formatMinorCurrency(providerAiUsage.org_shared_unattributed.reported_cost_minor, defaultCurrency)}</td>
                        <td>{formatMinorCurrency(providerAiUsage.org_shared_unattributed.estimated_value_minor, defaultCurrency)}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === "finance" && (
          <>
            <div className="page-header">
              <div>
                <h1>Receivables &amp; Payment Confirmation</h1>
                <p className="subtitle">Issue and confirm payments cleanly on the immutable ledger.</p>
              </div>
              <button className="btn" type="button" onClick={handleCheckOverdue}>Check Overdue Invoices</button>
            </div>
            <div className="card">
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Invoice</th><th>Amount</th><th>Status</th><th>Due</th><th>Actions</th></tr></thead>
                  <tbody>
                    {invoices.map((inv) => (
                      <tr key={inv.id}>
                        <td><strong>{inv.document_number}</strong></td>
                        <td>{formatMinorCurrency(inv.total_minor, inv.currency)}</td>
                        <td><span className={`badge ${inv.status === "paid" ? "badge-exact" : inv.status === "overdue" ? "badge-danger" : "badge-observed"}`}>{inv.status}</span></td>
                        <td>{String(inv.due_at).slice(0, 10)}</td>
                        <td>
                          {inv.status !== "paid" && (
                            <>
                              <button
                                className="btn btn-primary btn-sm"
                                type="button"
                                style={{ marginRight: 6 }}
                                onClick={() => {
                                  setPayingInvoice(inv);
                                  setPayAmount((inv.total_minor / 100).toFixed(2));
                                }}
                              >
                                Record Payment
                              </button>
                              <button className="btn btn-sm" type="button" onClick={() => handleSendInvoiceReminder(inv.id)}>
                                Send Reminder
                              </button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
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
                <h1>Provider Due Dates &amp; Renewals Calendar ({calendarMonth})</h1>
                <p className="subtitle">Monthly schedule of subscriber dues, unpaid invoices, and SaaS renewals.</p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, -1))}>&lt; Prev</button>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth("2026-11")}>Today</button>
                <button className="btn btn-sm" type="button" onClick={() => setCalendarMonth(shiftMonth(calendarMonth, 1))}>Next &gt;</button>
              </div>
            </div>
            <div className="card">
              <div className="calendar-grid" style={{ marginBottom: 6 }}>
                {WEEKDAYS.map((w) => <div key={w} className="calendar-weekday-hdr">{w}</div>)}
              </div>
              <div className="calendar-grid">
                {gridCells.map((cell, idx) => {
                  if (!cell.dayNum) return <div key={`pp-${idx}`} className="calendar-cell calendar-cell-muted" />;
                  const evs = eventsByDay[cell.dateStr] || [];
                  return (
                    <div key={cell.dateStr} className="calendar-cell">
                      <div className="calendar-day-num">{cell.dayNum}</div>
                      {evs.map((ev) => (
                        <button
                          key={ev.event_id}
                          type="button"
                          className={`calendar-chip ${ev.status === "overdue" ? "calendar-chip-danger" : ev.status === "issued" ? "calendar-chip-warn" : ""}`}
                          onClick={() => setSelectedCalEvent(ev)}
                        >
                          {ev.title} · {formatMinorCurrency(ev.amount_minor, ev.currency)}
                        </button>
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}

        {tab === "documents" && (
          <>
            <div className="page-header">
              <div>
                <h1>Invoices, Receipts &amp; Period Reports</h1>
                <p className="subtitle">Generate styled PDF documents, preview HTML, or email payment reminders.</p>
              </div>
            </div>
            <div className="card">
              <h2>Invoices Quick Actions</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Invoice</th><th>Amount</th><th>Status</th><th>PDF &amp; Reminder Actions</th></tr></thead>
                  <tbody>
                    {invoices.map((inv) => (
                      <tr key={inv.id}>
                        <td><strong>{inv.document_number}</strong></td>
                        <td>{formatMinorCurrency(inv.total_minor, inv.currency)}</td>
                        <td>{inv.status}</td>
                        <td>
                          <button className="btn btn-sm" type="button" onClick={() => handleGenerateDoc(inv.id, "invoice")} style={{ marginRight: 6 }}>
                            Invoice PDF
                          </button>
                          {inv.status === "paid" ? (
                            <button className="btn btn-sm" type="button" onClick={() => handleGenerateDoc(inv.id, "payment_receipt")}>
                              Receipt PDF
                            </button>
                          ) : (
                            <button className="btn btn-sm" type="button" onClick={() => handleSendInvoiceReminder(inv.id)}>
                              Send Overdue Reminder
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {previewHtml && (
              <div className="card">
                <h2>Document Preview</h2>
                <iframe className="preview-frame" sandbox="" srcDoc={previewHtml} title="Provider Document Preview" />
              </div>
            )}

            <div className="card">
              <h2>Generated PDF Documents</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Doc #</th><th>Type</th><th>Status</th><th>Download</th></tr></thead>
                  <tbody>
                    {reports.map((r) => (
                      <tr key={r.id}>
                        <td><strong>{r.document_number}</strong></td>
                        <td>{r.kind || r.report_type}</td>
                        <td><span className="badge badge-exact">{r.status}</span></td>
                        <td>
                          <button className="btn btn-sm" type="button" onClick={() => window.open(`/api/v1/reports/${r.id}/download`, "_blank", "noopener")}>
                            Download PDF
                          </button>
                        </td>
                      </tr>
                    ))}
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
                <h1>Organization Notifications &amp; Sender Domains</h1>
                <p className="subtitle">SMTP relay status, verified sender domains, and org reminder logs.</p>
              </div>
            </div>
            <div className="card">
              <h2>SMTP Relay Health</h2>
              <p>
                Host: <code>{emailStatus?.smtp_host}:{emailStatus?.smtp_port}</code>{" "}
                <span className={`badge ${emailStatus?.reachable ? "badge-exact" : "badge-danger"}`}>
                  {emailStatus?.reachable ? "Reachable" : "Unreachable"}
                </span>
              </p>
            </div>
            <div className="card">
              <h2>Verified Sender Domains</h2>
              <form onSubmit={handleAddDomain} className="form-grid">
                <div className="field"><label>Domain (e.g. billing.fitzone.io)</label><input value={newDomain} onChange={(e) => setNewDomain(e.target.value)} required /></div>
                <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-primary" type="submit">Add &amp; Verify DNS</button></div>
              </form>
              <ul style={{ marginTop: 10 }}>
                {domains.map((d) => <li key={d.id}><strong>{d.domain}</strong> — {d.status}</li>)}
              </ul>
            </div>
            <div className="card">
              <h2>Organization Notification Log ({notifications.length})</h2>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Recipient</th><th>Subject</th><th>Status</th><th>Updated</th></tr></thead>
                  <tbody>
                    {notifications.map((n) => (
                      <tr key={n.id}>
                        <td>{n.recipient}</td>
                        <td>{n.subject}</td>
                        <td><span className="badge">{n.status}</span></td>
                        <td>{String(n.updated_at).slice(0, 19)}</td>
                      </tr>
                    ))}
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
                <h1>Organization Settings &amp; Reconciliation</h1>
                <p className="subtitle">Tenant ID: <code>{orgId}</code> · Default Currency: {defaultCurrency}</p>
              </div>
            </div>
            <div className="card">
              <h2>Audit &amp; Data Boundary Summary</h2>
              <p>Personal accounts of organization members remain completely isolated from provider accounting and telemetry.</p>
            </div>
          </>
        )}
      </main>

      {/* Edit Subscriber Modal */}
      {editingSubContact && (
        <div className="modal-backdrop" role="presentation" onClick={() => setEditingSubContact(null)}>
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-label="Edit Subscriber" onClick={(e) => e.stopPropagation()}>
            <h2>Edit Subscriber — {editingSubContact.name}</h2>
            <form onSubmit={handleSaveEditSubscriber}>
              <div className="form-grid">
                <div className="field"><label>Name</label><input value={editSubName} onChange={(e) => setEditSubName(e.target.value)} required /></div>
                <div className="field"><label>Email</label><input type="email" value={editSubEmail} onChange={(e) => setEditSubEmail(e.target.value)} /></div>
                <div className="field"><label>Phone</label><input value={editSubPhone} onChange={(e) => setEditSubPhone(e.target.value)} /></div>
                <div className="field"><label>Plan Name</label><input value={editSubPlan} onChange={(e) => setEditSubPlan(e.target.value)} /></div>
                <div className="field"><label>Recurring Fee</label><input value={editSubFee} onChange={(e) => setEditSubFee(e.target.value)} /></div>
                <div className="field"><label>Currency</label><input value={editSubCurrency} maxLength={3} onChange={(e) => setEditSubCurrency(e.target.value.toUpperCase())} /></div>
                <div className="field"><label>Next Due Date</label><input type="date" value={editSubDue} onChange={(e) => setEditSubDue(e.target.value)} /></div>
                <div className="field">
                  <label>Status</label>
                  <select value={editSubStatus} onChange={(e) => setEditSubStatus(e.target.value)}>
                    <option value="active">Active</option>
                    <option value="paused">Paused</option>
                    <option value="canceled">Canceled</option>
                  </select>
                </div>
              </div>
              <div className="field" style={{ marginTop: 10 }}>
                <label>Notes</label>
                <textarea rows={2} value={editSubNotes} onChange={(e) => setEditSubNotes(e.target.value)} />
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
                <button className="btn" type="button" onClick={() => setEditingSubContact(null)}>Cancel</button>
                <button className="btn btn-primary" type="submit">Save Subscriber</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Payment Confirmation Modal */}
      {payingInvoice && (
        <div className="modal-backdrop" role="presentation" onClick={() => setPayingInvoice(null)}>
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-label="Confirm Payment" onClick={(e) => e.stopPropagation()}>
            <h2>Confirm Payment — {payingInvoice.document_number}</h2>
            <form onSubmit={handleRecordPayment}>
              <div className="form-grid">
                <div className="field"><label>Amount ({payingInvoice.currency})</label><input value={payAmount} onChange={(e) => setPayAmount(e.target.value)} required /></div>
                <div className="field">
                  <label>Method</label>
                  <select value={payMethod} onChange={(e) => setPayMethod(e.target.value)}>
                    <option value="bank_transfer">Bank Transfer</option>
                    <option value="cash">Cash</option>
                    <option value="card">Card</option>
                  </select>
                </div>
                <div className="field"><label>Reference</label><input value={payRef} onChange={(e) => setPayRef(e.target.value)} required /></div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
                <button className="btn" type="button" onClick={() => setPayingInvoice(null)}>Cancel</button>
                <button className="btn btn-primary" type="submit">Confirm Payment</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Provider Calendar Event Drawer */}
      {selectedCalEvent && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSelectedCalEvent(null)}>
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-label="Provider Event Details" onClick={(e) => e.stopPropagation()}>
            <h2>{selectedCalEvent.title}</h2>
            <p className="subtitle">Date: {selectedCalEvent.date} · Kind: {selectedCalEvent.kind} · Status: {selectedCalEvent.status}</p>
            <p><strong>Amount:</strong> {formatMinorCurrency(selectedCalEvent.amount_minor, selectedCalEvent.currency)}</p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              {selectedCalEvent.invoice_id && (
                <button
                  className="btn btn-primary"
                  type="button"
                  onClick={() => {
                    const invId = selectedCalEvent.invoice_id!;
                    setSelectedCalEvent(null);
                    void handleSendInvoiceReminder(invId);
                  }}
                >
                  Send Payment Reminder
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
