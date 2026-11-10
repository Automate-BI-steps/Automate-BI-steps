from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from src.agents.dashboard.dashboard_planner import DashboardPlan


def _line_chart_svg(df: Any) -> str:
    """Monthly Import vs Export line chart as inline SVG (no external library)."""
    import pandas as pd

    if df is None or df.empty or not {"date", "direction", "value"}.issubset(df.columns):
        return '<div class="chart-empty">No trade rows to chart.</div>'
    monthly = (
        df.assign(month=pd.to_datetime(df["date"]).dt.to_period("M").astype(str))
        .groupby(["month", "direction"])["value"].sum()
        .unstack(fill_value=0)
        .reindex(columns=["Import", "Export"], fill_value=0)
        .sort_index()
    )
    months = list(monthly.index)
    width, height, pad = 640, 240, 36
    top = max(float(monthly.values.max()), 1.0)
    n = len(months)

    def x_at(i: int) -> float:
        return pad + (i * (width - 2 * pad) / (n - 1) if n > 1 else (width - 2 * pad) / 2)

    def y_at(value: float) -> float:
        return height - pad - (value / top) * (height - 2 * pad)

    colors = {"Import": "#118DFF", "Export": "#E66C37"}
    lines, dots = [], []
    for direction in ("Import", "Export"):
        values = [float(monthly.iloc[i][direction]) for i in range(n)]
        coords = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        lines.append(f'<polyline fill="none" stroke="{colors[direction]}" stroke-width="2.5" points="{pts}"/>')
        for i, (x, y) in enumerate(coords):
            tip = html.escape(f"{direction} {months[i]}: {values[i]:,.0f}")
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colors[direction]}"><title>{tip}</title></circle>')
    labels = [
        f'<text x="{x_at(i):.1f}" y="{height - 12}" font-size="11" fill="#94a3b8" text-anchor="middle">{html.escape(m)}</text>'
        for i, m in enumerate(months)
    ]
    legend = (
        f'<text x="{width - 170}" y="16" font-size="12" fill="#118DFF">Import</text>'
        f'<text x="{width - 90}" y="16" font-size="12" fill="#E66C37">Export</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Imports and exports by month">'
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#334155"/>'
        + "".join(lines) + "".join(dots) + "".join(labels) + legend + "</svg>"
    )


def _direction_filter_html(df: Any) -> str:
    """Static filter panel: totals per direction (the values a slicer would select)."""
    if df is None or df.empty or not {"direction", "value"}.issubset(df.columns):
        return '<div class="chart-empty">No direction values.</div>'
    totals = df.groupby("direction")["value"].sum()
    rows = "".join(
        f'<div class="bar-row"><div class="bar-label">{html.escape(str(d))}</div>'
        f'<div class="bar-val" style="margin-left:0">{float(v):,.0f}</div></div>'
        for d, v in totals.items()
    )
    return f'<div>{rows}<div class="chart-note">Direction filter: Import and Export totals.</div></div>'


def _panel_block(title: str, body: str) -> str:
    return '<section class="build-panel"><h2>' + html.escape(title) + "</h2>" + body + "</section>"


def _plain_rule(text: str) -> str:
    """First line of a knowledge chunk, without markdown markers."""
    import re

    first = text.strip().splitlines()[0]
    return re.sub(r"[#*`]|^\s*-\s*", "", first).strip()[:160]


