from typing import List

from app.models.fact_reasoning_models import FactCandidateInput


CANDIDATE_SCORE_FORMULA = (
    "Candidate Score = "
    "(0.30 × Grain Score) + "
    "(0.25 × Relationship Score) + "
    "(0.25 × Business Event Score) + "
    "(0.20 × Measure Score)"
)


SYSTEM_PROMPT = """
You are the FACT TABLE REASONING ENGINE of an ETL architecture.

Your ONLY responsibility is to identify the strongest fact-table
candidate from the candidates provided by the user.

============================================================
1. CANDIDATE SCORE
============================================================

For EVERY candidate, calculate:

Candidate Score =
    0.30 × Grain Score
  + 0.25 × Relationship Score
  + 0.25 × Business Event Score
  + 0.20 × Measure Score

The four input scores are already calculated by deterministic
Python analyzers.

You MUST use these values exactly.

Do NOT invent new scores.
Do NOT modify the four input scores.
Do NOT ignore any criterion.

============================================================
2. SCORE NORMALIZATION
============================================================

All four input scores are normalized decimal values:

0.0 <= score <= 1.0

The final Candidate Score must also be:

0.0 <= Candidate Score <= 1.0

NEVER:
- use percentages
- multiply scores by 100
- return negative numbers
- invent arbitrary values
- use placeholder values
- return 0 when the actual calculated score is non-zero

============================================================
3. CALCULATE THE SCORE YOURSELF
============================================================

You MUST calculate the Candidate Score.

Use exactly:

Candidate Score =
    (0.30 × Grain Score)
  + (0.25 × Relationship Score)
  + (0.25 × Business Event Score)
  + (0.20 × Measure Score)

Example:

Grain = 1.0000
Relationship = 0.9659
Business Event = 0.8500
Measure = 1.0000

Calculation:

0.30 × 1.0000 = 0.300000
0.25 × 0.9659 = 0.241475
0.25 × 0.8500 = 0.212500
0.20 × 1.0000 = 0.200000

Total = 0.953975
Rounded = 0.9540

============================================================
4. EVALUATE ALL CANDIDATES
============================================================

You MUST calculate the Candidate Score for EVERY candidate.

Do NOT select a fact table before evaluating all candidates.

After calculating all candidates:

1. Compare their Candidate Scores.
2. Select the candidate with the highest Candidate Score.
3. Return that candidate's exact table name.
4. Return that candidate's actual calculated Candidate Score.
5. Explain the selection briefly using the four criteria.

There is NO ambiguity analysis.
There is NO confidence calculation.
There is NO runner-up output.
There is NO human-review decision.

The highest Candidate Score determines the fact table.

============================================================
5. TABLE NAMES
============================================================

You MUST select the fact table from the exact table names
provided in the candidate list.

Never invent or modify a table name.

============================================================
6. REQUIRED OUTPUT
============================================================

Return ONLY a JSON object containing exactly:

{
    "selected_fact": "<exact table name>",
    "candidate_score": <calculated score>,
    "reasoning": [
        "<reason 1>",
        "<reason 2>",
        "<reason 3>"
    ]
}

The JSON object MUST contain exactly these three fields:
- selected_fact
- candidate_score
- reasoning

Do NOT return:
- runner_up
- runner_up_score
- confidence
- ambiguity
- decision
- HUMAN_REVIEW_REQUIRED
- ambiguity_threshold
- additional fields

============================================================
7. IMPORTANT
============================================================

Python has already calculated the four criterion scores.

You are responsible for:
- calculating the final Candidate Score
- comparing all candidates
- selecting the strongest fact table
- providing reasoning

Python does NOT choose the fact table.
Python only validates your arithmetic afterward.

The candidate_score field MUST contain the actual calculated
score of the selected candidate.

The final answer MUST contain only the required JSON object.
"""


def build_fact_user_prompt(
    candidates: List[FactCandidateInput],
) -> str:
    candidate_names = [candidate.table_name for candidate in candidates]

    lines = [
        "FACT TABLE CANDIDATE ANALYSIS",
        "",
        "============================================================",
        "EXACT CANDIDATE TABLE NAMES",
        "============================================================",
        "",
        str(candidate_names),
        "",
        "You MUST select the fact table from these exact names.",
        "",
        "============================================================",
        "CANDIDATE SCORE FORMULA",
        "============================================================",
        "",
        CANDIDATE_SCORE_FORMULA,
        "",
        "Weights:",
        "  Grain Score           = 0.30",
        "  Relationship Score   = 0.25",
        "  Business Event Score = 0.25",
        "  Measure Score        = 0.20",
        "",
        "============================================================",
        "NUMERIC RULES",
        "============================================================",
        "",
        "All criterion scores are normalized decimals between 0.0 and 1.0.",
        "The final Candidate Score must also be between 0.0 and 1.0.",
        "Do NOT use percentages.",
        "Do NOT multiply scores by 100.",
        "Do NOT return negative values.",
        "Do NOT invent values.",
        "Do NOT use placeholder values.",
        "",
        "============================================================",
        "CANDIDATES",
        "============================================================",
        "",
    ]

    for index, candidate in enumerate(candidates, start=1):
        lines.extend([
            f"Candidate {index}:",
            f"Table: {candidate.table_name}",
            "",
            "Authoritative criterion scores:",
            f"  Grain Score: {candidate.grain_score:.4f}",
            f"  Relationship Score: {candidate.relationship_score:.4f}",
            f"  Business Event Score: {candidate.event_score:.4f}",
            f"  Measure Score: {candidate.measure_score:.4f}",
            "",
            "Evidence:",
            _format_evidence(candidate.evidence),
            "",
            "Required calculation:",
            "Candidate Score = (0.30 × Grain) + (0.25 × Relationship) + "
            "(0.25 × Event) + (0.20 × Measure)",
            "",
            "You MUST calculate this Candidate Score.",
            "",
            "------------------------------------------------------------",
            "",
        ])

    lines.extend([
        "============================================================",
        "FINAL FACT TABLE SELECTION",
        "============================================================",
        "",
        "You MUST now:",
        "1. Calculate the Candidate Score for EVERY candidate.",
        "2. Compare ALL Candidate Scores.",
        "3. Select the candidate with the HIGHEST Candidate Score.",
        "4. Return its EXACT table name.",
        "5. Return its ACTUAL calculated Candidate Score.",
        "6. Provide concise reasoning based on the four criteria.",
        "",
        "There is no ambiguity calculation.",
        "There is no confidence calculation.",
        "There is no runner-up output.",
        "There is no human-review decision.",
        "",
        "Return ONLY the required JSON object.",
    ])

    return "\n".join(lines)


def _format_evidence(evidence) -> str:
    if not evidence:
        return "No additional evidence."

    if isinstance(evidence, dict):
        return "\n".join(
            f"  {key}: {value}" for key, value in evidence.items()
        )

    if isinstance(evidence, list):
        return "\n".join(f"  - {item}" for item in evidence)

    return f"  {evidence}"
