from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from athlete_os.services.execution_store import (
    DailyExecution,
    ExecutionSource,
    ExecutionType,
    resolve_daily_execution,
)
from athlete_os.services.head_coach_memory import (
    get_latest_head_coach_decision,
)
from athlete_os.services.intervals_client import get_activities_normalized
from athlete_os.tools.head_coach import AssessmentState, HeadCoachAssessment


BEHAVIOR_ASSESSMENT_POLICY_VERSION = "behavior_assessment_v0"
RECOMMENDATION_RESPONSE_POLICY_VERSION = "recommendation_response_v0"

LoadClassification = Literal["UNKNOWN"]
ResponseState = Literal[
    "WITHIN_GUIDANCE", "MORE_CONSERVATIVE", "ABOVE_GUIDANCE", "UNKNOWN"
]


class BehaviorExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ExecutionType
    source: ExecutionSource
    activity_id: str | None = None
    notes: str | None = None


class ActivityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_count: int = Field(ge=1)
    activity_ids: list[str]
    types: list[str]
    moving_time_min: float | None
    distance_km: float | None
    elevation_gain_m: float | None
    training_load: float | None
    training_load_complete: bool
    average_hr: float | None
    max_hr: float | None


class BehaviorAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    policy_version: str
    execution: BehaviorExecution
    activity_evidence: ActivityEvidence | None
    load_classification: LoadClassification
    reasons: list[str]


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    policy_version: str
    coach_decision: AssessmentState
    response: ResponseState
    reasons: list[str]
    behavior_policy_version: str


class DailyBehaviorReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    head_coach_decision_id: int | None
    behavior: BehaviorAssessment
    recommendation_response: RecommendationResponse | None


def _validated_date(value: date | None) -> date:
    result = value or date.today()
    if result > date.today():
        raise ValueError("date must not be in the future")
    return result


def _sum_known(activities: list[dict], field: str) -> float | None:
    values = [activity.get(field) for activity in activities]
    known = [value for value in values if value is not None]
    if not known:
        return None
    return round(sum(known), 2)


def _complete_sum(activities: list[dict], field: str) -> float | None:
    values = [activity.get(field) for activity in activities]
    if any(value is None for value in values):
        return None
    return round(sum(values), 2)


def _average_hr(activities: list[dict]) -> float | None:
    values = [activity.get("average_hr") for activity in activities]
    if any(value is None for value in values):
        return None
    if len(values) == 1:
        return values[0]

    durations = [activity.get("moving_time_min") for activity in activities]
    if any(value is None for value in durations) or sum(durations) <= 0:
        return None
    return round(
        sum(hr * duration for hr, duration in zip(values, durations))
        / sum(durations),
        1,
    )


def aggregate_activity_evidence(
    assessment_date: date, activities: list[dict]
) -> ActivityEvidence | None:
    dated = [
        activity
        for activity in activities
        if isinstance(activity.get("date"), str)
        and activity["date"][:10] == assessment_date.isoformat()
    ]
    if not dated:
        return None

    loads = [activity.get("training_load") for activity in dated]
    max_hr_values = [activity.get("max_hr") for activity in dated]
    return ActivityEvidence(
        activity_count=len(dated),
        activity_ids=[
            str(activity["id"])
            for activity in dated
            if activity.get("id") is not None
        ],
        types=list(dict.fromkeys(
            activity["type"]
            for activity in dated
            if activity.get("type") is not None
        )),
        moving_time_min=_complete_sum(dated, "moving_time_min"),
        distance_km=_complete_sum(dated, "distance_km"),
        elevation_gain_m=_complete_sum(dated, "elevation_gain_m"),
        training_load=_sum_known(dated, "training_load"),
        training_load_complete=all(load is not None for load in loads),
        average_hr=_average_hr(dated),
        max_hr=(
            max(max_hr_values)
            if all(value is not None for value in max_hr_values)
            else None
        ),
    )


