from datetime import date, timedelta
from math import exp, log

from athlete_os.services.intervals_client import (
    get_recent_wellness_normalized,
)


RECENT_DAYS = 7
BACKGROUND_DAYS = 42
ORDINAL_SCORES = {
    "reported_sleep_quality": {
        "none": 0,
        "poor": 1,
        "average": 2,
        "good": 3,
        "excellent": 4,
    },
    "reported_fatigue": {
        "none": 0,
        "low": 1,
        "average": 2,
        "high": 3,
        "extreme": 4,
    },
    "reported_soreness": {
        "none": 0,
        "low": 1,
        "average": 2,
        "high": 3,
        "extreme": 4,
    },
    "reported_stress": {
        "none": 0,
        "low": 1,
        "average": 2,
        "high": 3,
        "extreme": 4,
    },
    "reported_mood": {
        "poor": 1,
        "average": 2,
        "good": 3,
        "excellent": 4,
    },
    "reported_motivation": {
        "none": 0,
        "poor": 1,
        "average": 2,
        "good": 3,
        "excellent": 4,
    },
}


def _record_date(record: dict) -> date | None:
    value = record.get("date")
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _window_records(
    records_by_date: dict[date, dict], as_of: date, window_days: int
) -> list[tuple[date, dict]]:
    oldest = as_of - timedelta(days=window_days - 1)
    return [
        (record_date, record)
        for record_date, record in records_by_date.items()
        if oldest <= record_date <= as_of
    ]


def _coverage(observed_days: int, window_days: int) -> float:
    return round(observed_days / window_days * 100, 1)


def _numeric_window(
    records_by_date: dict[date, dict],
    as_of: date,
    field: str,
    window_days: int,
) -> dict:
    values = [
        record[field]
        for _, record in _window_records(records_by_date, as_of, window_days)
        if record.get(field) is not None
    ]
    return {
        "mean": round(sum(values) / len(values), 2) if values else None,
        "observed_days": len(values),
        "window_days": window_days,
        "coverage_pct": _coverage(len(values), window_days),
    }


def _latest_numeric(
    records_by_date: dict[date, dict], field: str
) -> dict | None:
    observations = [
        (record_date, record[field])
        for record_date, record in records_by_date.items()
        if record.get(field) is not None
    ]
    if not observations:
        return None
    record_date, value = max(observations, key=lambda item: item[0])
    return {"date": record_date.isoformat(), "value": value}


def _numeric_metric(
    records_by_date: dict[date, dict], as_of: date, field: str
) -> dict:
    return {
        "latest": _latest_numeric(records_by_date, field),
        "recent_7d": _numeric_window(
            records_by_date, as_of, field, RECENT_DAYS
        ),
        "background_42d": _numeric_window(
            records_by_date, as_of, field, BACKGROUND_DAYS
        ),
    }


def _rhr_measurement_context(
    records_by_date: dict[date, dict], as_of: date, window_days: int
) -> dict:
    rhr_records = [
        record
        for _, record in _window_records(records_by_date, as_of, window_days)
        if record.get("resting_hr") is not None
    ]
    supported_days = sum(
        record.get("sleep_hours") is not None for record in rhr_records
    )
    unknown_days = len(rhr_records) - supported_days

    if not rhr_records:
        comparability = "no_data"
    elif supported_days == len(rhr_records):
        comparability = "consistent_overnight_supported"
    elif unknown_days == len(rhr_records):
        comparability = "consistent_unknown"
    else:
        comparability = "mixed"

    return {
        "overnight_supported_days": supported_days,
        "overnight_unknown_days": unknown_days,
        "comparability": comparability,
    }


def _resting_hr_metric(records_by_date: dict[date, dict], as_of: date) -> dict:
    metric = _numeric_metric(records_by_date, as_of, "resting_hr")
    if metric["latest"] is not None:
        latest_date = date.fromisoformat(metric["latest"]["date"])
        metric["latest"]["measurement_context"] = (
            "overnight_supported"
            if records_by_date[latest_date].get("sleep_hours") is not None
            else "overnight_unknown"
        )
    metric["recent_7d"]["measurement_context"] = _rhr_measurement_context(
        records_by_date, as_of, RECENT_DAYS
    )
    metric["background_42d"]["measurement_context"] = (
        _rhr_measurement_context(records_by_date, as_of, BACKGROUND_DAYS)
    )
    return metric


def _hrv_window(
    records_by_date: dict[date, dict], as_of: date, window_days: int
) -> dict:
    values = [
        record["hrv_rmssd"]
        for _, record in _window_records(records_by_date, as_of, window_days)
        if record.get("hrv_rmssd") is not None
        and record["hrv_rmssd"] > 0
    ]
    mean_ln_rmssd = (
        sum(log(value) for value in values) / len(values) if values else None
    )
    return {
        "mean_ln_rmssd": (
            round(mean_ln_rmssd, 2) if mean_ln_rmssd is not None else None
        ),
        "geometric_mean_rmssd": (
            round(exp(mean_ln_rmssd), 2)
            if mean_ln_rmssd is not None
            else None
        ),
        "observed_days": len(values),
        "window_days": window_days,
        "coverage_pct": _coverage(len(values), window_days),
    }


