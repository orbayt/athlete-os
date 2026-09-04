from datetime import date

from athlete_os.services.intervals_client import (
    encode_recovery_checkin,
    update_wellness,
)
from athlete_os.services.journal_store import save_daily_journal


LABELS = {
    "sleep_quality": {"none", "poor", "average", "good", "excellent"},
    "fatigue": {"none", "low", "average", "high", "extreme"},
    "soreness": {"none", "low", "average", "high", "extreme"},
    "stress": {"none", "low", "average", "high", "extreme"},
    "mood": {"poor", "average", "good", "excellent"},
    "motivation": {"none", "poor", "average", "good", "excellent"},
}


def _normalize_label(field: str, value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in LABELS[field]:
        accepted = ", ".join(sorted(LABELS[field]))
        raise ValueError(f"{field} must be one of: {accepted}")
    return normalized


def _checkin_date(date_value: str | None) -> date:
    if date_value is None:
        return date.today()

    try:
        checkin_date = date.fromisoformat(date_value)
    except ValueError as error:
        raise ValueError("date_value must use YYYY-MM-DD format") from error

    if checkin_date.isoformat() != date_value:
        raise ValueError("date_value must use YYYY-MM-DD format")

    if checkin_date > date.today():
        raise ValueError("date_value must not be in the future")
    return checkin_date


def record_recovery_checkin(
    sleep_hours: float | None = None,
    sleep_quality: str | None = None,
    fatigue: str | None = None,
    soreness: str | None = None,
    stress: str | None = None,
    mood: str | None = None,
    motivation: str | None = None,
    context_note: str | None = None,
    date_value: str | None = None,
) -> dict:
    """Record recovery values and backwards-compatible Journal input."""

    checkin_date = _checkin_date(date_value)
    recorded = {}

    if sleep_hours is not None:
        if not 0 <= sleep_hours <= 24:
            raise ValueError("sleep_hours must be between 0 and 24")
        recorded["sleep_hours"] = sleep_hours

    labels = {
        "sleep_quality": sleep_quality,
        "fatigue": fatigue,
        "soreness": soreness,
        "stress": stress,
        "mood": mood,
        "motivation": motivation,
    }
    for field, value in labels.items():
        if value is not None:
            recorded[field] = _normalize_label(field, value)

    if context_note is not None:
        normalized_context_note = context_note.strip()
        if not normalized_context_note:
            raise ValueError("context_note must not be empty")
        recorded["context_note"] = normalized_context_note

    if (sleep_hours is None) != (sleep_quality is None):
        raise ValueError(
            "sleep_hours and sleep_quality must be supplied together"
        )

    if sleep_hours is not None:
        normalized_sleep_quality = recorded["sleep_quality"]
        if sleep_hours == 0 and normalized_sleep_quality != "none":
            raise ValueError(
                'sleep_quality must be "none" when sleep_hours is 0'
            )
        if sleep_hours > 0 and normalized_sleep_quality == "none":
            raise ValueError(
                'sleep_quality must not be "none" when sleep_hours is greater than 0'
            )

    if not recorded:
        raise ValueError("at least one recovery value must be supplied")

    if context_note is not None:
        save_daily_journal(
            checkin_date.isoformat(), recorded["context_note"]
        )

    provider_recorded = {
        key: value for key, value in recorded.items() if key != "context_note"
    }
    if provider_recorded:
        update_wellness(checkin_date, encode_recovery_checkin(provider_recorded))
    return {
        "date": checkin_date.isoformat(),
        "recorded": recorded,
    }
