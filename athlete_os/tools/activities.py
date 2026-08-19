from datetime import date

from athlete_os.services.intervals_client import (
    get_activities_normalized,
    get_recent_activities_normalized,
)


def recent_activities(days: int = 30) -> list[dict]:
    """Return recent activities in normalized Athlete OS format."""

    return get_recent_activities_normalized(days)


def activities(oldest: str, newest: str) -> list[dict]:
    """Return activities between two dates in Athlete OS format.

    Dates must use YYYY-MM-DD format.
    """

    oldest_date = date.fromisoformat(oldest)
    newest_date = date.fromisoformat(newest)

    if oldest_date > newest_date:
        raise ValueError("oldest must be before or equal to newest")

    return get_activities_normalized(oldest_date, newest_date)