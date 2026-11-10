"""Adaptateur de l'Agent 6 (Auditor / XAI) vers les VRAIS agents du pipeline.

Le code de Dhouha (`core.py`) ne lit que des CSV (le « contrat », voir README_auditor.md). Ici :

  - `build_contract(state, out_dir)` transforme ce que les agents ont laissé dans le state commun
    (profilage, nettoyage, ETL, KPI, dashboard) et dans leurs fichiers en ces CSV ;
  - `PipelineAuditor` adapte les quelques points de `core.Auditor` qui dépendaient du jeu simulé du
    notebook (recalcul des KPI, provenance, textes du journal).

Règle d'intégrité : l'Auditor n'invente rien. Ce que les agents n'enregistrent pas (confiance d'une
correction, lignes modifiées une à une, recommandations) reste VIDE ou est signalé dans les limites du
rapport ; rien n'est complété pour « faire joli ».
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .core import SYSTEM as CORE_SYSTEM
from .core import Auditor, split

CONTEXT_FILE = "pipeline_context.json"
UNTREATED_RULE = "R-NOTREATED-01"
READ_FIX_FALLBACK = "R-READFIX-01"


class AuditorInputError(RuntimeError):
    """Rien d'exploitable à auditer dans le state."""


# ------------------------------------------------------------------ petits utilitaires
def _short(value, n=140) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _raw(value, n=120) -> str:
    """Valeur avant/après : on garde les espaces (une correction d'espaces serait invisible sinon), les sauts de ligne sont écrits \\n."""
    text = "" if value is None else str(value).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return text if len(text) <= n else text[: n - 1] + "…"


def _rule_id(name) -> str:
    """cast_numeric -> R-CASTNUMERIC-01 (les ids de règles du contrat ne contiennent que des lettres)."""
    return "R-" + (re.sub(r"[^A-Z]", "", str(name).upper()) or "STEP") + "-01"


def _issue_id(raw, idx) -> str:
    m = re.search(r"(\d+)", str(raw))
    return f"P{int(m.group(1)):03d}" if m else f"P{idx:03d}"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _num(value, default=0):
    try:
        return default if value is None or (isinstance(value, float) and np.isnan(value)) else float(value)
    except (TypeError, ValueError):
        return default


def _status_word(raw) -> str:
    raw = str(raw or "").lower()
    if raw in ("done", "ok", "success", "verified"):
        return "success"
    if raw in ("partial_success", "warn", "waiting_user"):
        return "partial"
    if raw in ("error", "failed"):
        return "error"
    return "not_run"


