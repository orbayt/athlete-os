from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SignalState = Literal["poor", "strained", "mixed", "good", "unknown"]
TrainingLoadState = Literal["low", "normal", "high", "unknown"]
ConstraintType = Literal[
    "musculoskeletal",
    "illness",
    "sleep_debt",
    "life_stress",
    "travel",
    "other",
]
ConstraintStatus = Literal["active", "resolving", "resolved"]
ConstraintSeverity = Literal["low", "moderate", "high"]
AssessmentState = Literal[
    "rest", "active_recovery", "test_load", "easy", "normal"
]
Confidence = Literal["low", "medium", "high"]


class HeadCoachConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ConstraintType
    detail: str
    status: ConstraintStatus = "active"
    severity: ConstraintSeverity = "low"


class HeadCoachSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    recovery_state: SignalState
    sleep_state: SignalState
    recent_training_load: TrainingLoadState
    recent_activity_days: int = Field(ge=0)
    days_since_run: int | None = Field(default=None, ge=0)
    motivation_state: SignalState
    constraints: list[HeadCoachConstraint] = Field(default_factory=list)
    objective_data_coverage: float = Field(ge=0.0, le=1.0)
    subjective_data_available: bool


class HeadCoachAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    state: AssessmentState
    reality: str
    interpretation: str
    session_guidance: str
    why: list[str]
    watch_for: list[str]
    next_decision: str
    confidence: Confidence


def _systemic_strain(signals: HeadCoachSignals) -> bool:
    strained_states = {"poor", "strained"}
    return (
        signals.recovery_state in strained_states
        or signals.sleep_state in strained_states
    )


def _relevant_constraints(
    signals: HeadCoachSignals,
) -> list[HeadCoachConstraint]:
    return [
        constraint
        for constraint in signals.constraints
        if constraint.status != "resolved"
    ]


def _choose_state(signals: HeadCoachSignals) -> AssessmentState:
    constraints = _relevant_constraints(signals)
    active = [item for item in constraints if item.status == "active"]
    active_musculoskeletal = any(
        item.type == "musculoskeletal" for item in active
    )

    if any(item.severity == "high" for item in active):
        return "rest"
    if any(item.type == "illness" for item in active):
        return "rest"
    if _systemic_strain(signals) and active_musculoskeletal:
        return "rest"
    if _systemic_strain(signals):
        return "active_recovery"
    if any(item.type == "musculoskeletal" for item in constraints):
        return "test_load"
    if signals.days_since_run is not None and signals.days_since_run >= 7:
        return "easy"
    if "mixed" in {signals.recovery_state, signals.sleep_state}:
        return "easy"
    if signals.recovery_state == "good" and signals.sleep_state == "good":
        return "normal"
    return "easy"


def _confidence(signals: HeadCoachSignals) -> Confidence:
    score = signals.objective_data_coverage
    if signals.subjective_data_available:
        score += 0.20
    if signals.recovery_state != "unknown":
        score += 0.15
    if signals.sleep_state != "unknown":
        score += 0.10
    if signals.days_since_run is not None:
        score += 0.05

    if score >= 0.90:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _constraint_reason(constraint: HeadCoachConstraint) -> str:
    return (
        f"{constraint.detail}: {constraint.status}, "
        f"{constraint.severity} severity."
    )


def _build_reasons(signals: HeadCoachSignals) -> list[str]:
    reasons = []
    if signals.recovery_state != "unknown":
        reasons.append(f"Recovery state is {signals.recovery_state}.")
    if signals.sleep_state != "unknown":
        reasons.append(f"Sleep state is {signals.sleep_state}.")
    if signals.recent_training_load != "unknown":
        reasons.append(
            f"Recent training load is {signals.recent_training_load}."
        )
    if signals.motivation_state != "unknown":
        reasons.append(f"Motivation state is {signals.motivation_state}.")
    if signals.recent_activity_days > 0:
        reasons.append(
            f"Recent activity days: {signals.recent_activity_days}."
        )
    if signals.days_since_run is not None:
        reasons.append(f"Days since last run: {signals.days_since_run}.")
    reasons.extend(
        _constraint_reason(constraint)
        for constraint in _relevant_constraints(signals)
    )
    if not reasons:
        reasons.append(
            "Available data is insufficient for a strong readiness conclusion."
        )
    return reasons


