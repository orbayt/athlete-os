from datetime import date, timedelta

from athlete_os.services.intervals_client import (
    get_activities_normalized,
    get_wellness_normalized,
)
from athlete_os.services.journal_store import journal_history
from athlete_os.tools.head_coach import (
    HeadCoachAssessment,
    HeadCoachConstraint,
    HeadCoachSignals,
    build_head_coach_assessment,
)
from athlete_os.tools.recovery_context import (
    BACKGROUND_DAYS,
    RECENT_DAYS,
    build_recovery_context,
)
from athlete_os.tools.training_context import (
    HISTORY_DAYS,
    build_training_context,
)


CONTEXT_DAYS = 14
CURRENT_DAYS = 2


def _as_of_date(value: date | None) -> date:
    parsed = value or date.today()
    if parsed > date.today():
        raise ValueError("as_of must not be in the future")
    return parsed


def _record_date(record: dict) -> date | None:
    value = record.get("date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_run(activity: dict) -> bool:
    activity_type = activity.get("type")
    return isinstance(activity_type, str) and "run" in activity_type.lower()


def _days_since_run(activities: list[dict], as_of: date) -> int | None:
    run_dates = [
        activity_date
        for activity in activities
        if _is_run(activity)
        and (activity_date := _record_date(activity)) is not None
        and activity_date <= as_of
    ]
    if not run_dates:
        return None
    return (as_of - max(run_dates)).days


def _recent_activity_days(activities: list[dict], as_of: date) -> int:
    oldest = as_of - timedelta(days=RECENT_DAYS - 1)
    return len(
        {
            activity_date
            for activity in activities
            if (activity_date := _record_date(activity)) is not None
            and oldest <= activity_date <= as_of
        }
    )


def _training_load_state(training: dict) -> str:
    coverage = training["data_coverage"]
    if coverage["activities_with_training_load"] == 0:
        return "unknown"
    if training["load"]["totals"]["7d"] == 0:
        return "low"

    recent = training["load"]["recent"]["observed_value"]
    background = training["load"]["background"]["observed_value"]
    if background == 0:
        return "high" if recent > 0 else "low"

    ratio = recent / background
    if ratio < 0.75:
        return "low"
    if ratio > 1.25:
        return "high"
    return "normal"


def _recent_latest(metric: dict, as_of: date) -> dict | None:
    latest = metric.get("latest")
    if latest is None:
        return None
    measured = date.fromisoformat(latest["date"])
    if as_of - measured >= timedelta(days=CURRENT_DAYS):
        return None
    return latest


def _recovery_state(recovery: dict, as_of: date) -> str:
    subjective = recovery["subjective"]
    observations = []
    for name in ("fatigue", "soreness", "stress"):
        latest = _recent_latest(subjective[name], as_of)
        if latest is not None:
            label = latest["label"]
            observations.append(
                "poor"
                if label == "extreme"
                else "strained"
                if label == "high"
                else "mixed"
                if label == "average"
                else "good"
            )
    mood = _recent_latest(subjective["mood"], as_of)
    if mood is not None:
        observations.append(
            "poor"
            if mood["label"] == "poor"
            else "mixed"
            if mood["label"] == "average"
            else "good"
        )

    if not observations:
        return "unknown"
    if "poor" in observations:
        return "poor"
    if "strained" in observations:
        return "strained"
    if "mixed" in observations or len(set(observations)) > 1:
        return "mixed"
    return "good"


def _sleep_observation(hours, quality=None, score=None) -> str | None:
    states = []
    if hours is not None:
        states.append("poor" if hours < 5 else "strained" if hours < 6 else "good")
    if quality is not None:
        states.append(
            "poor"
            if quality in {"none", "poor"}
            else "strained"
            if quality == "average"
            else "good"
        )
    if score is not None:
        states.append("poor" if score < 50 else "strained" if score < 70 else "good")
    if not states:
        return None
    if len(set(states)) > 1:
        return "mixed"
    return states[0]


def _sleep_state(recovery: dict, as_of: date) -> str:
    objective = recovery["objective"]["sleep"]
    subjective = recovery["subjective"]
    objective_hours = _recent_latest(objective["duration_hours"], as_of)
    objective_score = _recent_latest(objective["score"], as_of)
    reported_hours = _recent_latest(subjective["sleep_duration_hours"], as_of)
    reported_quality = _recent_latest(subjective["sleep_quality"], as_of)

    objective_state = _sleep_observation(
        objective_hours["value"] if objective_hours else None,
        score=objective_score["value"] if objective_score else None,
    )
    subjective_state = _sleep_observation(
        reported_hours["value"] if reported_hours else None,
        quality=reported_quality["label"] if reported_quality else None,
    )
    available = [state for state in (objective_state, subjective_state) if state]
    if not available:
        return "unknown"
    if len(set(available)) > 1:
        return "mixed"
    return available[0]


def _motivation_state(recovery: dict, as_of: date) -> str:
    latest = _recent_latest(recovery["subjective"]["motivation"], as_of)
    if latest is None:
        return "unknown"
    return {
        "none": "poor",
        "poor": "poor",
        "average": "mixed",
        "good": "good",
        "excellent": "good",
    }[latest["label"]]


def _constraint_status(entry_date: date, as_of: date) -> str | None:
    age = (as_of - entry_date).days
    if age < 0:
        return None
    if age == 0:
        return "active"
    if age < RECENT_DAYS:
        return "resolving"
    return None


def _constraints(
    recovery: dict, history: dict[str, dict], as_of: date
) -> list[HeadCoachConstraint]:
    tag_types = {
        "injury_niggle": "musculoskeletal",
        "illness": "illness",
        "sleep_disruption": "sleep_debt",
        "work_stress": "life_stress",
        "family_load": "life_stress",
        "environmental_stress": "life_stress",
        "travel": "travel",
    }
    candidates = []
    for date_value, entry in history.items():
        entry_date = date.fromisoformat(date_value)
        status = _constraint_status(entry_date, as_of)
        if status is None:
            continue
        for context in entry.get("context", []):
            constraint_type = tag_types.get(context["tag"])
            if constraint_type:
                impact = context.get("injury_impact")
                trend = context.get("injury_trend")
                severity = {
                    "training_only": "low",
                    "daily_noticeable": "moderate",
                    "daily_limiting": "high",
                }.get(impact, "low")
                candidates.append(
                    (
                        constraint_type,
                        context["tag"].replace("_", " "),
                        status,
                        severity,
                        impact,
                        trend,
                    )
                )

    for field, constraint_type, detail in (
        ("stress", "life_stress", "Reported stress"),
    ):
        latest = recovery["subjective"][field]["latest"]
        if latest is None or latest["label"] not in {"high", "extreme"}:
            continue
        measured = date.fromisoformat(latest["date"])
        status = _constraint_status(measured, as_of)
        if status:
            severity = "high" if latest["label"] == "extreme" else "moderate"
            candidates.append((constraint_type, detail, status, severity, None, None))

    # One constraint per type: prefer active, then higher severity.
    status_rank = {"active": 2, "resolving": 1}
    severity_rank = {"low": 0, "moderate": 1, "high": 2}
    merged = {}
    for constraint_type, detail, status, severity, impact, trend in candidates:
        candidate = (
            status_rank[status], severity_rank[severity], detail, status,
            severity, impact, trend,
        )
        if constraint_type not in merged or candidate[:2] > merged[constraint_type][:2]:
            merged[constraint_type] = candidate
    return [
        HeadCoachConstraint(
            type=constraint_type,
            detail=values[2].capitalize(),
            status=values[3],
            severity=values[4],
            injury_impact=values[5],
            injury_trend=values[6],
        )
        for constraint_type, values in sorted(merged.items())
    ]


def _objective_coverage(training: dict, recovery: dict) -> float:
    coverage = training["data_coverage"]
    training_available = coverage["activities_with_training_load"] > 0
    recovery_available = any(
        recovery["objective"][metric]["recent_7d"]["observed_days"] > 0
        for metric in ("resting_hr", "hrv")
    )
    sleep_available = (
        recovery["objective"]["sleep"]["duration_hours"]["recent_7d"][
            "observed_days"
        ]
        > 0
    )
    return round(
        sum((training_available, recovery_available, sleep_available)) / 3,
        2,
    )


def _subjective_available(
    recovery: dict, history: dict[str, dict], as_of: date
) -> bool:
    oldest = as_of - timedelta(days=RECENT_DAYS - 1)
    if any(
        entry.get("context")
        for date_value, entry in history.items()
        if oldest <= date.fromisoformat(date_value) <= as_of
    ):
        return True
    return any(
        metric["recent_7d"]["observed_days"] > 0
        for metric in recovery["subjective"].values()
    )


def build_head_coach_signals(*, as_of: date | None = None) -> HeadCoachSignals:
    """Adapt normalized Athlete OS state into Daily Head Coach signals.

    Activity-day and subjective relevance use the repository's seven-day
    recovery window. Constraint tags from today are active; tags from the
    preceding six days are resolving; older tags are omitted.
    """

    as_of_date = _as_of_date(as_of)
    activities = get_activities_normalized(
        as_of_date - timedelta(days=HISTORY_DAYS - 1), as_of_date
    )
    wellness = get_wellness_normalized(
        as_of_date - timedelta(days=BACKGROUND_DAYS - 1), as_of_date
    )
    history = journal_history(CONTEXT_DAYS, as_of=as_of_date)
    training = build_training_context(activities, as_of_date)
    recovery = build_recovery_context(wellness, as_of_date)

    return HeadCoachSignals(
        as_of=as_of_date,
        recovery_state=_recovery_state(recovery, as_of_date),
        sleep_state=_sleep_state(recovery, as_of_date),
        recent_training_load=_training_load_state(training),
        recent_activity_days=_recent_activity_days(activities, as_of_date),
        days_since_run=_days_since_run(activities, as_of_date),
        motivation_state=_motivation_state(recovery, as_of_date),
        constraints=_constraints(recovery, history, as_of_date),
        objective_data_coverage=_objective_coverage(training, recovery),
        subjective_data_available=_subjective_available(
            recovery, history, as_of_date
        ),
    )


def get_daily_head_coach(*, as_of: date | None = None) -> HeadCoachAssessment:
    return build_head_coach_assessment(build_head_coach_signals(as_of=as_of))
