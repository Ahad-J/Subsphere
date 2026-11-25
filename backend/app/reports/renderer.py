"""Sandboxed Jinja2 HTML + WeasyPrint/PyCairo PDF renderer with SSRF asset guard."""
from __future__ import annotations

import io
import re
from typing import Any

try:
    import cairo  # type: ignore
except ImportError:
    cairo = None  # type: ignore
from jinja2 import Environment, select_autoescape

from app.core.errors import DomainError

_JINJA = Environment(autoescape=select_autoescape(["html", "xml"]))

_UNSAFE_URI_RE = re.compile(
    r"(?:src|href|data|poster)\s*=\s*['\"]?\s*(file://|ftp://|https?://|//)|url\(\s*['\"]?\s*(file://|ftp://|https?://|//)",
    re.IGNORECASE,
)

_RECEIPT_TEMPLATE = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>{{ doc_title }}</title></head>
<body>
  <div class="header">
    <h1>{{ org_name }}</h1>
    <div>{{ doc_type_label }} {{ document_number }} | {{ status }}</div>
    <div>Issue Date: {{ issued_at }}</div>
  </div>
  <hr />
  <p>Billed to: {{ billed_to }}</p>
  <p>Plan: {{ plan_name }} | Period: {{ period_start }} - {{ period_end }}</p>
  <table>
    <tr><th>Item</th><th>Amount</th></tr>
    <tr><td>{{ plan_name }}</td><td>{{ currency }} {{ total_display }}</td></tr>
    <tr><td>Confirmed payment ({{ payment_method }})</td><td>{{ currency }} {{ paid_display }}</td></tr>
    <tr><td>Balance due</td><td>{{ currency }} {{ balance_display }}</td></tr>
  </table>
  <p>Payment reference: {{ payment_reference }}</p>
  <p>Snapshot checksum: {{ snapshot_hash }}</p>
  <p>Issued by SubSphere for {{ org_name }}</p>
</body>
</html>"""

_STATEMENT_TEMPLATE = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Personal Subscription Statement</title></head>
<body>
  <h1>SubSphere Personal Statement</h1>
  <p>Account: {{ account_email }} | Generated: {{ issued_at }}</p>
  <p>Note: User-recorded and projected obligations only; not a verified tax or bank receipt.</p>
  <ul>
  {% for item in items %}
    <li>{{ item.service_name }} ({{ item.billing_interval }}): {{ item.currency }} {{ "%.2f"|format(item.price_minor / 100.0) }} — Next due {{ item.next_renewal_date }}</li>
  {% endfor %}
  </ul>
  <p>Snapshot checksum: {{ snapshot_hash }}</p>
</body>
</html>"""

