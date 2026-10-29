
import json
import os
import re
from typing import Any

import requests

from app.models.fact_reasoning_models import (
    DimensionCandidateDecision,
    DimensionDecision,
    DimensionSelection,
)
from app.reasoning.dimension_prompts import (
    SYSTEM_PROMPT,
    build_dimension_user_prompt,
)


class DimensionReasoner:
    """LLM-based direct and indirect dimension selection engine."""

    def __init__(
        self,
        model: str = "gemma4:31b-cloud",
        base_url: str = "https://ollama.com/v1",
        timeout: int = 120,
        num_predict: int = 2500,
        api_key: str | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.num_predict = num_predict
        self.api_key = api_key or os.getenv("OLLAMA_API_KEY")

    def reason(
        self,
        fact_table: str,
        direct_candidates: list[str],
        indirect_candidates: list[str],
        candidate_paths: dict[str, list[str]],
        relationship_evidence: list[dict[str, Any]],
    ) -> DimensionDecision:
        """
        Ask the configured LLM to select dimensions from discovered candidates.

        Python validates the output contract and discovered paths.
        The LLM remains responsible for semantic selection.
        """
        if not isinstance(fact_table, str) or not fact_table.strip():
            raise ValueError(
                "Dimension reasoning requires a confirmed fact table."
            )

        direct = list(dict.fromkeys(direct_candidates))
        indirect = list(dict.fromkeys(indirect_candidates))

        if set(direct) & set(indirect):
            raise ValueError(
                "A candidate cannot be both direct and indirect."
            )

        candidates = direct + indirect

        if fact_table in candidates:
            raise ValueError(
                "The fact table cannot also be a dimension candidate."
            )

        if not candidates:
            return DimensionDecision(
                selected_dimensions=[],
                candidate_decisions=[],
                reasoning=["No dimension candidates are available."],
            )

        user_prompt = build_dimension_user_prompt(
            fact_table=fact_table,
            direct_candidates=direct,
            indirect_candidates=indirect,
            candidate_paths=candidate_paths,
            relationship_evidence=relationship_evidence,
        )

        # The cloud endpoint may return valid JSON with an incorrect shape.
        # Retry once with the exact validation error and the previous response.
        # This repairs formatting only; the model still makes all semantic choices.
        response = self._call_ollama(user_prompt)
        last_error: Exception | None = None
        decision_data: dict[str, Any] | None = None

        for attempt in range(2):
            try:
                decision_data = self._parse_response(response)
                self._validate_decision(
                    decision_data=decision_data,
                    fact_table=fact_table,
                    direct_candidates=direct,
                    indirect_candidates=indirect,
                    candidate_paths=candidate_paths,
                )
                break
            except (ValueError, TypeError, KeyError) as exc:
                last_error = exc
                if attempt == 1:
                    preview = response[:1600]
                    raise ValueError(
                        "Dimension model failed contract validation after one "
                        "repair attempt. "
                        f"Validation error: {exc}. Response preview: {preview}"
                    ) from exc

                response = self._call_ollama(
                    self._build_repair_prompt(
                        original_prompt=user_prompt,
                        validation_error=str(exc),
                        invalid_response=response,
                        candidates=candidates,
                        fact_table=fact_table,
                    )
                )

        if decision_data is None:
            raise ValueError(
                f"Dimension model returned no usable decision: {last_error}"
            )

        selections = [
            DimensionSelection(
                table_name=item["table_name"],
                dimension_type=item["dimension_type"],
                path=item["path"],
                reasoning=item["reasoning"],
            )
            for item in decision_data["selected_dimensions"]
        ]

        candidate_decisions = [
            DimensionCandidateDecision(
                table_name=item["table_name"],
                decision=item["decision"],
                reasoning=item["reasoning"],
            )
            for item in decision_data["candidate_decisions"]
        ]

        return DimensionDecision(
            selected_dimensions=selections,
            candidate_decisions=candidate_decisions,
            reasoning=decision_data["reasoning"],
        )

    @staticmethod
    def _build_repair_prompt(
        original_prompt: str,
        validation_error: str,
        invalid_response: str,
        candidates: list[str],
        fact_table: str,
    ) -> str:
        """Ask the LLM to repair its JSON contract without changing the task."""
        return (
            original_prompt
            + "\n\nYOUR PREVIOUS RESPONSE FAILED VALIDATION."
            + f"\nValidation error: {validation_error}"
            + f"\nConfirmed fact table: {fact_table}"
            + f"\nCandidates that must each have exactly one decision: {candidates}"
            + "\nReturn a corrected complete JSON object with exactly these top-level "
              "fields: selected_dimensions (array), candidate_decisions (array), "
              "reasoning (non-empty array of strings)."
            + "\nEach candidate_decisions array item must be an object with exactly "
              "table_name, decision, reasoning. Do not return a dictionary keyed "
              "by table name. Do not omit candidates. Keep your semantic choices "
              "unless the validation error requires a contract correction."
            + "\nPrevious invalid response (for correction only):\n"
            + invalid_response[:5000]
            + "\nReturn JSON only."
        )

    def _call_ollama(self, user_prompt: str) -> str:
        """Call local Ollama or Ollama Cloud and return model text.

        Cloud calls use Ollama's OpenAI-compatible endpoint. Local calls keep
        using /api/chat and its native structured-output schema.
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    user_prompt
                    + "\n\nReturn only one valid JSON object matching the "
                    "required dimension decision contract. Do not include "
                    "Markdown fences, commentary, or thinking tags."
                ),
            },
        ]
        is_cloud = ".ollama.com" in self.base_url or self.base_url.endswith("/v1")

        if is_cloud:
            if not self.api_key:
                raise RuntimeError(
                    "OLLAMA_API_KEY is missing. Set it in the environment "
                    "before calling Ollama Cloud."
                )
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0,
                "max_tokens": self.num_predict,
                # JSON mode prevents prose/fences; Python still validates the schema.
                "response_format": {"type": "json_object"},
            }
        else:
            url = f"{self.base_url}/api/chat"
            headers = {"Content-Type": "application/json"}
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": 0.0,
                    "num_predict": self.num_predict,
                },
                "format": self._response_schema(),
            }

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise RuntimeError(
                f"Ollama {'Cloud' if is_cloud else 'local'} timed out after "
                f"{self.timeout} seconds while running model '{self.model}'."
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Unable to connect to Ollama at {url}: {exc}"
            ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama {'Cloud' if is_cloud else 'local'} returned an error.\n"
                f"HTTP status: {response.status_code}\n"
                f"Response: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Ollama returned an invalid API response.") from exc

        if is_cloud:
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError(
                    f"Ollama Cloud response has no choices: {data}"
                )
            message = choices[0].get("message", {})
            content = message.get("content") if isinstance(message, dict) else None
        else:
            message = data.get("message")
            if not isinstance(message, dict):
                raise RuntimeError(
                    f"Ollama response has no valid message object: {data}"
                )
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                thinking = message.get("thinking", "")
                raise RuntimeError(
                    "Ollama returned empty message content. "
                    f"Thinking content present: {bool(thinking)}."
                )

        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(
                "Ollama returned empty response content. "
                f"Response: {data}"
            )

        return content.strip()

    @staticmethod
    def _response_schema() -> dict[str, Any]:
        """JSON schema required from Phi through Ollama."""
        selection_schema = {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "dimension_type": {
                    "type": "string",
                    "enum": ["direct", "indirect"],
                },
                "path": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                },
                "reasoning": {"type": "string"},
            },
            "required": [
                "table_name",
                "dimension_type",
                "path",
                "reasoning",
            ],
            "additionalProperties": False,
        }

        candidate_decision_schema = {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "decision": {
                    "type": "string",
                    "enum": ["selected", "rejected"],
                },
                "reasoning": {"type": "string"},
            },
            "required": [
                "table_name",
                "decision",
                "reasoning",
            ],
            "additionalProperties": False,
        }

        return {
            "type": "object",
            "properties": {
                "selected_dimensions": {
                    "type": "array",
                    "items": selection_schema,
                },
                "candidate_decisions": {
                    "type": "array",
                    "items": candidate_decision_schema,
                },
                "reasoning": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": [
                "selected_dimensions",
                "candidate_decisions",
                "reasoning",
            ],
            "additionalProperties": False,
        }

    @staticmethod
    def _parse_response(response: str) -> dict[str, Any]:
        """
        Parse the model response.

        Accept a JSON object directly or extract one from surrounding
        text after removing a complete <think> block.
        """
        if not isinstance(response, str) or not response.strip():
            raise ValueError("Phi returned an empty response.")

        cleaned = re.sub(
            r"<think\b[^>]*>.*?</think\s*>",
            "",
            response.strip(),
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = None

        if data is None:
            decoder = json.JSONDecoder()

            for match in re.finditer(r"\{", cleaned):
                try:
                    candidate, _ = decoder.raw_decode(
                        cleaned[match.start():]
                    )
                except json.JSONDecodeError:
                    continue

                if isinstance(candidate, dict):
                    data = candidate
                    break

        if not isinstance(data, dict):
            raise ValueError(
                "The model did not return a valid JSON object. "
                f"Response preview: {cleaned[:1200]}"
            )

        return data

    @staticmethod
    def _validate_decision(
        decision_data: dict[str, Any],
        fact_table: str,
        direct_candidates: list[str],
        indirect_candidates: list[str],
        candidate_paths: dict[str, list[str]],
    ) -> None:
        """
        Validate contract consistency without choosing dimensions.

        Semantic selection remains Phi's responsibility.
        """
        expected_fields = {
            "selected_dimensions",
            "candidate_decisions",
            "reasoning",
        }

        if set(decision_data) != expected_fields:
            raise ValueError(
                "Model response does not match the dimension decision "
                f"contract. Expected: {sorted(expected_fields)}; "
                f"received: {sorted(decision_data)}"
            )

        selected = decision_data["selected_dimensions"]
        candidate_decisions = decision_data["candidate_decisions"]
        reasoning = decision_data["reasoning"]

        if not isinstance(selected, list):
            raise ValueError("selected_dimensions must be a list.")

        if not isinstance(candidate_decisions, list):
            raise ValueError("candidate_decisions must be a list.")

        if (
            not isinstance(reasoning, list)
            or not reasoning
            or not all(
                isinstance(item, str) and item.strip()
                for item in reasoning
            )
        ):
            raise ValueError(
                "reasoning must be a non-empty list of non-empty strings."
            )

        direct = set(direct_candidates)
        indirect = set(indirect_candidates)

        if direct & indirect:
            raise ValueError(
                "A candidate cannot be both direct and indirect."
            )

        allowed = direct | indirect
        selected_names: set[str] = set()

        for item in selected:
            if not isinstance(item, dict):
                raise ValueError(
                    "Every selected dimension must be an object."
                )

            expected_selection_fields = {
                "table_name",
                "dimension_type",
                "path",
                "reasoning",
            }

            if set(item) != expected_selection_fields:
                raise ValueError(
                    "A selected dimension does not match its contract."
                )

            name = item["table_name"]
            kind = item["dimension_type"]
            path = item["path"]
            explanation = item["reasoning"]

            if not isinstance(name, str) or name not in allowed:
                raise ValueError(
                    f"Model selected an unknown candidate: {name}"
                )

            if name == fact_table:
                raise ValueError(
                    "The fact table cannot be its own dimension."
                )

            if name in selected_names:
                raise ValueError(f"Duplicate dimension: {name}")

            selected_names.add(name)

            if (
                not isinstance(path, list)
                or not all(
                    isinstance(part, str) and part.strip()
                    for part in path
                )
            ):
                raise ValueError(
                    f"Invalid path for dimension '{name}'."
                )

            if (
                len(path) < 2
                or path[0] != fact_table
                or path[-1] != name
            ):
                raise ValueError(
                    f"Path endpoints are invalid for dimension '{name}'."
                )

            if name in direct:
                expected_path = [fact_table, name]
            else:
                expected_path = candidate_paths.get(name)

            if expected_path is None or path != expected_path:
                raise ValueError(
                    "Model returned a path that does not match the "
                    f"discovered path for '{name}'."
                )

            expected_type = "direct" if name in direct else "indirect"

            if kind != expected_type:
                raise ValueError(
                    f"Incorrect dimension_type for '{name}': "
                    f"expected '{expected_type}'."
                )

            if (
                not isinstance(explanation, str)
                or not explanation.strip()
            ):
                raise ValueError(
                    f"Missing reasoning for dimension '{name}'."
                )

        decisions_by_name: dict[str, str] = {}

        for item in candidate_decisions:
            if not isinstance(item, dict):
                raise ValueError(
                    "Every candidate decision must be an object."
                )

            expected_decision_fields = {
                "table_name",
                "decision",
                "reasoning",
            }

            if set(item) != expected_decision_fields:
                raise ValueError(
                    "A candidate decision does not match its contract."
                )

            name = item["table_name"]
            decision = item["decision"]
            explanation = item["reasoning"]

            if not isinstance(name, str) or name not in allowed:
                raise ValueError(
                    f"Unknown candidate in decisions: {name}"
                )

            if name in decisions_by_name:
                raise ValueError(
                    f"Duplicate candidate decision: {name}"
                )

            if decision not in {"selected", "rejected"}:
                raise ValueError(
                    f"Invalid decision for candidate '{name}'."
                )

            if (
                not isinstance(explanation, str)
                or not explanation.strip()
            ):
                raise ValueError(
                    f"Missing decision reasoning for '{name}'."
                )

            decisions_by_name[name] = decision

        if set(decisions_by_name) != allowed:
            missing = sorted(allowed - set(decisions_by_name))
            unexpected = sorted(set(decisions_by_name) - allowed)

            raise ValueError(
                "Candidate decisions must cover every candidate exactly "
                f"once. Missing: {missing}; unexpected: {unexpected}"
            )

        for name in allowed:
            expected_decision = (
                "selected" if name in selected_names else "rejected"
            )

            if decisions_by_name[name] != expected_decision:
                raise ValueError(
                    f"Inconsistent decision for '{name}': "
                    f"candidate_decisions says "
                    f"'{decisions_by_name[name]}', but "
                    f"selected_dimensions implies "
                    f"'{expected_decision}'."
                )