def _behavior_reasons(
    execution: DailyExecution, evidence: ActivityEvidence | None
) -> list[str]:
    if execution.execution_type == "REST":
        return ["Athlete reported rest.", "No activity load is inferred."]
    if execution.execution_type == "ACTIVE_RECOVERY":
        return [
            "Athlete reported active recovery.",
            "No activity intensity is inferred from that report.",
        ]
    if execution.execution_type == "ACTIVITY":
        source = "Provider" if evidence is not None else "Manual"
        return [
            f"{source} activity evidence exists.",
            "No athlete-specific load classification policy is defined.",
        ]
    return ["No resolved execution evidence is available."]


def build_behavior_assessment(
    *,
    assessment_date: date,
    execution: DailyExecution,
    activities: list[dict],
) -> BehaviorAssessment:
    if execution.date != assessment_date:
        raise ValueError("execution date must match assessment date")
    evidence = aggregate_activity_evidence(assessment_date, activities)
    return BehaviorAssessment(
        date=assessment_date,
        policy_version=BEHAVIOR_ASSESSMENT_POLICY_VERSION,
        execution=BehaviorExecution(
            type=execution.execution_type,
            source=execution.source,
            activity_id=execution.activity_id,
            notes=execution.notes,
        ),
        activity_evidence=evidence,
        load_classification="UNKNOWN",
        reasons=_behavior_reasons(execution, evidence),
    )


def _response_state(
    coach_decision: AssessmentState, execution_type: ExecutionType
) -> ResponseState:
    if coach_decision == "rest":
        return "WITHIN_GUIDANCE" if execution_type == "REST" else "UNKNOWN"
    if coach_decision == "active_recovery":
        if execution_type == "ACTIVE_RECOVERY":
            return "WITHIN_GUIDANCE"
        if execution_type == "REST":
            return "MORE_CONSERVATIVE"
        return "UNKNOWN"
    if execution_type in {"REST", "ACTIVE_RECOVERY"}:
        return "MORE_CONSERVATIVE"
    return "UNKNOWN"


def _response_reasons(
    coach_decision: AssessmentState,
    execution_type: ExecutionType,
    response: ResponseState,
) -> list[str]:
    guidance = {
        "rest": "Head Coach recommended no structured training.",
        "active_recovery": "Head Coach allowed very light movement.",
        "test_load": "Head Coach proposed a bounded test load.",
        "easy": "Head Coach allowed easy aerobic training.",
        "normal": "Head Coach allowed normal training.",
    }
    execution = {
        "REST": "Athlete reported rest.",
        "ACTIVE_RECOVERY": "Athlete reported active recovery.",
        "ACTIVITY": "Activity evidence exists, but its load is unclassified.",
        "UNKNOWN": "Actual execution is unknown.",
    }
    reasons = [guidance[coach_decision], execution[execution_type]]
    if response == "UNKNOWN" and execution_type == "ACTIVITY":
        reasons.append(
            "No athlete-specific rule relates this activity to the guidance envelope."
        )
    return reasons


def build_recommendation_response(
    *,
    assessment: HeadCoachAssessment,
    behavior: BehaviorAssessment,
) -> RecommendationResponse:
    if assessment.date != behavior.date:
        raise ValueError("coach and behavior dates must match")
    response = _response_state(assessment.state, behavior.execution.type)
    return RecommendationResponse(
        date=behavior.date,
        policy_version=RECOMMENDATION_RESPONSE_POLICY_VERSION,
        coach_decision=assessment.state,
        response=response,
        reasons=_response_reasons(
            assessment.state, behavior.execution.type, response
        ),
        behavior_policy_version=behavior.policy_version,
    )


def get_daily_behavior_review(
    *, assessment_date: date | None = None
) -> DailyBehaviorReview:
    resolved_date = _validated_date(assessment_date)
    activities = get_activities_normalized(resolved_date, resolved_date)
    execution = resolve_daily_execution(resolved_date.isoformat(), activities)
    behavior = build_behavior_assessment(
        assessment_date=resolved_date,
        execution=execution,
        activities=activities,
    )
    decision = get_latest_head_coach_decision(assessment_date=resolved_date)
    response = (
        build_recommendation_response(
            assessment=decision.assessment, behavior=behavior
        )
        if decision is not None
        else None
    )
    return DailyBehaviorReview(
        date=resolved_date,
        head_coach_decision_id=decision.id if decision is not None else None,
        behavior=behavior,
        recommendation_response=response,
    )
