"""Une clé LLM par agent, pour ne pas vider une seule clé API.

Chaque agent lit d'abord sa clé propre, sinon la clé partagée :

    Agent 1 Profilage   GROQ_API_KEY_AGENT1   (sinon GROQ_API_KEY)
    Agent 2 Nettoyage   GROQ_API_KEY_AGENT2   (sinon GROQ_API_KEY)
    Agent 3 ETL         GROQ_API_KEY_AGENT3   (sinon GROQ_API_KEY)
    Agent 4 KPI         GROQ_API_KEY_AGENT4   (sinon GROQ_API_KEY)
    Agent 5 Dashboard   GROQ_API_KEY_AGENT5   (sinon GROQ_API_KEY)
    Agent 6 Auditor     GROQ_API_KEY_AGENT6   (sinon GROQ_API_KEY)

Le code des agents lit toujours GROQ_API_KEY : `agent_env(n)` pose temporairement la clé de
l'agent n dans ces variables pendant son étape, puis remet l'état précédent. Une clé vide compte comme absente.
`BI_FLOW_USE_LLM=false` coupe tous les appels LLM (les agents travaillent alors avec leurs règles).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # ne remplace pas une variable déjà définie

# numéro d'agent -> nom affiché ; un seul fournisseur : Groq
AGENTS: dict[int, str] = {1: "Profilage", 2: "Nettoyage", 3: "ETL", 4: "KPI", 5: "Dashboard", 6: "Auditor"}
SHARED_VAR = "GROQ_API_KEY"


def _get(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def own_var(n: int) -> str:
    """Nom de la variable de la clé propre de l'agent n (ex. GROQ_API_KEY_AGENT6)."""
    return f"{SHARED_VAR}_AGENT{n}"


def agent_key(n: int) -> str:
    """Clé à utiliser pour l'agent n : la sienne, sinon la clé partagée, sinon ''."""
    return _get(own_var(n)) or _get(SHARED_VAR)


def key_source(n: int) -> str | None:
    """'agent' (clé propre), 'shared' (clé partagée) ou None (aucune clé)."""
    if _get(own_var(n)):
        return "agent"
    return "shared" if _get(SHARED_VAR) else None


def llm_allowed() -> bool:
    return _get("BI_FLOW_USE_LLM").lower() not in ("0", "false", "non", "no", "off")


def llm_enabled(n: int) -> bool:
    """Vrai si l'agent n peut appeler un LLM : une clé existe et le LLM n'est pas coupé globalement."""
    return llm_allowed() and bool(agent_key(n))


@contextmanager
def agent_env(n: int):
    """Pendant l'étape de l'agent n, GROQ_API_KEY vaut la clé de cet agent (si elle est définie)."""
    shared_var = SHARED_VAR
    own = _get(own_var(n))
    old = os.environ.get(shared_var)
    if own:
        os.environ[shared_var] = own
        _reset_caches()
    try:
        yield
    finally:
        if own:
            if old is None:
                os.environ.pop(shared_var, None)
            else:
                os.environ[shared_var] = old
            _reset_caches()


def _reset_caches() -> None:
    """Le LLM de l'Agent 1 est mis en cache avec sa clé : on le reconstruit quand la clé change."""
    try:
        from src import config

        config.get_llm.cache_clear()
    except Exception:  # noqa: BLE001 - dépendances absentes : rien à vider
        pass


def status() -> list[dict]:
    """État des clés par agent, pour l'interface (jamais la clé elle-même)."""
    return [{"agent": n, "name": name, "key": key_source(n), "var": own_var(n)} for n, name in AGENTS.items()]
