"""
tests/test_feedback.py â€” Phase 2A FeedbackLogger tests
"""
from __future__ import annotations

import json

import pytest
from src.agents.dashboard.dashboard_feedback import (
    FeedbackLogger,
    EVENT_CANDIDATE_SELECTED,
    EVENT_CANDIDATE_REJECTED,
    EVENT_OPTIMIZATION_RESULT,
    EVENT_COMPILER_FAILURE,
    EVENT_LAYOUT_CHANGE_ACCEPTED,
    EVENT_LAYOUT_CHANGE_REJECTED,
    EVENT_USER_ACCEPTED,
    EVENT_USER_REJECTED,
)


# â”€â”€ Basic write / read â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_writes_candidate_selected(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_candidate_selected(
        run_id="run-001",
        candidate_id="candidate_executive",
        strategy_name="Executive Summary",
        score=88.5,
        reason="Highest score",
    )
    events = logger.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == EVENT_CANDIDATE_SELECTED
    assert e["candidate_id"] == "candidate_executive"
    assert e["score"] == pytest.approx(88.5, abs=0.001)
    assert e["run_id"] == "run-001"


def test_feedback_writes_candidate_rejected(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_candidate_rejected(
        run_id="run-002",
        candidate_id="candidate_analytical",
        reason="Failed deterministic check",
        errors=["Missing table: Ghost"],
    )
    events = logger.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == EVENT_CANDIDATE_REJECTED
    assert "Missing table: Ghost" in e["errors"]


def test_feedback_writes_optimization_result(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_optimization_result(
        run_id="run-003",
        baseline_candidate_id="candidate_executive",
        baseline_score=82.0,
        optimized_candidate_id="candidate_executive",
        optimized_score=85.5,
        improved=True,
        operations_applied=["remove_redundant_visuals", "add_missing_evidence_references"],
        operations_rejected=[],
        validation_outcome="ACCEPTED",
    )
    events = logger.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == EVENT_OPTIMIZATION_RESULT
    assert e["improved"] is True
    assert e["improvement_delta"] == pytest.approx(3.5, abs=0.001)
    assert "remove_redundant_visuals" in e["operations_applied"]


def test_feedback_writes_compiler_failure(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_compiler_failure(
        run_id="run-004",
        candidate_id="candidate_decision",
        errors=["Visual 'x' references unknown Measure 'FakeKPI'"],
        warnings=["Missing ExecutiveOverview"],
    )
    events = logger.read_events()
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == EVENT_COMPILER_FAILURE
    assert len(e["errors"]) == 1
    assert len(e["warnings"]) == 1


def test_feedback_writes_layout_change_accepted(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_layout_change(
        run_id="run-005",
        operation_type="reorder_pages_for_audience",
        accepted=True,
        score_before=80.0,
        score_after=85.0,
    )
    events = logger.read_events()
    e = events[0]
    assert e["event_type"] == EVENT_LAYOUT_CHANGE_ACCEPTED
    assert e["accepted"] is True
    assert e["score_after"] == pytest.approx(85.0, abs=0.001)


def test_feedback_writes_layout_change_rejected(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_layout_change(
        run_id="run-006",
        operation_type="add_missing_kpi_card",
        accepted=False,
        score_before=80.0,
        rejection_reason="Score did not improve",
    )
    events = logger.read_events()
    e = events[0]
    assert e["event_type"] == EVENT_LAYOUT_CHANGE_REJECTED
    assert e["rejection_reason"] == "Score did not improve"


def test_feedback_writes_user_feedback(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_user_feedback(candidate_id="candidate_executive", accepted=True, comment="Looks great")
    logger.log_user_feedback(candidate_id="candidate_analytical", accepted=False, comment="Missing product breakdown")
    events = logger.read_events()
    assert len(events) == 2
    assert events[0]["event_type"] == EVENT_USER_ACCEPTED
    assert events[1]["event_type"] == EVENT_USER_REJECTED


# â”€â”€ Disabled logging â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_disabled_writes_nothing(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl", enabled=False)
    logger.log_candidate_selected(
        candidate_id="x", strategy_name="y", score=90.0
    )
    assert not (tmp_path / "fb.jsonl").exists()
    assert logger.read_events() == []


# â”€â”€ Multiple events / append â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_appends_events_correctly(tmp_path):
    path = tmp_path / "fb.jsonl"
    logger = FeedbackLogger(path)
    for i in range(5):
        logger.log_candidate_selected(
            run_id=f"run-{i}",
            candidate_id=f"cand_{i}",
            strategy_name=f"Strategy {i}",
            score=float(70 + i),
        )
    events = logger.read_events(limit=10)
    assert len(events) == 5
    # Verify order preserved
    for i, e in enumerate(events):
        assert e["candidate_id"] == f"cand_{i}"


# â”€â”€ Malformed records skipped â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_skips_malformed_records(tmp_path):
    path = tmp_path / "fb.jsonl"
    # Write one valid and one malformed line
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"event_type": "candidate_selected", "candidate_id": "ok"}\n')
        fh.write("THIS IS NOT JSON {\n")
        fh.write('{"event_type": "optimization_result", "improved": false}\n')

    logger = FeedbackLogger(path)
    events = logger.read_events()
    assert len(events) == 2  # malformed line skipped
    assert events[0]["candidate_id"] == "ok"
    assert events[1]["improved"] is False


def test_feedback_read_nonexistent_file_returns_empty(tmp_path):
    logger = FeedbackLogger(tmp_path / "nonexistent.jsonl")
    assert logger.read_events() == []


# â”€â”€ count_events_by_type â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_count_events_by_type(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    logger.log_candidate_selected(candidate_id="a", strategy_name="s", score=80.0)
    logger.log_candidate_selected(candidate_id="b", strategy_name="s", score=70.0)
    logger.log_candidate_rejected(candidate_id="c", reason="hard gate")
    logger.log_optimization_result(
        baseline_candidate_id="a", baseline_score=80.0,
        optimized_candidate_id="a", optimized_score=85.0, improved=True,
    )
    counts = logger.count_events_by_type()
    assert counts.get(EVENT_CANDIDATE_SELECTED) == 2
    assert counts.get(EVENT_CANDIDATE_REJECTED) == 1
    assert counts.get(EVENT_OPTIMIZATION_RESULT) == 1


# â”€â”€ Score safety â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_feedback_does_not_store_raw_data(tmp_path):
    """Log entries must not contain the full bundle data or secrets."""
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    # Simulate writing with a very long comment (should be truncated)
    long_comment = "A" * 2000
    logger.log_user_feedback(candidate_id="x", accepted=True, comment=long_comment)
    events = logger.read_events()
    assert len(events[0]["comment"]) <= 500


def test_feedback_score_breakdown_strips_non_numeric(tmp_path):
    logger = FeedbackLogger(tmp_path / "fb.jsonl")
    dirty_scores = {
        "kpi_relevance": 90.0,
        "bad_key": "not_a_number",
        "another": None,
        "good": 75.5,
    }
    logger.log_optimization_result(
        baseline_candidate_id="x", baseline_score=80.0,
        optimized_candidate_id="x", optimized_score=85.0, improved=True,
        criterion_before=dirty_scores,
    )
    events = logger.read_events()
    cb = events[0]["criterion_before"]
    assert "kpi_relevance" in cb
    assert "good" in cb
    # Non-numeric keys should be dropped
    assert "bad_key" not in cb
    assert "another" not in cb

