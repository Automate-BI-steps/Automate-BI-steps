"""Ligne de commande de l'Auditor.

    python -m src.agents.auditor.cli demo [--out DOSSIER]        # jeu simulé du notebook de Dhouha -> audit complet
    python -m src.agents.auditor.cli run  --run-dir DOSSIER_RUN   # audit d'un run du pipeline (lit state.json)
    python -m src.agents.auditor.cli ask  --run-dir DOSSIER_RUN "Pourquoi ... ?"   # question libre sur un run déjà audité
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--out", default="outputs/auditor_demo")
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--run-dir", required=True)
    a.add_argument("question")
    args = ap.parse_args(argv)

    from src.agents.auditor.adapter import PipelineAuditor
    from src.agents.auditor.core import Auditor
    if args.cmd == "demo":
        from src.agents.auditor.demo_data import Simulator
        Simulator(args.out).simulate()
        print(json.dumps(Auditor(args.out).run(), ensure_ascii=False, indent=1))
        return 0
    run_dir = Path(args.run_dir)
    if args.cmd == "run":
        from src.agents.auditor.step import run_auditor_step
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        res = run_auditor_step(state)["auditor"]
        print(json.dumps({k: v for k, v in res.items() if k in ("status", "summary", "artifacts", "errors")}, ensure_ascii=False, indent=1))
        return 0 if res["status"] != "ERROR" else 1
    aud = PipelineAuditor(run_dir / "auditor", log=lambda _m: None).prepare()
    text, check = aud.ask(args.question)
    print(text, "\n\n[vérification]", json.dumps(check, ensure_ascii=False), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
