"""Relie le projet Power BI généré (PBIP) à ses fichiers de données, puis l'ouvre si on le demande.

Le modèle sémantique lit trois CSV (FactTrade, BusinessInsights, DataQuality) dans un dossier donné par le
paramètre Power Query `SourceFolder`. Le générateur y écrit le chemin ABSOLU du dossier `*.source` tel qu'il
existe sur la machine qui a lancé le pipeline : si le projet est déplacé ou dézippé ailleurs, ce chemin ne mène plus aux données.
Ce script remet `SourceFolder` sur le dossier `*.source` situé à côté du projet.

    python relier_power_bi.py                    # le dossier du script est celui du projet
    python relier_power_bi.py CHEMIN\\DU\\PROJET   # ou un autre dossier
    python relier_power_bi.py --ouvrir           # ouvre ensuite le .pbip dans Power BI Desktop (Windows)
    python relier_power_bi.py --source D:\\csv    # CSV rangés ailleurs

Bibliothèque standard uniquement : ce fichier est copié tel quel dans chaque projet généré.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PARAM = "SourceFolder"
LITERAL = re.compile(r'^"(?:[^"]|"")*"')  # littéral texte M en tête de l'expression du paramètre


def _strings(node):
    """Toutes les chaînes du modèle (le JSON échappe les guillemets du code M : on lit donc le modèle décodé)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _strings(item)


def relink(project_dir: Path, source_dir: Path | None = None) -> dict:
    """Écrit le chemin du dossier de CSV dans model.bim. Retourne {model, source, files, missing}."""
    project_dir = Path(project_dir).resolve()
    models = sorted(project_dir.glob("*.SemanticModel/model.bim"))
    if not models:
        raise FileNotFoundError(f"Aucun *.SemanticModel/model.bim dans {project_dir} : ce n'est pas un projet Power BI généré.")
    model_path = models[0]
    if source_dir is None:
        found = sorted(p for p in project_dir.glob("*.source") if p.is_dir())
        if not found:
            raise FileNotFoundError(f"Aucun dossier *.source dans {project_dir} : indiquez-le avec --source.")
        source_dir = found[0]
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Dossier de CSV introuvable : {source_dir}")

    text = model_path.read_text(encoding="utf-8")
    model = json.loads(text)
    expressions = model.get("model", {}).get("expressions", [])
    param = next((e for e in expressions if e.get("name") == PARAM), None)
    if param is None or not isinstance(param.get("expression"), str):
        raise ValueError(f"Le modèle n'a pas de paramètre {PARAM} (au format attendu) : rien à relier.")
    folder = str(source_dir) + os.sep
    literal = '"' + folder.replace('"', '""') + '"'
    if not LITERAL.match(param["expression"]):
        raise ValueError(f"Expression inattendue pour {PARAM} : {param['expression'][:80]}")
    param["expression"] = LITERAL.sub(lambda _m: literal, param["expression"], count=1)

    files = sorted(set(re.findall(r'File\.Contents\(' + PARAM + r' & "([^"]+)"\)', "\n".join(_strings(model)))))
    missing = [f for f in files if not (source_dir / f).is_file()]
    model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return {"model": model_path, "source": source_dir, "files": files, "missing": missing}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Relie le projet Power BI généré à ses CSV.")
    ap.add_argument("projet", nargs="?", default=str(Path(__file__).resolve().parent), help="dossier contenant le .pbip (défaut : dossier du script)")
    ap.add_argument("--source", help="dossier des CSV (défaut : le dossier *.source du projet)")
    ap.add_argument("--ouvrir", action="store_true", help="ouvre le .pbip dans Power BI Desktop (Windows)")
    args = ap.parse_args(argv)
    try:
        res = relink(Path(args.projet), Path(args.source) if args.source else None)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    print(f"SourceFolder = {res['source']}{os.sep}")
    print(f"Fichiers attendus : {', '.join(res['files']) or '(aucun)'}")
    if res["missing"]:
        print(f"ATTENTION, introuvable(s) dans ce dossier : {', '.join(res['missing'])}", file=sys.stderr)
        return 2
    pbip = sorted(Path(args.projet).resolve().glob("*.pbip"))
    if args.ouvrir:
        if not pbip:
            print("Aucun fichier .pbip à ouvrir.", file=sys.stderr)
            return 1
        if hasattr(os, "startfile"):
            os.startfile(str(pbip[0]))  # noqa: S606 - ouverture par l'application associée (Power BI Desktop)
        else:
            print(f"Ouvrez {pbip[0]} dans Power BI Desktop (Windows).")
    elif pbip:
        print(f"Ouvrez maintenant {pbip[0].name} dans Power BI Desktop, puis cliquez sur Actualiser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
