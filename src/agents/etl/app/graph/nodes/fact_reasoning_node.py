import json
import os
from typing import Any, Dict, List

import requests

from app.models.fact_reasoning_models import (
    FactCandidateInput,
    FactDecision,
)

from app.reasoning.fact_prompts import (
    SYSTEM_PROMPT,
    build_fact_user_prompt,
)


class FactReasoner:
    """LLM-based fact-table reasoning engine.

    The LLM (Groq, gpt-oss) selects the fact table.
    Python only validates the returned result and its arithmetic.
    """

    CANDIDATE_SCORE_WEIGHTS = {
        "grain_score": 0.30,
        "relationship_score": 0.25,
        "event_score": 0.25,
        "measure_score": 0.20,
    }

    def __init__(
        self,
        model: str = "openai/gpt-oss-120b",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: int = 120,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def reason(
        self,
        candidates: List[FactCandidateInput],
    ) -> FactDecision:
        if not candidates:
            raise ValueError(
                "Fact reasoning requires at least one candidate."
            )

        user_prompt = build_fact_user_prompt(candidates)

        response = self._call_llm(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            candidates=candidates,
        )

        decision_data = self._parse_response(response)

        self._validate_decision(
            decision_data=decision_data,
            candidates=candidates,
        )

        return FactDecision(
            selected_fact=decision_data["selected_fact"],
            candidate_score=round(
                float(decision_data["candidate_score"]),
                4,
            ),
            reasoning=decision_data["reasoning"],
        )

    
    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        candidates: List[FactCandidateInput],
    ) -> str:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Configure your Groq API key."
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    user_prompt
                    + "\n\nReturn only one valid JSON object. "
                    "Do not include Markdown fences or commentary."
                ),
            },
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
            # modèles gpt-oss (Groq) : le raisonnement compte dans max_tokens, on le garde court
            **({"reasoning_effort": "low"} if str(self.model).startswith("openai/gpt-oss") else {}),
        }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to connect to the LLM server at {url}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                "LLM returned an error.\n"
                f"HTTP status: {response.status_code}\n"
                f"Response: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "LLM returned invalid JSON."
            ) from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "LLM response contains no choices."
            )

        message = choices[0].get("message", {})

        if not isinstance(message, dict):
            raise RuntimeError(
                "LLM response does not contain a valid message object."
            )

        content = message.get("content")

        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(
                "LLM response does not contain text content."
            )

        return content.strip()


    @staticmethod
    def _parse_response(response: str) -> Dict[str, Any]:
        try:
            data = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "LLM returned invalid JSON.\n"
                f"Raw response:\n{response}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                "LLM response must be a JSON object."
            )

        return data

    def _validate_decision(
        self,
        decision_data: Dict[str, Any],
        candidates: List[FactCandidateInput],
    ) -> None:
        expected_keys = {
            "selected_fact",
            "candidate_score",
            "reasoning",
        }

        actual_keys = set(decision_data.keys())

        if actual_keys != expected_keys:
            raise ValueError(
                "LLM response does not match the required contract. "
                f"Expected fields: {sorted(expected_keys)}. "
                f"Received fields: {sorted(actual_keys)}."
            )

        candidate_names = {
            candidate.table_name
            for candidate in candidates
        }

        selected_fact = decision_data.get("selected_fact")

        if selected_fact not in candidate_names:
            raise ValueError(
                "LLM selected a table that does not exist among the "
                f"candidates: {selected_fact}"
            )

        candidate_score = decision_data.get("candidate_score")

        if not self._is_valid_probability(candidate_score):
            raise ValueError(
                f"Invalid candidate score: {candidate_score}"
            )

        selected_candidate = next(
            candidate
            for candidate in candidates
            if candidate.table_name == selected_fact
        )

        expected_score = self._calculate_score(selected_candidate)

        if abs(float(candidate_score) - expected_score) > 0.001:
            raise ValueError(
                "The returned Candidate Score does not match the required formula.\n"
                f"Selected fact: {selected_fact}\n"
                f"Returned score: {candidate_score}\n"
                f"Expected score: {expected_score}"
            )

        reasoning = decision_data.get("reasoning")

        if not isinstance(reasoning, list):
            raise ValueError(
                "Reasoning must be a list of strings."
            )

        if len(reasoning) == 0:
            raise ValueError(
                "LLM must provide at least one reasoning statement."
            )

        if not all(isinstance(item, str) for item in reasoning):
            raise ValueError(
                "Every reasoning item must be a string."
            )

    @classmethod
    def _calculate_score(
        cls,
        candidate: FactCandidateInput,
    ) -> float:
        score = (
            cls.CANDIDATE_SCORE_WEIGHTS["grain_score"]
            * candidate.grain_score
            + cls.CANDIDATE_SCORE_WEIGHTS["relationship_score"]
            * candidate.relationship_score
            + cls.CANDIDATE_SCORE_WEIGHTS["event_score"]
            * candidate.event_score
            + cls.CANDIDATE_SCORE_WEIGHTS["measure_score"]
            * candidate.measure_score
        )

        return round(
            max(0.0, min(1.0, score)),
            4,
        )

    @staticmethod
    def _is_valid_probability(value: Any) -> bool:
        if isinstance(value, bool):
            return False

        if not isinstance(value, (int, float)):
            return False

        return 0.0 <= float(value) <= 1.0


def fact_reasoning_node(state: dict) -> dict:
    """Use the LLM to select the fact table; Python validates the contract."""
    try:
        reasoner = FactReasoner(
            model=state.get("reasoning_model", "openai/gpt-oss-120b"),
            base_url=state.get("llm_base_url", "https://api.groq.com/openai/v1"),
            timeout=state.get("reasoning_timeout", 120),
        )
        decision = reasoner.reason(state["fact_candidates"])
        return {"fact_decision": decision, "fact_reasoning_error": None}
    except Exception as exc:
        return {"fact_decision": None, "fact_reasoning_error": str(exc)}
