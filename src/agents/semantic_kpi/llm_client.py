"""Modèle de langage de l'Agent 4 (mapping des colonnes et rédaction des constats) : Groq.

Clé propre de l'Agent 4 (GROQ_API_KEY_AGENT4, sinon GROQ_API_KEY ; voir src/llm_keys.py : elle arrive ici dans GROQ_API_KEY).
Le principe de l'agent ne change pas : le LLM propose, le code vérifie (colonnes existantes, chiffres autorisés),
et toute erreur d'appel ramène aux règles.
"""
from __future__ import annotations

import os


def make_llm():
    from langchain_groq import ChatGroq

    key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY absente pour l'Agent 4 (GROQ_API_KEY_AGENT4 ou GROQ_API_KEY)")
    model = os.environ.get("KPI_GROQ_MODEL") or os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
    extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
    return ChatGroq(model=model, temperature=0, api_key=key, max_retries=1, timeout=45, **extra)
