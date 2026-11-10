# Agent 6 : Auditor / XAI (Dhouha)

Source : notebook `BIAuditor_XAI.ipynb` (branche `Auditor`). **Partie B** du notebook (l'Auditor) est devenue un module ; **partie A**
(simulation de sept agents sur des données douanières synthétiques) est conservée comme jeu de démonstration.

| Fichier | Rôle |
|---|---|
| `core.py` | `Auditor` : la logique du notebook cellule par cellule (B.1 chargement, B.2 journal d'audit unifié, B.3 lignée / provenance / réconciliation, B.4 RAG, B.5 LLM, B.6 preuves + réponses vérifiées, B.7 quatre questions XAI, B.8 « Pourquoi ? » libre, B.9 file de validation humaine + rapport) |
| `demo_data.py` | `Simulator` : la partie A du notebook (jeu simulé). `python -m src.agents.auditor.cli demo` |
| `adapter.py` | `build_contract(state, dossier)` : state et fichiers des vrais agents -> les CSV du contrat ; `PipelineAuditor` : recalcul, provenance et textes adaptés aux vrais agents |
| `step.py` / `cli.py` | Étape du pipeline (section `auditor` du state) / ligne de commande (`demo`, `run`, `ask`) |
| `requirements_auditor.txt` | Paquets facultatifs : ChromaDB + sentence-transformers, `anthropic`, `transformers` |

## Principe conservé du notebook
Les **faits** viennent d'enregistrements déterministes (journaux, lignée, recalcul indépendant) ; un modèle de langage ne fait que les
**reformuler**, et chaque réponse est vérifiée : ids de preuves cités existants, nombres présents dans les preuves, phrases sans soutien retirées,
repli sur « preuves seules » si rien n'est fiable. Fournisseur : `AUDITOR_LLM_PROVIDER=auto` par défaut (Groq avec la clé de l'Agent 6, `GROQ_API_KEY_AGENT6`, sinon la clé partagée `GROQ_API_KEY` ; à défaut Claude si `ANTHROPIC_API_KEY` ; à défaut aucun) ; `groq` dans `.env.example`. Sans modèle (`none`) ou sans clé, les explications sont les preuves elles-mêmes, citées.

## Sorties (`<dossier_run>/auditor/`)
`audit_trail.csv` (événement par événement, tous agents), `lineage.csv` (visuel -> KPI -> colonnes -> règles -> problèmes), `reconciliation.csv`
(valeur publiée vs recalculée), `xai_explanations.csv` (4 types de questions), `human_review_queue.csv`, **`rapport_audit.pdf`** (rapport final en français, `pdf_report.py`, téléchargé depuis l'appli), plus les CSV du contrat
et `pipeline_context.json`. `xai_report.md` n'est gardé que si le PDF n'a pas pu être produit (reportlab absent).

## Le contrat CSV (ce que l'adaptateur écrit pour les vrais agents)
| Fichier | Construit à partir de |
|---|---|
| `profiler_report.csv` | `profiling.issues` (ISS-003 -> `P003`) |
| `cleaning_log.csv`, `cleaning_rules.csv` | `cleaning.steps` : une trace par étape de l'agent de nettoyage (`C0001`...), une règle par outil (`cast_numeric` -> `R-CASTNUMERIC-01`) ; les problèmes sans étape sont ajoutés (corrigés à la lecture, ou règle `R-NOTREATED-01` = « reste à traiter », en attente d'un humain) ; décisions de l'utilisateur lues dans `cleaning.questions` / `answers` |
| `etl_log.csv`, `column_lineage.csv` | `E01` = construction de la table à plat par l'Agent 4 (journal `agent4_audit_log.json`, colonne `origine`) ; le schéma en étoile de l'Agent 3 est listé « non consommé » |
| `kpi_catalog.csv`, `kpi_values.csv` | catalogue et résultats de l'Agent 4 (`K01`...) |
| `analyst_insights.csv` | `kpi.kpi_analysis` (`I01`...) : pas d'agent BI Analyst dans le pipeline, donc aucune recommandation |
| `dashboard_visuals.csv` | visuels réellement présents dans le projet Power BI généré + plan du générateur |
| `orchestrator_runs.csv` | statuts et horaires de la timeline, essais du raisonnement ETL |

## Ce que l'audit prouve, et ce qu'il ne prouve pas
- **Réconciliation** : l'Auditor recalcule chaque KPI (CA, commandes, panier moyen, croissance sur mêmes mois, classements, délais, taux) avec son propre code
  pandas depuis la table à plat de l'Agent 4 et compare à la valeur publiée (arrondie à 2 décimales). `MATCH` = l'agrégation suit la définition.
  Une ligne de contrôle compare aussi le nombre de lignes de la table à plat à celui de la table nettoyée de plus fin grain (la jointure n'est vérifiée que par ce comptage).
- **Provenance** au niveau colonne / étape, pas ligne : l'agent de nettoyage consigne ses corrections par étape (table, colonne, outil, nombre de valeurs changées).
  Le rapport le dit dans ses limites.
- La « confiance » d'une étape est celle du profilage pour le problème lié (1,0 sans problème lié), pas une confiance de l'outil de nettoyage.
- Les explications sont en anglais (textes de Dhouha) ; les libellés enregistrés par les agents restent dans leur langue.

## Non testé ici (pas de réseau)
Les fournisseurs `groq`, `anthropic`, `hf` et le chemin ChromaDB + sentence-transformers n'ont pas été exécutés contre un vrai service (`anthropic` est testé avec un client simulé : pas de `temperature`, qui est refusée par les modèles récents, et réflexion coupée sur Sonnet 5.5) ; le défaut (preuves seules + recherche lexicale)
est ce que les tests couvrent. Une erreur d'appel de modèle n'arrête pas l'audit : la réponse retombe sur les preuves.
