"""Lance l'agent 4 :   python run.py "C:\\chemin\\vers\\data"
Avec le LLM (Ollama) :   $env:USE_LLM="1"   puis   python run.py ...
"""
import json
import sys

from agent import en_json, run

if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else "data"
    etat = run(chemin)

    print("\n=== 1. Colonnes comprises (rôle -> colonne) ===")
    for role, col in etat.get("semantic_mapping", {}).items():
        print(f"  {role:<14} -> {col}")

    print("\n=== 2. KPI calculés ===")
    for r in etat.get("kpi_results", {}).values():
        v = r["valeur"]
        if isinstance(v, list):                       # classement : on affiche les 3 premiers
            v = ", ".join(f"{p['nom']} ({p['part_pct']} %)" for p in v[:3])
        elif isinstance(v, dict):                     # croissance annuelle
            v = f"croissance (mêmes mois) : {en_json(v['croissance_pct'])}"
        print(f"  {r['nom']:<28} {v} {r['unite'] if not isinstance(r['valeur'], (list, dict)) else ''}")

    ignores = next((e.get("ignores_detail", {}) for e in etat.get("audit_log", [])
                    if e["etape"] == "calculer_kpi"), {})
    if ignores:
        print("\n=== 3. KPI non calculables sur ce dataset ===")
        for kid, d in ignores.items():
            print(f"  {d['nom']} ({kid})\n    formule : {d['formule']}\n    {d['raison']}")

    print("\n=== 4. Contrôles ===")
    for c in etat.get("kpi_checks", []):
        print(f"  [{c['statut']}] {c['controle']} : {c['detail']}")

    print("\n=== 5. Analyse des KPI ===")
    for a in etat.get("kpi_analysis", []):
        print(f"  [{a['niveau']}] {a['nom']} : {a['constat']}")
        if a.get("interpretation"):
            print(f"      -> {a['interpretation']}")
    if etat.get("kpi_summary"):
        print("\n  Résumé (LLM) :", etat["kpi_summary"])

    if etat.get("errors"):
        print("\nErreurs :", etat["errors"])
    print("\nFichiers écrits :", json.dumps(etat.get("outputs"), indent=2))