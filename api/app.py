"""Backend web BI Flow (FastAPI) : profilage (Agent 1) -> nettoyage (Agent 2) -> contrôle, pour l'interface React.

Lancement (depuis la racine du dépôt) :
    uvicorn api.app:app --reload          puis ouvrir http://127.0.0.1:8000  (interface compilée dans client/dist)
    cd client && npm run dev              interface en développement sur http://127.0.0.1:5173

Chaque étape est une commande de src/orchestrator.py lancée dans un sous-processus : les agents tournent
exactement comme en ligne de commande, et la clé Groq saisie dans l'interface ne vit que dans
l'environnement de ce sous-processus (jamais écrite). La mémoire du run est outputs/runs/<run_id>/state.json :
chaque agent la relit, y écrit sa section et une note de passage de relais (`timeline`).
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.agents.cleaning import llm_planner  # noqa: E402

RUNS_DIR = Path(os.environ.get("RUNS_DIR") or ROOT / "outputs" / "runs").resolve()
CLIENT_DIST = ROOT / "client" / "dist"
ALLOWED = {".csv", ".xlsx", ".xlsm"}
MAX_FILE_MB = 25
MAX_FILES = 20
RUN_ID = re.compile(r"^run_\d{8}T\d{6}_[0-9a-f]{6}$")
CHOICES = {"empty", "cap", "keep", "advice"}  # vider, ramener à la limite, garder, suivre le conseil

app = FastAPI(title="BI Flow - Profilage et nettoyage", version="3.0")
LIVE: dict[str, dict] = {}  # avancement des runs en cours (le reste est dans state.json)
LOCK = threading.Lock()


# --------------------------------------------------------------------------- utilitaires
def safe_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", Path(name or "fichier").name)[:120] or "fichier"


def run_dir(run_id: str) -> Path:
    if not RUN_ID.match(run_id) or not (RUNS_DIR / run_id).is_dir():
        raise HTTPException(404, "Run inconnu")
    return RUNS_DIR / run_id


def read_state(run_id: str) -> dict | None:
    try:
        return json.loads((run_dir(run_id) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # pas encore créé


def artifact(path: str | None) -> Path | None:
    """Fichier produit par un agent : uniquement sous la racine du dépôt."""
    if not path:
        return None
    p = Path(path).resolve()
    return p if p.is_file() and p.is_relative_to(ROOT) else None


def live_info(run_id: str) -> dict:
    with LOCK:
        info = LIVE.get(run_id)
        return {"running": info["running"], "step": info["step"], "error": info["error"], "log": info["log"][-20:]} \
            if info else {"running": False}


# --------------------------------------------------------------------------- exécution des agents
def step(run_id: str, command: str, args: list[str], env: dict[str, str]) -> dict:
    with LOCK:
        LIVE[run_id]["step"] = command
    proc = subprocess.run([sys.executable, "-m", "src.orchestrator", command, str(RUNS_DIR / run_id), *args],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **env})
    with LOCK:
        LIVE[run_id]["log"] += [f"[{command}] {line}" for line in proc.stderr.splitlines() if line.strip()][-50:]
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return {"ok": False, "status": "error", "error": f"Étape {command} : code {proc.returncode}"}


def chain(run_id: str, steps: list[tuple]) -> None:
    """Chaque étape : (commande, args, env) ou (commande, args, env, best_effort=True) pour une
    étape qui ne doit pas bloquer la suite si elle échoue (etl et kpi ne dépendent pas l'un de
    l'autre, voir src/graph.py)."""
    try:
        for command, args, env, *rest in steps:
            best_effort = rest[0] if rest else False
            res = step(run_id, command, args, env)
            if not res.get("ok") and not best_effort:
                with LOCK:
                    LIVE[run_id]["error"] = res.get("error") or f"Échec de l'étape {command}"
                return
            if res.get("status") == "waiting_user":  # l'utilisateur doit répondre avant la suite
                return
    finally:
        with LOCK:
            LIVE[run_id].update(running=False, step=None)


def launch(run_id: str, steps: list) -> bool:
    with LOCK:
        if LIVE.get(run_id, {}).get("running"):
            return False
        LIVE[run_id] = {"running": True, "step": None, "error": None, "log": LIVE.get(run_id, {}).get("log", [])}
    threading.Thread(target=chain, args=(run_id, steps), daemon=True).start()
    return True


# --------------------------------------------------------------------------- API
@app.get("/api/config")
def config():
    llm_planner.load_env()
    return {"allowed_extensions": sorted(ALLOWED), "max_file_mb": MAX_FILE_MB, "max_files": MAX_FILES,
            "groq_key_configured": bool(os.environ.get("GROQ_API_KEY")),
            "default_model": os.environ.get("GROQ_MODEL") or llm_planner.PREFERRED_MODELS[0],
            "models": llm_planner.PREFERRED_MODELS,
            "ollama_key_configured": bool(os.environ.get("OLLAMA_API_KEY"))}


@app.post("/api/runs")
async def create_run(files: list[UploadFile] = File(...), planner: str = Form("rules"), model: str = Form(""),
                     context: str = Form(""), impute: bool = Form(False), groq_api_key: str = Form(""),
                     ollama_api_key: str = Form("")):
    if planner not in {"llm", "rules"}:
        raise HTTPException(400, "planner doit valoir 'llm' ou 'rules'")
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"Déposez entre 1 et {MAX_FILES} fichiers")
    if model and model not in llm_planner.PREFERRED_MODELS:
        raise HTTPException(400, f"Modèle non proposé : {model}")
    data = []
    for f in files:
        name, content = safe_name(f.filename), await f.read()
        if Path(name).suffix.lower() not in ALLOWED:
            raise HTTPException(400, f"Format refusé pour {name} : seuls {', '.join(sorted(ALLOWED))} sont acceptés")
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(400, f"{name} dépasse {MAX_FILE_MB} Mo")
        if not content.strip():
            raise HTTPException(400, f"{name} est vide")
        data.append((name, content))
    run_id = f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
    uploads = RUNS_DIR / run_id / "uploads"
    uploads.mkdir(parents=True)
    for name, content in data:
        (uploads / name).write_bytes(content)

    init = ["--planner", planner, "--context", context[:2000]] + (["--model", model] if planner == "llm" and model else []) \
        + (["--impute"] if impute else [])
    # les clés saisies dans l'interface ne servent qu'aux sous-processus de ce run : elles ne sont écrites nulle part
    key_env = {"GROQ_API_KEY": groq_api_key.strip()} if planner == "llm" and groq_api_key.strip() else {}
    ollama_env = {"OLLAMA_API_KEY": ollama_api_key.strip()} if ollama_api_key.strip() else {}
    launch(run_id, [("init", init, {}), ("profile", [], key_env), ("clean", [], key_env), ("verify", [], {}),
                    ("etl", [], ollama_env, True), ("kpi", [], {}, True)])
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(RUNS_DIR.glob("run_*"), reverse=True)[:50] if RUNS_DIR.is_dir() else []:
        s = read_state(d.name)
        if not s:
            continue
        prof, ver = s.get("profiling", {}).get("summary", {}), s.get("cleaning", {}).get("verification", {})
        out.append({"run_id": d.name, "created_at": s.get("run", {}).get("created_at"), "status": s.get("run", {}).get("status"),
                    "files": s.get("input", {}).get("uploads", []), "tables": prof.get("tables_count"),
                    "issues": prof.get("issues_count"), "score_before": ver.get("score_before", prof.get("global_quality_score")),
                    "score_after": ver.get("score_after")})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    """State complet du run (mémoire partagée par les agents) + avancement en direct."""
    run_dir(run_id)
    return {"state": read_state(run_id), "live": live_info(run_id)}


