"""Adaptateur d'entrée de l'Agent 5 (Dashboard, code de Hashem Omri, non modifié).

    semantic_kpi --(state.kpi : table à plat + catalogue + constats)--> dashboard

Le générateur de Hashem lit un DOSSIER de fichiers (voir son README) : une table de faits à plat,
`data_model.json`, `kpi_catalog.json` et, en option, `bi_analyst_findings.json`,
`data_quality_report.json`, `xai_lineage.json`. Ce module fabrique ce dossier à partir de la
mémoire du run, sans rien inventer :

  - table de faits  : `fact_trade.csv` écrite par l'Agent 4 (celle sur laquelle ses KPI sont calculés) ;
  - catalogue KPI   : `kpi_catalog.json` de l'Agent 4, converti au format du générateur
                      (id, name, definition, expression, source_columns, dax) ;
  - constats        : `kpi_analysis` de l'Agent 4 (il n'y a pas d'agent « BI Analyst » dans ce projet) ;
  - qualité         : vérification de l'Agent 2 + contrôles de l'Agent 4 ;
  - lignée          : colonne source de chaque KPI (champ `origine` du catalogue de l'Agent 4).

Deux contrats d'entrée existent côté générateur, choisis automatiquement :
  - mode « trade » (import/export : date, country, product, direction, value) quand l'Agent 4 a
    reconnu les rôles date / pays / produit / flux / montant ET que le générateur accepte la table ;
  - mode « générique » sinon (ex. Northwind : pas de colonne Import/Export) : c'est le repli prévu
    par le code de Hashem (cartes KPI, constats, qualité).

Schéma en étoile (mode générique) : `star_schema.py` reconstruit le modèle Power BI à partir des tables
nettoyées que l'Agent 4 a jointes (`state.kpi.data_path`), avec toutes leurs colonnes, et l'écrit dans
`star/` + `data_model.json > star`. La table à plat reste la base du plan, des formules et du HTML.

Règle d'intégrité : une formule DAX de l'Agent 4 n'est transmise que si TOUTES ses références
(table, colonnes, mesures) existent dans le modèle que le générateur va produire ; sinon le KPI est
signalé « non transmis » (warning), jamais transmis avec une formule qui ne se chargerait pas.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

TRADE_ROLES = ("date", "country", "product", "flow", "value")
TRADE_TITLES = {"date": "Date", "country": "Country", "product": "Product", "direction": "Direction", "value": "Value"}
KNOWN_TRADE_IDS = {"total_imports", "total_exports", "trade_balance", "transaction_count"}  # formules intégrées au générateur
_REF = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]|\[([^\]]+)\]")


class DashboardInputError(RuntimeError):
    """Entrées de l'Agent 5 indisponibles (ex. l'Agent 4 n'a produit ni table ni catalogue)."""


