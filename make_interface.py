"""Lance l'agent 4 et ouvre son interface dans le navigateur.

    python make_interface.py "C:\\chemin\\vers\\data"

Crée  outputs\\interface.html  (page autonome : les résultats sont intégrés dans la page).
"""
import json
import os
import sys
import webbrowser
from pathlib import Path

from agent import en_json, run
from verify_kpis import verifier

ICI = Path(__file__).parent


def main():
    chemin = sys.argv[1] if len(sys.argv) > 1 else "data"
    etat = run(chemin)
    if not etat.get("kpi_results"):
        print("Aucun KPI calculé. Erreurs :", etat.get("errors"))
        return
    reference = ICI / "reference_powerbi.csv"                # à remplir avec ton rapport Power BI manuel
    justesse = verifier(etat["kpi_results"], chemin, reference)
    donnees = en_json({"res": etat["kpi_results"], "cat": etat["kpi_catalog"], "aud": etat["audit_log"],
                       "acc": justesse})
    contenu = json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/")
    page = (ICI / "interface_template.html").read_text("utf-8")
    page = page.replace("<script>\nconst $=", f"<script>window.__DATA__={contenu};</script>\n<script>\nconst $=", 1)
    dossier = Path(os.getenv("OUTPUT_DIR", "outputs"))
    dossier.mkdir(parents=True, exist_ok=True)
    sortie = dossier / "interface.html"
    sortie.write_text(page, "utf-8")
    print(f"Interface créée : {sortie.resolve()}")
    webbrowser.open(sortie.resolve().as_uri())


if __name__ == "__main__":
    main()
