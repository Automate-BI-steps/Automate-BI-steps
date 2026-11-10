"""Agent 6 (Auditor / XAI, code de Dhouha) : noyau sur le jeu simulé du notebook + adaptateur sur un state de pipeline."""
import json
from pathlib import Path

import pandas as pd
import pytest

from src.agents.auditor.adapter import AuditorInputError, PipelineAuditor, build_contract
from src.agents.auditor.core import Auditor, AuditorConfig, ground_answer
from src.agents.auditor.demo_data import Simulator
from src.agents.auditor.step import run_auditor_step

NO_LLM = AuditorConfig(llm_provider="none", retrieval="lexical")


# ------------------------------------------------------------------ vérification des réponses (B.6)
def test_ground_answer_retire_les_phrases_non_soutenues():
    evidence = [{"id": "C0100", "text": "The rate used was 3.35 TND per EUR for row 4."}]
    ok, info = ground_answer("The rate used was 3.35 TND per EUR [C0100]. The rate used was 9.99 TND per EUR [C0100].", evidence)
    assert info["status"] == "filtered" and "9.99" not in ok and "[C0100]" in ok
    assert ground_answer("Aucune phrase exploitable", evidence)[1]["status"] == "failed"


# ------------------------------------------------------------------ noyau sur le jeu simulé du notebook
@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("auditor_demo")
    Simulator(out, n_base_rows=250).simulate()
    aud = Auditor(out, cfg=NO_LLM, llm=None, log=lambda _m: None)
    return out, aud, aud.run()


def test_audit_du_jeu_simule_produit_tous_les_fichiers(demo):
    out, _, summary = demo
    for name in ("audit_trail.csv", "lineage.csv", "reconciliation.csv", "xai_explanations.csv", "human_review_queue.csv", "xai_report.md"):
        assert (out / name).exists(), name
    assert summary["reconciliation"] == {"MATCH": summary["reconciliation"]["MATCH"]} and summary["reconciliation"]["MATCH"] > 0   # K01-K05 recalculés = publiés
    assert summary["review_queue"] > 0 and summary["model"] == "template-fallback"


def test_une_valeur_publiee_alteree_est_signalee(demo, tmp_path):
    out, _, _ = demo
    work = tmp_path / "tampered"
    work.mkdir()
    for f in out.glob("*.csv"):
        (work / f.name).write_bytes(f.read_bytes())
    kv = pd.read_csv(work / "kpi_values.csv")
    kv.loc[0, "value"] = kv.loc[0, "value"] * 1.10                      # un chiffre publié est faux de 10 %
    kv.to_csv(work / "kpi_values.csv", index=False)
    aud = Auditor(work, cfg=NO_LLM, llm=None, log=lambda _m: None).prepare()
    assert (aud.recon.status == "MISMATCH").sum() == 1


class StubLLM:
    """LLM factice : un nombre inventé doit être retiré, puis la réponse conservée si ses autres phrases sont soutenues."""
    name = "stub"

    def __call__(self, system, user, max_new_tokens=None):
        return "The figure is checked against the evidence [K01]. The figure rose by 987654.32 percent [K01]."


def test_reponses_llm_verifiees_contre_les_preuves(demo):
    out, _, _ = demo
    aud = Auditor(out, cfg=NO_LLM, llm=StubLLM(), log=lambda _m: None).prepare()
    text, check = aud.answer_with_evidence("Why K01?", aud.kpi_evidence("K01"))
    assert "987654" not in text and check["n_dropped"] >= 1
    assert all(c in aud.TRAIL or c.startswith(("KV", "RC", "PV", "LN")) for c in check["cited"])


def test_question_libre_repond_avec_les_ids_des_preuves(demo):
    _, aud, _ = demo
    text, check = aud.ask("Why does K01 Total Exports exist?")
    assert "[K01]" in text and check["ok"]


