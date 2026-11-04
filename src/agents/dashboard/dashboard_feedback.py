"""
dashboard_feedback.py â€” Phase 2A Structured Feedback Logger

Lightweight JSONL event logger for optimization and selection events.
Complies with all Phase 2A constraints:
- Never stores secrets, API keys, or raw source data.
- Logging is configurable and can be disabled.
- Malformed historical records are skipped safely.
- Historical feedback cannot override hard validation rules.
- Does not train or update model weights automatically.
"""
from __future__ import annotations

import collections
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("bi_flow.dashboard_feedback")

# â”€â”€ Event type constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

EVENT_CANDIDATE_SELECTED = "candidate_selected"
EVENT_CANDIDATE_REJECTED = "candidate_rejected"
EVENT_LAYOUT_CHANGE_REQUESTED = "layout_change_requested"
EVENT_LAYOUT_CHANGE_ACCEPTED = "layout_change_accepted"
EVENT_LAYOUT_CHANGE_REJECTED = "layout_change_rejected"
EVENT_OPTIMIZATION_RESULT = "optimization_result"
EVENT_COMPILER_FAILURE = "compiler_failure"
EVENT_USER_ACCEPTED = "user_accepted"
EVENT_USER_REJECTED = "user_rejected"

KNOWN_EVENTS = {
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_REJECTED,
    EVENT_LAYOUT_CHANGE_REQUESTED,
    EVENT_LAYOUT_CHANGE_ACCEPTED,
    EVENT_LAYOUT_CHANGE_REJECTED,
    EVENT_OPTIMIZATION_RESULT,
    EVENT_COMPILER_FAILURE,
    EVENT_USER_ACCEPTED,
    EVENT_USER_REJECTED,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_score_breakdown(scores: dict[str, Any] | None) -> dict[str, float]:
    """Extract only numeric scores; drop non-numeric entries silently."""
    if not isinstance(scores, dict):
        return {}
    result: dict[str, float] = {}
    for k, v in scores.items():
        try:
            result[str(k)] = round(float(v), 4)
        except (TypeError, ValueError):
            pass
    return result


# â”€â”€ FeedbackLogger â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class FeedbackLogger:
    """
    Structured JSONL feedback event logger.

    Each event is a self-contained JSON object on a single line.
    The file is opened in append mode so existing records are preserved.
    """

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # â”€â”€ Core write â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _write(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            line = json.dumps(event, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            logger.warning("FeedbackLogger: failed to write event: %s", traceback.format_exc(limit=3))

    def _base_event(
        self,
        event_type: str,
        run_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": _utc_now(),
            "event_type": event_type,
            "run_id": run_id or "",
            "request_id": request_id or "",
        }

    # â”€â”€ Public event methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def log_candidate_selected(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        candidate_id: str,
        strategy_name: str,
        score: float,
        reason: str = "",
        criterion_scores: dict[str, Any] | None = None,
    ) -> None:
        event = self._base_event(EVENT_CANDIDATE_SELECTED, run_id, request_id)
        event.update({
            "candidate_id": candidate_id,
            "strategy_name": strategy_name,
            "score": round(float(score), 4),
            "reason": reason,
            "criterion_scores": _safe_score_breakdown(criterion_scores),
        })
        self._write(event)

    def log_candidate_rejected(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        candidate_id: str,
        reason: str,
        score: float = 0.0,
        errors: list[str] | None = None,
    ) -> None:
        event = self._base_event(EVENT_CANDIDATE_REJECTED, run_id, request_id)
        event.update({
            "candidate_id": candidate_id,
            "reason": reason,
            "score": round(float(score), 4),
            "errors": errors or [],
        })
        self._write(event)

    def log_optimization_result(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        baseline_candidate_id: str,
        baseline_score: float,
        optimized_candidate_id: str,
        optimized_score: float,
        improved: bool,
        operations_applied: list[str] | None = None,
        operations_rejected: list[str] | None = None,
        criterion_before: dict[str, Any] | None = None,
        criterion_after: dict[str, Any] | None = None,
        validation_outcome: str = "",
    ) -> None:
        event = self._base_event(EVENT_OPTIMIZATION_RESULT, run_id, request_id)
        event.update({
            "baseline_candidate_id": baseline_candidate_id,
            "baseline_score": round(float(baseline_score), 4),
            "optimized_candidate_id": optimized_candidate_id,
            "optimized_score": round(float(optimized_score), 4),
            "improvement_delta": round(float(optimized_score) - float(baseline_score), 4),
            "improved": bool(improved),
            "operations_applied": [str(o) for o in (operations_applied or [])],
            "operations_rejected": [str(o) for o in (operations_rejected or [])],
            "criterion_before": _safe_score_breakdown(criterion_before),
            "criterion_after": _safe_score_breakdown(criterion_after),
            "validation_outcome": str(validation_outcome),
        })
        self._write(event)

    def log_compiler_failure(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        candidate_id: str,
        errors: list[str],
        warnings: list[str] | None = None,
    ) -> None:
        event = self._base_event(EVENT_COMPILER_FAILURE, run_id, request_id)
        event.update({
            "candidate_id": candidate_id,
            "errors": errors,
            "warnings": warnings or [],
        })
        self._write(event)

    def log_layout_change(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        operation_type: str,
        accepted: bool,
        score_before: float,
        score_after: float | None = None,
        rejection_reason: str = "",
    ) -> None:
        event_type = EVENT_LAYOUT_CHANGE_ACCEPTED if accepted else EVENT_LAYOUT_CHANGE_REJECTED
        event = self._base_event(event_type, run_id, request_id)
        event.update({
            "operation_type": operation_type,
            "accepted": accepted,
            "score_before": round(float(score_before), 4),
            "score_after": round(float(score_after), 4) if score_after is not None else None,
            "rejection_reason": rejection_reason,
        })
        self._write(event)

    def log_user_feedback(
        self,
        *,
        run_id: str = "",
        request_id: str = "",
        accepted: bool,
        candidate_id: str,
        comment: str = "",
    ) -> None:
        event_type = EVENT_USER_ACCEPTED if accepted else EVENT_USER_REJECTED
        event = self._base_event(event_type, run_id, request_id)
        event.update({
            "candidate_id": candidate_id,
            "comment": comment[:500],  # truncate; never store raw analytical data
        })
        self._write(event)

    # â”€â”€ Read / audit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def read_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Read up to `limit` recent events from the log file.

        Uses a deque tail-read so memory usage is O(limit) regardless of file size.
        Malformed records are skipped safely.
        """
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            # Read only the last `limit` raw lines to avoid loading the entire file.
            with self.path.open("r", encoding="utf-8") as fh:
                tail_lines = collections.deque(fh, maxlen=limit)
            for line in tail_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        events.append(record)
                except json.JSONDecodeError:
                    logger.debug("FeedbackLogger: skipping malformed record: %s", line[:80])
        except OSError:
            logger.warning("FeedbackLogger: could not read feedback file %s", self.path)
        return events

    def count_events_by_type(self) -> dict[str, int]:
        """Return a count of each event type from the log. Never raises."""
        counts: dict[str, int] = {}
        for event in self.read_events(limit=10_000):
            etype = event.get("event_type", "unknown")
            counts[etype] = counts.get(etype, 0) + 1
        return counts