# ------------------------------------------------------------------ le contrat CSV à partir du state
def build_contract(state: dict, out_dir) -> dict:
    """Écrit les CSV lus par `Auditor` dans `out_dir` + `pipeline_context.json`. Renvoie un petit résumé."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run, inp = state.get("run") or {}, state.get("input") or {}
    prof, cln, etl = state.get("profiling") or {}, state.get("cleaning") or {}, state.get("etl") or {}
    kpi, dash = state.get("kpi") or {}, state.get("dashboard") or {}
    if not (prof.get("issues") is not None and prof.get("tables")) and not cln.get("steps") and not kpi.get("kpi_results"):
        raise AuditorInputError("Aucune étape du pipeline n'a laissé de trace à auditer (profilage, nettoyage ou KPI).")

    run_id = str(run.get("run_id") or "run")
    created = str(run.get("created_at") or "")
    times: dict[str, list[str]] = {}
    for e in state.get("timeline") or []:
        times.setdefault(str(e.get("agent")), []).append(str(e.get("at") or ""))

    def t_first(*agents):
        return next((times[a][0] for a in agents if times.get(a)), created)

    def t_last(*agents):
        return next((times[a][-1] for a in agents if times.get(a)), created)

    warnings: list[str] = []
    limitations: list[str] = []

    # ---- Agent 1 : profilage ---------------------------------------------------------------
    pid, prof_rows = {}, []
    for i, it in enumerate(prof.get("issues") or [], 1):
        pid[it.get("issue_id")] = _issue_id(it.get("issue_id"), i)
        col = f"{it.get('table')}.{it['column']}" if it.get("column") else f"{it.get('table')} (whole file)"
        ex = (it.get("examples") or [""])[0]
        prof_rows.append(dict(issue_id=pid[it.get("issue_id")], column=col, issue_type=it.get("issue_type", ""),
                              affected_rows=int(_num(it.get("affected_rows"))), pct_rows=round(_num(it.get("affected_pct")), 2),
                              severity=it.get("severity", ""), example=_short(ex, 80),
                              recommended_action=_short(it.get("suggested_action_details") or it.get("suggested_action"), 160).rstrip("."),
                              detected_by=f"Data Profiler ({it.get('detected_by') or 'rule'})", detected_at=t_first("profiling")))
    pd.DataFrame(prof_rows, columns=["issue_id", "column", "issue_type", "affected_rows", "pct_rows", "severity", "example",
                                     "recommended_action", "detected_by", "detected_at"]).to_csv(out / "profiler_report.csv", index=False)
    issue_conf = {pid[it.get("issue_id")]: _num(it.get("confidence"), 1.0) for it in prof.get("issues") or []}

    # ---- Agent 2 : nettoyage (une trace par étape de l'agent, pas par ligne) ---------------------
    steps = cln.get("steps") or []
    planner = cln.get("planner")
    planner = str((planner or {}).get("mode") if isinstance(planner, dict) else planner or "rules")
    questions = {(q.get("table"), q.get("column"), q.get("tool")): q for q in cln.get("questions") or []}
    answers = cln.get("answers") or {}
    items = (cln.get("issue_resolution") or {}).get("items") or []
    step_issue = {s.get("step"): it.get("issue_id") for it in items for s in it.get("steps") or []}
    events: list[dict] = []
    rules: dict[str, dict] = {}

    def add_rule(rid, description, method):
        rules.setdefault(rid, dict(rule_id=rid, description=description, method=method, issues=set()))

    def add_event(*, table, column, scope, tool, rid, status, kind, before, after, reason, issue, needs, approval, approver,
                  step_no, values_changed, rows_before, rows_after, row_id, when):
        p = pid.get(issue, "")
        if p:
            rules[rid]["issues"].add(p)
        eid = f"C{len(events) + 1:04d}"
        events.append(dict(event_id=eid, row_id=row_id, declaration_id=table, column=column, before=_raw(before), after=_raw(after),
                           rule_id=rid, rule_description=rules[rid]["description"], method=rules[rid]["method"],
                           confidence=issue_conf.get(p, 1.0), reason=_short(reason, 300), profiler_issue_id=p, needs_human_validation=needs,
                           approval_status=approval, approver=approver, timestamp=when, table=table, step_no=step_no, tool=tool, kind=kind,
                           status=status, scope=scope, values_changed=values_changed, rows_before=rows_before, rows_after=rows_after))

    done_at = (steps[-1].get("timestamp") if steps else None) or t_last("cleaning")
    for st in steps:
        tool, status, kind = str(st.get("tool")), str(st.get("status")), str(st.get("kind"))
        rid = _rule_id(tool)
        add_rule(rid, st.get("label") or tool, f"{tool} (cleaning agent, planner: {planner})")
        q = questions.get((st.get("table"), st.get("column"), tool))
        ans = answers.get(q.get("id")) if q else None
        needs = bool(q) or status == "flagged"
        if q:
            approval, approver = ("approved", "user") if ans else ("pending", "")
        else:
            approval, approver = ("pending", "") if status == "flagged" else ("auto_approved", "system")
        ex = (st.get("examples") or [None])[0]
        n = int(_num(st.get("values_changed")))
        row_id = "-"
        if status == "kept":
            before, after = "(values as found)", "(kept as is by the user's decision)"
        elif kind == "flag" or status == "flagged":
            before, after = "(values as found)", "(flagged, left unchanged)"
        elif ex and "avant" in ex:
            before, after, row_id = ex.get("avant"), ex.get("apres"), str(ex.get("row", "-"))
        elif ex:
            col = st.get("column")
            before = ex.get(col, "")
            after = "; ".join(f"{k}={v}" for k, v in ex.items() if k != col and v is not None)
        else:
            before, after = ("(no example recorded)", f"({n} value(s) changed)") if n else ("(type as found)", "(converted, no value changed)")
        reason = str(st.get("reason") or "") + (f" Decision of the user: {ans}." if ans else "")
        add_event(table=st.get("table"), column=st.get("column") or "", scope="column", tool=tool, rid=rid, status=status, kind=kind,
                  before=before, after=after, reason=reason, issue=step_issue.get(st.get("step")), needs=needs, approval=approval,
                  approver=approver, step_no=st.get("step"), values_changed=n, rows_before=int(_num(st.get("rows_before"))),
                  rows_after=int(_num(st.get("rows_after"))), row_id=row_id, when=str(st.get("timestamp") or done_at))
    for it in items:                       # problèmes sans étape : corrigés à la lecture, ou restés à traiter
        if it.get("steps"):
            continue
        table, col = it.get("table"), it.get("column") or ""
        if it.get("still_present"):
            add_rule(UNTREATED_RULE, "No cleaning tool applied: the issue is still present", "none (left for a human decision)")
            add_event(table=table, column=col, scope="column" if col else "table", tool="none", rid=UNTREATED_RULE, status="open", kind="none",
                      before="(unchanged)", after="(not modified)", issue=it.get("issue_id"), needs=True, approval="pending", approver="",
                      reason=f"{it.get('description', '')} Status: {it.get('status_label', 'open')}.", step_no="", values_changed=0,
                      rows_before="", rows_after="", row_id="-", when=done_at)
        else:
            rid = _rule_id(it.get("suggested_action")) if it.get("suggested_action") else READ_FIX_FALLBACK
            add_rule(rid, it.get("status_label") or "Fixed when the file was read", "applied by the pipeline when the file is read")
            add_event(table=table, column=col, scope="column" if col else "table", tool=str(it.get("suggested_action") or ""), rid=rid,
                      status="read_fix", kind="fix", before="(issue as detected)", after=it.get("status_label") or "fixed at read",
                      issue=it.get("issue_id"), needs=False, approval="auto_approved", approver="system", reason=it.get("description", ""),
                      step_no="", values_changed=0, rows_before="", rows_after="", row_id="-", when=done_at)
    log_cols = ["event_id", "row_id", "declaration_id", "column", "before", "after", "rule_id", "rule_description", "method", "confidence",
                "reason", "profiler_issue_id", "needs_human_validation", "approval_status", "approver", "timestamp", "table", "step_no",
                "tool", "kind", "status", "scope", "values_changed", "rows_before", "rows_after"]
    pd.DataFrame(events, columns=log_cols).to_csv(out / "cleaning_log.csv", index=False)
    pd.DataFrame([dict(rule_id=r["rule_id"], description=r["description"], method=r["method"],
                       profiler_issue_id="|".join(sorted(r["issues"]))) for r in rules.values()],
                 columns=["rule_id", "description", "method", "profiler_issue_id"]).to_csv(out / "cleaning_rules.csv", index=False)

    # ---- Agent 4 : table à plat, KPI --------------------------------------------------------------
    mapping = kpi.get("semantic_mapping") or {}
    fact_path = kpi.get("fact_table_path")
    fact_cols: list[str] = []
    fact_rows = 0
    if fact_path and Path(fact_path).exists():
        head = pd.read_csv(fact_path, nrows=0)
        fact_cols = list(head.columns)
        fact_rows = int(pd.read_csv(fact_path, usecols=[0]).shape[0])
    else:
        warnings.append("Table à plat de l'Agent 4 introuvable : aucun recalcul indépendant des KPI n'est possible.")
    audit4 = _read_json((kpi.get("outputs") or {}).get("audit_log")) or []
    load_step = next((s for s in audit4 if isinstance(s, dict) and s.get("etape") == "charger_donnees"), {})
    tables4: dict = load_step.get("tables") or {}
    origine: dict = dict(load_step.get("origine") or {})
    catalog = _read_json((kpi.get("outputs") or {}).get("kpi_catalog")) or []
    results = kpi.get("kpi_results") or {}
    if not catalog:
        catalog = [dict(id=k, nom=v.get("nom", k), definition=v.get("definition", ""), formule=v.get("formule", ""), dax="",
                        unite=v.get("unite", ""), bo=v.get("bo", ""), pourquoi=v.get("pourquoi", ""),
                        colonnes_sources=v.get("colonnes_sources") or []) for k, v in results.items()]
    for k in catalog:                      # filet de sécurité si le journal de l'Agent 4 est absent
        for c, o in (k.get("origine") or {}).items():
            origine.setdefault(c, o)
    grain = max(tables4, key=lambda t: tables4[t]) if tables4 else ""

    # ---- Agent 3 + construction de la table à plat -----------------------------------------------
    etl_rows = [dict(step_id="E01", step_no=1, operation="build_fact", input_tables="|".join(tables4) or "cleaned tables",
                     output_table="Fact_Trade", rows_in=tables4.get(grain, fact_rows), rows_out=fact_rows,
                     columns_used="|".join(fact_cols),
                     description=(f"The Semantic & KPI agent joined the cleaned tables ({load_step.get('message', 'join of the cleaned tables')}) into one flat table "
                                  f"Fact_Trade of {fact_rows} rows and {len(fact_cols)} columns. Every KPI and the dashboard are computed from this table."),
                     timestamp=t_first("semantic_kpi", "kpi"))]
    if str(etl.get("status")) == "done":
        for i, (name, info) in enumerate(sorted((etl.get("tables") or {}).items()), 2):
            n = int(_num((info or {}).get("rows"))) if isinstance(info, dict) else 0
            cols = (info or {}).get("columns") if isinstance(info, dict) else None
            etl_rows.append(dict(step_id=f"E{i:02d}", step_no=i, operation="star_schema_not_consumed", input_tables="cleaned tables",
                                 output_table=name, rows_in=n, rows_out=n, columns_used="|".join(cols) if isinstance(cols, list) else "",
                                 description=(f"The ETL agent materialised the star-schema table {name} ({n} rows). The KPI and dashboard agents did not read it: "
                                              "they used the flat table of step E01."), timestamp=t_last("etl")))
        limitations.append("The star schema built by the ETL agent is listed for information only: the KPI and dashboard agents did not consume it, "
                           "so it is not part of the lineage of any figure.")
    pd.DataFrame(etl_rows, columns=["step_id", "step_no", "operation", "input_tables", "output_table", "rows_in", "rows_out", "columns_used",
                                    "description", "timestamp"]).to_csv(out / "etl_log.csv", index=False)

    # ---- lignée des colonnes de la table à plat -----------------------------------------------------
    def events_on(table, column):
        return [e for e in events if e["table"] == table and (e["column"] == column or e["scope"] == "table")]

    lineage = []
    for c in fact_cols:
        org = str(origine.get(c) or "")
        computed = org.lower().startswith("calcul")
        sources: list[tuple[str, str]] = []
        if computed:
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", org.split(":", 1)[-1]):
                o = str(origine.get(tok) or "")
                if o and not o.lower().startswith("calcul") and tok != c:
                    sources.append((o, tok))
        elif org:
            sources.append((org, c))
        evs = sorted({e["event_id"]: e for t, col in sources for e in events_on(t, col)}.values(), key=lambda e: e["event_id"])
        lineage.append(dict(fact_column=f"Fact_Trade[{c}]", source_table=org or "unknown", source_column=org.split(":", 1)[-1].strip() if computed else c,
                            raw_columns="|".join(f"{t}.{col}" for t, col in sources),
                            transformation=(f"computed by the Semantic & KPI agent: {org.split(':', 1)[-1].strip()}" if computed
                                            else (f"copied from table {org}" if org else "origin not recorded by the Semantic & KPI agent")),
                            etl_step="E01", cleaning_rules="|".join(sorted({e["rule_id"] for e in evs})),
                            cleaning_events="|".join(e["event_id"] for e in evs)))
    pd.DataFrame(lineage, columns=["fact_column", "source_table", "source_column", "raw_columns", "transformation", "etl_step",
                                   "cleaning_rules", "cleaning_events"]).to_csv(out / "column_lineage.csv", index=False)

    # ---- catalogue et valeurs de KPI ----------------------------------------------------------------
    kid_of, aid_of = {}, {}
    cat_rows, val_rows = [], []
    top_roles = TOP_ROLES
    fact_df = pd.read_csv(fact_path, low_memory=False) if fact_cols else pd.DataFrame()
    for i, k in enumerate(catalog, 1):
        kid, aid = f"K{i:02d}", k.get("id")
        kid_of[aid], aid_of[kid] = kid, aid
        res = results.get(aid) or {}
        extra = ""
        v = res.get("valeur")
        if isinstance(v, dict) and v.get("mois_couverts"):
            extra = " Months covered per year: " + ", ".join(f"{y} = {m}" for y, m in v["mois_couverts"].items()) + "."
        cat_rows.append(dict(kpi_id=kid, name=k.get("nom", aid), dax=k.get("dax") or k.get("formule", ""),
                             fact_columns="|".join(f"Fact_Trade[{c}]" for c in k.get("colonnes_sources") or []), unit=res.get("unite") or k.get("unite", ""),
                             description=_short(f"{k.get('definition', '')}. Formula: {k.get('formule', '')}.{extra} (Semantic & KPI id: {aid})", 400),
                             business_objective=_short(f"{k.get('bo', '')} {k.get('pourquoi', '')}".strip(), 200)))
        if not res or v is None:
            continue
        n_rows = int(_num(res.get("lignes_utilisees")))
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            val_rows.append(dict(kpi_id=kid, scope_type="all", scope_value="all", period="all", value=float(v), rows_used=n_rows))
        elif isinstance(v, dict) and "croissance_pct" in v:
            for y, g in (v.get("croissance_pct") or {}).items():
                val_rows.append(dict(kpi_id=kid, scope_type="all", scope_value="all", period=str(y), value=float(g), rows_used=n_rows))
            for y, ca in (v.get("ca_par_annee") or {}).items():
                val_rows.append(dict(kpi_id=kid, scope_type="year_total", scope_value="all", period=str(y), value=float(ca), rows_used=n_rows))
        elif isinstance(v, list) and aid in top_roles:
            col = mapping.get(top_roles[aid])
            for item in v:
                used = int((fact_df[col].astype(str) == str(item["nom"])).sum()) if col in fact_df else n_rows
                val_rows.append(dict(kpi_id=kid, scope_type=top_roles[aid], scope_value=str(item["nom"]), period="all",
                                     value=float(item["valeur"]), rows_used=used))
        elif isinstance(v, str):
            pass                                     # KPI descriptif (ex. meilleur employé) : rien à additionner ni à recalculer
        else:
            warnings.append(f"Valeur du KPI « {aid} » de forme inconnue : non transmise à l'Auditor.")
    pd.DataFrame(cat_rows, columns=["kpi_id", "name", "dax", "fact_columns", "unit", "description", "business_objective"]).to_csv(out / "kpi_catalog.csv", index=False)
    pd.DataFrame(val_rows, columns=["kpi_id", "scope_type", "scope_value", "period", "value", "rows_used"]).to_csv(out / "kpi_values.csv", index=False)

    # ---- constats (Agent 4 : pas d'agent BI Analyst dans le pipeline) --------------------------------------
    ins_rows = []
    for i, a in enumerate(kpi.get("kpi_analysis") or [], 1):
        ins_rows.append(dict(insight_id=f"I{i:02d}", kpi_id=kid_of.get(a.get("kpi_id"), ""), text=a.get("constat", ""),
                             method="rule-based finding written by the Semantic & KPI agent", flow="", years="", filter_expr="", group_by="",
                             rows_used="", metric_name="", metric_value="", threshold="", rule_text=a.get("regle", ""),
                             rule_triggered=a.get("niveau") == "attention", recommendation="", created_at=t_last("semantic_kpi", "kpi"),
                             level=a.get("niveau", "info")))
    pd.DataFrame(ins_rows, columns=["insight_id", "kpi_id", "text", "method", "flow", "years", "filter_expr", "group_by", "rows_used", "metric_name",
                                    "metric_value", "threshold", "rule_text", "rule_triggered", "recommendation", "created_at", "level"]
                 ).to_csv(out / "analyst_insights.csv", index=False)

    # ---- Agent 5 : visuels réellement générés -----------------------------------------------------------
    vis_rows = []
    out_dir5 = Path(dash.get("output_dir") or "")
    plan = _read_json(out_dir5 / "dashboard_plan.json") if dash.get("output_dir") else None
    report_dirs = sorted(out_dir5.glob("*.Report")) if dash.get("output_dir") and out_dir5.exists() else []
    for page in (plan or {}).get("pages") or []:
        for v in page.get("visuals") or []:
            vf = (report_dirs[0] / "definition" / "pages" / page.get("name", "") / "visuals" / v.get("id", "") / "visual.json") if report_dirs else None
            if report_dirs and not vf.exists():
                continue                                    # planifié mais pas généré dans le projet Power BI
            vtype = ((_read_json(vf) or {}).get("visual") or {}).get("visualType") if vf else None
            ref = kid_of.get(v.get("kpi_reference"), "")
            vis_rows.append(dict(visual_id=f"V{len(vis_rows) + 1:02d}", page=page.get("display_name", page.get("name", "")), visual_type=vtype or v.get("visual_type", ""),
                                 title=v.get("title", ""), kpi_ids=ref, dimension="none", insight_id="", chosen_because=v.get("selection_reason", ""),
                                 generated_at=t_last("dashboard")))
    pd.DataFrame(vis_rows, columns=["visual_id", "page", "visual_type", "title", "kpi_ids", "dimension", "insight_id", "chosen_because",
                                    "generated_at"]).to_csv(out / "dashboard_visuals.csv", index=False)
    skipped = dash.get("kpis_skipped") or []
    if skipped:
        limitations.append("KPIs defined by the Semantic & KPI agent but not shown on the dashboard: "
                           + "; ".join(f"{s.get('kpi_id')} ({s.get('reason')})" for s in skipped) + ".")
    if dash and str(dash.get("status")) == "PARTIAL_SUCCESS":
        limitations.append("The Power BI project was generated by Python and never opened in Power BI Desktop (refresh, DAX evaluation and rendering not executed).")
    v = (results.get("yoy_growth") or {}).get("valeur")
    if isinstance(v, dict) and v.get("mois_couverts") and len(set(v["mois_couverts"].values())) > 1:
        limitations.append("Years in the data cover different numbers of months (see the growth KPI); growth rates compare the same months only.")

    # ---- journal d'exécution (orchestrateur) ---------------------------------------------------------------
    errs: dict[str, str] = {}
    for e in state.get("errors") or []:
        errs.setdefault(str(e.get("agent")), str(e.get("error")))
    n_q, n_ans = len(questions), sum(1 for q in questions.values() if answers.get(q.get("id")))
    n_pending = sum(1 for e in events if e["needs_human_validation"] and e["approval_status"] == "pending")
    attempts = etl.get("fact_reasoning_attempts")
    spec = [
        ("Data Profiler", "profiling", prof.get("tables") and "done", ("profiling",), 0, "no", "", "profiling_report.json"),
        ("Quality & Cleaning", "cleaning", cln.get("status") if cln.get("steps") else None, ("cleaning", "verification"), 0, "yes" if (n_q or n_pending) else "no",
         (f"{n_ans} of {n_q} question(s) answered; " if n_q else "") + f"{n_pending} item(s) pending", "cleaned tables; comparison before/after"),
        ("ETL / Data Engineering", "etl", etl.get("status"), ("etl",), max(0, int(attempts) - 1) if isinstance(attempts, int) else 0, "no", "",
         "star-schema tables (not consumed downstream)" if str(etl.get("status")) == "done" else ""),
        ("Semantic & KPI", "semantic_kpi", kpi.get("status"), ("semantic_kpi", "kpi"), 0, "no", "", "fact_trade.csv; kpi_catalog.json; kpi_results.json"),
        ("Dashboard Generator", "dashboard", dash.get("status"), ("dashboard",), 0, "no", "", "dashboard.html; Power BI project"),
    ]
    orch = []
    for no, (label, key, raw_status, ag, retries, hv, hv_status, outputs) in enumerate(spec, 1):
        status = _status_word(raw_status)
        err = errs.get(key) or errs.get("kpi" if key == "semantic_kpi" else key, "")
        if key == "etl" and status == "error" and not err:
            err = _short(etl.get("fact_reasoning_error") or (etl.get("validation") or {}).get("errors") or "", 160)
        orch.append(dict(run_id=run_id, step_no=no, agent=label, status=status, started_at=t_first(*ag), ended_at=t_last(*ag), retries=retries,
                         error=_short(err, 200) if status == "error" else "", human_validation_required=hv, human_validation_status=hv_status, outputs=outputs))
    pd.DataFrame(orch).to_csv(out / "orchestrator_runs.csv", index=False)

    # ---- fichiers sources -------------------------------------------------------------------------------------
    files = list(inp.get("uploads") or [])
    if not files and inp.get("source_dir") and Path(inp["source_dir"]).exists():
        files = sorted(p.name for p in Path(inp["source_dir"]).iterdir() if p.is_file())
    pd.DataFrame({"source_file": files}).to_csv(out / "raw_trade_data.csv", index=False)

    # ---- limites + contexte pour les surcharges --------------------------------------------------------------------
    limitations[:0] = [
        "The cleaning agent logs corrections per step (table, column, tool, number of values changed), not per row: the Auditor can say which columns "
        "were touched and how, not which individual rows were modified.",
        f"Independent recomputation starts from the flat table Fact_Trade written by the Semantic & KPI agent: it verifies the aggregation of each published "
        f"figure, not the join itself, which is only checked by comparing its row count with the cleaned table {grain or 'of finest grain'}.",
        "The confidence shown for a cleaning step is the profiler's detection confidence for the linked issue (1.0 when no issue is linked), not a confidence of the cleaning tool.",
        "No BI Analyst agent exists in the pipeline: insights are the rule-based findings of the Semantic & KPI agent and carry no recommendation.",
        "Explanations are written in English from the recorded evidence; names and reasons recorded by the agents stay in their original language.",
    ]
    (out / CONTEXT_FILE).write_text(json.dumps(dict(
        run_id=run_id, kpi_id_map=aid_of, mapping=mapping, fact_table_path=str(fact_path or ""), clean_data_dir=str(cln.get("clean_data_dir") or ""),
        grain_table=grain, limitations=limitations), ensure_ascii=False, indent=1), encoding="utf-8")
    return dict(run_id=run_id, events=len(events), rules=len(rules), kpis=len(cat_rows), kpi_values=len(val_rows), insights=len(ins_rows),
                visuals=len(vis_rows), pending_review=n_pending, warnings=warnings, limitations=limitations)


# ------------------------------------------------------------------ l'Auditor pour les vrais agents
# KPI de classement -> rôle de l'Agent 4 qui découpe le chiffre d'affaires.
TOP_ROLES = {"top_countries": "country", "top_products": "product", "top_customers": "customer",
             "top_categories": "category", "top_employees": "employee"}


def _flow_col(raw: pd.Series) -> pd.Series:
    f = raw.astype(str).str.lower().str.strip()
    return pd.Series(np.select([f.str.startswith("exp"), f.str.startswith("imp")], ["export", "import"], default="autre"), index=raw.index)


def _r2(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 2)


class PipelineAuditor(Auditor):
    """`core.Auditor` + ce qui dépend des vrais agents : textes du journal par étape, recalcul des KPI de l'Agent 4, provenance au niveau colonne/étape."""

    SYSTEM = (CORE_SYSTEM.replace("for import/export trade data", "for business data (orders, sales or trade)")
              .replace("a customs compliance manager", "a business manager or auditor"))
    INSIGHT_AGENT = "Semantic & KPI"
    DIM_COLS: dict = {}
    QUARANTINE_RULES: set = set()
    LINEAGE_COLS = Auditor.LINEAGE_COLS + ["cleaning_events"]
    GLOSSARY_EXTRA = [
        ("G-OUTLIER", "Outliers (extreme values)", "An outlier is a value far outside the usual range of its column, found with statistical limits such as the interquartile-range fences. An outlier can be a real "
         "event or an entry error, so the decision (empty it, cap it at the limit, or keep it) belongs to the user. Values that are kept stay in every total."),
        ("G-MISSING-VALUES", "Missing values", "A missing value is an empty cell. The cleaning agent flags missing values and leaves them empty instead of inventing a replacement, because an imputed value would change totals and averages without being a real observation."),
        ("G-TYPE-CAST", "Type conversion", "Type conversion turns text into numbers, dates or booleans so that columns can be summed, compared or filtered. A conversion that changes no value only changes how the column is stored."),
        ("G-WHITESPACE", "Whitespace and invisible characters", "Extra spaces, line breaks or invisible characters make identical labels look different, which splits groups and breaks joins. Trimming them is a safe correction that changes only the formatting of the text."),
        ("G-ENCODING", "File encoding", "A text file saved as ISO-8859-1 but read as UTF-8 shows replacement characters instead of accents. Re-reading the file with the right encoding repairs the text without changing any value."),
        ("G-FLAT-TABLE", "Flat analysis table", "A flat table joins the cleaned tables so that every row carries the order line, the customer, the product and their attributes. Its grain is the finest table, so a join that only looks up attributes keeps the row count of that table; a different count means rows were lost or duplicated."),
        ("G-REVENUE", "Revenue and order count", "Revenue is the sum of the line amounts, here unit price times quantity times one minus the discount. The order count is the number of distinct order identifiers, and the average order value is revenue divided by the order count."),
        ("G-LEAD-TIME", "Lead time and on-time rate", "The lead time is the number of days between the order date and the shipping date, counted once per order. The on-time rate is the share of shipped orders whose shipping date is on or before the required date."),
        ("G-TOP-N", "Top-N ranking", "A top-N ranking lists the categories with the largest totals, with the share of each in the overall total. It describes concentration but says nothing about causes."),
        ("G-SAME-MONTHS", "Year-over-year growth on the same months", "Comparing a full year with a partial year gives a false growth. The growth rate is therefore computed only on the months present in both years, and the number of months covered by each year is reported."),
        ("G-RECONCILIATION", "Independent recomputation", "The Auditor recomputes each published figure with its own code from the flat table and compares the result with the published value. A match proves that the aggregation was done as defined, not that the data or the definition are right."),
    ]

    # ---- chargement -------------------------------------------------------------------------------------
    def load(self) -> "PipelineAuditor":
        self.ctx = _read_json(self.out / CONTEXT_FILE) or {}
        super().load()
        self.kid2aid = self.ctx.get("kpi_id_map") or {}
        self.LIMITATIONS = self.ctx.get("limitations") or []
        self._df = None
        for col in ("values_changed",):
            if col in self.clog:
                self.clog[col] = pd.to_numeric(self.clog[col], errors="coerce").fillna(0)
        self.PROF_SEVERITY = self.prof.set_index("issue_id")["severity"].to_dict()
        return self

    # ---- textes du journal -------------------------------------------------------------------------------
    def correction_subject(self, r) -> str:
        return f"{r.table}.{r.column}" if r.column else str(r.table)

    def correction_summary(self, r) -> str:
        who = f" by {r.approver}" if r.approver else ""
        where = f"column {r.column} of table {r.table}" if r.column else f"table {r.table}"
        step = f"Cleaning step {r.step_no}" if str(r.step_no) not in ("", "nan") else "Cleaning event"
        tail = (f" Human validation required: {'yes' if r.needs_human_validation else 'no'}; status {r.approval_status}{who}. "
                f"Linked profiler issue: {r.profiler_issue_id or 'none'}.")
        if r.status == "open":
            return (f"{step} on {where}: no cleaning tool was applied to this issue ({r.rule_id}); values are unchanged and the issue is still present. "
                    f"Reason: {r.reason}" + tail)
        if r.status == "read_fix":
            return f"{step} on {where}: {r.rule_description} ({r.rule_id}). Reason: {r.reason}" + tail
        if r.status == "flagged":
            return (f"{step} on {where}: tool {r.tool} ({r.rule_id}) flagged the issue and changed no value. Reason: {r.reason}" + tail)
        if r.status == "kept":
            return (f"{step} on {where}: tool {r.tool} ({r.rule_id}) was proposed and the values were kept as found. Reason: {r.reason}" + tail)
        return (f"{step} on {where}: tool {r.tool} ({r.rule_id}) was applied; {int(r.values_changed)} value(s) changed, rows {r.rows_before} to {r.rows_after}. "
                f"Example: '{r.before}' became '{r.after}'. Reason: {r.reason}" + tail)

    def correction_question(self, e) -> str:
        where = f"{e['table']}.{e['column']}" if e["column"] else str(e["table"])
        return f"What happened to {where} in cleaning event {e['event_id']} ({e['rule_description']}) and why?"

    def insight_summary(self, r) -> str:
        return (f"Finding {r.insight_id} (KPI {r.kpi_id or 'none'}): {r.text} Rule: {r.rule_text} Level: {r.level}. "
                f"Method: {r.method}. No recommendation is recorded.")

    def value_label(self, r) -> str:
        return str(r.period) if r.scope_type == "all" else f"{r.scope_value} [{r.scope_type}] ({r.period})"

    def review_priority(self, queue):
        sev = self.PROF_SEVERITY
        return np.array(["low" if a == "approved" else ("high" if sev.get(p) == "high" else "medium")
                         for a, p in zip(queue.approval_status, queue.profiler_issue_id)])

    def fmt_kpi(self, kid: str, v: float, scope_type: str = "all") -> str:
        unit = self.kpi_cat.loc[self.kpi_cat.kpi_id == kid, "unit"].iloc[0] if (self.kpi_cat.kpi_id == kid).any() else ""
        txt = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
        if unit == "%":
            return f"{txt}%"
        return f"{txt} {unit}".strip() if unit and unit != "valeur" else txt

    # ---- lignée et provenance au niveau colonne / étape ---------------------------------------------------------
    def lineage_for(self, kpi_id, dimension="none"):
        ln = super().lineage_for(kpi_id, dimension)
        cl = self.collin[self.collin.fact_column.isin(split(ln["fact_columns"]))]
        ev_ids = sorted({e for s in cl.cleaning_events for e in split(s)})
        sub = self.clog[self.clog.event_id.isin(ev_ids)]
        ln["cleaning_rules"] = "|".join(sorted(set(sub.rule_id)))
        ln["profiler_issues"] = "|".join(sorted({i for s in sub.profiler_issue_id for i in split(s)}))
        ln["cleaning_events"] = "|".join(ev_ids)
        return ln

    def _provenance(self, label, kpi_id) -> str:
        if not kpi_id or not (self.kpi_cat.kpi_id == kpi_id).any():
            return f"Provenance of {label}: no KPI is linked to it, so no source column can be traced."
        ln = self.lineage_for(kpi_id)
        sub = self.clog[self.clog.event_id.isin(split(ln["cleaning_events"]))]
        vals = self._all_rows(kpi_id)
        rows = f"{int(vals.rows_used.max())} rows" if len(vals) else "an unknown number of rows"
        n_changed = int((sub.values_changed > 0).sum())
        n_flag = int((sub.status == "flagged").sum())
        n_open = int((sub.status == "open").sum())
        n_pend = int(((sub.approval_status == "pending") & sub.needs_human_validation).sum())
        return (f"Provenance of {label}: the figure is computed from {rows} of the flat table Fact_Trade, reading the source columns {ln['raw_columns'] or 'not recorded'}. "
                f"{len(sub)} cleaning event(s) are recorded on those columns: {n_changed} changed values, {n_flag} flagged an issue, {n_open} issue(s) were left untreated, "
                f"and {n_pend} still wait for a human decision. Profiler issues linked to those columns: {ln['profiler_issues'] or 'none'}. "
                "The cleaning agent logs corrections per step, not per row, so which individual rows were modified cannot be told.")

    # ---- preuves du glossaire : choisies par type d'événement plutôt que par recherche (le texte des agents est en français) --
    GLOSSARY_BY_ISSUE = {"outlier": "G-OUTLIER", "missing_values": "G-MISSING-VALUES", "encoding_error": "G-ENCODING"}
    GLOSSARY_BY_TOOL = {"cap_outliers": "G-OUTLIER", "flag_missing": "G-MISSING-VALUES", "cast_numeric": "G-TYPE-CAST", "cast_boolean": "G-TYPE-CAST",
                        "parse_dates": "G-TYPE-CAST", "trim_whitespace": "G-WHITESPACE", "reencode": "G-ENCODING", "clean_invisible_chars": "G-WHITESPACE"}

    def _glossary_item(self, gid):
        row = self.glossary_df[self.glossary_df.id == gid]
        return [dict(id=gid, text=row.text.iloc[0])] if len(row) else []

    def explain_correction(self, event_id):
        e = self.clog[self.clog.event_id == event_id].iloc[0]
        evid = [self.E(event_id)] + [self.E(i) for i in (e["profiler_issue_id"], e["rule_id"], self._cleaning_step_id()) if i and i in self.TRAIL]
        issue_type = self.prof.set_index("issue_id")["issue_type"].to_dict().get(e["profiler_issue_id"], "")
        gid = self.GLOSSARY_BY_ISSUE.get(issue_type) or self.GLOSSARY_BY_TOOL.get(str(e["tool"]))
        return self.correction_question(e), evid + (self._glossary_item(gid) if gid else [])

    def explain_insight(self, insight_id):
        q, evid = super().explain_insight(insight_id)
        evid = [x for x in evid if not x["id"].startswith("G-")]
        kid = self.ins.loc[self.ins.insight_id == insight_id, "kpi_id"].iloc[0]
        if kid and (self.kpi_cat.kpi_id == kid).any():
            row = self.kpi_cat[self.kpi_cat.kpi_id == kid].iloc[0]
            evid += self.glossary_evidence(row["name"] + " " + row["description"], 1)
        return q, evid

    def provenance_kpi(self, kpi_id, last_period) -> str:
        return self._provenance(kpi_id, kpi_id)

    def provenance_insight(self, i) -> str:
        return self._provenance(f"insight {i.insight_id}", i.kpi_id)

    # ---- recalcul indépendant ----------------------------------------------------------------------------------------
    def _frame(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        path, m = self.ctx.get("fact_table_path"), self.ctx.get("mapping") or {}
        self._df = pd.DataFrame()
        if not path or not Path(path).exists() or m.get("value") is None:
            return self._df
        raw = pd.read_csv(path, low_memory=False)
        d = pd.DataFrame(index=raw.index)
        d["value"] = pd.to_numeric(raw[m["value"]], errors="coerce")
        for role in ("order_id", "country", "product", "customer", "category", "employee", "shipper"):
            if m.get(role) in raw:
                d[role] = raw[m[role]]
        for role in ("quantity", "unit_price", "freight"):
            if m.get(role) in raw:
                d[role] = pd.to_numeric(raw[m[role]], errors="coerce")
        for role in ("date", "shipped_date", "required_date"):
            if m.get(role) in raw:
                s = raw[m[role]]
                if pd.api.types.is_numeric_dtype(s) and s.dropna().between(1900, 2100).all():
                    s = s.astype("Int64").astype(str) + "-01-01"          # colonne = année seule (même règle documentée que l'Agent 4)
                d[role] = pd.to_datetime(s, errors="coerce")
        if m.get("flow") in raw:
            d["flow"] = _flow_col(raw[m["flow"]])
        self._df = d
        return d

    def recompute(self, kpi_id, scope_type, scope_value, period):
        """Recalcul pandas de chaque KPI publié, d'après sa définition (valeurs publiées arrondies à 2 décimales) ; None = non recalculable."""
        d, aid = self._frame(), self.kid2aid.get(kpi_id)
        if d.empty or not aid:
            return None
        try:
            return self._recompute(d, aid, scope_type, scope_value, period)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            return None

    @staticmethod
    def _recompute(d, aid, scope_type, scope_value, period):
        if aid == "total_revenue":
            return _r2(d.value.sum())
        if aid == "order_count":
            return float(d.order_id.nunique())
        if aid == "avg_order_value":
            n = d.order_id.nunique()
            return _r2(d.value.sum() / n) if n else None
        if aid == "yoy_growth":
            dd = d.dropna(subset=["date"])
            by_month = dd.groupby([dd.date.dt.year, dd.date.dt.month]).value.sum()
            y = int(period)
            if scope_type == "year_total":
                return _r2(by_month.loc[y].sum())
            common = by_month.loc[y].index.intersection(by_month.loc[y - 1].index)
            before = by_month.loc[y - 1][common].sum()
            return _r2((by_month.loc[y][common].sum() / before - 1) * 100) if len(common) and before else None
        if aid in TOP_ROLES:
            col = TOP_ROLES[aid]
            return _r2(d.loc[d[col].astype(str) == str(scope_value), "value"].sum())
        orders = d.drop_duplicates("order_id") if "order_id" in d else None
        if aid == "customer_count":
            return float(d.customer.nunique())
        if aid == "revenue_per_customer":
            return _r2(d.value.sum() / d.customer.nunique())
        if aid == "product_count":
            return float(d["product"].nunique())
        if aid == "units_sold":
            return _r2(d.quantity.sum())
        if aid == "units_per_order":
            return _r2(d.quantity.sum() / d.order_id.nunique())
        if aid in ("gross_revenue", "discount_amount", "discount_rate"):
            gross = float((d.unit_price * d.quantity).sum())
            return {"gross_revenue": _r2(gross), "discount_amount": _r2(gross - d.value.sum()),
                    "discount_rate": _r2((gross - d.value.sum()) / gross * 100) if gross else None}[aid]
        if aid in ("total_freight", "avg_freight", "freight_ratio"):
            freight = float(orders.freight.sum())
            return {"total_freight": _r2(freight), "avg_freight": _r2(freight / len(orders)),
                    "freight_ratio": _r2(freight / d.value.sum() * 100)}[aid]
        if aid == "shipped_orders":
            return float(orders.shipped_date.notna().sum())
        if aid == "unshipped_orders":
            return float(orders.shipped_date.isna().sum())
        if aid == "late_orders":
            return float((orders.shipped_date > orders.required_date).sum())
        if aid == "avg_lead_time":
            o = d.drop_duplicates("order_id")
            return _r2((o.shipped_date - o.date).dt.days.mean())
        if aid == "on_time_rate":
            o = d.drop_duplicates("order_id").dropna(subset=["shipped_date", "required_date"])
            return _r2((o.shipped_date <= o.required_date).mean() * 100) if len(o) else None
        if aid in ("total_exports", "total_imports", "trade_balance", "coverage_ratio") and "flow" in d:
            exp, imp = d.loc[d.flow == "export", "value"].sum(), d.loc[d.flow == "import", "value"].sum()
            return {"total_exports": _r2(exp), "total_imports": _r2(imp), "trade_balance": _r2(exp - imp),
                    "coverage_ratio": _r2(exp / imp * 100) if imp else None}[aid]
        return None

    def insight_rows(self, i):
        return None                               # les constats de l'Agent 4 n'indiquent pas le nombre de lignes utilisées

    def reconcile(self) -> pd.DataFrame:
        super().reconcile()
        path, clean_dir, grain = self.ctx.get("fact_table_path"), self.ctx.get("clean_data_dir"), self.ctx.get("grain_table")
        cleaned = Path(clean_dir or "") / f"{grain}.csv" if grain else None
        if path and Path(path).exists() and cleaned is not None and cleaned.exists():
            published = int(pd.read_csv(path, usecols=[0]).shape[0])
            mine = int(pd.read_csv(cleaned, usecols=[0]).shape[0])
            row = dict(kpi_id="E01", scope_type="etl_rows", scope_value=f"Fact_Trade vs cleaned table {grain}", period="all", published=published,
                       recomputed=mine, abs_diff=abs(published - mine), status="MATCH" if published == mine else "MISMATCH")
            self.recon = pd.concat([self.recon, pd.DataFrame([row])], ignore_index=True)
            self.save(self.recon, "reconciliation.csv")
            self.log(str(self.recon.status.value_counts().to_dict()))
        return self.recon
