from athlete_os.services.journal_store import record_daily_checkin


DAILY_CHECKIN_FIELDS = (
    "sleep_hours",
    "sleep_quality",
    "fatigue",
    "soreness",
    "stress",
    "mood",
    "motivation",
)


def daily_checkin_is_complete(values: dict) -> bool:
    """Return whether every canonical structured daily field was supplied."""

    return all(values.get(field) is not None for field in DAILY_CHECKIN_FIELDS)


def record_daily_checkin_if_complete(date_value: str, values: dict) -> bool:
    """Record the shared completion marker without clearing earlier completion."""

    if not daily_checkin_is_complete(values):
        return False
    record_daily_checkin(date_value)
    return True