# ------------------------------------------------------------------ adaptateur sur un state de pipeline
def make_state(tmp_path, *, tamper=False):
    run = tmp_path / "run"
    (run / "semantic_kpi").mkdir(parents=True)
    (run / "clean").mkdir()
    orders = pd.DataFrame({"orderID": [1, 1, 2, 3], "orderDate": ["2024-01-05", "2024-01-05", "2024-01-09", "2025-01-11"],
                           "shippedDate": ["2024-01-08", "2024-01-08", "2024-01-20", "2025-01-13"], "requiredDate": ["2024-01-10", "2024-01-10", "2024-01-15", "2025-01-15"],
                           "country": ["FR", "FR", "DE", "FR"], "productName": ["A", "B", "A", "B"], "lineTotal": [10.0, 20.0, 30.0, 40.0]})
    orders.to_csv(run / "clean" / "order_details.csv", index=False)
    published = orders.copy()
    if tamper:
        published = published.iloc[:3]                                  # une ligne perdue à la construction de la table à plat
    published.to_csv(run / "semantic_kpi" / "fact_trade.csv", index=False)
    (run / "semantic_kpi" / "kpi_catalog.json").write_text(json.dumps([
        {"id": "total_revenue", "nom": "Total Revenue", "definition": "CA", "formule": "somme", "dax": "SUM(Fact_Trade[lineTotal])", "unite": "valeur",
         "colonnes_sources": ["lineTotal"], "bo": "BO3", "pourquoi": "base"},
        {"id": "order_count", "nom": "Order Count", "definition": "commandes", "formule": "distinct", "dax": "DISTINCTCOUNT(Fact_Trade[orderID])", "unite": "commandes",
         "colonnes_sources": ["orderID"], "bo": "BO3", "pourquoi": "volume"},
        {"id": "top_countries", "nom": "Top Countries", "definition": "pays", "formule": "somme par pays", "dax": "x", "unite": "valeur",
         "colonnes_sources": ["lineTotal", "country"], "bo": "BO3", "pourquoi": "concentration"}]), encoding="utf-8")
    (run / "semantic_kpi" / "agent4_audit_log.json").write_text(json.dumps([{"etape": "charger_donnees", "message": "jointure", "tables": {"order_details": 4, "orders": 3},
        "origine": {"orderID": "order_details", "orderDate": "orders", "shippedDate": "orders", "requiredDate": "orders", "country": "customers",
                    "productName": "products", "lineTotal": "calculé : unitPrice × quantity × (1 − discount)", "unitPrice": "order_details",
                    "quantity": "order_details", "discount": "order_details"}}]), encoding="utf-8")
    steps = [
        {"step": 1, "timestamp": "2026-11-01T10:00:00", "table": "order_details", "tool": "cast_numeric", "label": "Convertir en nombre", "kind": "fix", "column": "quantity",
         "status": "applied", "reason": "100 % numériques", "rows_before": 4, "rows_after": 4, "values_changed": 0, "examples": []},
        {"step": 2, "timestamp": "2026-11-01T10:00:01", "table": "customers", "tool": "trim_whitespace", "label": "Retirer les espaces", "kind": "fix", "column": "country",
         "status": "applied", "reason": "1 valeur avec espaces", "rows_before": 3, "rows_after": 3, "values_changed": 1, "examples": [{"row": 2, "avant": "FR ", "apres": "FR"}]},
        {"step": 3, "timestamp": "2026-11-01T10:00:02", "table": "orders", "tool": "flag_missing", "label": "Signaler", "kind": "flag", "column": "shippedDate",
         "status": "flagged", "reason": "1 valeur manquante", "rows_before": 3, "rows_after": 3, "values_changed": 0, "examples": []}]
    items = [{"issue_id": "ISS-001", "table": "order_details", "column": "quantity", "issue_type": "outlier", "description": "valeurs extrêmes", "status": "open",
              "status_label": "Reste à traiter", "still_present": True, "steps": []},
             {"issue_id": "ISS-002", "table": "orders", "column": "shippedDate", "issue_type": "missing_values", "description": "manquantes", "status": "flagged",
              "status_label": "Signalé", "still_present": True, "steps": [{"step": 3}]}]
    issues = [{"issue_id": "ISS-001", "table": "order_details", "column": "quantity", "issue_type": "outlier", "severity": "high", "description": "x", "affected_rows": 1,
               "affected_pct": 25.0, "examples": [130], "suggested_action": "cap_outliers", "detected_by": "rule", "confidence": 0.9},
              {"issue_id": "ISS-002", "table": "orders", "column": "shippedDate", "issue_type": "missing_values", "severity": "info", "description": "y", "affected_rows": 1,
               "affected_pct": 33.3, "examples": [], "suggested_action": "flag_missing", "detected_by": "rule", "confidence": 1.0}]
    results = {"total_revenue": {"valeur": 100.0, "unite": "valeur", "lignes_utilisees": 4}, "order_count": {"valeur": 3, "unite": "commandes", "lignes_utilisees": 4},
               "top_countries": {"valeur": [{"nom": "FR", "valeur": 70.0, "part_pct": 70.0}, {"nom": "DE", "valeur": 30.0, "part_pct": 30.0}], "unite": "valeur", "lignes_utilisees": 4}}
    return {"run": {"run_id": "t", "run_dir": str(run), "created_at": "2026-11-01T09:00:00"},
            "input": {"uploads": ["order_details.csv", "orders.csv"]},
            "timeline": [{"at": "2026-11-01T09:30:00", "agent": "profiling", "event": "done", "summary": ""}],
            "profiling": {"tables": {"order_details": {}}, "issues": issues},
            "cleaning": {"status": "done", "planner": {"mode": "rules"}, "steps": steps, "questions": [], "answers": {}, "clean_data_dir": str(run / "clean"),
                         "issue_resolution": {"items": items}},
            "etl": {"status": "error"},
            "kpi": {"status": "done", "fact_table_path": str(run / "semantic_kpi" / "fact_trade.csv"),
                    "outputs": {"kpi_catalog": str(run / "semantic_kpi" / "kpi_catalog.json"), "audit_log": str(run / "semantic_kpi" / "agent4_audit_log.json")},
                    "semantic_mapping": {"order_id": "orderID", "date": "orderDate", "country": "country", "product": "productName", "value": "lineTotal"},
                    "kpi_results": results,
                    "kpi_analysis": [{"kpi_id": "total_revenue", "nom": "Total Revenue", "constat": "Le CA est de 100.", "regle": "valeur", "niveau": "info"}]},
            "errors": []}


