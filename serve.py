"""Lance l'appli BI Flow (API FastAPI + interface compilée) : `python serve.py` puis http://127.0.0.1:8000.

`python serve.py --reload` recharge le serveur quand le code change, en ne surveillant QUE api/ et src/.
Avec `uvicorn api.app:app --reload`, tout le dépôt était surveillé : un fichier .py écrit dans outputs/runs/
(projet Power BI de l'Agent 5) redémarrait le serveur en plein pipeline, tuait l'étape en cours et
remplissait la console de « WatchFiles detected changes... Reloading ».
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Appli BI Flow")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="recharger quand le code de api/ ou src/ change (développement)")
    a = p.parse_args()
    uvicorn.run("api.app:app", host=a.host, port=a.port, reload=a.reload, log_level="warning",
                **({"reload_dirs": ["api", "src"], "reload_excludes": ["outputs/*", "client/*", "*.csv", "*.json"]} if a.reload else {}))


if __name__ == "__main__":
    main()
