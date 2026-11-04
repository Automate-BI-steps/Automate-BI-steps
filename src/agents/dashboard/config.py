from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field


class OperatingMode(str, Enum):
    DETERMINISTIC = "deterministic"
    AI_ASSISTED = "ai_assisted"
    AI_ASSISTED_WITHOUT_RAG = "ai_assisted_without_rag"


class GeneratorConfig(BaseModel):
    mode: OperatingMode = Field(default=OperatingMode.DETERMINISTIC)
    llm_provider: str = Field(default="mock", description="LLM provider: 'mock', 'openai', 'llm7', 'meta', or 'custom'")
    llm_model: str = Field(default="mock-planner-v1", description="Model identifier for the planner")
    api_key_env_var: str = Field(default="LLM_API_KEY", description="Environment variable holding the API key")
    api_base_url: str | None = Field(default=None, description="Custom base URL for the LLM API")
    timeout_seconds: float = Field(default=15.0, description="Network timeout in seconds for LLM calls")
    max_retries: int = Field(default=2, description="Maximum critic revision retries")
    rag_enabled: bool = Field(default=True, description="Enable RAG retrieval when in AI mode")
    knowledge_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "resources" / "knowledge",
        description="Directory containing approved business and BI knowledge documents"
    )
    max_retrieved_chunks: int = Field(default=3, description="Maximum number of knowledge chunks to supply to the planner")
    export_html: bool = Field(default=True, description="Export offline standalone HTML dashboard alongside PBIP")
    log_level: str = Field(default="INFO", description="Logging level")
    candidate_count: int = Field(default=3, description="Number of candidate dashboard plans to generate")
    max_revisions: int = Field(default=1, description="Maximum critic revision passes on selected candidate")
    selection_policy: str = Field(default="highest_score", description="Selection policy for candidates ('highest_score')")
    evaluator_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "objective_alignment": 0.15,
            "kpi_relevance": 0.15,
            "question_coverage": 0.15,
            "evidence_traceability": 0.15,
            "visualization_suitability": 0.15,
            "redundancy_penalty": 0.05,
            "page_organization": 0.10,
            "compiler_compliance": 0.10,
            # Phase 2A: two new criteria (weights renormalized inside DashboardEvaluator)
            "cross_page_consistency": 0.05,
            "actionability": 0.05,
        },
        description="Weights for quality criteria scoring; renormalized at runtime so any positive subset is valid"
    )
    # â”€â”€ Phase 2A: Optimizer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    optimizer_enabled: bool = Field(default=True, description="Enable adaptive layout optimizer in AI-assisted mode")
    optimizer_max_operations: int = Field(default=5, description="Maximum layout operations per optimization pass")
    # â”€â”€ Phase 2A: Feedback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    feedback_enabled: bool = Field(default=True, description="Enable structured feedback event logging")
    feedback_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "generated" / "optimization_feedback.jsonl",
        description="Path for JSONL feedback event log"
    )

    @property
    def api_key(self) -> str | None:
        if self.llm_provider == "groq":
            return os.environ.get("GROQ_API_KEY") or os.environ.get(self.api_key_env_var)
        if self.llm_provider == "meta":
            return (
                os.environ.get("MODEL_API_KEY")
                or os.environ.get("META_API_KEY")
                or os.environ.get(self.api_key_env_var)
            )
        if self.llm_provider == "llm7":
            return os.environ.get("MODEL_API_KEY") or os.environ.get(self.api_key_env_var)
        return os.environ.get(self.api_key_env_var) or os.environ.get("GROQ_API_KEY")

    @classmethod
    def from_env(cls) -> GeneratorConfig:
        llm_provider = os.environ.get("BI_FLOW_LLM_PROVIDER", "mock")
        default_mode = (
            OperatingMode.AI_ASSISTED.value
            if llm_provider != "mock"
            else OperatingMode.DETERMINISTIC.value
        )
        mode_str = os.environ.get("BI_FLOW_MODE", default_mode).lower()
        mode = OperatingMode(mode_str) if mode_str in [m.value for m in OperatingMode] else OperatingMode.DETERMINISTIC
        if llm_provider == "gemini":
            default_model = "gemini-2.5-flash"
        elif llm_provider == "groq":
            default_model = "llama-3.3-70b-versatile"
        elif llm_provider == "meta":
            default_model = "muse-wonder-1.0"
        elif llm_provider == "llm7":
            default_model = "default"
        else:
            default_model = "mock-planner-v1"
        llm_model = os.environ.get("BI_FLOW_LLM_MODEL", default_model)
        api_base_url = os.environ.get(
            "BI_FLOW_LLM_BASE_URL",
            "https://api.llm7.io/v1" if llm_provider == "llm7" else "",
        ) or None
        export_html = os.environ.get("BI_FLOW_EXPORT_HTML", "true").lower() in ("true", "1", "yes")
        return cls(
            mode=mode,
            llm_provider=llm_provider,
            llm_model=llm_model,
            api_base_url=api_base_url,
            export_html=export_html,
        )

