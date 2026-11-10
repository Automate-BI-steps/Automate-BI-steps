# BI Flow : de fichiers CSV / Excel au tableau de bord Power BI audité, avec interface React

Pipeline de six agents qui partagent un même **state** (mémoire) :

| Agent | Code | Écrit dans le state |
|---|---|---|
| 1. Profilage (Chiheb), inchangé | `src/agents/profiling/` | `profiling` : rapport JSON (tables, colonnes, relations, problèmes, score) |
| 2. Cleaning / transforming (Bedis) | `src/agents/cleaning/` | `cleaning` : tables propres, journal des corrections, questions, bilan |
| 3. ETL, schéma en étoile (code d'origine) | `src/agents/etl/` | `etl` : faits + dimensions matérialisés |
| 4. Semantic & KPI (code d'origine) | `src/agents/semantic_kpi/` | `kpi` : table à plat, catalogue et valeurs des KPI, analyse |
| 5. Dashboard (Hashem Omri), code inchangé | `src/agents/dashboard/` | `dashboard` : projet Power BI (PBIP), aperçu HTML |
| 6. Auditor / XAI (Dhouha), code du notebook en module | `src/agents/auditor/` | `auditor` : journal d'audit, lignée, recalcul des chiffres, explications, validation humaine |

```
CSV / Excel -> [profilage] -> [nettoyage] -> [contrôle] -+-> [ETL] ----------------------------+
                                                         +-> [KPI] -> [dashboard] -------------+-> [auditor]
```
L'ETL et le KPI lisent tous deux les tables nettoyées (ils ne dépendent pas l'un de l'autre) ; le dashboard lit la table à plat
et le catalogue du KPI ; l'auditor lit tout le state et les fichiers des agents. Chaque agent garde son code et son dossier :
seules les couches de liaison (`prep.py` / `step.py` du dashboard, `adapter.py` / `step.py` de l'auditor, `node.py` / `runner.py`
du KPI) ont été écrites pour les brancher. Détails : `src/agents/dashboard/README_dashboard.md`, `src/agents/auditor/README_auditor.md`.

## Mémoire (state)
- Le state est défini dans `src/state.py` (sections `run`, `input`, `profiling`, `cleaning`, `etl`, `kpi`, `dashboard`, `auditor`, `timeline`, `errors`).
- L'agent de nettoyage **lit `profiling`** : il relit les CSV listés dans `profiling.metadata.files` avec l'encodage et
  le séparateur détectés, et donne les problèmes (`profiling.issues`) au planificateur LLM.
- Chaque passage de relais ajoute une note à `timeline`, et le state complet est enregistré dans
  `outputs/runs/<run_id>/state.json`. Quand l'agent de nettoyage attend l'accord de l'utilisateur (valeurs extrêmes),
  il reprend ensuite depuis ce fichier, sans rien perdre.
- Après le nettoyage, un second profilage (règles seules) mesure le gain et chaque problème du profilage est relié à son
  traitement (`cleaning.issue_resolution`, `cleaning.verification`).

## Lancer
```bash
pip install -r requirements.txt
cp .env.example .env            # clés LLM facultatives, une par agent (sans clé : règles seules)

# ligne de commande
python main.py --outliers advice

# application web : backend Python (FastAPI) + frontend React
cd client && npm install && npm run build && cd ..
python serve.py                  # http://127.0.0.1:8000
# python serve.py --reload       # développement : ne surveille que api/ et src/

# développement du frontend (rechargement à chaud)
cd client && npm run dev         # http://127.0.0.1:5173 (API proxifiée vers le port 8000)
```
`python -m src.orchestrator all <dossier_run>` enchaîne tout ; chaque étape existe aussi seule : `init`, `profile`, `clean`,
`answer`, `verify`, `etl`, `kpi`, `dashboard`, `auditor`. L'audit se refait seul : `python -m src.agents.auditor.cli run --run-dir <dossier_run>`,
et une question libre se pose avec `... cli ask --run-dir <dossier_run> "Pourquoi ... ?"`.

## Interface
Huit onglets : **Profilage** (diagnostic de l'Agent 1), **Nettoyage** (validation des valeurs extrêmes puis comparaison
avant / après), **Bilan** (score avant / après, état de chaque problème), **ETL (Agent 3)**, **KPI (Agent 4)**, **Dashboard (Agent 5)**
(KPI transmis ou écartés, aperçu, projet Power BI en .zip), **Audit XAI (Agent 6)** (chiffres recalculés, questions « Pourquoi ? »,
points à valider, lignée) et **Mémoire** (passages de relais et state explorable).

## Réglages (voir `.env.example`, tous actifs)
Copier `.env.example` en `.env` : les réglages sont déjà activés, il ne reste qu'à renseigner les clés. Clé vide = repli automatique, sans erreur.
- **Une clé LLM par agent** (`src/llm_keys.py`) : `GROQ_API_KEY_AGENT1` (profilage), `_AGENT2` (nettoyage), `_AGENT4` (KPI),
  `_AGENT3` (ETL, via Groq avec un modèle gpt-oss ; `ETL_LLM_PROVIDER=ollama` le remet sur Ollama Cloud avec `OLLAMA_API_KEY_AGENT3`), `_AGENT5` (dashboard, seulement en mode IA) et `_AGENT6` (Auditor). Un agent sans clé propre
  prend la clé partagée `GROQ_API_KEY` ; sans aucune clé il travaille avec ses règles, sans erreur.
  L'état de chaque clé (propre, partagée, absente) s'affiche en bas de la barre de gauche de l'appli, jamais la clé.
  Chez Groq les limites de débit sont comptées par compte et par modèle, pas par clé : pour vraiment répartir la charge, mettez
  les clés dans des comptes différents (ou un modèle différent par agent). `GROQ_MODEL` règle le modèle par défaut.
  `BI_FLOW_USE_LLM=false` coupe tous les appels LLM d'un coup.
- Les clés Groq et Ollama se saisissent aussi dans l'interface (valables pour le run en cours, non enregistrées) ; elles ne
  servent qu'aux agents qui n'ont pas de clé propre.
- Les agents 1, 2, 4 et 6 utilisent le LLM dès qu'ils ont une clé (le nettoyage en mode « auto » : LLM si l'Agent 2 a une clé,
  sinon règles). L'Agent 4 : le LLM propose le mapping des colonnes et rédige les constats, le code vérifie les colonnes et les
  chiffres (`KPI_USE_LLM=0` le coupe). L'Agent 6 : `AUDITOR_LLM_PROVIDER=auto` (Groq avec la clé de l'Agent 6, sinon Claude si
  `ANTHROPIC_API_KEY`, sinon preuves seules).
