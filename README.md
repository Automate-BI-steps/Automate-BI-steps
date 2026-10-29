# BI Flow : profilage -> nettoyage, avec interface React

Pipeline en deux agents qui partagent un même **state** (mémoire) :

| Agent | Code | Écrit dans le state |
|---|---|---|
| 1. Profilage (Chiheb), inchangé | `src/agents/profiling/` | `profiling` : rapport JSON (tables, colonnes, relations, problèmes, score) |
| 2. Cleaning / transforming (Bedis) | `src/agents/cleaning/` | `cleaning` : tables propres, journal des corrections, questions, bilan |

```
CSV / Excel -> [profilage] -> profiling (JSON + chemins des CSV) -> [nettoyage] -> [contrôle] -> tables propres
```

## Mémoire (state)
- Le state est défini dans `src/state.py` (sections `run`, `input`, `profiling`, `cleaning`, `timeline`, `errors`).
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
cp .env.example .env            # GROQ_API_KEY facultative (sans clé : règles seules)

# ligne de commande
python main.py --outliers advice

# application web : backend Python (FastAPI) + frontend React
cd client && npm install && npm run build && cd ..
uvicorn api.app:app --reload     # http://127.0.0.1:8000

# développement du frontend (rechargement à chaud)
cd client && npm run dev         # http://127.0.0.1:5173 (API proxifiée vers le port 8000)
```
`python -m src.orchestrator all <dossier_run>` exécute les étapes une par une (`init`, `profile`, `clean`, `answer`, `verify`).

## Interface
Quatre onglets : **Profilage** (diagnostic de l'Agent 1), **Nettoyage** (validation des valeurs extrêmes puis comparaison
avant / après), **Bilan** (score avant / après, état de chaque problème), **Mémoire** (passages de relais et state explorable).

Tests : `python -m pytest tests src/agents/cleaning/tests`.