# --------------------------------------------------------------------------- lecture de la mémoire
def _read_json(path: str | Path | None, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else default
    except (OSError, ValueError):
        return default


def load_upstream(state: dict) -> dict:
    """Ce que l'Agent 4 a laissé dans le state et sur disque ; lève DashboardInputError s'il manque l'essentiel."""
    kpi = state.get("kpi") or {}
    fact = kpi.get("fact_table_path")
    if kpi.get("status") != "done" or not fact or not Path(fact).is_file():
        raise DashboardInputError("L'Agent 4 (KPI) n'a pas produit de table de faits : rien à mettre en tableau de bord.")
    catalog = _read_json((kpi.get("outputs") or {}).get("kpi_catalog"), [])
    if not catalog:
        raise DashboardInputError("Le catalogue KPI de l'Agent 4 est introuvable.")
    return {"fact": pd.read_csv(fact, encoding="utf-8"), "fact_path": fact, "catalog": catalog,
            "mapping": kpi.get("semantic_mapping") or {}, "results": kpi.get("kpi_results") or {},
            "analysis": kpi.get("kpi_analysis") or [], "checks": kpi.get("kpi_checks") or [],
            "data_path": kpi.get("data_path")}


# --------------------------------------------------------------------------- choix du contrat
def trade_frame(fact: pd.DataFrame, mapping: dict) -> tuple[pd.DataFrame | None, dict, str | None]:
    """(table au contrat trade, {colonne d'origine -> clé trade}, raison du refus). Le refus n'est pas une erreur."""
    missing = [r for r in TRADE_ROLES if not mapping.get(r) or mapping[r] not in fact.columns]
    if missing:
        return None, {}, f"rôles absents chez l'Agent 4 : {', '.join(missing)}"
    rename = {mapping["date"]: "date", mapping["country"]: "country", mapping["product"]: "product",
              mapping["flow"]: "direction", mapping["value"]: "value"}
    frame = fact[list(rename)].rename(columns=rename)
    flow = frame["direction"].astype(str).str.lower().str.strip()
    frame["direction"] = flow.map(lambda v: "Export" if v.startswith("exp") else "Import" if v.startswith("imp") else None)
    from src.agents.dashboard.dashboard_generator import InputValidationError, validate_frame

    try:
        validate_frame(frame, "cleaned_trade.csv", trade_mode=True)  # le générateur lui-même juge si la table lui convient
    except InputValidationError as exc:
        return None, {}, f"table refusée par le générateur en mode trade : {exc}"
    return frame, {orig: key for orig, key in rename.items()}, None


def model_columns(fact: pd.DataFrame, trade: bool, rename: dict) -> dict[str, str]:
    """colonne d'origine (minuscule) -> nom de colonne dans le modèle Power BI produit par le générateur."""
    if trade:
        return {orig.lower(): TRADE_TITLES[key] for orig, key in rename.items()}
    return {str(c).lower(): str(c)[0].upper() + str(c)[1:] for c in fact.columns}  # le générateur met la 1re lettre en majuscule


# --------------------------------------------------------------------------- formules DAX
def rewrite_dax(dax: str, source_table: str, target_table: str, columns: dict[str, str],
                measures: set[str]) -> tuple[str | None, str | None]:
    """(DAX réécrit, raison du refus). Toute référence qui ne se résout pas dans le modèle = refus."""
    problems: list[str] = []

    def sub(m: re.Match) -> str:
        table, col, measure = m.group(1), m.group(2), m.group(3)
        if measure is not None:
            if measure not in measures:
                problems.append(f"mesure [{measure}] absente")
            return m.group(0)
        if table.lower() != source_table.lower():
            problems.append(f"{table}[{col}] : table absente du modèle généré")
            return m.group(0)
        target = columns.get(col.lower())
        if target is None:
            problems.append(f"{table}[{col}] : colonne absente du modèle généré")
            return m.group(0)
        return f"{target_table}[{target}]"

    out = _REF.sub(sub, dax)
    return (None, "; ".join(dict.fromkeys(problems))) if problems else (out, None)


def convert_catalog(catalog: list[dict], columns: dict[str, str], trade: bool, fact_table: str,
                    source_table: str, rename: dict) -> tuple[list[dict], list[dict]]:
    """Catalogue de l'Agent 4 -> catalogue du générateur ; renvoie (kpis, kpis non transmis avec raison)."""
    names = {k["nom"] for k in catalog}
    kpis: dict[str, dict] = {}
    for k in catalog:
        sources = [TRADE_TITLES[rename[c]].lower() if trade and c in rename else c for c in k.get("colonnes_sources", [])]
        kpis[k["id"]] = {"id": k["id"], "name": k["nom"], "definition": k["definition"], "expression": k["formule"],
                         "source_columns": sources, "supported": True, "dax": None, "_dax": k.get("dax", "")}
    reasons: dict[str, str] = {}
    live = set(names)
    while True:  # une mesure qui en référence une autre doit disparaître avec elle
        dropped = False
        for kid, kpi in kpis.items():
            if kid in reasons:
                continue
            if trade and kid in KNOWN_TRADE_IDS:
                continue  # formule DAX intégrée au générateur (mode trade)
            new, why = rewrite_dax(kpi["_dax"], source_table, fact_table, columns, live)
            if new is None:
                reasons[kid] = why or "formule DAX non transmissible"
                live.discard(kpi["name"])
                dropped = True
            else:
                kpi["dax"] = new
        if not dropped:
            break
    skipped = []
    for kid, why in reasons.items():
        kpis[kid]["supported"] = False
        skipped.append({"kpi_id": kid, "reason": why})
    for kpi in kpis.values():
        kpi.pop("_dax", None)
    return list(kpis.values()), skipped


@contextmanager
def published_kpi_values(kpis: list[dict], results: dict):
    """Le rendu HTML du générateur ne sait afficher que les KPI dont il connaît la formule pandas
    (`KNOWN_KPIS`, import/export). Pour les autres, on lui déclare la valeur déjà publiée par l'Agent 4
    (nombre simple) : sinon toutes les cartes affichent « N/A ». L'enregistrement est retiré à la sortie."""
    from src.agents.dashboard import dashboard_generator as gen

    added = []
    for kpi in kpis:
        value = (results.get(kpi["id"]) or {}).get("valeur")
        if kpi["supported"] and kpi["id"] not in gen.KNOWN_KPIS and isinstance(value, (int, float)) and not isinstance(value, bool):
            gen.KNOWN_KPIS[kpi["id"]] = (kpi["dax"] or "", lambda data, v=float(value): v)
            added.append(kpi["id"])
    try:
        yield added
    finally:
        for kid in added:
            gen.KNOWN_KPIS.pop(kid, None)


# --------------------------------------------------------------------------- schéma en étoile
def star_from_clean_tables(data_path: str | None, flat: pd.DataFrame, fact_table: str):
    """(StarSchema, None) ou (None, raison) : étoile construite sur les tables nettoyées lues par l'Agent 4."""
    from src.agents.dashboard.star_schema import build_star, read_tables

    if not data_path or not Path(data_path).exists():
        return None, "tables nettoyées introuvables"
    try:
        return build_star(read_tables(data_path), flat, fact_table)
    except Exception as exc:  # une étoile ratée ne doit pas bloquer le tableau de bord : repli à plat
        return None, f"{type(exc).__name__} : {exc}"


# --------------------------------------------------------------------------- fichiers d'entrée
def _quality_report(state: dict, up: dict) -> dict:
    cleaning = state.get("cleaning") or {}
    ver, summary = cleaning.get("verification") or {}, cleaning.get("summary") or {}
    open_issues = [i for i in (cleaning.get("issue_resolution") or {}).get("items", []) if i.get("still_present")]
    checks = {"score_qualite_avant_nettoyage": ver.get("score_before"), "score_qualite_apres_nettoyage": ver.get("score_after"),
              "etapes_de_nettoyage_appliquees": summary.get("steps_applied"), "valeurs_corrigees": summary.get("values_changed"),
              "lignes_supprimees": summary.get("rows_removed"), "problemes_restant_a_traiter": len(open_issues)}
    checks.update({f"Agent 4 : {c['controle']}": f"{c['statut']} ({c['detail']})" for c in up["checks"] if isinstance(c, dict)})
    return {"status": str(summary.get("status") or "not_provided").lower(), "source": "cleaning + semantic_kpi",
            "checks": {k: v for k, v in checks.items() if v is not None},
            "issues": [f"{i['table']}.{i.get('column') or '*'} : {i['issue_type']} ({i.get('status_label')})" for i in open_issues]}


def _findings(up: dict, frame: pd.DataFrame, date_col: str | None) -> dict:
    period = "unavailable"
    if date_col and date_col in frame.columns:
        d = pd.to_datetime(frame[date_col], errors="coerce").dropna()
        if len(d):
            period = f"{d.min():%Y-%m-%d} / {d.max():%Y-%m-%d}"
    return {"findings": [{"id": f"{a['kpi_id']}-{i}", "text": a["constat"], "kpi_id": a["kpi_id"], "level": a.get("niveau")}
                         for i, a in enumerate(up["analysis"], 1) if a.get("constat")],
            "recommendations": [], "reporting_period": period, "confidence": "rule_based",
            "source": "semantic_kpi.kpi_analysis (aucune recommandation inventée)"}


def build_input(state: dict, out_dir: Path) -> dict:
    """Écrit le dossier d'entrée de l'Agent 5 et renvoie `dashboard_prep` (clés lues par son nœud)."""
    up = load_upstream(state)
    fact, mapping = up["fact"], up["mapping"]
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    frame, rename, why = trade_frame(fact, mapping)
    trade = frame is not None
    if not trade:
        warnings.append(f"Mode générique du générateur (pas de contrat import/export) : {why}.")
    source_table = os.environ.get("FACT_TABLE", "Fact_Trade")  # nom de table dans les formules DAX de l'Agent 4
    fact_table = "FactTrade" if trade else source_table
    columns = model_columns(fact, trade, rename)
    kpis, skipped = convert_catalog(up["catalog"], columns, trade, fact_table, source_table, rename)
    for s in skipped:
        warnings.append(f"KPI « {s['kpi_id']} » non transmis au générateur : {s['reason']}.")

    if trade:
        frame.to_csv(out_dir / "cleaned_trade.csv", index=False, encoding="utf-8")
        data_model = {"fact_table": "FactTrade", "grain": "Une ligne par transaction (table à plat de l'Agent 4)",
                      "columns": {k: f"colonne « {o} » (rôle reconnu par l'Agent 4)" for o, k in rename.items()}}
    else:
        fact.to_csv(out_dir / f"{fact_table}.csv", index=False, encoding="utf-8")
        # Le générateur crée une table de dimension par colonne déclarée : on ne déclare que les colonnes
        # qualitatives et les dates (ni montants, ni quantités, ni identifiants numériques).
        dims = [c for c in fact.columns if not pd.api.types.is_numeric_dtype(fact[c]) or "date" in str(c).lower()]
        data_model = {"fact_table": fact_table, "grain": "Une ligne par produit et par commande (table à plat de l'Agent 4)",
                      "columns": {c: f"colonne « {c} »" for c in dims},
                      # Rôles reconnus par l'Agent 4 (date, pays, produit, montant...) : le rapport générique
                      # s'en sert pour choisir la date de la table calendrier et les axes de ses pages.
                      "roles": {r: c for r, c in mapping.items() if isinstance(c, str) and c in fact.columns}}
        # Schéma en étoile du modèle Power BI, reconstruit à partir des tables nettoyées (toutes leurs
        # colonnes) ; la table à plat ci-dessus reste la base du plan, des formules et du HTML.
        star, why_star = star_from_clean_tables(up.get("data_path"), fact, fact_table)
        if star is not None:
            data_model["star"] = star.write(out_dir)
            data_model["grain"] = "Une ligne par produit et par commande (table de faits du schéma en étoile)"
        else:
            warnings.append(f"Schéma en étoile non construit ({why_star}) : modèle à plat.")
    etl = state.get("etl") or {}
    data_model.update({"validated": False, "source": "semantic_kpi.fact_trade",
                       "etl_reference": {"confirmed_fact": etl.get("confirmed_fact"), "selected_dimensions": etl.get("selected_dimensions")}
                       if etl.get("status") == "done" else None})
    lineage = {"upstream_agent": "semantic_kpi", "scope": "KPI -> colonnes sources (Agent 4)",
               "kpi_lineage": {k["id"]: {"columns": k.get("colonnes_sources"), "origin": k.get("origine")} for k in up["catalog"]}}

    def write(name: str, obj: Any) -> None:
        (out_dir / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    write("data_model.json", data_model)
    write("kpi_catalog.json", {"kpis": kpis})
    write("bi_analyst_findings.json", _findings(up, fact, mapping.get("date")))  # dates : colonne d'origine
    write("data_quality_report.json", _quality_report(state, up))
    write("xai_lineage.json", lineage)

    # Réglages du générateur : ceux de l'environnement (BI_FLOW_MODE, BI_FLOW_LLM_PROVIDER... voir
    # .env.example ; sans rien, mode déterministe), sauf le journal de retours écrit dans le run
    # (par défaut il irait dans <racine du dépôt>/generated/).
    from src.agents.dashboard.config import GeneratorConfig

    cfg = GeneratorConfig.from_env().model_dump(mode="json")
    cfg["feedback_path"] = str(out_dir.parent / "dashboard" / "optimization_feedback.jsonl")
    return {"input_dir": str(out_dir), "input_mode": "trade" if trade else "generic", "config": cfg, "warnings": warnings,
            "kpis": kpis, "kpis_skipped": skipped, "results": up["results"]}
