from datetime import date

from athlete_os.services.intervals_client import (
    get_recent_wellness_normalized,
    get_wellness_normalized,
)


def recent_wellness(days: int = 7) -> list[dict]:
    """Return recent wellness data in normalized Athlete OS format."""

    return get_recent_wellness_normalized(days)


def wellness(oldest: str, newest: str) -> list[dict]:
    """Return wellness data between two dates in Athlete OS format.

    Dates must use YYYY-MM-DD format.
    """

    oldest_date = date.fromisoformat(oldest)
    newest_date = date.fromisoformat(newest)

    if oldest_date > newest_date:
        raise ValueError("oldest must be before or equal to newest")

    return get_wellness_normalized(oldest_date, newest_date)