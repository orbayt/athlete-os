from athlete_os.services.intervals_client import (
    get_recent_activities_normalized,
    get_recent_wellness_normalized,
)


def _activity_totals(activities: list[dict]) -> dict:
    total_distance_km = sum(
        activity.get("distance_km") or 0 for activity in activities
    )
    total_moving_time_min = sum(
        activity.get("moving_time_min") or 0 for activity in activities
    )
    total_elevation_gain_m = sum(
        activity.get("elevation_gain_m") or 0 for activity in activities
    )
    total_training_load = sum(
        activity.get("training_load") or 0 for activity in activities
    )

    return {
        "activity_count": len(activities),
        "distance_km": round(total_distance_km, 2),
        "moving_time_hours": round(total_moving_time_min / 60, 2),
        "elevation_gain_m": round(total_elevation_gain_m),
        "training_load": round(total_training_load, 1),
    }


def _latest_metric(wellness: list[dict], field: str) -> dict | None:
    records = [
        record
        for record in wellness
        if record.get("date") is not None and record.get(field) is not None
    ]
    if not records:
        return None

    latest = max(records, key=lambda record: record["date"])
    return {"date": latest["date"], "value": latest[field]}


def training_state(days: int = 7) -> dict:
    """Return a combined training and recovery state summary."""

    if days < 1:
        raise ValueError("days must be at least 1")

    activities = get_recent_activities_normalized(days)
    wellness = get_recent_wellness_normalized(days)

    activities_by_type = {}
    for activity in activities:
        activity_type = activity.get("type") or "Unknown"
        activities_by_type.setdefault(activity_type, []).append(activity)

    latest_wellness = None

    if wellness:
        latest_wellness = max(
            wellness,
            key=lambda record: record["date"],
        )

    current_state = None

    if latest_wellness:
        fitness = latest_wellness.get("fitness_ctl")
        fatigue = latest_wellness.get("fatigue_atl")

        form = None
        if fitness is not None and fatigue is not None:
            form = round(fitness - fatigue, 2)

        current_state = {
            "date": latest_wellness.get("date"),
            "fitness_ctl": fitness,
            "fatigue_atl": fatigue,
            "form": form,
            "ramp_rate": latest_wellness.get("ramp_rate"),
            "resting_hr": latest_wellness.get("resting_hr"),
            "hrv_rmssd": latest_wellness.get("hrv_rmssd"),
            "sleep_hours": latest_wellness.get("sleep_hours"),
            "sleep_score": latest_wellness.get("sleep_score"),
            "steps": latest_wellness.get("steps"),
        }

    return {
        "period_days": days,
        "training": {
            **_activity_totals(activities),
            "by_type": {
                activity_type: _activity_totals(type_activities)
                for activity_type, type_activities in activities_by_type.items()
            },
        },
        "current_state": current_state,
        "latest_recovery": {
            "hrv_rmssd": _latest_metric(wellness, "hrv_rmssd"),
            "sleep_hours": _latest_metric(wellness, "sleep_hours"),
            "sleep_score": _latest_metric(wellness, "sleep_score"),
        },
        "data_coverage": {
            "activity_days": len(
                {
                    activity["date"][:10]
                    for activity in activities
                    if activity.get("date")
                }
            ),
            "wellness_days": len(wellness),
            "hrv_days": sum(
                1
                for record in wellness
                if record.get("hrv_rmssd") is not None
            ),
            "sleep_days": sum(
                1
                for record in wellness
                if record.get("sleep_hours") is not None
            ),
        },
    }