def _hrv_metric(records_by_date: dict[date, dict], as_of: date) -> dict:
    observations = [
        (record_date, record["hrv_rmssd"])
        for record_date, record in records_by_date.items()
        if record.get("hrv_rmssd") is not None
        and record["hrv_rmssd"] > 0
    ]
    latest = None
    if observations:
        record_date, value = max(observations, key=lambda item: item[0])
        latest = {"date": record_date.isoformat(), "rmssd_ms": value}

    return {
        "latest": latest,
        "recent_7d": _hrv_window(records_by_date, as_of, RECENT_DAYS),
        "background_42d": _hrv_window(
            records_by_date, as_of, BACKGROUND_DAYS
        ),
    }


def _ordinal_window(
    records_by_date: dict[date, dict],
    as_of: date,
    field: str,
    window_days: int,
) -> dict:
    scores = ORDINAL_SCORES[field]
    values = [
        scores[record[field]]
        for _, record in _window_records(records_by_date, as_of, window_days)
        if record.get(field) in scores
    ]
    return {
        "mean_score": (
            round(sum(values) / len(values), 2) if values else None
        ),
        "observed_days": len(values),
        "window_days": window_days,
        "coverage_pct": _coverage(len(values), window_days),
    }


def _ordinal_metric(
    records_by_date: dict[date, dict], as_of: date, field: str
) -> dict:
    scores = ORDINAL_SCORES[field]
    observations = [
        (record_date, record[field])
        for record_date, record in records_by_date.items()
        if record.get(field) in scores
    ]
    latest = None
    if observations:
        record_date, label = max(observations, key=lambda item: item[0])
        latest = {
            "date": record_date.isoformat(),
            "label": label,
            "score": scores[label],
        }

    return {
        "latest": latest,
        "recent_7d": _ordinal_window(
            records_by_date, as_of, field, RECENT_DAYS
        ),
        "background_42d": _ordinal_window(
            records_by_date, as_of, field, BACKGROUND_DAYS
        ),
    }


def _context_notes(
    records_by_date: dict[date, dict], as_of: date
) -> dict:
    notes = [
        {
            "date": record_date.isoformat(),
            "text": record["context_note"],
        }
        for record_date, record in records_by_date.items()
        if isinstance(record.get("context_note"), str)
        and record["context_note"].strip()
    ]
    notes.sort(key=lambda note: note["date"], reverse=True)
    recent_start = as_of - timedelta(days=RECENT_DAYS - 1)

    return {
        "latest": notes[0] if notes else None,
        "recent_7d": [
            note
            for note in notes
            if date.fromisoformat(note["date"]) >= recent_start
        ],
    }


def build_recovery_context(wellness: list[dict], as_of: date) -> dict:
    """Build deterministic recovery context from normalized wellness data."""

    history_start = as_of - timedelta(days=BACKGROUND_DAYS - 1)
    records_by_date = {}
    for record in wellness:
        record_date = _record_date(record)
        if record_date is not None and history_start <= record_date <= as_of:
            records_by_date[record_date] = record

    return {
        "as_of": as_of.isoformat(),
        "model": {
            "recent_window_days": RECENT_DAYS,
            "background_window_days": BACKGROUND_DAYS,
            "missing_data_policy": "exclude_and_report",
            "hrv_transform": "ln_rmssd",
            "ordinal_scale_direction": {
                "sleep_quality": "higher_is_better",
                "fatigue": "higher_is_worse",
                "soreness": "higher_is_worse",
                "stress": "higher_is_worse",
                "mood": "higher_is_better",
                "motivation": "higher_is_better",
            },
        },
        "objective": {
            "resting_hr": _resting_hr_metric(records_by_date, as_of),
            "hrv": _hrv_metric(records_by_date, as_of),
            "sleep": {
                "duration_hours": _numeric_metric(
                    records_by_date, as_of, "sleep_hours"
                ),
                "score": _numeric_metric(
                    records_by_date, as_of, "sleep_score"
                ),
            },
        },
        "subjective": {
            "sleep_duration_hours": _numeric_metric(
                records_by_date, as_of, "reported_sleep_hours"
            ),
            "sleep_quality": _ordinal_metric(
                records_by_date, as_of, "reported_sleep_quality"
            ),
            "fatigue": _ordinal_metric(
                records_by_date, as_of, "reported_fatigue"
            ),
            "soreness": _ordinal_metric(
                records_by_date, as_of, "reported_soreness"
            ),
            "stress": _ordinal_metric(
                records_by_date, as_of, "reported_stress"
            ),
            "mood": _ordinal_metric(
                records_by_date, as_of, "reported_mood"
            ),
            "motivation": _ordinal_metric(
                records_by_date, as_of, "reported_motivation"
            ),
        },
        "contextual": {
            "context_notes": _context_notes(records_by_date, as_of),
        },
        "data_coverage": {
            "wellness_records": len(records_by_date),
        },
    }


def recovery_context() -> dict:
    """Return provider-independent objective and subjective recovery context."""

    as_of = date.today()
    wellness = get_recent_wellness_normalized(BACKGROUND_DAYS)
    return build_recovery_context(wellness, as_of)