ASK_REVIEW_BLOCK = """
<section class="build-panel" id="ask-review">
  <h2>Ask the dashboard (LLM)</h2>
  <p class="chart-note">Questions are answered from this dashboard's KPIs, plan and findings only. Needs the local server: run <code>python dashboard_server.py</code> and open the local address 127.0.0.1 on port 8765.</p>
  <textarea id="question" rows="2" style="width:100%;background:#0f172a;color:#f8fafc;border:1px solid #334155;border-radius:6px;padding:8px" placeholder="e.g. Which country has the largest export surplus?"></textarea>
  <p><button onclick="askQuestion()" style="background:#38bdf8;border:0;border-radius:6px;padding:8px 14px;cursor:pointer">Ask</button></p>
  <div id="answer" style="white-space:pre-wrap;font-size:13px"></div>

  <h2 style="margin-top:24px">Review</h2>
  <p class="chart-note">Your comment is stored with the feedback log (generated/optimization_feedback.jsonl).</p>
  <textarea id="comment" rows="2" style="width:100%;background:#0f172a;color:#f8fafc;border:1px solid #334155;border-radius:6px;padding:8px" placeholder="What should change on this dashboard?"></textarea>
  <p>
    <button onclick="sendReview(true)" style="background:#34d399;border:0;border-radius:6px;padding:8px 14px;cursor:pointer">Accept</button>
    <button onclick="sendReview(false)" style="background:#f87171;border:0;border-radius:6px;padding:8px 14px;cursor:pointer">Reject</button>
  </p>
  <div id="review-status" class="chart-note"></div>
</section>
<script>
async function postJson(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
}
async function askQuestion() {
  const q = document.getElementById("question").value.trim();
  const out = document.getElementById("answer");
  if (!q) { out.textContent = "Type a question first."; return; }
  out.textContent = "Asking the LLM...";
  try {
    const r = await postJson("/api/ask", { question: q });
    out.textContent = r.answer || ("LLM not available: " + (r.error || "unknown error"));
  } catch (e) {
    out.textContent = "Open this page through dashboard_server.py (local address 127.0.0.1, port 8765) to ask questions.";
  }
}
async function sendReview(accepted) {
  const comment = document.getElementById("comment").value.trim();
  const status = document.getElementById("review-status");
  try {
    await postJson("/api/review", { accepted: accepted, comment: comment });
    status.textContent = accepted ? "Accepted, thank you." : "Rejected, the comment is saved.";
  } catch (e) {
    status.textContent = "Open this page through dashboard_server.py to save reviews.";
  }
}
</script>
"""

def _build_panel(plan: DashboardPlan, critic_report: Any | None) -> str:
    """Shows how the dashboard was built: mode, LLM, RAG, candidate scores, critic, optimizer."""
    meta = plan.metadata or {}
    esc = html.escape
    if plan.planning_mode == "deterministic":
        body = (
            '<p class="chart-note">Deterministic build: the LLM copilot, RAG retrieval, candidate scoring and '
            "optimizer were not run. Run <code>generate_clean.py</code> to see them.</p>"
        )
        return _panel_block("How this dashboard was built", body)

    parts = []
    provider = str(meta.get("llm_provider", "unknown"))
    mock_note = " (offline mock: canned answers, not a live model)" if provider == "mock" else ""
    parts.append(
        f"<p><strong>Planning mode:</strong> {esc(plan.planning_mode)} &bull; "
        f"<strong>LLM provider:</strong> {esc(provider)}{esc(mock_note)}</p>"
    )

    spec = meta.get("copilot_spec")
    if spec:
        questions = "".join(f"<li>{esc(q)}</li>" for q in spec.get("business_questions", []))
        unresolved = "".join(f"<li>{esc(q)}</li>" for q in spec.get("unresolved_questions", []))
        parts.append(
            "<h3>Copilot specification</h3>"
            f"<p><strong>Audience:</strong> {esc(spec.get('target_audience', ''))}</p>"
            f"<p><strong>Objective:</strong> {esc(spec.get('business_objective', ''))}</p>"
            f"<p><strong>KPIs:</strong> {esc(', '.join(spec.get('required_kpi_ids', [])) or 'none')}</p>"
            f"<ul>{questions}</ul>"
            + (f"<p><strong>Not in the KPI catalog:</strong></p><ul>{unresolved}</ul>" if unresolved else "")
        )

    rules = sorted({_plain_rule(v.evidence.domain_rule) for p in plan.pages for v in p.visuals if v.evidence and v.evidence.domain_rule.strip()})
    if rules:
        parts.append(
            "<h3>Knowledge (RAG)</h3><p>Domain guidance attached to the visuals, from resources/knowledge:</p><ul>"
            + "".join(f"<li>{esc(r)}</li>" for r in rules)
            + "</ul>"
        )
    else:
        parts.append('<h3>Knowledge (RAG)</h3><p class="chart-note">No knowledge chunk matched this request.</p>')

    cands = meta.get("all_candidates") or []
    evaluation = meta.get("evaluation_summary") or {}
    if cands:
        selected_id = evaluation.get("selected_candidate_id")
        rows = "".join(
            f"<tr><td>{esc(str(c.get('strategy_name', '')))}</td><td>{c.get('page_count', '')}</td>"
            f"<td>{c.get('visual_count', '')}</td><td>{float(c.get('total_score', 0)):.2f}</td>"
            f"<td>{'Selected' if c.get('candidate_id') == selected_id else 'Eligible'}</td></tr>"
            for c in cands
        )
        parts.append(
            "<h3>Candidate scoring</h3>"
            '<table class="data-table"><thead><tr><th>Strategy</th><th>Pages</th><th>Visuals</th>'
            "<th>Score /100</th><th>Verdict</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f'<p class="chart-note">{esc(str(evaluation.get("selection_rationale", "")))}</p>'
        )

    if critic_report is not None:
        errs = "".join(f"<li>{esc(e)}</li>" for e in getattr(critic_report, "errors", []))
        parts.append(
            f"<h3>Critic</h3><p>Status: <strong>{esc(str(critic_report.status))}</strong></p>"
            + (f"<ul>{errs}</ul>" if errs else "")
        )

    opt = meta.get("optimizer_report")
    if opt:
        parts.append(
            "<h3>Optimizer</h3>"
            f"<p>Baseline {float(opt.get('baseline_score', 0)):.2f} &rarr; optimized {float(opt.get('optimized_score', 0)):.2f} "
            f"(improved: {esc(str(opt.get('improved')))}, operations: {opt.get('operations_count', 0)})</p>"
        )
    return _panel_block("How this dashboard was built", "".join(parts))


