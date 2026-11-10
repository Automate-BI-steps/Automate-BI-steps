"""Rapport final de l'Agent 6 en PDF (rapport_audit.pdf), à télécharger depuis l'appli sans l'afficher.

Construit avec reportlab à partir des tables de l'Auditor (déjà calculées par PipelineAuditor.run) et du state
du run (étapes du pipeline, valeurs des KPI de l'Agent 4) :

  1. couverture : run, date, chiffres clés de l'audit ;
  2. déroulé du pipeline (une ligne par agent) ;
  3. KPI publiés et leur recalcul indépendant ;
  4. qualité des données et corrections par règle ;
  5. explications vérifiées (corrections, KPI, constats, recommandations) ;
  6. lignage visuel -> KPI -> source ;
  7. points en attente de validation humaine ;
  8. limites.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#13213D")
RED = colors.HexColor("#9E1B32")
PANEL = colors.HexColor("#E6EBF3")
LINE = colors.HexColor("#C9D3E3")
MUTED = colors.HexColor("#5B6B86")
STATUS_FR = {"MATCH": "concorde", "MISMATCH": "écart", "NOT_RECOMPUTED": "non recalculable"}
ANSWER_FR = {"verified": "vérifiée", "filtered": "vérifiée (phrases non soutenues retirées)",
             "template_fallback": "preuves seules", "failed": "échec"}
QTYPE_FR = (("correction", "Pourquoi ces valeurs ont-elles été corrigées ?"), ("kpi", "Pourquoi ces KPI ?"),
            ("insight", "D'où viennent les constats ?"), ("recommendation", "Comment les recommandations sont-elles justifiées ?"))
AGENT_FR = {"profiling": "Profilage", "cleaning": "Nettoyage", "verification": "Vérification", "etl": "ETL",
            "kpi": "Sémantique & KPI", "dashboard": "Tableau de bord", "auditor": "Audit & XAI", "init": "Initialisation"}


def _safe(text: Any, limit: int | None = None) -> str:
    """Texte pour Helvetica (Latin-1) et le mini-HTML de reportlab : caractères hors Latin-1 remplacés, < > & échappés."""
    s = "" if text is None or (isinstance(text, float) and text != text) else str(text)
    s = s.replace("→", "->").replace("’", "'").replace("“", '"').replace("”", '"').replace("…", "...").replace("—", "-").replace("–", "-")
    s = s.encode("latin-1", "replace").decode("latin-1")
    if limit and len(s) > limit:
        s = s[: limit - 3] + "..."
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return _safe(value)
    if isinstance(value, (int, float)):
        if value != value:
            return "-"
        if float(value).is_integer():
            return f"{value:,.0f}".replace(",", " ")
        return f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return _safe(value, 60)


def _kpi_value(value: Any) -> str:
    """Valeur d'un KPI lisible : nombre, classement (« USA 19,4 % · Germany 18,2 % ») ou valeurs par année."""
    if isinstance(value, list):
        items = [v for v in value if isinstance(v, dict)][:5]
        parts = [f"{v.get('nom', '?')} {_num(v.get('part_pct'))} %" if v.get("part_pct") is not None else f"{v.get('nom', '?')} {_num(v.get('valeur'))}"
                 for v in items]
        return " · ".join(parts)
    if isinstance(value, dict):
        growth = value.get("croissance_pct")
        if isinstance(growth, dict):
            return " · ".join(f"{k} : {_num(v)} %" for k, v in growth.items())
        return " · ".join(f"{k} : {_num(v)}" for k, v in list(value.items())[:5] if not isinstance(v, (dict, list)))
    return _num(value) if not isinstance(value, str) else value


class _Styles:
    def __init__(self) -> None:
        base = getSampleStyleSheet()
        self.title = ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24, textColor=NAVY, spaceAfter=6)
        self.sub = ParagraphStyle("s", parent=base["Normal"], fontSize=10, textColor=MUTED, spaceAfter=18)
        self.h1 = ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, spaceBefore=6, spaceAfter=8, keepWithNext=1)
        self.h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4, keepWithNext=1)
        self.body = ParagraphStyle("b", parent=base["Normal"], fontSize=9, leading=12)
        self.small = ParagraphStyle("sm", parent=base["Normal"], fontSize=7.5, leading=9.5, textColor=MUTED)
        self.cell = ParagraphStyle("c", parent=base["Normal"], fontSize=7.5, leading=9.5)
        self.head = ParagraphStyle("hd", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9.5, textColor=colors.white)
        self.big = ParagraphStyle("big", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=RED, alignment=TA_CENTER)
        self.label = ParagraphStyle("lab", parent=base["Normal"], fontSize=8, textColor=MUTED, alignment=TA_CENTER)