def _decision_constraints(
    signals: HeadCoachSignals, state: AssessmentState
) -> list[HeadCoachConstraint]:
    constraints = _relevant_constraints(signals)
    if state == "test_load":
        return [item for item in constraints if item.type == "musculoskeletal"]
    if state != "rest":
        return []

    return [
        item
        for item in constraints
        if item.status == "active"
        and (
            item.severity == "high"
            or item.type == "illness"
            or (_systemic_strain(signals) and item.type == "musculoskeletal")
        )
    ]


def _reality(signals: HeadCoachSignals, state: AssessmentState) -> str:
    run_gap = (
        "unknown"
        if signals.days_since_run is None
        else str(signals.days_since_run)
    )
    reality = (
        f"Recovery is {signals.recovery_state}; sleep is {signals.sleep_state}; "
        f"recent training load is {signals.recent_training_load}; "
        f"days since last run: {run_gap}."
    )
    constraints = _decision_constraints(signals, state)
    if not constraints:
        return reality

    summaries = [
        f"{item.status} {item.severity}-severity "
        f"{item.type.replace('_', ' ')} constraint"
        for item in constraints
    ]
    label = "constraint" if len(summaries) == 1 else "constraints"
    return (
        f"{reality[:-1]}; decision-relevant {label}: "
        f"{', '.join(summaries)}."
    )


def _assessment_for_state(state: AssessmentState) -> dict[str, object]:
    assessments = {
        "rest": {
            "interpretation": "Current constraints or systemic strain make structured training inappropriate today.",
            "session_guidance": "No structured training today.",
            "watch_for": [
                "Whether symptoms or systemic strain improve, remain unchanged, or worsen."
            ],
            "next_decision": "Reassess recovery, sleep, and active constraints tomorrow before adding load.",
        },
        "active_recovery": {
            "interpretation": "Systemic strain is present, without a constraint that requires complete rest.",
            "session_guidance": "Very light movement may be useful, but today should not be treated as a training stimulus.",
            "watch_for": [
                "Whether light movement improves or worsens how the athlete feels."
            ],
            "next_decision": "Use the response to light movement and tomorrow's recovery state as the new baseline.",
        },
        "test_load": {
            "interpretation": "Systemic recovery may support movement, but local training-load tolerance remains uncertain.",
            "session_guidance": "The goal is to test the system, not train it: 30–45 minutes very easy, using walking, jog-walk, or easy running depending on symptoms.",
            "watch_for": [
                "Symptoms during activity.",
                "Symptoms later after activity.",
                "Whether movement feels better, unchanged, or worse.",
            ],
            "next_decision": "If no meaningful worsening occurs, progress toward easy running. If symptoms increase, reduce load again.",
        },
        "easy": {
            "interpretation": "The available picture supports movement, but not an unrestricted training decision.",
            "session_guidance": "Easy aerobic training only. Do not turn the session into a fitness test.",
            "watch_for": [
                "Unexpected strain or worsening symptoms during and after the session."
            ],
            "next_decision": "Treat the actual response to today's easy session as tomorrow's new baseline.",
        },
        "normal": {
            "interpretation": "Recovery and sleep are good, with no higher-priority constraint limiting training.",
            "session_guidance": "Proceed with normal training.",
            "watch_for": [
                "The actual response during and after today's session."
            ],
            "next_decision": "Treat the actual response to today's session as tomorrow's new baseline.",
        },
    }
    return assessments[state]


def build_head_coach_assessment(
    signals: HeadCoachSignals,
) -> HeadCoachAssessment:
    """Apply the deterministic Daily Head Coach v0 policy."""

    state = _choose_state(signals)
    language = _assessment_for_state(state)
    return HeadCoachAssessment(
        date=signals.as_of,
        state=state,
        reality=_reality(signals, state),
        interpretation=language["interpretation"],
        session_guidance=language["session_guidance"],
        why=_build_reasons(signals),
        watch_for=language["watch_for"],
        next_decision=language["next_decision"],
        confidence=_confidence(signals),
    )