def render_html_dashboard(
    plan: DashboardPlan,
    bundle: Any,
    kpi_values: dict[str, float],
    output_path: Path,
    critic_report: Any | None = None,
    run_id: str = "offline_run",
) -> Path:
    """Render a standalone, zero-dependency, offline HTML dashboard."""
    kpi_map = {k.id: k.name for k in bundle.kpis}

    # Prepare findings rows
    findings_rows = []
    for f in bundle.findings.get("findings", []):
        text = f.get("text") if isinstance(f, dict) else str(f)
        findings_rows.append(("Finding", text))
    for r in bundle.findings.get("recommendations", []):
        findings_rows.append(("Recommendation", str(r)))

    # Prepare quality rows
    quality_rows = [
        ("Upstream Quality Status", str(bundle.quality.get("status", "unknown"))),
        ("Rows Loaded", str(len(bundle.data))),
    ]
    for check, res in sorted((bundle.quality.get("checks") or {}).items()):
        quality_rows.append((f"Upstream check: {check}", str(res)))

    # Compute country and product aggregations from bundle.data
    df = bundle.data
    has_trade = {"country", "direction", "value"}.issubset(df.columns)
    has_prod = {"product", "direction", "value"}.issubset(df.columns)
    country_agg = df.groupby(["country", "direction"])["value"].sum().unstack(fill_value=0) if (not df.empty and has_trade) else None
    product_agg = df.groupby(["product", "direction"])["value"].sum().unstack(fill_value=0) if (not df.empty and has_prod) else None

    # Generic datasets: every chart is computed with pandas from the same measures as the PBIP.
    generic = not getattr(bundle, "trade_mode", True)
    if generic:
        from src.agents.dashboard.dashboard_generator import dax
        from src.agents.dashboard import generic_report as gr

        g_measures = dax(bundle.kpis, fact_table=(bundle.data_model or {}).get("fact_table", "FactTrade"))
        g_frame = gr.model_frame(bundle)
        g_names = {k.id: k.name for k in bundle.kpis}

    # HTML template with embedded styling and tab switching logic
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(plan.dashboard_title)}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --primary: #38bdf8;
      --accent: #818cf8;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #34d399;
      --warning: #fbbf24;
      --danger: #f87171;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; }}
    header {{ margin-bottom: 24px; border-bottom: 1px solid var(--card-border); padding-bottom: 16px; display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; }}
    h1 {{ font-size: 26px; font-weight: 700; color: #fff; margin-bottom: 6px; }}
    .subtitle {{ color: var(--text-muted); font-size: 14px; max-width: 800px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase; }}
    .badge-ai {{ background: rgba(129, 140, 248, 0.2); color: var(--accent); border: 1px solid var(--accent); }}
    .badge-det {{ background: rgba(56, 189, 248, 0.2); color: var(--primary); border: 1px solid var(--primary); }}
    .meta-box {{ text-align: right; font-size: 12px; color: var(--text-muted); }}
    
    .nav-tabs {{ display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid var(--card-border); padding-bottom: 8px; overflow-x: auto; }}
    .tab-btn {{ background: transparent; border: none; color: var(--text-muted); padding: 8px 16px; font-size: 14px; font-weight: 600; cursor: pointer; border-radius: 6px; transition: all 0.2s; }}
    .tab-btn:hover {{ background: rgba(255,255,255,0.05); color: #fff; }}
    .tab-btn.active {{ background: var(--card-bg); color: var(--primary); border: 1px solid var(--card-border); border-bottom-color: var(--primary); }}
    
    .page-content {{ display: none; }}
    .page-content.active {{ display: block; animation: fadeIn 0.2s ease-in; }}
    @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}

    .grid-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{ background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 18px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }}
    .card-title {{ font-size: 13px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; }}
    .card-value {{ font-size: 28px; font-weight: 700; color: #fff; }}
    .card-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 6px; }}

    .grid-visuals {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; margin-bottom: 24px; }}
    .visual-box {{ background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 20px; }}
    .visual-title {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
    
    table.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    table.data-table th {{ text-align: left; padding: 10px 12px; background: rgba(0,0,0,0.2); color: var(--text-muted); border-bottom: 1px solid var(--card-border); font-weight: 600; }}
    table.data-table td {{ padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
    table.data-table tr:hover td {{ background: rgba(255,255,255,0.02); }}

    .bar-row {{ display: flex; align-items: center; margin-bottom: 10px; font-size: 13px; }}
    .bar-label {{ width: 100px; color: var(--text-muted); }}
    .bar-track {{ flex: 1; background: rgba(255,255,255,0.05); height: 24px; border-radius: 4px; overflow: hidden; display: flex; }}
    .bar-seg-import {{ background: #118DFF; height: 100%; transition: width 0.3s; }}
    .bar-seg-export {{ background: #E66C37; height: 100%; transition: width 0.3s; }}
    .bar-val {{ width: 80px; text-align: right; font-weight: 600; margin-left: 10px; }}

    .critic-banner {{ background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 8px; padding: 14px; margin-bottom: 20px; font-size: 13px; }}
    .chart-empty, .chart-note {{ font-size: 12px; color: var(--text-muted); margin-top: 8px; }}
    .build-panel {{ margin-top: 28px; background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 20px; }}
    .build-panel h2 {{ font-size: 18px; margin-bottom: 8px; }}
    .build-panel h3 {{ font-size: 14px; margin: 16px 0 6px; color: var(--primary); }}
    .build-panel ul {{ margin-left: 20px; font-size: 13px; }}
    .build-panel p {{ font-size: 13px; }}
    .critic-status {{ font-weight: 700; color: var(--success); }}
  </style>
</head>
<body>

  <header>
    <div>
      <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 6px;">
        <h1>{html.escape(plan.dashboard_title)}</h1>
        <span class="badge { 'badge-ai' if 'ai' in plan.planning_mode else 'badge-det' }">{html.escape(plan.planning_mode)}</span>
      </div>
      <p class="subtitle">{html.escape(plan.business_objective)}</p>
      <p class="subtitle" style="margin-top: 4px; font-size: 12px;">Target Audience: <strong>{html.escape(plan.target_audience)}</strong></p>
    </div>
    <div class="meta-box">
      <div>Run ID: <code>{html.escape(run_id[:12])}</code></div>
      <div>Format: Microsoft PBIP + Offline HTML</div>
    </div>
  </header>
"""

    if critic_report:
        doc += f"""
  <div class="critic-banner">
    <div>Critic Audit: <span class="critic-status">{html.escape(critic_report.status)}</span> &bull; Deterministic checks passed: <strong>{critic_report.passed_deterministic}</strong></div>
    <div style="color: var(--text-muted); margin-top: 4px;">{html.escape('; '.join(critic_report.quality_feedback))}</div>
  </div>
"""

    # Navigation tabs
    doc += '  <nav class="nav-tabs">\n'
    for idx, page in enumerate(plan.pages):
        active = "active" if idx == 0 else ""
        doc += f'    <button class="tab-btn {active}" data-tab="{page.name}" onclick="switchTab(\'{page.name}\')">{html.escape(page.display_name)}</button>\n'
    doc += '  </nav>\n'

    # Render each page
    for idx, page in enumerate(plan.pages):
        active = "active" if idx == 0 else ""
        doc += f'  <div id="{page.name}" class="page-content {active}">\n'
        doc += f'    <p style="color: var(--text-muted); margin-bottom: 18px; font-size: 14px;">{html.escape(page.description)}</p>\n'

        # If page has cards, render card grid
        cards = [v for v in page.visuals if v.visual_type == "card"]
        other_visuals = [v for v in page.visuals if v.visual_type != "card"]

        if cards:
            doc += '    <div class="grid-cards">\n'
            for c in cards:
                val_formatted = "N/A"
                if c.kpi_reference and c.kpi_reference in kpi_values:
                    val = kpi_values[c.kpi_reference]
                    val_formatted = f"{val:,.0f}" if val.is_integer() else f"{val:,.2f}"
                if generic:
                    name = g_names.get(c.kpi_reference or "", c.title)
                    expr = g_measures.get(name)
                    val = kpi_values.get(c.kpi_reference) if c.kpi_reference in kpi_values else (
                        gr.evaluate(expr, g_frame, g_frame, g_measures) if expr else None)
                    if val is not None and expr:
                        val_formatted = gr.fmt(float(val), gr.format_string(name, expr))
                doc += f"""      <div class="card">
        <div class="card-title">{html.escape(c.title)}</div>
        <div class="card-value">{val_formatted}</div>
        <div class="card-sub">{html.escape(c.selection_reason)}</div>
      </div>\n"""
            doc += '    </div>\n'

        if other_visuals:
            doc += '    <div class="grid-visuals">\n'
            for v in other_visuals:
                doc += f"""      <div class="visual-box">
        <div class="visual-title">
          <span>{html.escape(v.title)}</span>
          <span style="font-size: 11px; color: var(--text-muted); text-transform: uppercase;">{html.escape(v.visual_type)}</span>
        </div>\n"""

                generic_body = gr.render_visual_html(v, bundle, g_frame, g_measures) if generic else None
                if generic_body is not None:
                    doc += "        " + generic_body + "\n"

                elif page.name == "BusinessInsights" or v.id == "insights":
                    doc += '        <table class="data-table"><thead><tr><th>Category</th><th>Validated Insight</th></tr></thead><tbody>\n'
                    for kind, txt in findings_rows:
                        doc += f"          <tr><td><strong>{html.escape(kind)}</strong></td><td>{html.escape(txt)}</td></tr>\n"
                    doc += "        </tbody></table>\n"

                elif page.name == "DataQualityTraceability" or v.id == "quality":
                    doc += '        <table class="data-table"><thead><tr><th>Audit Check</th><th>Result</th></tr></thead><tbody>\n'
                    for chk, res in quality_rows:
                        doc += f"          <tr><td>{html.escape(chk)}</td><td><code>{html.escape(res)}</code></td></tr>\n"
                    doc += "        </tbody></table>\n"

                elif page.name == "CountryAnalysis" and country_agg is not None:
                    doc += '        <div>\n'
                    raw_max = max(country_agg.sum(axis=1)) if not country_agg.empty else 1.0
                    max_val = float(raw_max) if raw_max > 0 else 1.0
                    for c_name, row in country_agg.iterrows():
                        imp = float(row.get("Import", 0.0))
                        exp = float(row.get("Export", 0.0))
                        tot = imp + exp
                        p_imp = min(100.0, max(0.0, (imp / max_val) * 100)) if max_val > 0 else 0.0
                        p_exp = min(100.0, max(0.0, (exp / max_val) * 100)) if max_val > 0 else 0.0
                        doc += f"""          <div class="bar-row">
            <div class="bar-label">{html.escape(str(c_name))}</div>
            <div class="bar-track">
              <div class="bar-seg-import" style="width: {p_imp:.1f}%;" title="Import: {imp:,.0f}"></div>
              <div class="bar-seg-export" style="width: {p_exp:.1f}%;" title="Export: {exp:,.0f}"></div>
            </div>
            <div class="bar-val">{tot:,.0f}</div>
          </div>\n"""
                    doc += '        </div>\n'

                elif page.name == "ProductAnalysis" and product_agg is not None:
                    doc += '        <div>\n'
                    raw_max = max(product_agg.sum(axis=1)) if not product_agg.empty else 1.0
                    max_val = float(raw_max) if raw_max > 0 else 1.0
                    for p_name, row in product_agg.iterrows():
                        imp = float(row.get("Import", 0.0))
                        exp = float(row.get("Export", 0.0))
                        tot = imp + exp
                        p_imp = min(100.0, max(0.0, (imp / max_val) * 100)) if max_val > 0 else 0.0
                        p_exp = min(100.0, max(0.0, (exp / max_val) * 100)) if max_val > 0 else 0.0
                        doc += f"""          <div class="bar-row">
            <div class="bar-label">{html.escape(str(p_name))}</div>
            <div class="bar-track">
              <div class="bar-seg-import" style="width: {p_imp:.1f}%;" title="Import: {imp:,.0f}"></div>
              <div class="bar-seg-export" style="width: {p_exp:.1f}%;" title="Export: {exp:,.0f}"></div>
            </div>
            <div class="bar-val">{tot:,.0f}</div>
          </div>\n"""
                    doc += '        </div>\n'

                elif v.visual_type == "lineChart":
                    doc += "        " + _line_chart_svg(bundle.data) + "\n"

                elif v.visual_type == "slicer":
                    doc += "        " + _direction_filter_html(bundle.data) + "\n"

                else:
                    doc += f"""        <div style="background: rgba(0,0,0,0.15); border-radius: 6px; padding: 16px; color: var(--text-muted); font-size: 13px;">
          <div>Interactive visual container: <strong>{html.escape(v.id)}</strong></div>
          <div style="margin-top: 6px;">Roles: <code>{html.escape(json.dumps(v.roles))}</code></div>
          <div style="margin-top: 6px; font-style: italic;">{html.escape(v.selection_reason)}</div>
        </div>\n"""

                doc += '      </div>\n'
            doc += '    </div>\n'

        doc += '  </div>\n'

    doc += ASK_REVIEW_BLOCK
    doc += _build_panel(plan, critic_report)

    doc += """
  <script>
    function switchTab(pageName) {
      document.querySelectorAll('.page-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      const target = document.getElementById(pageName);
      if (target) target.classList.add('active');
      const btn = document.querySelector(`.tab-btn[data-tab="${pageName}"]`);
      if (btn) btn.classList.add('active');
    }
  </script>
</body>
</html>
"""
    output_path.write_text(doc, encoding="utf-8")
    return output_path

