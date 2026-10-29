"""Tests du planificateur LLM sans appel réel : un faux serveur Groq local répond à /models et /chat/completions."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import llm_planner  # noqa: E402
from agent import run  # noqa: E402

SAMPLES = HERE / "samples"
REQUESTS = []
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"  # le faux serveur est local


class FakeGroq(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.headers["Authorization"] == "Bearer test-key"
        self._send({"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "llama-3.1-8b-instant"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append(req)
        table = json.loads(req["messages"][1]["content"])
        decisions = []
        for c in table["candidates"]:
            refuse = c["tool"] == "normalize_country"
            decisions.append({"id": c["id"], "accept": not refuse,
                              "reason": "les alias de pays doivent être validés par le métier" if refuse else "correction sûre"})
        content = {"analysis": f"Table {table['table']} : référentiel clients, clé customerID.", "table_role": "dimension",
                   "decisions": decisions, "recommendations": ["Vérifier la ville manquante du client ligne 11"]}
        self._send({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]})


def test_planificateur_llm():
    srv = HTTPServer(("127.0.0.1", 0), FakeGroq)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    llm_planner.API_URL = f"http://127.0.0.1:{srv.server_port}"
    try:
        client = llm_planner.GroqClient(api_key="test-key")          # openai/gpt-oss-120b absent -> repli
        assert client.model == "llama-3.3-70b-versatile"
        with tempfile.TemporaryDirectory() as tmp:
            s = run([SAMPLES / "clients_sale.csv"], Path(tmp) / "clean", Path(tmp) / "hist",
                    planner=llm_planner.LLMPlanner(client))
            assert REQUESTS and REQUESTS[-1]["response_format"] == {"type": "json_object"}
            assert s["planner"]["model"] == "llama-3.3-70b-versatile"
            rejected = [x for x in s["steps"] if x["status"] == "rejected"]
            assert [x["tool"] for x in rejected] == ["normalize_country"]
            assert "LLM : les alias" in rejected[0]["reason"]
            assert s["tables"]["clients_sale"]["llm"]["table_role"] == "dimension"
            md = (Path(tmp) / "hist" / "runs" / s["run_id"] / "trace.md").read_text()
            assert "Analyse LLM" in md and "Vérifier la ville" in md
            html = (Path(tmp) / "hist" / "runs" / s["run_id"] / "comparaison.html").read_text()
            assert "Analyse du LLM" in html and "Non appliqué sur conseil du LLM" in html
            clients = s["view"]["tables"][0]
            assert clients["llm"]["analysis"].startswith("Table clients_sale")
            assert clients["llm_refused"][0]["reason"] == "les alias de pays doivent être validés par le métier"
    finally:
        srv.shutdown()


def test_repli_sans_cle():
    os.environ.pop("GROQ_API_KEY", None)
    llm_planner.load_env = lambda *a, **k: None  # ignorer un éventuel .env local
    with tempfile.TemporaryDirectory() as tmp:
        s = run([SAMPLES / "clients_sale.csv"], Path(tmp) / "c", Path(tmp) / "h", planner_mode="llm")
        assert s["planner"]["mode"] == "rules" and "GROQ_API_KEY" in s["planner"]["warning"]


if __name__ == "__main__":
    test_planificateur_llm()
    test_repli_sans_cle()
    print("OK")
