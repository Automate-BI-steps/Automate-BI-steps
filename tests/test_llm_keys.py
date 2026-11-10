"""Une clé LLM par agent : priorité, repli, interrupteur général, branchement des agents 4 et 6 et de l'appli."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import llm_keys

VARS = ["GROQ_API_KEY", "ANTHROPIC_API_KEY", "BI_FLOW_USE_LLM", "KPI_USE_LLM", "KPI_LLM_PROVIDER",
        "KPI_GROQ_MODEL", "GROQ_MODEL", "AUDITOR_LLM_PROVIDER", "AUDITOR_GROQ_MODEL"] + \
       [f"GROQ_API_KEY_AGENT{n}" for n in (1, 2, 3, 4, 5, 6)] + ["ETL_GROQ_MODEL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


def test_la_cle_propre_l_emporte_sur_la_cle_partagee(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "partagee")
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "cle6")
    assert llm_keys.agent_key(6) == "cle6" and llm_keys.key_source(6) == "agent"
    assert llm_keys.agent_key(2) == "partagee" and llm_keys.key_source(2) == "shared"


def test_cle_vide_ou_blanche_compte_comme_absente(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_AGENT1", "   ")
    assert llm_keys.agent_key(1) == "" and llm_keys.key_source(1) is None
    assert not llm_keys.llm_enabled(1)


def test_l_agent_3_utilise_une_cle_groq(monkeypatch):
    assert llm_keys.agent_key(3) == ""
    monkeypatch.setenv("GROQ_API_KEY", "groq")
    assert llm_keys.agent_key(3) == "groq" and llm_keys.key_source(3) == "shared"
    monkeypatch.setenv("GROQ_API_KEY_AGENT3", "groq3")
    assert llm_keys.agent_key(3) == "groq3" and llm_keys.own_var(3) == "GROQ_API_KEY_AGENT3"


def test_interrupteur_general(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_AGENT4", "k")
    assert llm_keys.llm_enabled(4)
    for off in ("false", "0", "non"):
        monkeypatch.setenv("BI_FLOW_USE_LLM", off)
        assert not llm_keys.llm_enabled(4)


def test_agent_env_pose_puis_restaure_la_cle(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "partagee")
    monkeypatch.setenv("GROQ_API_KEY_AGENT4", "cle4")
    with llm_keys.agent_env(4):
        assert os.environ["GROQ_API_KEY"] == "cle4"
    assert os.environ["GROQ_API_KEY"] == "partagee"
    with llm_keys.agent_env(2):  # pas de clé propre : la clé partagée reste en place
        assert os.environ["GROQ_API_KEY"] == "partagee"


def test_agent_env_sans_cle_partagee_retire_la_variable(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "cle6")
    with llm_keys.agent_env(6):
        assert os.environ["GROQ_API_KEY"] == "cle6"
    assert "GROQ_API_KEY" not in os.environ


def test_agent_env_restaure_meme_si_l_etape_echoue(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "partagee")
    monkeypatch.setenv("GROQ_API_KEY_AGENT5", "cle5")
    with pytest.raises(RuntimeError):
        with llm_keys.agent_env(5):
            raise RuntimeError("boom")
    assert os.environ["GROQ_API_KEY"] == "partagee"


def test_status_ne_contient_jamais_la_cle(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "gsk_secret_value")
    rows = llm_keys.status()
    assert [r["agent"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert next(r for r in rows if r["agent"] == 6)["key"] == "agent"
    assert "gsk_secret_value" not in repr(rows)


# ------------------------------------------------------------------------------------------ Agent 6
def test_auditor_auto_choisit_groq_avec_la_cle_de_l_agent_6(monkeypatch):
    from src.agents.auditor.core import AuditorConfig, resolve_provider

    assert resolve_provider(None) == "none"  # aucune clé : preuves seules
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert resolve_provider("auto") == "anthropic"
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "g")
    assert resolve_provider("auto") == "groq"
    assert AuditorConfig.from_env().llm_provider == "groq"
    monkeypatch.setenv("AUDITOR_LLM_PROVIDER", "anthropic")  # un choix explicite est respecté
    assert AuditorConfig.from_env().llm_provider == "anthropic"
    monkeypatch.setenv("BI_FLOW_USE_LLM", "false")
    assert resolve_provider("groq") == "none" and resolve_provider("auto") == "none"


def test_auditor_groq_se_construit_avec_la_cle_de_l_agent_6(monkeypatch):
    from src.agents.auditor.core import LLM, AuditorConfig

    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "gsk_agent6")
    cfg = AuditorConfig(llm_provider="groq", groq_model="openai/gpt-oss-120b")
    with llm_keys.agent_env(6):
        llm = LLM(cfg).load()
    assert llm.client.groq_api_key.get_secret_value() == "gsk_agent6"
    assert llm.client.reasoning_effort == "low" and llm.client.max_tokens > cfg.max_new_tokens
    with pytest.raises(RuntimeError, match="GROQ_API_KEY absente"):
        LLM(cfg).load()  # hors de l'étape : aucune clé


# ------------------------------------------------------------------------------------------ Agent 4
def test_agent_4_active_le_llm_seulement_avec_une_cle(monkeypatch):
    from src.agents.semantic_kpi.node import _use_llm

    assert not _use_llm()
    monkeypatch.setenv("GROQ_API_KEY_AGENT4", "k")
    assert _use_llm()
    monkeypatch.setenv("KPI_USE_LLM", "0")
    assert not _use_llm()
    monkeypatch.setenv("KPI_USE_LLM", "1")
    monkeypatch.delenv("GROQ_API_KEY_AGENT4")
    assert _use_llm()  # forcé (Ollama local possible)
    monkeypatch.setenv("BI_FLOW_USE_LLM", "false")
    assert not _use_llm()


def test_agent_4_groq_utilise_la_cle_de_l_etape(monkeypatch):
    import sys

    kpi_dir = str(Path(__file__).resolve().parent.parent / "src" / "agents" / "semantic_kpi")
    monkeypatch.syspath_prepend(kpi_dir)
    sys.modules.pop("llm_client", None)
    from llm_client import make_llm

    with pytest.raises(RuntimeError, match="GROQ_API_KEY absente"):
        make_llm()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_kpi")
    llm = make_llm()
    assert llm.groq_api_key.get_secret_value() == "gsk_kpi" and llm.model_name == "openai/gpt-oss-120b"
    monkeypatch.setenv("KPI_GROQ_MODEL", "openai/gpt-oss-20b")
    assert make_llm().model_name == "openai/gpt-oss-20b"


# ------------------------------------------------------------------------------------------ orchestrateur et appli
def test_l_orchestrateur_donne_a_chaque_etape_la_cle_de_son_agent(monkeypatch):
    from src import orchestrator

    monkeypatch.setenv("GROQ_API_KEY", "partagee")
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "cle6")
    seen = {}
    monkeypatch.setattr(orchestrator, "run_auditor_step", lambda state: seen.setdefault("auditor", os.environ["GROQ_API_KEY"]) and
                        {"auditor": {"status": "OK"}})
    monkeypatch.setattr(orchestrator, "load_state", lambda run_dir: {"run": {}, "errors": [], "timeline": []})
    monkeypatch.setattr(orchestrator, "save_state", lambda run_dir, state: None)
    orchestrator.cmd_auditor(Path("."), None)
    assert seen["auditor"] == "cle6"
    assert os.environ["GROQ_API_KEY"] == "partagee"


def test_api_config_expose_l_etat_des_cles_sans_les_cles(monkeypatch):
    from api import app as app_module

    monkeypatch.setenv("GROQ_API_KEY", "gsk_shared_secret")
    monkeypatch.setenv("GROQ_API_KEY_AGENT6", "gsk_agent6_secret")
    got = TestClient(app_module.app).get("/api/config").json()
    by_agent = {a["agent"]: a["key"] for a in got["llm_agents"]}
    assert by_agent[6] == "agent" and by_agent[2] == "shared" and by_agent[3] == "shared"
    assert got["default_planner"] == "llm" and got["auditor_llm"] == "groq" and got["llm_enabled"] is True
    assert "secret" not in str(got)


def test_api_planner_auto_suit_la_presence_d_une_cle(monkeypatch, tmp_path):
    from api import app as app_module

    monkeypatch.setattr(app_module, "launch", lambda run_id, steps: True)
    monkeypatch.setattr(app_module, "RUNS_DIR", tmp_path)
    client = TestClient(app_module.app)
    send = lambda: client.post("/api/runs", files={"files": ("t.csv", b"a,b\n1,2\n")}).json()["run_id"]  # noqa: E731

    seen = {}
    monkeypatch.setattr(app_module, "launch", lambda run_id, steps: seen.update(init=steps[0][1]) or True)
    send()
    assert seen["init"][:2] == ["--planner", "rules"]
    monkeypatch.setenv("GROQ_API_KEY_AGENT2", "k")
    send()
    assert seen["init"][:2] == ["--planner", "llm"]


# ------------------------------------------------------------------------------------------ Agent 3 sur Groq
def _fake_run(captured):
    import json
    import subprocess

    def run(cmd, cwd=None, env=None, capture_output=None, text=None, timeout=None):
        captured.update(cmd=cmd, env=env)
        out = json.dumps({"ok": False, "fact_reasoning_error": "GROQ_API_KEY is not set. Configure your Groq API key.",
                          "fact_reasoning_attempts": 1, "tables": {}, "validation": {}})
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
    return run


def test_etl_appelle_groq_avec_la_cle_de_l_agent_3(monkeypatch, tmp_path):
    from src.agents.etl import node

    monkeypatch.setenv("GROQ_API_KEY_AGENT3", "gsk_etl")
    seen = {}
    monkeypatch.setattr(node.subprocess, "run", _fake_run(seen))
    with llm_keys.agent_env(3):
        res = node.run_etl({"input": {"work_dir": str(tmp_path), "source_dir": str(tmp_path)}})
    cmd = seen["cmd"]
    assert cmd[cmd.index("--llm-base-url") + 1] == "https://api.groq.com/openai/v1"
    assert cmd[cmd.index("--reasoning-model") + 1] == "openai/gpt-oss-120b"
    assert seen["env"]["GROQ_API_KEY"] == "gsk_etl"
    assert res["etl"]["status"] == "error"


def test_etl_sans_cle_groq_donne_un_message_clair(monkeypatch, tmp_path):
    from src.agents.etl import node

    seen = {}
    monkeypatch.setattr(node.subprocess, "run", _fake_run(seen))
    res = node.run_etl({"input": {"work_dir": str(tmp_path), "source_dir": str(tmp_path)}})
    assert seen["env"]["GROQ_API_KEY"] == ""
    assert "GROQ_API_KEY_AGENT3" in res["timeline"][0]["summary"]


def test_etl_n_a_plus_aucune_trace_d_ollama():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sources = [*root.joinpath("src").rglob("*.py"), *root.joinpath("api").rglob("*.py"), root / ".env.example"]
    found = [str(p.relative_to(root)) for p in sources if "ollama" in p.read_text(encoding="utf-8", errors="ignore").lower()
             and p.name != "test_llm_keys.py"]
    assert not found, found