@app.post("/api/runs/{run_id}/answers")
def answer_run(run_id: str, payload: dict = Body(...)):
    """Réponses aux questions sur les valeurs extrêmes : {"answers": {"q1": "empty" | "cap" | "keep" | "advice"}}."""
    state = read_state(run_id) or {}
    if state.get("run", {}).get("status") != "waiting_user":
        raise HTTPException(409, "Ce run n'attend pas de réponse")
    answers = payload.get("answers") or {}
    ids = {q["id"] for q in state.get("cleaning", {}).get("questions", [])}
    if not isinstance(answers, dict) or any(k not in ids or v not in CHOICES for k, v in answers.items()):
        raise HTTPException(400, "Chaque réponse doit valoir 'empty', 'cap', 'keep' ou 'advice'")
    if not launch(run_id, [("answer", ["--answers", json.dumps(answers)], {}), ("verify", [], {}),
                           ("etl", [], {}, True), ("kpi", [], {}, True)]):
        raise HTTPException(409, "Une étape est déjà en cours")
    return {"ok": True}


@app.get("/api/runs/{run_id}/view")
def get_view(run_id: str):
    """Vue simplifiée avant / après produite par l'agent de nettoyage."""
    p = artifact((read_state(run_id) or {}).get("cleaning", {}).get("artifacts", {}).get("view_json"))
    if not p:
        raise HTTPException(404, "Comparaison pas encore disponible")
    return Response(p.read_text(encoding="utf-8"), media_type="application/json")