def test_contrat_construit_a_partir_du_state(tmp_path):
    state = make_state(tmp_path)
    info = build_contract(state, tmp_path / "aud")
    log = pd.read_csv(tmp_path / "aud" / "cleaning_log.csv", dtype=str).fillna("")
    assert info["events"] == 4 and list(log.event_id) == ["C0001", "C0002", "C0003", "C0004"]      # 3 étapes + 1 problème resté à traiter
    flagged, open_ = log[log.status == "flagged"].iloc[0], log[log.status == "open"].iloc[0]
    assert flagged.needs_human_validation == "True" and flagged.approval_status == "pending" and open_.rule_id == "R-NOTREATED-01"
    assert open_.profiler_issue_id == "P001" and log.loc[1, "before"] == "FR " and log.loc[1, "after"] == "FR" and log.loc[1, "row_id"] == "2"
    lin = pd.read_csv(tmp_path / "aud" / "column_lineage.csv", dtype=str).fillna("").set_index("fact_column")
    assert "R-NOTREATED-01" in lin.loc["Fact_Trade[lineTotal]", "cleaning_rules"]          # lineTotal = unitPrice × quantity × … -> les étapes sur quantity
    assert lin.loc["Fact_Trade[orderID]", "cleaning_rules"] == ""                           # aucune étape n'a touché orderID : rien n'est inventé
    assert lin.loc["Fact_Trade[shippedDate]", "cleaning_events"] == "C0003"


