from datetime import date, timedelta

from athlete_os.services.intervals_client import (
    get_activities_normalized,
    get_wellness_normalized,
)
from athlete_os.services.journal_store import (
    journal_history,
    latest_daily_journal,
)
from athlete_os.tools.recovery_context import (
    BACKGROUND_DAYS,
    build_recovery_context,
)
from athlete_os.tools.training_context import (
    HISTORY_DAYS,
    build_training_context,
)


TIMELINE_DAYS = 7


def _as_of_date(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("as_of must use YYYY-MM-DD format") from error
    if parsed.isoformat() != value:
        raise ValueError("as_of must use YYYY-MM-DD format")
    if parsed > date.today():
        raise ValueError("as_of must not be in the future")
    return parsed


def _record_date(record: dict) -> str | None:
    value = record.get("date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _latest_activity(activities: list[dict], as_of: date) -> dict | None:
    dated = [
        activity
        for activity in activities
        if (activity_date := _record_date(activity)) is not None
        and activity_date <= as_of.isoformat()
    ]
    if not dated:
        return None
    activity = max(dated, key=lambda item: item.get("date") or "")
    return {
        "date": _record_date(activity),
        "type": activity.get("type"),
        "distance_km": activity.get("distance_km"),
        "moving_time_min": activity.get("moving_time_min"),
        "training_load": activity.get("training_load"),
    }


def _combined_latest(
    first: dict | None,
    second: dict | None,
    first_key: str,
    second_key: str,
) -> dict | None:
    observations = [item for item in (first, second) if item is not None]
    if not observations:
        return None
    latest_date = max(item["date"] for item in observations)
    return {
        "date": latest_date,
        first_key: first.get("value") if first and first["date"] == latest_date else None,
        second_key: (
            second.get("label") if second and second["date"] == latest_date else None
        ),
    }


def _objective_sleep_latest(recovery: dict) -> dict | None:
    duration = recovery["objective"]["sleep"]["duration_hours"]["latest"]
    score = recovery["objective"]["sleep"]["score"]["latest"]
    observations = [item for item in (duration, score) if item is not None]
    if not observations:
        return None
    latest_date = max(item["date"] for item in observations)
    return {
        "date": latest_date,
        "hours": (
            duration["value"] if duration and duration["date"] == latest_date else None
        ),
        "score": score["value"] if score and score["date"] == latest_date else None,
    }


def _latest_subjective(metric: dict) -> dict | None:
    latest = metric["latest"]
    if latest is None:
        return None
    return {"date": latest["date"], "value": latest["label"]}


def _timeline(
    as_of: date,
    activities: list[dict],
    wellness: list[dict],
    local_history: dict[str, dict],
) -> list[dict]:
    activity_days: dict[str, list[dict]] = {}
    for activity in activities:
        if activity_date := _record_date(activity):
            activity_days.setdefault(activity_date, []).append(activity)
    wellness_days = {
        record_date: record
        for record in wellness
        if (record_date := _record_date(record)) is not None
    }

    rows = []
    for offset in range(TIMELINE_DAYS):
        row_date = (as_of - timedelta(days=offset)).isoformat()
        day_activities = activity_days.get(row_date, [])
        day_wellness = wellness_days.get(row_date, {})
        local = local_history.get(row_date, {})
        rows.append(
            {
                "date": row_date,
                "training": {
                    "activity_count": len(day_activities),
                    "training_load": sum(
                        item["training_load"]
                        for item in day_activities
                        if item.get("training_load") is not None
                    ),
                    "distance_km": round(
                        sum(
                            item["distance_km"]
                            for item in day_activities
                            if item.get("distance_km") is not None
                        ),
                        2,
                    ),
                    "moving_time_min": round(
                        sum(
                            item["moving_time_min"]
                            for item in day_activities
                            if item.get("moving_time_min") is not None
                        ),
                        1,
                    ),
                },
                "steps": day_wellness.get("steps"),
                "resting_hr": day_wellness.get("resting_hr"),
                "hrv_rmssd": day_wellness.get("hrv_rmssd"),
                "sleep": {
                    "objective_hours": day_wellness.get("sleep_hours"),
                    "objective_score": day_wellness.get("sleep_score"),
                    "reported_hours": day_wellness.get("reported_sleep_hours"),
                    "reported_quality": day_wellness.get(
                        "reported_sleep_quality"
                    ),
                },
                "subjective": {
                    "fatigue": day_wellness.get("reported_fatigue"),
                    "soreness": day_wellness.get("reported_soreness"),
                    "stress": day_wellness.get("reported_stress"),
                    "mood": day_wellness.get("reported_mood"),
                    "motivation": day_wellness.get("reported_motivation"),
                },
                "context_tags": [
                    {"tag": context["tag"], "source": context["source"]}
                    for context in local.get("context", [])
                ],
                "journal": local.get("journal_text"),
            }
        )
    return rows


def athlete_context(as_of: str | None = None) -> dict:
    """Return deterministic, provider-independent current athlete context."""

    as_of_date = _as_of_date(as_of)
    activities = get_activities_normalized(
        as_of_date - timedelta(days=HISTORY_DAYS - 1), as_of_date
    )
    wellness = get_wellness_normalized(
        as_of_date - timedelta(days=BACKGROUND_DAYS - 1), as_of_date
    )
    training = build_training_context(activities, as_of_date)
    recovery = build_recovery_context(wellness, as_of_date)
    local_history = journal_history(TIMELINE_DAYS, as_of=as_of_date)

    subjective = recovery["subjective"]
    rhr = recovery["objective"]["resting_hr"]
    hrv = recovery["objective"]["hrv"]
    objective_sleep = recovery["objective"]["sleep"]["duration_hours"]
    reported_sleep = subjective["sleep_duration_hours"]

    recent_tags = [
        {"date": entry_date, "tag": item["tag"], "source": item["source"]}
        for entry_date, entry in local_history.items()
        for item in entry.get("context", [])
    ]
    recent_tags.sort(key=lambda item: (item["date"], item["tag"]), reverse=True)

    return {
        "as_of": as_of_date.isoformat(),
        "training": {
            "recent_load": training["load"]["recent"]["observed_value"],
            "background_load": training["load"]["background"]["observed_value"],
            "totals": {
                key: training["load"]["totals"][key]
                for key in ("3d", "7d", "28d")
            },
            "last_activity": _latest_activity(activities, as_of_date),
        },
        "recovery": {
            "resting_hr": {"latest": rhr["latest"]},
            "hrv": {"latest": hrv["latest"]},
            "sleep": {
                "objective_latest": _objective_sleep_latest(recovery),
                "reported_latest": _combined_latest(
                    reported_sleep["latest"],
                    subjective["sleep_quality"]["latest"],
                    "hours",
                    "quality",
                ),
            },
        },
        "subjective": {
            key: _latest_subjective(subjective[key])
            for key in ("fatigue", "soreness", "stress", "mood", "motivation")
        },
        "life_context": {
            "latest_journal": latest_daily_journal(as_of_date.isoformat()),
            "recent_tags": recent_tags,
        },
        "recent_timeline": _timeline(
            as_of_date, activities, wellness, local_history
        ),
        "data_quality": {
            "resting_hr": {
                "recent_7d_coverage_pct": rhr["recent_7d"]["coverage_pct"],
                "comparability": rhr["recent_7d"]["measurement_context"][
                    "comparability"
                ],
            },
            "hrv": {
                "recent_7d_coverage_pct": hrv["recent_7d"]["coverage_pct"]
            },
            "objective_sleep": {
                "recent_7d_coverage_pct": objective_sleep["recent_7d"][
                    "coverage_pct"
                ]
            },
            "reported_sleep": {
                "recent_7d_coverage_pct": reported_sleep["recent_7d"][
                    "coverage_pct"
                ]
            },
        },
    }
