from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from athlete_os.services.intervals_client import get_recent_wellness_normalized
from athlete_os.services.journal_store import (
    journal_history,
    replace_manual_context,
    save_daily_journal,
    validate_context_tags,
)
from athlete_os.tools.recovery_checkin import record_recovery_checkin
from athlete_os.tools.recovery_context import recovery_context
from athlete_os.tools.training_context import training_context


app = FastAPI(title="Athlete OS")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _load_dashboard() -> tuple[dict | None, dict | None, list[str]]:
    errors = []
    training = None
    recovery = None

    try:
        training = training_context()
    except Exception as error:
        errors.append(f"Training data: {error}")

    try:
        recovery = recovery_context()
    except Exception as error:
        errors.append(f"Recovery data: {error}")

    return training, recovery, errors


def _optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _optional_float(value: str | None) -> float | None:
    value = _optional_text(value)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise ValueError("sleep_hours must be a number") from error


def _journal_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _has_recovery_values(values: dict) -> bool:
    return any(value is not None for value in values.values())


@app.get("/")
def dashboard(request: Request, message: str | None = None, error: str | None = None):
    training, recovery, provider_errors = _load_dashboard()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "training": training,
            "recovery": recovery,
            "provider_errors": provider_errors,
            "message": message,
            "error": error,
            "today": date.today().isoformat(),
        },
    )


def _save_checkin(submitted: dict[str, list[str]]) -> RedirectResponse:
    def field(name: str) -> str:
        return submitted.get(name, [""])[-1]

    date_value = field("date")
    sleep_hours = field("sleep_hours")
    sleep_quality = field("sleep_quality")
    fatigue = field("fatigue")
    soreness = field("soreness")
    stress = field("stress")
    mood = field("mood")
    motivation = field("motivation")
    journal_text = field("journal_text")
    context_tags = field("context_tags")
    local_saved = False
    recovery_saved = False
    try:
        normalized_date = _optional_text(date_value)
        normalized_journal = _journal_text(journal_text)
        submitted_tags = [tag for tag in context_tags.split(",") if tag]
        validated_tags = validate_context_tags(submitted_tags)
        recovery_values = {
            "date_value": normalized_date,
            "sleep_hours": _optional_float(sleep_hours),
            "sleep_quality": _optional_text(sleep_quality),
            "fatigue": _optional_text(fatigue),
            "soreness": _optional_text(soreness),
            "stress": _optional_text(stress),
            "mood": _optional_text(mood),
            "motivation": _optional_text(motivation),
            "context_note": normalized_journal,
        }

        if normalized_journal is not None or validated_tags:
            if normalized_date is None:
                normalized_date = date.today().isoformat()
                recovery_values["date_value"] = normalized_date
            if normalized_journal is not None:
                save_daily_journal(normalized_date, normalized_journal)
            replace_manual_context(normalized_date, validated_tags)
            local_saved = True

        if _has_recovery_values(
            {key: value for key, value in recovery_values.items() if key != "date_value"}
        ):
            result = record_recovery_checkin(**recovery_values)
            recovery_saved = True
            saved_date = result["date"]
        elif not local_saved:
            raise ValueError("at least one recovery value, journal entry, or context tag must be supplied")
        else:
            saved_date = normalized_date

        parts = []
        if local_saved:
            parts.append("journal context saved locally")
        if recovery_saved:
            parts.append("recovery check-in saved")
        query = urlencode({"message": f"{saved_date}: {'; '.join(parts)}."})
    except Exception as error:
        prefix = "Journal context was saved locally, but " if local_saved else ""
        query = urlencode({"error": f"{prefix}{error}"})

    return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/checkin")
async def save_checkin(request: Request):
    submitted = parse_qs(
        (await request.body()).decode("utf-8"), keep_blank_values=True
    )
    return _save_checkin(submitted)


@app.get("/history")
def history(request: Request, days: int = 14):
    selected_days = days if days in {14, 30, 60} else max(1, min(days, 90))
    today = date.today()
    local_error = None
    provider_error = None
    local = {}
    wellness = []

    try:
        local = journal_history(selected_days, as_of=today)
    except Exception as error:
        local_error = str(error)
    try:
        wellness = get_recent_wellness_normalized(selected_days)
    except Exception as error:
        provider_error = str(error)

    wellness_by_date = {
        record["date"][:10]: record
        for record in wellness
        if isinstance(record.get("date"), str)
    }
    entries = []
    for offset in range(selected_days):
        entry_date = (today - timedelta(days=offset)).isoformat()
        local_entry = local.get(entry_date, {})
        entries.append(
            {
                "date": entry_date,
                "wellness": wellness_by_date.get(entry_date),
                "journal_text": local_entry.get("journal_text"),
                "context": local_entry.get("context", []),
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={
            "entries": entries,
            "days": selected_days,
            "local_error": local_error,
            "provider_error": provider_error,
        },
    )


def main() -> None:
    uvicorn.run("athlete_os.ui.app:app", host="127.0.0.1", port=8000)
