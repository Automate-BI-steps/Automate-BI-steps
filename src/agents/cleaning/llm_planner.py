"""Outil LLM de l'agent de nettoyage : analyse une table et choisit les outils à appliquer (API Groq).

Principe (garde-fous pour la traçabilité) :
  1. les règles de tools.py proposent des candidats (outil, colonne, raison) en simulation ;
  2. le LLM reçoit le profil de la table et ces candidats, écrit une analyse et accepte ou refuse chaque candidat ;
  3. l'agent n'applique que les outils du registre : le LLM ne peut ni inventer un outil ni injecter de valeurs,
     il choisit. Chaque verdict et sa justification sont tracés.
Si la clé est absente ou si l'API échoue, l'agent repasse en mode règles et le trace.

Configuration (variables d'environnement ou fichier .env à la racine du projet) :
  GROQ_API_KEY : clé Groq (https://console.groq.com/keys)
  GROQ_MODEL   : modèle souhaité (défaut : openai/gpt-oss-120b). S'il n'est pas disponible sur le compte,
                 le premier modèle disponible de PREFERRED_MODELS est utilisé.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

try:  # paquet src.agents.cleaning
    from .tools import ROW_ID, TOOLS, data_cols
except ImportError:  # script lancé depuis ce dossier
    from tools import ROW_ID, TOOLS, data_cols

API_URL = "https://api.groq.com/openai/v1"
PREFERRED_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
ROOT = Path(__file__).resolve().parents[3]  # racine du dépôt (.env)


def load_env(path: Path = ROOT / ".env") -> None:
    """Charge un fichier .env simple (CLE=valeur) sans dépendance externe."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            os.environ.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 60):
        load_env()
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY absente (variable d'environnement ou fichier .env)")
        self.timeout = timeout
        self.model = self._pick_model(model or os.environ.get("GROQ_MODEL") or PREFERRED_MODELS[0])

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{API_URL}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "bi-flow-cleaning-agent/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Groq HTTP {e.code} : {e.read().decode('utf-8', 'replace')[:300]}") from e

    def _pick_model(self, wanted: str) -> str:
        """Vérifie que le modèle existe sur le compte (GET /models), sinon prend le premier disponible."""
        available = {m["id"] for m in self._request("GET", "/models").get("data", [])}
        for m in [wanted] + PREFERRED_MODELS:
            if m in available:
                return m
        raise RuntimeError(f"Aucun modèle attendu disponible chez Groq. Modèles du compte : {sorted(available)[:10]}")

    def chat_json(self, system: str, user: str) -> dict:
        body = {"model": self.model, "temperature": 0.1, "max_completion_tokens": 4096,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        try:
            data = self._request("POST", "/chat/completions", body)
        except RuntimeError as e:
            if "response_format" not in str(e) and "json" not in str(e).lower():
                raise
            body.pop("response_format")  # modèle sans mode JSON : on demande du JSON dans le texte
            data = self._request("POST", "/chat/completions", body)
        return parse_json(data["choices"][0]["message"]["content"])


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0) if m else text)


# --------------------------------------------------------------------------- prompt
SYSTEM_PROMPT = """Tu es l'agent Quality & Cleaning d'un pipeline Power BI (projet BI Flow).
Tu analyses UNE table et tu décides quels outils de nettoyage appliquer pour la rendre prête pour un ETL
(noms de colonnes propres, bons types, clés de jointure cohérentes, taux entre 0 et 1, textes composites décomposés).
Règles :
- Tu choisis uniquement parmi les candidats fournis (outil + colonne) : accepte ou refuse chacun.
- Refuse une correction si elle risque de détruire de l'information métier (ex. convertir un code avec zéros initiaux en nombre, fusionner deux catégories réellement différentes, lire une date au mauvais format).
- Les outils de type "flag" ne modifient rien : garde-les acceptés sauf s'ils sont manifestement non pertinents.
- split_packaging garde la colonne d'origine et ajoute des colonnes (nombre d'unités, taille, unité, contenant, quantité totale en g ou ml) : accepte-le si la colonne décrit un conditionnement.
- Les candidats de type "ask" (valeurs extrêmes) seront soumis à l'utilisateur : accept=true signifie que tu lui conseilles de corriger (mettre à vide une erreur de saisie comme 9999, sinon ramener à la limite), accept=false de garder les valeurs (ex. prix de luxe réels). Explique ton avis simplement.
- Si "profiling_issues" est fourni, c'est le diagnostic de l'agent de profilage (problème, gravité, action suggérée) : appuie-toi dessus pour accepter les corrections qui y répondent, sans t'y limiter.
- Justifie chaque décision en une phrase, en français.
- Réponds uniquement en JSON avec ce schéma :
{"analysis": "3 à 6 phrases : nature de la table, grain probable, clés, qualité, risques",
 "table_role": "fait | dimension | référence | inconnu",
 "decisions": [{"id": <entier du candidat>, "accept": true|false, "reason": "..."}],
 "recommendations": ["conseils courts pour la suite, hors outils automatiques"]}"""


def profile_table(df: pd.DataFrame, max_samples: int = 6) -> list[dict]:
    rows = []
    for c in data_cols(df):
        s = df[c]
        row = {"column": c, "dtype": str(s.dtype), "nulls": int(s.isna().sum()),
               "distinct": int(s.nunique(dropna=True)),
               "samples": [str(v)[:40] for v in s.dropna().drop_duplicates().head(max_samples)]}
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and s.notna().any():
            x = s.dropna().astype(float)
            row.update({"min": round(float(x.min()), 2), "median": round(float(x.median()), 2), "max": round(float(x.max()), 2)})
        rows.append(row)
    return rows


PROFILING_FIELDS = ("issue_id", "column", "issue_type", "severity", "description", "suggested_action", "is_expected")


def build_prompt(name: str, source: str, df: pd.DataFrame, candidates: list[dict],
                 profiling_issues: list[dict] | None = None) -> str:
    catalog = [{"tool": t.name, "label": t.label, "kind": t.kind, "scope": t.scope} for t in TOOLS]
    payload = {"table": name, "source": source, "rows": len(df), "columns": profile_table(df),
               "tool_catalog": catalog, "candidates": candidates}
    if profiling_issues:
        payload["profiling_issues"] = [{k: i.get(k) for k in PROFILING_FIELDS} for i in profiling_issues]
    return json.dumps(payload, ensure_ascii=False, indent=1, default=str)


class LLMPlanner:
    """Planificateur LLM : renvoie l'analyse de la table et un verdict par candidat."""

    def __init__(self, client: GroqClient):
        self.client = client

    @property
    def model(self) -> str:
        return self.client.model

    def plan(self, name: str, source: str, df: pd.DataFrame, candidates: list[dict],
             profiling_issues: list[dict] | None = None) -> dict:
        result = self.client.chat_json(SYSTEM_PROMPT, build_prompt(name, source, df.drop(columns=[ROW_ID], errors="ignore"),
                                                                   candidates, profiling_issues))
        verdicts = {}
        for d in result.get("decisions", []):
            try:
                verdicts[int(d["id"])] = (bool(d.get("accept", True)), str(d.get("reason", "")))
            except (KeyError, TypeError, ValueError):
                continue
        return {"analysis": str(result.get("analysis", "")), "table_role": str(result.get("table_role", "inconnu")),
                "recommendations": [str(r) for r in result.get("recommendations", [])], "verdicts": verdicts}
