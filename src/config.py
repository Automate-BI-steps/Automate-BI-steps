"""Configuration globale du projet : chemins, variables d'environnement, LLM."""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Utilise le magasin de certificats Windows : évite les erreurs SSL
# quand un antivirus ou un proxy inspecte les connexions HTTPS.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

DEFAULT_DATA_DIR = PROJECT_ROOT / "Datasets" / "Import&Export" / "data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


@lru_cache
def get_llm():
    """Retourne le LLM Groq, ou None si aucune clé n'est configurée."""
    if not os.getenv("GROQ_API_KEY"):
        return None
    from langchain_groq import ChatGroq

    return ChatGroq(model=GROQ_MODEL, temperature=0, max_retries=2)