FILES = {
    "report": (("cleaning", "report_html"), "text/html"),
    "profiling.md": (("profiling", "markdown_report"), "text/markdown"),
    "profiling.json": (("profiling", "json_report"), "application/json"),
    "trace.md": (("cleaning", "trace_md"), "text/markdown"),
}


@app.get("/api/runs/{run_id}/files/{name}")
def get_file(run_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "Fichier inconnu")
    (section, key), media = FILES[name]
    p = artifact((read_state(run_id) or {}).get(section, {}).get("artifacts", {}).get(key))
    if not p:
        raise HTTPException(404, "Fichier pas encore produit")
    text = p.read_text(encoding="utf-8")
    return HTMLResponse(text) if media == "text/html" else PlainTextResponse(text, media_type=f"{media}; charset=utf-8")


@app.get("/api/runs/{run_id}/state.json")
def download_state(run_id: str):
    p = run_dir(run_id) / "state.json"
    if not p.is_file():
        raise HTTPException(404, "Run inconnu")
    return FileResponse(p, media_type="application/json", filename=f"state_{run_id}.json")


@app.get("/api/runs/{run_id}/download")
def download(run_id: str):
    """ZIP : tables nettoyées, rapport de profilage, comparaison avant / après, trace et state."""
    state = read_state(run_id) or {}
    clean = state.get("cleaning", {}).get("clean_data_dir")
    if not clean or not Path(clean).is_dir():
        raise HTTPException(404, "Nettoyage pas encore terminé")
    prof_a, clean_a = state.get("profiling", {}).get("artifacts", {}), state.get("cleaning", {}).get("artifacts", {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(clean).glob("*.csv")):
            z.write(p, f"tables_nettoyees/{p.name}")
        for path, name in [(prof_a.get("json_report"), "profiling_report.json"), (prof_a.get("markdown_report"), "profiling_report.md"),
                           (clean_a.get("report_html"), "comparaison.html"), (clean_a.get("trace_md"), "trace_nettoyage.md")]:
            if p := artifact(path):
                z.write(p, name)
        z.write(run_dir(run_id) / "state.json", "state.json")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="bi_flow_{run_id}.zip"'})


# --------------------------------------------------------------------------- interface React compilée
if CLIENT_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=CLIENT_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    index = CLIENT_DIST / "index.html"
    if path.startswith("api/"):
        raise HTTPException(404, "Route inconnue")
    if not index.is_file():
        return PlainTextResponse("Interface non compilée : cd client && npm install && npm run build "
                                 "(ou npm run dev pour le mode développement).")
    return FileResponse(index)
