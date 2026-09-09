from datetime import date, timedelta
from statistics import median

from athlete_os.services.intervals_client import (
    get_activities_normalized,
    get_activity_normalized,
)


RUN_HISTORY_POLICY_VERSION = "run_history_context_v0"
RUNNING_FAMILY_TYPES = ("Run", "TrailRun")
REFERENCE_WINDOWS = (84, 210)
SURROUNDING_DAYS = 7
METRICS = (
    "distance_km",
    "moving_time_min",
    "elevation_gain_m",
    "training_load",
    "average_hr",
    "max_hr",
)
EXPOSURE_METRICS = (
    "distance_km",
    "moving_time_min",
    "elevation_gain_m",
)


def is_running_activity(activity: dict) -> bool:
    """Return whether an activity belongs to the explicit running family."""

    return activity.get("type") in RUNNING_FAMILY_TYPES


def _activity_date(activity: dict) -> date | None:
    value = activity.get("date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _metric_reference(activities: list[dict], field: str) -> dict:
    values = [activity.get(field) for activity in activities]
    known = [value for value in values if value is not None]
    return {
        "known_count": len(known),
        "missing_count": len(values) - len(known),
        "median": round(median(known), 2) if known else None,
        "minimum": min(known) if known else None,
        "maximum": max(known) if known else None,
    }


def _observed_subtotal(activities: list[dict], field: str) -> dict:
    values = [activity.get(field) for activity in activities]
    known = [value for value in values if value is not None]
    return {
        "observed_subtotal": round(sum(known), 2) if known else None,
        "known_count": len(known),
        "missing_count": len(values) - len(known),
        "complete": len(known) == len(values),
    }


def _exposure_totals(
    activities: list[dict], *, include_provider_load: bool
) -> dict:
    result = {
        "activity_count": len(activities),
        "distinct_activity_days": len(
            {
                activity_date
                for activity in activities
                if (activity_date := _activity_date(activity)) is not None
            }
        ),
        "metrics": {
            field: _observed_subtotal(activities, field)
            for field in EXPOSURE_METRICS
        },
    }
    if include_provider_load:
        result["provider_load"] = _observed_subtotal(
            activities, "training_load"
        )
    return result


def _activity_exposure(activities: list[dict]) -> dict:
    by_type = {}
    for activity_type in sorted(
        {activity.get("type") or "Unknown" for activity in activities}
    ):
        typed = [
            activity
            for activity in activities
            if (activity.get("type") or "Unknown") == activity_type
        ]
        by_type[activity_type] = _exposure_totals(
            typed, include_provider_load=True
        )

    running = [activity for activity in activities if is_running_activity(activity)]
    return {
        "all_activities": _exposure_totals(
            activities, include_provider_load=False
        ),
        "by_original_type": by_type,
        "running_family": {
            **_exposure_totals(running, include_provider_load=True),
            "family_types": list(RUNNING_FAMILY_TYPES),
            "provider_load_provenance": "normalized provider training_load",
            "physiological_equivalence_across_types_verified": False,
        },
    }


def _retrieval_coverage(
    *,
    requested_oldest: date,
    requested_newest: date,
    retrieved_oldest: date,
    retrieved_newest: date,
    provider_request_succeeded: bool,
) -> dict:
    range_retrieved = (
        provider_request_succeeded
        and retrieved_oldest <= requested_oldest
        and retrieved_newest >= requested_newest
    )
    if not provider_request_succeeded:
        limitation = "The provider request did not succeed."
    elif not range_retrieved:
        limitation = (
            "The retrieved history does not cover the full requested range; "
            "dates outside it must not be interpreted as having no activity."
        )
    else:
        limitation = None
    return {
        "requested_range": {
            "oldest": requested_oldest.isoformat(),
            "newest": requested_newest.isoformat(),
        },
        "retrieved_range": {
            "oldest": retrieved_oldest.isoformat(),
            "newest": retrieved_newest.isoformat(),
        },
        "provider_request_succeeded": provider_request_succeeded,
        "requested_range_retrieved": range_retrieved,
        "recording_completeness": "unknown",
        "unobserved_dates_may_have_activity": not range_retrieved,
        "retrieval_limitation": limitation,
    }


def _reference(
    running: list[dict],
    selected_date: date,
    days: int,
    retrieved_oldest: date,
    retrieved_newest: date,
    provider_request_succeeded: bool,
) -> dict:
    oldest = selected_date - timedelta(days=days)
    newest = selected_date - timedelta(days=1)
    observations = [
        activity
        for activity in running
        if (activity_date := _activity_date(activity)) is not None
        and oldest <= activity_date <= newest
    ]
    coverage = _retrieval_coverage(
        requested_oldest=oldest,
        requested_newest=newest,
        retrieved_oldest=retrieved_oldest,
        retrieved_newest=retrieved_newest,
        provider_request_succeeded=provider_request_succeeded,
    )
    return {
        "window_days": days,
        **coverage,
        "session_count": len(observations),
        "distinct_running_days": len(
            {_activity_date(activity) for activity in observations}
        ),
        "metrics": {
            field: _metric_reference(observations, field)
            for field in METRICS
        },
    }


def build_run_history_context(
    *,
    selected_activity: dict,
    normalized_history: list[dict],
    selected_date: date,
    retrieved_oldest: date,
    retrieved_newest: date,
    provider_request_succeeded: bool,
) -> dict:
    """Build factual context for a selected normalized running activity."""

    if retrieved_oldest > retrieved_newest:
        raise ValueError("retrieved_oldest must be before or equal to retrieved_newest")
    if _activity_date(selected_activity) != selected_date:
        raise ValueError("selected activity date must match selected_date")
    if not is_running_activity(selected_activity):
        raise ValueError("selected activity must be Run or TrailRun")

    history = [
        activity
        for activity in normalized_history
        if (activity_date := _activity_date(activity)) is not None
        and retrieved_oldest <= activity_date <= retrieved_newest
    ]
    running = [activity for activity in history if is_running_activity(activity)]
    prior_running = [
        activity
        for activity in running
        if (activity_date := _activity_date(activity)) is not None
        and activity_date < selected_date
    ]
    previous_date = max(
        (_activity_date(activity) for activity in prior_running), default=None
    )
    selected_day = [
        activity for activity in history if _activity_date(activity) == selected_date
    ]
    prior_seven_oldest = selected_date - timedelta(days=SURROUNDING_DAYS)
    prior_seven_newest = selected_date - timedelta(days=1)
    prior_seven = [
        activity
        for activity in history
        if (activity_date := _activity_date(activity)) is not None
        and prior_seven_oldest <= activity_date <= prior_seven_newest
    ]
    previous_limitation = None
    if previous_date is None:
        previous_limitation = (
            "No previous running activity was observed in the retrieved history; "
            "this does not establish that the athlete had never run."
        )

    return {
        "policy_version": RUN_HISTORY_POLICY_VERSION,
        "selected_activity": {
            "id": selected_activity.get("id"),
            "date": selected_date.isoformat(),
            "original_type": selected_activity.get("type"),
            **{
                field: selected_activity.get(field)
                for field in METRICS
            },
        },
        "retrieval": {
            "requested_range": {
                "oldest": (selected_date - timedelta(days=210)).isoformat(),
                "newest": selected_date.isoformat(),
            },
            "retrieved_range": {
                "oldest": retrieved_oldest.isoformat(),
                "newest": retrieved_newest.isoformat(),
            },
            "provider_request_succeeded": provider_request_succeeded,
            "recording_completeness": "unknown",
            "note": "A successful provider fetch does not prove every activity was recorded.",
        },
        "shared_running_history": {
            "family_types": list(RUNNING_FAMILY_TYPES),
            "previous_observed_running_date": (
                previous_date.isoformat() if previous_date else None
            ),
            "calendar_day_interval_since_previous_run": (
                (selected_date - previous_date).days if previous_date else None
            ),
            "previous_run_limitation": previous_limitation,
            "observed_prior_session_count": len(prior_running),
            "observed_prior_distinct_running_days": len(
                {_activity_date(activity) for activity in prior_running}
            ),
            "prior_84d": _reference(
                running,
                selected_date,
                84,
                retrieved_oldest,
                retrieved_newest,
                provider_request_succeeded,
            ),
            "prior_210d": _reference(
                running,
                selected_date,
                210,
                retrieved_oldest,
                retrieved_newest,
                provider_request_succeeded,
            ),
        },
        "surrounding_activity": {
            "selected_day": {
                "retrieval_coverage": _retrieval_coverage(
                    requested_oldest=selected_date,
                    requested_newest=selected_date,
                    retrieved_oldest=retrieved_oldest,
                    retrieved_newest=retrieved_newest,
                    provider_request_succeeded=provider_request_succeeded,
                ),
                "retrospective_recorded_exposure": True,
                **_activity_exposure(selected_day),
            },
            "prior_7_complete_days": {
                "retrieval_coverage": _retrieval_coverage(
                    requested_oldest=prior_seven_oldest,
                    requested_newest=prior_seven_newest,
                    retrieved_oldest=retrieved_oldest,
                    retrieved_newest=retrieved_newest,
                    provider_request_succeeded=provider_request_succeeded,
                ),
                **_activity_exposure(prior_seven),
            },
            "steps": {
                "included": False,
                "reason": "Steps are wellness evidence and are kept separate from recorded activities.",
            },
        },
        "unsupported_evidence": [
            "Perceived effort is not present in the normalized activity contract.",
            "Symptom response is not present in the normalized activity contract.",
            "No activity intensity classification is produced.",
        ],
    }


def get_run_history_context(activity_id: str) -> dict:
    """Resolve an activity and fetch its date-relative normalized history."""

    if not isinstance(activity_id, str) or not activity_id.strip():
        raise ValueError("activity_id must not be empty")
    activity_id = activity_id.strip()
    if "/" in activity_id or len(activity_id) > 200:
        raise ValueError("activity_id is invalid")

    selected = get_activity_normalized(activity_id)
    selected_date = _activity_date(selected)
    if selected_date is None:
        raise ValueError("selected activity has no valid date")
    if str(selected.get("id")) != activity_id:
        raise ValueError("provider activity id does not match activity_id")

    oldest = selected_date - timedelta(days=210)
    history = get_activities_normalized(oldest, selected_date)
    if not any(
        str(activity.get("id")) == activity_id for activity in history
    ):
        history = [*history, selected]
    return build_run_history_context(
        selected_activity=selected,
        normalized_history=history,
        selected_date=selected_date,
        retrieved_oldest=oldest,
        retrieved_newest=selected_date,
        provider_request_succeeded=True,
    )