def test_etape_auditor_recalcule_et_concorde(tmp_path):
    res = run_auditor_step(make_state(tmp_path))
    a = res["auditor"]
    assert a["status"] == "OK" and a["summary"]["reconciliation"] == {"MATCH": 5} and a["summary"]["model"] == "template-fallback"   # CA + commandes + 2 pays + nombre de lignes de la table à plat
    assert {r["status"] for r in a["reconciliation"]} == {"MATCH"} and any(r["kpi_id"] == "E01" for r in a["reconciliation"])
    pdf = Path(a["artifacts"]["report_pdf"])                                                # rapport final : PDF, le Markdown n'est plus gardé
    assert pdf.read_bytes().startswith(b"%PDF") and "xai_report" not in a["artifacts"] and not (pdf.parent / "xai_report.md").exists()
    assert a["review_queue"] and res["timeline"][0]["agent"] == "auditor"
    assert any("per step" in x for x in a["limitations"])


def test_une_ligne_perdue_dans_la_table_a_plat_est_detectee(tmp_path):
    a = run_auditor_step(make_state(tmp_path, tamper=True))["auditor"]
    bad = [r for r in a["reconciliation"] if r["status"] == "MISMATCH"]
    assert a["status"] == "WARN" and any(r["kpi_id"] == "E01" for r in bad)                # nombre de lignes différent de la table nettoyée
    assert any(r["kpi_id"] == "K01" for r in bad)                                          # et le CA publié ne correspond plus à la table à plat


def test_question_libre_sur_un_run_audite(tmp_path):
    run_auditor_step(make_state(tmp_path))
    aud = PipelineAuditor(tmp_path / "run" / "auditor", cfg=NO_LLM, llm=None, log=lambda _m: None).prepare()
    text, check = aud.ask("Is the shippedDate column fully clean?")
    assert check["ok"] and "[C0003]" in text


def test_sans_trace_a_auditer_l_etape_echoue_proprement(tmp_path):
    with pytest.raises(AuditorInputError):
        build_contract({"run": {"run_dir": str(tmp_path)}}, tmp_path / "aud")
    res = run_auditor_step({"run": {"run_dir": str(tmp_path)}})
    assert res["auditor"]["status"] == "ERROR" and res["errors"][0]["agent"] == "auditor"


# ------------------------------------------------------------------ fournisseur Anthropic (client simulé : pas de réseau)
class _FakeMessages:
    def __init__(self, blocks):
        self.blocks, self.calls = blocks, []

    def create(self, **kw):
        self.calls.append(kw)
        return type("R", (), {"content": self.blocks})()


def _fake_anthropic(monkeypatch, blocks, keys):
    import sys
    import types
    messages = _FakeMessages(blocks)

    class Client:
        def __init__(self, api_key=None):
            keys.append(api_key)
            self.messages = messages
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))
    return messages


def test_fournisseur_anthropic_n_envoie_ni_temperature_ni_reflexion_et_ignore_les_blocs_de_reflexion(monkeypatch):
    from types import SimpleNamespace as NS
    from src.agents.auditor.core import LLM
    keys = []
    messages = _fake_anthropic(monkeypatch, [NS(type="thinking", thinking=""), NS(type="text", text=" Réponse [C0001]. ")], keys)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # clé vide dans .env : le SDK doit chercher ses identifiants lui-même
    llm = LLM(AuditorConfig(llm_provider="anthropic")).load()
    assert keys == [None] and llm.name == "claude-sonnet-5-5"
    assert llm("système", "question") == "Réponse [C0001]."
    call = messages.calls[0]
    assert "temperature" not in call and call["thinking"] == {"type": "between_tools"} and call["max_tokens"] == 280
    # un autre modèle ne reçoit pas `between_tools` (réservé à Sonnet 5.5)
    LLM(AuditorConfig(llm_provider="anthropic", anthropic_model="claude-opus-5-5")).load()("s", "q")
    assert "thinking" not in messages.calls[-1]

