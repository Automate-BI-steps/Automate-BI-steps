# Agent 4 — Semantic & KPI (DSO4)

**Mission (rapport) :** définir et calculer automatiquement les indicateurs métier, comme les mesures
DAX de Power BI, et produire le *KPI catalog* avec la source de chaque chiffre.

## Les 6 étapes (fichier `agent.py`)
1. `charger_donnees` : récupère `fact_trade` (ou la fabrique depuis les 7 CSV avec `data.py`)
2. `comprendre_colonnes` : associe chaque colonne à un rôle (`mapping.py`)
3. `calculer_kpi` : calcule les KPI possibles (`kpis.py`)
4. `documenter_kpi` : KPI catalog + mesures DAX
5. `controler` : contrôles de cohérence
6. `exporter` : fichiers de sortie + journal d'audit

## Lancer (PowerShell)
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py "C:\Users\user\Downloads\Import&Export\Import&Export\data"
```
Résultats dans `outputs\` : `kpi_catalog.json`, `kpi_results.json`, `dax_measures.dax`, `agent4_audit_log.json`.

## Interface (recommandé)
```
python make_interface.py "C:\Users\user\Downloads\Import&Export\Import&Export\data"
```
Lance l'agent puis ouvre `outputs\interface.html` : résultats, fonctionnement étape par étape, KPI catalog.

## Justesse des KPI (« KPI accuracy » du rapport)
```
python verify_kpis.py "C:\Users\user\Downloads\Import&Export\Import&Export\data"
```
Compare l'agent à un recalcul indépendant en pandas brut. Pour comparer à ton rapport Power BI manuel,
remplis la colonne `valeur_power_bi` de `reference_powerbi.csv` : les lignes remplies sont ajoutées à la comparaison.

## Tester
```
$env:BIFLOW_DATA="C:\Users\user\Downloads\Import&Export\Import&Export\data"
python -m pytest -q
```