- `BI_FLOW_MODE=deterministic`, `BI_FLOW_LLM_PROVIDER=mock` : Agent 5 sans LLM. Le mode IA échoue sur Northwind (voir `src/agents/dashboard/README_dashboard.md`).
- `BI_FLOW_DEBUG_OUTPUTS=false` : l'Agent 5 n'écrit que le projet Power BI, `dashboard_plan.json` et `dashboard_generation_report.json`
  (`true` ajoute les fichiers de diagnostic). Avec un schéma en étoile, le rapport par rôle est déterministe : le mode IA est ignoré.
- `AUDITOR_LLM_PROVIDER` (`auto`, `groq`, `anthropic`, `none`, `hf`), `AUDITOR_GROQ_MODEL`, `AUDITOR_ANTHROPIC_MODEL` (défaut
  `claude-sonnet-5-5`), `AUDITOR_RETRIEVAL=auto` : Agent 6. Sans clé, les explications sont les preuves enregistrées, citées une à une ;
  avec une clé, le LLM ne fait que les reformuler et chaque réponse est vérifiée contre les preuves.

## Relier le résultat à Power BI Desktop
Onglet **Dashboard**, bouton « Projet Power BI (.zip) » (ou `dashboard/` dans le dossier du run) : dézipper, ouvrir `dashboard_generator.pbip`,
*Actualiser*. Les données sont dans `dashboard_generator.source/`. Si le dossier a été déplacé : `python relier_power_bi.py` dans le projet.
Détail : `LIRE_MOI_POWER_BI.md` (dans le zip). Le projet n'a jamais été ouvert dans Power BI Desktop (statut `PARTIAL_SUCCESS`).

Rapport final de l'Agent 6 : `auditor/rapport_audit.pdf`, téléchargé depuis l'onglet **Audit** (bouton « Rapport PDF »).

Pourquoi `serve.py` et pas `uvicorn api.app:app --reload` : ce dernier surveille tout le dépôt ; l'Agent 5 écrivait un `.py` dans
`outputs/runs/`, ce qui redémarrait le serveur au milieu du pipeline (étape tuée, console remplie de « Reloading »). Le `.py` n'est
plus écrit que dans le zip, et `serve.py --reload` ne surveille que `api/` et `src/`.

Tests : `python -m pytest tests src/agents/cleaning/tests`.