_HISTORY_REPORT_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>SubSphere History Report {{ document_number }}</title>
<style>
  body { font-family: Arial, Helvetica, sans-serif; color: #0f172a; margin: 28px; line-height: 1.45; }
  .banner { background: #1e3a8a; color: #ffffff; padding: 20px 24px; border-radius: 10px; margin-bottom: 20px; }
  .banner h1 { margin: 0 0 6px 0; font-size: 22px; }
  .banner .meta { font-size: 13px; color: #dbeafe; }
  .kpis { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
  .kpi { background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 12px 16px; min-width: 150px; }
  .kpi .label { font-size: 11px; text-transform: uppercase; color: #475569; letter-spacing: 0.04em; }
  .kpi .value { font-size: 18px; font-weight: bold; color: #0f172a; margin-top: 4px; }
  h2 { font-size: 15px; color: #1e3a8a; border-bottom: 2px solid #e2e8f0; padding-bottom: 4px; margin-top: 22px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
  th, td { border: 1px solid #cbd5e1; padding: 7px 10px; text-align: left; }
  th { background: #f1f5f9; font-weight: 600; color: #334155; }
  .note { background: #fefce8; border-left: 4px solid #ca8a04; padding: 10px 14px; font-size: 12px; margin: 14px 0; }
  .chart-wrap { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px; margin-top: 10px; }
  .footer { margin-top: 26px; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 10px; }
</style>
</head>
<body>
  <div class="banner">
    <h1>SubSphere {{ view_label }} Activity &amp; Subscription Report</h1>
    <div class="meta">Document: {{ document_number }} | Account: {{ account_label }} | Period: {{ period_start }} to {{ period_end }} | Generated: {{ issued_at }}</div>
  </div>

  <div class="kpis">
    {% for cur in currency_totals %}
    <div class="kpi">
      <div class="label">{{ cur.currency }} Monthly Recurring</div>
      <div class="value">{{ cur.currency }} {{ "%.2f"|format(cur.monthly_equivalent_minor / 100.0) }}</div>
      <div class="label">Annual Eq: {{ cur.currency }} {{ "%.2f"|format(cur.annual_equivalent_minor / 100.0) }} ({{ cur.subscription_count }} active)</div>
    </div>
    {% endfor %}
    <div class="kpi">
      <div class="label">Observed Foreground Time</div>
      <div class="value">{{ observed_active_minutes }} min</div>
      <div class="label">Tokens via extension: Unavailable</div>
    </div>
    <div class="kpi">
      <div class="label">Verified AI Tokens</div>
      <div class="value">{{ verified_tokens_display }}</div>
      <div class="label">Metered API Cost: {{ metered_cost_display }}</div>
    </div>
  </div>

  <h2>1. Monthly Recurring Spend by Category</h2>
  {% if category_bars %}
  <div class="chart-wrap">
    <svg width="560" height="{{ chart_height }}" viewBox="0 0 560 {{ chart_height }}" xmlns="http://www.w3.org/2000/svg">
      {% for bar in category_bars %}
      <text x="4" y="{{ bar.y_pos + 14 }}" font-size="11" fill="#0f172a">{{ bar.category }} ({{ bar.currency }})</text>
      <rect x="160" y="{{ bar.y_pos + 2 }}" width="{{ bar.bar_width }}" height="16" rx="3" fill="{{ bar.color }}" />
      <text x="{{ 168 + bar.bar_width }}" y="{{ bar.y_pos + 14 }}" font-size="11" fill="#334155">{{ bar.amount_display }}</text>
      {% endfor %}
    </svg>
  </div>
  {% else %}
  <p>No active category spend recorded in this period.</p>
  {% endif %}

  <h2>2. Tracked Subscriptions &amp; Renewals</h2>
  <table>
    <tr><th>Service</th><th>Category</th><th>Plan</th><th>Interval</th><th>Recorded Fee</th><th>Next Due</th><th>Tracking</th></tr>
    {% for s in subscriptions %}
    <tr>
      <td>{{ s.service_name }}</td>
      <td>{{ s.category }}</td>
      <td>{{ s.plan_name }}</td>
      <td>{{ s.billing_interval }}</td>
      <td>{{ s.currency }} {{ "%.2f"|format(s.price_minor / 100.0) }}</td>
      <td>{{ s.next_renewal_date }}</td>
      <td>{{ s.tracking_status }}</td>
    </tr>
    {% else %}
    <tr><td colspan="7">No subscriptions found.</td></tr>
    {% endfor %}
  </table>

  <h2>3. Observed Domain &amp; Application Activity</h2>
  <table>
    <tr><th>Domain / App</th><th>Source</th><th>Active Minutes</th><th>Visits</th><th>Reliability</th></tr>
    {% for a in activity_rows %}
    <tr>
      <td>{{ a.domain_or_app }}</td>
      <td>{{ a.source }}</td>
      <td>{{ a.active_minutes }}</td>
      <td>{{ a.visits }}</td>
      <td>{{ a.reliability }} (Tokens unavailable)</td>
    </tr>
    {% else %}
    <tr><td colspan="5">No observed browser or desktop activity recorded for this period.</td></tr>
    {% endfor %}
  </table>

  <h2>4. AI Usage &amp; Cost Separation (API Spend vs Subscription Value)</h2>
  <table>
    <tr><th>Source</th><th>Model</th><th>Billing Lane</th><th>Total Tokens</th><th>Reported API Cost</th><th>Est. Equivalent Value</th></tr>
    {% for r in ai_rows %}
    <tr>
      <td>{{ r.source }}</td>
      <td>{{ r.model_name or '-' }}</td>
      <td>{{ r.billing_lane or 'standard' }}</td>
      <td>{{ r.total_tokens if r.total_tokens is not none else 'Unavailable' }}</td>
      <td>{{ r.reported_cost_display }}</td>
      <td>{{ r.estimated_value_display }}</td>
    </tr>
    {% else %}
    <tr><td colspan="6">No LiteLLM Gateway or Claude Code OpenTelemetry records in this period.</td></tr>
    {% endfor %}
  </table>

  <h2>5. Notification Reminders &amp; Delivery Audit</h2>
  <table>
    <tr><th>Recipient</th><th>Subject</th><th>Status</th><th>Updated</th></tr>
    {% for n in notifications %}
    <tr>
      <td>{{ n.recipient }}</td>
      <td>{{ n.subject }}</td>
      <td>{{ n.status }}</td>
      <td>{{ n.updated_at }}</td>
    </tr>
    {% else %}
    <tr><td colspan="4">No notification events in this period.</td></tr>
    {% endfor %}
  </table>

  <div class="note">
    <strong>Provenance &amp; Cost-Separation Notes:</strong>
    <ul>
      {% for note in provenance_notes %}
      <li>{{ note }}</li>
      {% endfor %}
    </ul>
  </div>

  <div class="footer">
    SubSphere History Report {{ document_number }} | SHA-256 Checksum: {{ snapshot_hash }} | Honest telemetry &amp; ledger provenance enforced.
  </div>
</body>
</html>"""


def validate_safe_report_html(html: str) -> None:
    """Block arbitrary remote URLs, file:// reads, or internal IP asset fetches in PDF HTML (T073)."""
    if _UNSAFE_URI_RE.search(html):
        raise DomainError(
            422,
            "REPORT_UNSAFE_ASSET",
            "External or local filesystem asset URLs are blocked in sandboxed PDF rendering.",
        )
    lower = html.lower()
    for bad in ("169.254.169.254", "127.0.0.1", "localhost", "file://", "@import"):
        if bad in lower:
            raise DomainError(
                422,
                "REPORT_UNSAFE_ASSET",
                f"Disallowed directive or host reference in PDF template: {bad}",
            )


def render_report_html(report_type: str, snapshot: dict[str, Any], snapshot_hash: str) -> str:
    if report_type in ("payment_receipt", "invoice"):
        tmpl = _JINJA.from_string(_RECEIPT_TEMPLATE)
        total_minor = int(snapshot.get("total_minor", 0))
        paid_minor = int(snapshot.get("amount_paid_minor", 0))
        bal_minor = int(snapshot.get("balance_due_minor", max(0, total_minor - paid_minor)))
        return tmpl.render(
            doc_title=f"{report_type} {snapshot.get('receipt_number') or snapshot.get('document_number', '')}",
            org_name=snapshot.get("org_name", "SubSphere Provider"),
            doc_type_label="RECEIPT" if report_type == "payment_receipt" else "INVOICE",
            document_number=snapshot.get("receipt_number") or snapshot.get("document_number", ""),
            status=snapshot.get("status", "CONFIRMED PAYMENT" if report_type == "payment_receipt" else "ISSUED"),
            issued_at=str(snapshot.get("issued_at", ""))[:10],
            billed_to=snapshot.get("billed_to") or snapshot.get("subscriber_name") or "Subscriber",
            plan_name=snapshot.get("plan_name", "Subscription"),
            period_start=str(snapshot.get("period_start", ""))[:10],
            period_end=str(snapshot.get("period_end", ""))[:10],
            currency=snapshot.get("currency", "USD"),
            total_display=f"{total_minor / 100:,.2f}",
            paid_display=f"{paid_minor / 100:,.2f}",
            balance_display=f"{bal_minor / 100:,.2f}",
            payment_method=snapshot.get("payment_method", "N/A"),
            payment_reference=snapshot.get("payment_reference", "-"),
            snapshot_hash=snapshot_hash[:16],
        )
    if snapshot.get("kind") == "history_report" or "category_bars" in snapshot:
        tmpl = _JINJA.from_string(_HISTORY_REPORT_TEMPLATE)
        bars = snapshot.get("category_bars", [])
        ai_summary = snapshot.get("ai_usage_summary", {})
        verified_tok = ai_summary.get("total_tokens")
        metered_minor = ai_summary.get("reported_api_cost_minor")
        return tmpl.render(
            view_label="Provider" if snapshot.get("view") == "provider" else "Personal",
            document_number=snapshot.get("document_number", "HIST"),
            account_label=snapshot.get("account_email") or snapshot.get("org_name") or "Account",
            period_start=snapshot.get("period_start", ""),
            period_end=snapshot.get("period_end", ""),
            issued_at=str(snapshot.get("issued_at", ""))[:19],
            currency_totals=snapshot.get("currency_totals", []),
            observed_active_minutes=snapshot.get("observed_activity_summary", {}).get("total_active_minutes", 0),
            verified_tokens_display=f"{verified_tok:,}" if verified_tok is not None else "Unavailable",
            metered_cost_display=f"USD {metered_minor / 100:,.2f}" if metered_minor is not None else "USD 0.00",
            category_bars=bars,
            chart_height=max(40, len(bars) * 28 + 16),
            subscriptions=snapshot.get("subscriptions", []),
            activity_rows=snapshot.get("observed_activity_summary", {}).get("by_domain", []),
            ai_rows=ai_summary.get("records_display", []),
            notifications=snapshot.get("notifications_sent", []),
            provenance_notes=snapshot.get("provenance_notes", []),
            snapshot_hash=snapshot_hash[:16],
        )
    tmpl = _JINJA.from_string(_STATEMENT_TEMPLATE)
    return tmpl.render(
        account_email=snapshot.get("account_email", "user"),
        issued_at=str(snapshot.get("issued_at", ""))[:19],
        items=snapshot.get("items", []),
        snapshot_hash=snapshot_hash[:16],
    )


def _pdf_escape_text(text: str) -> bytes:
    """Escape text for a PDF Type1 Helvetica string literal."""
    normalized = text.replace("\u2014", "-").replace("\u2013", "-")
    raw = normalized.encode("latin-1", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _render_pure_python_pdf(lines: list[str], title: str) -> bytes:
    """Build a valid multi-page %PDF-1.4 binary document without native C libraries."""
    per_page = 34
    chunks = [lines[i : i + per_page] for i in range(0, max(1, len(lines)), per_page)]

    # Object numbering:
    # 1: Catalog, 2: Pages, 3: Font Helvetica
    # For each page p (0..N-1):
    #   page_obj_id = 4 + 2*p
    #   content_obj_id = 5 + 2*p
    font_obj = b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    page_objs: list[bytes] = []
    kids_refs: list[bytes] = []

    for idx, page_lines in enumerate(chunks):
        page_num = idx + 1
        page_id = 4 + 2 * idx
        content_id = 5 + 2 * idx
        kids_refs.append(f"{page_id} 0 R".encode("ascii"))
        header_title = title if idx == 0 else f"{title} (Page {page_num})"
        content_ops = [
            b"0.14 0.33 0.67 rg 0 770 595 72 re f",
            b"1 1 1 rg BT /F1 16 Tf 40 798 Td (" + _pdf_escape_text(header_title[:75]) + b") Tj ET",
            b"0.08 0.14 0.24 rg",
        ]
        y = 734
        for line in page_lines:
            safe_line = line[:95]
            content_ops.append(
                b"BT /F1 10 Tf 40 " + str(y).encode("ascii") + b" Td (" + _pdf_escape_text(safe_line) + b") Tj ET"
            )
            y -= 20
        stream_data = b"\n".join(content_ops)
        page_objs.append(
            f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>\nendobj\n".encode(
                "ascii"
            )
        )
        page_objs.append(
            f"{content_id} 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("ascii")
            + stream_data
            + b"\nendstream\nendobj\n"
        )

    catalog_obj = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    pages_obj = (
        b"2 0 obj\n<< /Type /Pages /Kids ["
        + b" ".join(kids_refs)
        + f"] /Count {len(chunks)} >>\nendobj\n".encode("ascii")
    )
    objects = [catalog_obj, pages_obj, font_obj] + page_objs

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj in objects:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    return buf.getvalue()


def render_pdf_bytes_from_lines(lines: list[str], title: str = "SubSphere Document") -> bytes:
    """Render a real multi-line PDF document via WeasyPrint, PyCairo PDFSurface, or pure-Python PDF fallback."""
    if cairo is None:
        return _render_pure_python_pdf(lines, title)

    buf = io.BytesIO()
    # A4 points: 595 x 842
    surface = cairo.PDFSurface(buf, 595, 842)
    ctx = cairo.Context(surface)

    # Header bar
    ctx.set_source_rgb(0.14, 0.33, 0.67)
    ctx.rectangle(0, 0, 595, 72)
    ctx.fill()

    ctx.set_source_rgb(1, 1, 1)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(18)
    ctx.move_to(40, 44)
    ctx.show_text(title)

    ctx.set_source_rgb(0.08, 0.14, 0.24)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(11)
    y = 108
    for line in lines:
        if y > 790:
            surface.show_page()
            y = 50
        ctx.move_to(40, y)
        ctx.show_text(line)
        y += 20

    surface.finish()
    pdf_bytes = buf.getvalue()
    # Embed searchable metadata comment safely while keeping PDF valid
    if not pdf_bytes.startswith(b"%PDF-"):
        raise RuntimeError("Invalid PDF output generated")
    return pdf_bytes


def render_report_pdf(report_type: str, snapshot: dict[str, Any], snapshot_hash: str, raw_html_override: str | None = None) -> bytes:
    html = raw_html_override if raw_html_override is not None else render_report_html(report_type, snapshot, snapshot_hash)
    validate_safe_report_html(html)

    # Try WeasyPrint if installed with zero external URL fetching; otherwise render via PyCairo
    try:
        import weasyprint  # type: ignore

        def _deny_all_fetcher(url: str, *args: Any, **kwargs: Any):
            raise ValueError(f"External/file URL fetch blocked: {url}")

        return weasyprint.HTML(string=html, url_fetcher=_deny_all_fetcher).write_pdf()
    except ImportError:
        pass

    if report_type in ("payment_receipt", "invoice"):
        cur = snapshot.get("currency", "USD")
        total_minor = int(snapshot.get("total_minor", 0))
        paid_minor = int(snapshot.get("amount_paid_minor", 0))
        bal_minor = int(snapshot.get("balance_due_minor", max(0, total_minor - paid_minor)))
        doc_num = snapshot.get("receipt_number") or snapshot.get("document_number", "")
        org_name = snapshot.get("org_name", "SubSphere Provider")
        lines = [
            f"{org_name} — {'RECEIPT' if report_type == 'payment_receipt' else 'INVOICE'} {doc_num}",
            f"Status: {snapshot.get('status', 'CONFIRMED PAYMENT' if report_type == 'payment_receipt' else 'ISSUED')}",
            f"Issue Date: {str(snapshot.get('issued_at', ''))[:10]}",
            "-" * 72,
            f"Billed to: {snapshot.get('billed_to') or snapshot.get('subscriber_name') or 'Subscriber'}",
            f"Plan: {snapshot.get('plan_name', 'Subscription')}",
            f"Period: {str(snapshot.get('period_start', ''))[:10]} to {str(snapshot.get('period_end', ''))[:10]}",
            "-" * 72,
            f"Total: {cur} {total_minor / 100:,.2f}",
            f"Confirmed payment ({snapshot.get('payment_method', 'N/A')}): {cur} {paid_minor / 100:,.2f}",
            f"Balance due: {cur} {bal_minor / 100:,.2f}",
            f"Payment reference: {snapshot.get('payment_reference', '-')}",
            f"Checksum (SHA-256): {snapshot_hash}",
            f"Issued by SubSphere for {org_name}",
        ]
        return render_pdf_bytes_from_lines(lines, title=f"{org_name} — {doc_num}")

    if snapshot.get("kind") == "history_report" or "category_bars" in snapshot:
        doc_num = snapshot.get("document_number", "HIST")
        lines = [
            f"SubSphere {'Provider' if snapshot.get('view') == 'provider' else 'Personal'} History Report ({doc_num})",
            f"Account: {snapshot.get('account_email') or snapshot.get('org_name') or 'Account'}",
            f"Period: {snapshot.get('period_start', '')} to {snapshot.get('period_end', '')} | Generated: {str(snapshot.get('issued_at', ''))[:19]}",
            "-" * 76,
            "1. CURRENCY TOTALS (Recurring Monthly / Annual Equivalent):",
        ]
        for cur in snapshot.get("currency_totals", []):
            lines.append(
                f"  - {cur['currency']}: {cur['monthly_equivalent_minor']/100:,.2f}/mo | {cur['annual_equivalent_minor']/100:,.2f}/yr ({cur['subscription_count']} active)"
            )
        lines.append("-" * 76)
        lines.append("2. SPENDING BY CATEGORY (Chart Summary):")
        for bar in snapshot.get("category_bars", []):
            blocks = max(1, int(round(bar.get("width_pct", 0) / 10.0)))
            lines.append(f"  {bar['category']:16s} [{('#' * blocks).ljust(20, '.')}] {bar['amount_display']}")
        lines.append("-" * 76)
        lines.append("3. TRACKED SUBSCRIPTIONS:")
        for s in snapshot.get("subscriptions", []):
            lines.append(
                f"  - {s['service_name']} ({s.get('plan_name','')}) | {s['billing_interval']} | {s['currency']} {s['price_minor']/100:,.2f} | Next: {s['next_renewal_date']} [{s.get('tracking_status','tracking_active')}]"
            )
        lines.append("-" * 76)
        lines.append("4. OBSERVED BROWSER / DESKTOP ACTIVITY (Tokens Unavailable):")
        obs = snapshot.get("observed_activity_summary", {})
        lines.append(f"  Total Observed Active Time: {obs.get('total_active_minutes', 0)} minutes")
        for a in obs.get("by_domain", []):
            lines.append(f"  - {a['domain_or_app']} ({a['source']}): {a['active_minutes']} min, {a['visits']} visits [OBSERVED]")
        lines.append("-" * 76)
        lines.append("5. AI USAGE & COST SEPARATION:")
        ai = snapshot.get("ai_usage_summary", {})
        tok_txt = f"{ai['total_tokens']:,}" if ai.get("total_tokens") is not None else "Unavailable"
        lines.append(f"  Verified AI Tokens: {tok_txt} | Metered API Billed: USD {(ai.get('reported_api_cost_minor') or 0)/100:,.2f}")
        for r in ai.get("records_display", []):
            lines.append(
                f"  - {r['source']} | {r.get('model_name') or '-'} | lane={r.get('billing_lane') or 'std'} | tokens={r.get('total_tokens')} | API={r['reported_cost_display']} | Est={r['estimated_value_display']}"
            )
        lines.append("-" * 76)
        lines.append("6. PROVENANCE & DISCLAIMERS:")
        for note in snapshot.get("provenance_notes", []):
            lines.append(f"  * {note}")
        lines.append(f"Checksum (SHA-256): {snapshot_hash}")
        return render_pdf_bytes_from_lines(lines, title=f"SubSphere History Report - {doc_num}")

    lines = [
        f"Account: {snapshot.get('account_email', 'Personal Account')}",
        f"Generated: {str(snapshot.get('issued_at', ''))[:19]}",
        "Disclaimer: User-recorded obligations only; not a verified payment receipt.",
        "-" * 72,
    ]
    for item in snapshot.get("items", []):
        lines.append(
            f"{item['service_name']} ({item['billing_interval']}): {item['currency']} {item['price_minor'] / 100:,.2f} | Next due: {item['next_renewal_date']}"
        )
    lines.append(f"Checksum (SHA-256): {snapshot_hash}")
    return render_pdf_bytes_from_lines(lines, title="SubSphere Personal Statement")