def _table(st: _Styles, headers: list[str], rows: list[list[Any]], widths: list[float], limit: int | None = None) -> Table | Paragraph:
    if not rows:
        return Paragraph("<i>Aucune ligne.</i>", st.small)
    body = [[Paragraph(_safe(h), st.head) for h in headers]]
    body += [[Paragraph(_safe(c, limit) if not isinstance(c, (int, float)) else _num(c), st.cell) for c in r] for r in rows]
    t = Table(body, colWidths=[w * cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5FA")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _tiles(st: _Styles, items: list[tuple[str, str]]) -> Table:
    cells = [[Paragraph(_safe(v), st.big) for v, _ in items], [Paragraph(_safe(l), st.label) for _, l in items]]
    width = 17.0 / len(items)
    t = Table(cells, colWidths=[width * cm] * len(items), rowHeights=[1.1 * cm, 0.8 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white)]))
    return t


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 0.55 * cm, A4[0], 0.55 * cm, fill=1, stroke=0)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(2 * cm, 1.1 * cm, "BI Flow - rapport d'audit de l'Agent 6 (Auditor / XAI)")
    canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"page {doc.page}")
    canvas.restoreState()


def build_pdf(aud: Any, state: dict, path: Path) -> Path:
    """Écrit le rapport PDF et renvoie son chemin. `aud` : PipelineAuditor après run()."""
    st = _Styles()
    run = state.get("run") or {}
    run_dir = str(run.get("run_dir") or path.parent.parent)
    run_id = run.get("run_id") if run.get("run_id") not in (None, "", "run") else Path(run_dir).name
    recon = aud.recon
    computed = recon[recon.status != "NOT_RECOMPUTED"]
    matched = int((recon.status == "MATCH").sum())
    mismatched = int((recon.status == "MISMATCH").sum())
    pending = int((aud.queue.approval_status == "pending").sum()) if len(aud.queue) else 0
    story: list[Any] = []

    # 1. Couverture
    story += [Spacer(1, 1.2 * cm), Paragraph("Rapport d'audit BI Flow", st.title),
              Paragraph(f"Run <b>{_safe(run_id)}</b> - généré le {dt.datetime.now().strftime('%d/%m/%Y à %H:%M')} par l'Agent 6. "
                        f"Explications rédigées par <b>{_safe(aud.llm.name if aud.llm else 'modèles (sans LLM)')}</b> puis vérifiées contre les preuves.",
                        st.sub),
              _tiles(st, [(_num(len(aud.trail)), "événements tracés"), (f"{matched}/{len(computed)}", "chiffres recalculés qui concordent"),
                          (_num(mismatched), "écart(s) détecté(s)"), (_num(pending), "point(s) à valider")]),
              Spacer(1, 0.6 * cm)]
    verdict = ("Tous les chiffres recalculables concordent avec les valeurs publiées." if not mismatched
               else f"{mismatched} chiffre(s) publié(s) ne concordent pas avec le recalcul indépendant : voir la section 3.")
    if pending:
        verdict += " Des corrections restent provisoires tant qu'un humain ne les a pas validées."
    story.append(Paragraph(f"<b>Conclusion :</b> {_safe(verdict)}", st.body))

    # 2. Pipeline
    story += [Paragraph("1. Déroulé du pipeline", st.h1)]
    # dernier événement de chaque agent (les relances d'une étape remplacent les précédentes), chemins du run raccourcis
    last: dict[str, dict] = {}
    for e in state.get("timeline") or []:
        if isinstance(e, dict) and e.get("agent") not in {"user", "orchestrator"}:
            last.pop(str(e.get("agent")), None)
            last[str(e.get("agent"))] = e
    rows = [[str(e.get("at", ""))[:16].replace("T", " "), AGENT_FR.get(agent, agent), e.get("event"),
             str(e.get("summary", "")).replace(run_dir + "/", "").replace(run_dir + "\\", "").replace(run_dir, "run")]
            for agent, e in sorted(last.items(), key=lambda kv: str(kv[1].get("at", "")))]
    story.append(_table(st, ["Heure", "Agent", "Événement", "Résumé"], rows, [2.6, 2.6, 1.8, 10.0], limit=420))

    # 3. KPI
    story += [PageBreak(), Paragraph("2. KPI publiés et recalcul indépendant", st.h1),
              Paragraph("Chaque valeur publiée par l'Agent 4 est recalculée par l'Auditor à partir de la table nettoyée, "
                        "sans réutiliser le code de l'Agent 4.", st.body), Spacer(1, 0.2 * cm)]
    results = (state.get("kpi") or {}).get("kpi_results") or {}
    # identifiants de l'Auditor (K01...) -> identifiants de l'Agent 4 (total_revenue...), lus dans kpi_catalog.csv
    to_agent4 = {}
    for r in getattr(aud, "kpi_cat", []).itertuples() if len(getattr(aud, "kpi_cat", [])) else []:
        m = re.search(r"Semantic & KPI id: ([A-Za-z0-9_]+)", str(r.description))
        if m:
            to_agent4[r.kpi_id] = m.group(1)
    status: dict[str, list[str]] = {}
    for r in recon.itertuples():
        status.setdefault(to_agent4.get(r.kpi_id, r.kpi_id), []).append(r.status)

    def verdict_of(kid: str) -> str:
        found = status.get(kid, [])
        if "MISMATCH" in found:
            return f"écart ({found.count('MISMATCH')})"
        n = found.count("MATCH")
        return f"concorde ({n})" if n else "non recalculé"

    rows = [[v.get("nom"), _kpi_value(v.get("valeur")), v.get("unite"), verdict_of(kid), v.get("definition")]
            for kid, v in results.items() if isinstance(v, dict)]
    story.append(_table(st, ["KPI", "Valeur", "Unité", "Recalcul", "Définition"], rows, [3.4, 4.6, 1.8, 2.2, 5.0], limit=220))
    if mismatched:
        bad = recon[recon.status == "MISMATCH"]
        story += [Paragraph("Écarts détectés", st.h2),
                  _table(st, ["KPI", "Périmètre", "Période", "Publié", "Recalculé"],
                         [[r.kpi_id, f"{r.scope_type} {r.scope_value}", r.period, r.published, r.recomputed] for r in bad.itertuples()],
                         [3.5, 4.5, 2.5, 3.0, 3.5])]

    # 4. Qualité
    story += [Paragraph("3. Qualité des données et corrections", st.h1)]
    prof = aud.prof
    if len(prof):
        cols = [c for c in ("issue_type", "column", "affected_rows", "pct_rows", "severity") if c in prof.columns]
        story.append(_table(st, ["Problème", "Colonne", "Lignes", "% lignes", "Gravité"][:len(cols)],
                            prof[cols].head(40).values.tolist(), [4.5, 4.5, 2.5, 2.5, 3.0][:len(cols)], limit=80))
    clog = aud.clog[aud.clog.rule_id != "R-FX-01"] if len(aud.clog) else aud.clog
    if len(clog):
        by_rule = clog.groupby("rule_id").agg(corrections=("event_id", "count"),
                                             en_attente=("approval_status", lambda s: int((s == "pending").sum()))).reset_index()
        story += [Paragraph("Corrections par règle", st.h2),
                  _table(st, ["Règle", "Corrections", "En attente de validation"], by_rule.values.tolist(), [7.0, 4.0, 6.0])]

    # 5. Explications
    story += [PageBreak(), Paragraph("4. Explications vérifiées", st.h1)]
    for qtype, title in QTYPE_FR:
        part = aud.xai[aud.xai.question_type == qtype].head(12)
        if not len(part):
            continue
        story.append(Paragraph(title, st.h2))
        for r in part.itertuples():
            story.append(KeepTogether([
                Paragraph(f"<b>{_safe(r.subject_id)}</b> - <i>{_safe(r.question, 300)}</i>", st.body),
                Paragraph(_safe(r.answer, 1500), st.body),
                Paragraph(f"Réponse {_safe(ANSWER_FR.get(r.status, r.status))} ; preuves : {_safe(r.evidence_ids, 200)}", st.small),
                Spacer(1, 0.18 * cm)]))

    # 6. Lignage
    story += [Paragraph("5. Lignage : visuel -> KPI -> source", st.h1)]
    lin = aud.lineage
    cols = [c for c in ("visual_title", "kpi_id", "etl_steps", "cleaning_rules", "profiler_issues") if c in lin.columns]
    story.append(_table(st, ["Visuel", "KPI", "Étapes ETL", "Règles de nettoyage", "Problèmes du profilage"][:len(cols)],
                        lin[cols].head(60).values.tolist(), [4.0, 3.0, 3.0, 3.5, 3.5][:len(cols)], limit=140))

    # 7. Validation humaine
    story += [Paragraph("6. Points en attente de validation humaine", st.h1)]
    q = aud.queue
    cols = [c for c in ("event_id", "column", "before", "after", "rule_id", "approval_status", "priority") if c in q.columns]
    story.append(_table(st, ["Événement", "Colonne", "Avant", "Après", "Règle", "Statut", "Priorité"][:len(cols)],
                        q[cols].head(25).values.tolist(), [2.4, 2.4, 2.6, 2.6, 2.6, 2.2, 2.2][:len(cols)], limit=60))
    if len(q) > 25:
        story.append(Paragraph(f"... et {len(q) - 25} autre(s) point(s) dans human_review_queue.csv.", st.small))

    # 8. Limites
    story += [Paragraph("7. Limites", st.h1)]
    limits = list(getattr(aud, "LIMITATIONS", []) or []) + [
        "Les textes du modèle de langage sont vérifiés pour les identifiants et les nombres cités, pas pour chaque nuance : "
        "les preuves déterministes font foi.",
        "Les corrections marquées « pending » restent provisoires tant qu'un humain ne les a pas validées.",
        "Le rendu du rapport Power BI (actualisation, DAX, visuels) n'est pas exécuté par l'Auditor."]
    for item in limits:
        story.append(Paragraph(f"- {_safe(item)}", st.body))

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm, bottomMargin=1.8 * cm,
                            title=f"Rapport d'audit BI Flow - {run_id}", author="BI Flow - Agent 6")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path
