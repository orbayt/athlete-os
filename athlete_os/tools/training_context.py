from datetime import date, timedelta
from math import exp

from athlete_os.services.intervals_client import (
    get_recent_activities_normalized,
)


HISTORY_DAYS = 210
RECENT_TIME_CONSTANT_DAYS = 7
BACKGROUND_TIME_CONSTANT_DAYS = 42
TOTAL_WINDOWS = (3, 7, 28, 84)
ACTIVITY_FIELDS = (
    "id",
    "date",
    "name",
    "type",
    "distance_km",
    "moving_time_min",
    "elevation_gain_m",
    "training_load",
)


def _activity_date(activity: dict) -> date | None:
    value = activity.get("date")
    if not value:
        return None

    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _window_total(
    daily_loads: dict[date, float], as_of: date, days: int
) -> float:
    oldest = as_of - timedelta(days=days - 1)
    return sum(
        load
        for load_date, load in daily_loads.items()
        if oldest <= load_date <= as_of
    )


def _ewma(
    daily_loads: dict[date, float],
    oldest: date,
    newest: date,
    time_constant_days: int,
) -> float:
    alpha = 1 - exp(-1 / time_constant_days)
    value = 0.0
    current = oldest
    while current <= newest:
        daily_load = daily_loads.get(current, 0)
        value += alpha * (daily_load - value)
        current += timedelta(days=1)
    return value


def _weekly_trajectory(
    daily_loads: dict[date, float], as_of: date
) -> list[dict]:
    current_week_start = as_of - timedelta(days=as_of.weekday())
    first_week_start = current_week_start - timedelta(weeks=11)

    weeks = []
    for week_index in range(12):
        week_start = first_week_start + timedelta(weeks=week_index)
        training_load = sum(
            daily_loads.get(week_start + timedelta(days=day), 0)
            for day in range(7)
        )
        weeks.append(
            {
                "week_start": week_start.isoformat(),
                "training_load": round(training_load, 2),
                "is_complete_week": week_start < current_week_start,
            }
        )

    return weeks


def build_training_context(activities: list[dict], as_of: date) -> dict:
    """Build deterministic training-load context from normalized activities."""

    history_start = as_of - timedelta(days=HISTORY_DAYS - 1)
    dated_activities = [
        (activity, activity_date)
        for activity in activities
        if (activity_date := _activity_date(activity)) is not None
        and history_start <= activity_date <= as_of
    ]

    daily_loads = {}
    for activity, activity_date in dated_activities:
        training_load = activity.get("training_load")
        if training_load is not None:
            daily_loads[activity_date] = (
                daily_loads.get(activity_date, 0) + training_load
            )

    recent_start = as_of - timedelta(days=27)
    highest_load_activities = [
        activity
        for activity, activity_date in dated_activities
        if activity_date >= recent_start
        and activity.get("training_load") is not None
    ]
    highest_load_activities.sort(
        key=lambda activity: (
            activity["training_load"],
            activity.get("date") or "",
        ),
        reverse=True,
    )

    activity_days = {activity_date for _, activity_date in dated_activities}
    missing_load_days = {
        activity_date
        for activity, activity_date in dated_activities
        if activity.get("training_load") is None
    }
    recent_completeness_start = as_of - timedelta(days=6)

    return {
        "as_of": as_of.isoformat(),
        "model": {
            "load_input": "daily_training_load",
            "method": "ewma",
            "missing_load_policy": "exclude_and_report",
            "recent_time_constant_days": RECENT_TIME_CONSTANT_DAYS,
            "background_time_constant_days": BACKGROUND_TIME_CONSTANT_DAYS,
            "history_days": HISTORY_DAYS,
        },
        "load": {
            "recent": {
                "observed_value": round(
                    _ewma(
                        daily_loads,
                        history_start,
                        as_of,
                        RECENT_TIME_CONSTANT_DAYS,
                    ),
                    2,
                ),
                "time_constant_days": RECENT_TIME_CONSTANT_DAYS,
                "complete": not missing_load_days,
                "recent_window_complete": not any(
                    activity_date >= recent_completeness_start
                    for activity_date in missing_load_days
                ),
            },
            "background": {
                "observed_value": round(
                    _ewma(
                        daily_loads,
                        history_start,
                        as_of,
                        BACKGROUND_TIME_CONSTANT_DAYS,
                    ),
                    2,
                ),
                "time_constant_days": BACKGROUND_TIME_CONSTANT_DAYS,
                "complete": not missing_load_days,
            },
            "totals": {
                f"{days}d": round(_window_total(daily_loads, as_of, days), 2)
                for days in TOTAL_WINDOWS
            },
            "trajectory": {
                "weekly_training_load": _weekly_trajectory(
                    daily_loads, as_of
                )
            },
        },
        "highest_load_activities_28d": [
            {field: activity.get(field) for field in ACTIVITY_FIELDS}
            for activity in highest_load_activities[:5]
        ],
        "data_coverage": {
            "history_days": HISTORY_DAYS,
            "activity_count": len(dated_activities),
            "activity_days": len(activity_days),
            "activities_with_training_load": sum(
                1
                for activity, _ in dated_activities
                if activity.get("training_load") is not None
            ),
            "activities_missing_training_load": sum(
                1
                for activity, _ in dated_activities
                if activity.get("training_load") is None
            ),
            "days_with_missing_training_load": len(missing_load_days),
        },
    }


def training_context() -> dict:
    """Return provider-independent multi-timescale training-load context."""

    as_of = date.today()
    activities = get_recent_activities_normalized(HISTORY_DAYS)
    return build_training_context(activities, as_of)
