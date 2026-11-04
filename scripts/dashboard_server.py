"""Local server for the generated dashboard: LLM questions and reviews.

Run from the repository root:
    .venv\\Scripts\\python.exe dashboard_server.py
then open http://127.0.0.1:8765

Environment (all optional):
    BI_FLOW_LLM_PROVIDER   'mock' (default, no LLM), 'gemini' (Google Gemini) or 'custom' (OpenAI-compatible endpoint)
    BI_FLOW_LLM_MODEL      model name, e.g. gpt-oss:20b
    BI_FLOW_LLM_BASE_URL   endpoint root, e.g. https://ollama.com/v1
    LLM_API_KEY            API key, read from the environment only (never stored in files)
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.agents.dashboard.config import GeneratorConfig
from src.agents.dashboard.dashboard_feedback import FeedbackLogger
from src.agents.dashboard.dashboard_planner import GeminiLLMProvider, HTTPLLMProvider, LLMProvider, MetaResponsesProvider

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "generated"
HOST, PORT = "127.0.0.1", 8765
MAX_QUESTION = 500


def build_context() -> dict[str, Any]:
    """Facts the LLM may use, read from the last generated report and the upstream inputs."""
    report = json.loads((GENERATED / "dashboard_generation_report.json").read_text(encoding="utf-8"))
    plan = report.get("dashboard_plan") or {}
    meta = plan.get("metadata") or {}
    findings = json.loads((ROOT / "input" / "bi_analyst_findings.json").read_text(encoding="utf-8"))
    quality = json.loads((ROOT / "input" / "data_quality_report.json").read_text(encoding="utf-8"))
    return {
        "dashboard_title": plan.get("dashboard_title"),
        "target_audience": plan.get("target_audience"),
        "pages": report.get("report_pages", []),
        "kpi_values": report.get("expected_kpi_values", {}),
        "copilot_spec": meta.get("copilot_spec", {}),
        "candidates": meta.get("all_candidates", []),
        "critic_status": meta.get("critic_status"),
        "analyst_findings": findings.get("findings", []),
        "recommendations": findings.get("recommendations", []),
        "data_quality": quality.get("status"),
    }


def make_provider() -> LLMProvider | None:
    cfg = GeneratorConfig.from_env()
    if cfg.llm_provider == "mock":
        return None
    if cfg.llm_provider == "gemini":
        return GeminiLLMProvider(api_key=os.environ.get("LLM_API_KEY"), model=cfg.llm_model, timeout=cfg.timeout_seconds)
    if cfg.llm_provider == "meta":
        return MetaResponsesProvider(api_key=cfg.api_key, model=cfg.llm_model, timeout=cfg.timeout_seconds)
    return HTTPLLMProvider(
        api_key=cfg.api_key,
        base_url=cfg.api_base_url or "https://api.openai.com/v1",
        model=cfg.llm_model,
        timeout=cfg.timeout_seconds,
    )


SYSTEM_PROMPT = (
    "You answer questions about a trade BI dashboard. Use only the JSON context provided. "
    "If the context does not contain the answer, say that it is not in the dashboard. "
    "Answer in the language of the question, in plain sentences."
)


def answer_question(question: str) -> dict[str, Any]:
    question = question.strip()[:MAX_QUESTION]
    if not question:
        return {"error": "Empty question."}
    provider = make_provider()
    if provider is None:
        return {"error": "No LLM configured (BI_FLOW_LLM_PROVIDER is 'mock'). Set custom provider settings to ask questions."}
    context = json.dumps(build_context(), ensure_ascii=False, indent=1)
    prompt = f"Dashboard context:\n{context}\n\nQuestion: {question}"
    try:
        return {"answer": provider.generate(prompt, system_prompt=SYSTEM_PROMPT).strip()}
    except Exception as exc:  # network, auth, payment, timeout: report, never crash the server
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def save_review(accepted: bool, comment: str) -> dict[str, Any]:
    logger = FeedbackLogger(path=GENERATED / "optimization_feedback.jsonl", enabled=True)
    report = json.loads((GENERATED / "dashboard_generation_report.json").read_text(encoding="utf-8"))
    plan = report.get("dashboard_plan") or {}
    meta = plan.get("metadata") or {}
    logger.log_user_feedback(
        run_id=report.get("run_id", ""),
        accepted=accepted,
        candidate_id=meta.get("candidate_id", ""),
        comment=comment,
    )
    return {"saved": True}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if self.path not in ("/", "/index.html", "/dashboard.html"):
            self._json(404, {"error": "not found"})
            return
        page = GENERATED / "dashboard.html"
        if not page.exists():
            self._json(404, {"error": "Run generate_clean.py first."})
            return
        self._send(200, page.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON."})
            return
        if self.path == "/api/ask":
            result = answer_question(str(body.get("question", "")))
            self._json(200, result)
        elif self.path == "/api/review":
            result = save_review(bool(body.get("accepted")), str(body.get("comment", ""))[:500])
            self._json(200, result)
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard server on http://{HOST}:{PORT} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
