"""Intégration profilage -> nettoyage : la mémoire (state) passe d'un agent à l'autre.

    python -m pytest tests
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.agents.cleaning.profiling_link import resolve_issues

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "src" / "agents" / "cleaning" / "samples"


def test_resolve_issues_relie_profilage_et_nettoyage():
    profiling = {"issues": [
        {"issue_id": "ISS-001", "table": "t", "column": "prix", "issue_type": "type_mismatch", "severity": "high", "description": "d"},
        {"issue_id": "ISS-002", "table": "t", "column": None, "issue_type": "encoding_error", "severity": "medium", "description": "d"},
        {"issue_id": "ISS-003", "table": "t", "column": "x", "issue_type": "typo_suspected", "severity": "low", "description": "d"}]}
    steps = [{"step": 1, "table": "t", "tool": "cast_numeric", "label": "n", "column": "prix", "status": "applied", "values_changed": 3}]
    res = resolve_issues(profiling, {"steps": steps}, {"issues": []})
    status = {i["issue_id"]: i["status"] for i in res["items"]}
    assert status == {"ISS-001": "fixed", "ISS-002": "fixed_at_read", "ISS-003": "resolved"}


def test_run_complet_garde_la_memoire(tmp_path, monkeypatch):
    import api.app as app_module

    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    run = tmp_path / "run_20260101T000000_abcdef"
    (run / "uploads").mkdir(parents=True)
    for f in ("clients_sale.csv", "ventes_sale.xlsx"):
        (run / "uploads" / f).write_bytes((SAMPLES / f).read_bytes())
    import subprocess, sys

    def step(cmd, *args):
        out = subprocess.run([sys.executable, "-m", "src.orchestrator", cmd, str(run), *args], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8")
        return json.loads(out.stdout.strip().splitlines()[-1])

    assert step("init")["ok"] and step("profile")["status"] == "profiled"
    waiting = step("clean")
    assert waiting["status"] == "waiting_user"
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert state["profiling"]["summary"]["issues_count"] > 0          # le profilage est dans la mémoire
    assert state["cleaning"]["questions"] and (run / "cleaning_session.pkl").exists()
    qid = state["cleaning"]["questions"][0]["id"]
    assert step("answer", "--answers", json.dumps({qid: "keep"}))["status"] == "cleaned"
    assert step("verify")["status"] == "done"
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert [t["agent"] for t in state["timeline"]] == ["orchestrator", "profiling", "cleaning", "user", "cleaning", "verification"]
    assert state["cleaning"]["profiling_run_id"] == state["profiling"]["metadata"]["run_id"]
    assert state["cleaning"]["verification"]["score_after"] > state["cleaning"]["verification"]["score_before"]

    client = TestClient(app_module.app)
    got = client.get(f"/api/runs/{run.name}").json()
    assert got["state"]["run"]["status"] == "done"
    assert client.get(f"/api/runs/{run.name}/view").status_code == 200
    assert client.get(f"/api/runs/{run.name}/download").headers["content-type"] == "application/zip"
    assert client.get("/api/runs/..%2f..%2fetc").status_code in (404, 422)


def test_detect_dialect_ne_plante_pas_sur_un_texte_piege():
    """csv.Sniffer.has_header lève une ValueError sur certains textes : le profilage ne doit pas échouer."""
    from src.agents.profiling.readers import detect_dialect

    piege = '\t;" \t  \r\na",a;\r\n1"b'
    delimiter, has_header = detect_dialect(piege)
    assert delimiter in ",;\t|" and isinstance(has_header, bool)
