import logging
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from athlete_os.services.intervals_client import get_recent_wellness_normalized
from athlete_os.services.head_coach_context import get_daily_head_coach
from athlete_os.services.head_coach_memory import get_head_coach_decisions
from athlete_os.services.journal_store import (
    CONTEXT_TAG_LABELS,
    delete_daily_journal,
    journal_history,
    latest_daily_journal,
    replace_manual_context,
    record_daily_checkin,
    save_daily_journal,
    validate_context_tags,
)
from athlete_os.tools.recovery_checkin import record_recovery_checkin
from athlete_os.tools.recovery_context import recovery_context
from athlete_os.tools.training_context import training_context


app = FastAPI(title="Athlete OS")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
logger = logging.getLogger(__name__)


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


def _save_manual_context(
    date_value: str, tags: list[str], injury_impact: str, injury_trend: str
) -> None:
    if "injury_niggle" not in tags:
        replace_manual_context(date_value, tags)
        return
    replace_manual_context(
        date_value,
        tags,
        injury_impact=_optional_text(injury_impact),
        injury_trend=_optional_text(injury_trend),
    )


@app.get("/")
def dashboard(request: Request, message: str | None = None, error: str | None = None):
    today = date.today()
    training, recovery, provider_errors = _load_dashboard()
    head_coach = None
    head_coach_awaiting = False
    try:
        head_coach = get_daily_head_coach(as_of=today)
        head_coach_awaiting = head_coach is None
    except Exception:
        logger.exception(
            "Head Coach assessment generation failed for %s", today
        )
    latest_journal = None
    try:
        latest_journal = latest_daily_journal()
    except Exception as journal_error:
        provider_errors.append(f"Local journal: {journal_error}")
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "training": training,
            "recovery": recovery,
            "head_coach": head_coach,
            "head_coach_awaiting": head_coach_awaiting,
            "latest_journal": latest_journal,
            "provider_errors": provider_errors,
            "message": message,
            "error": error,
            "today": today.isoformat(),
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
    injury_impact = field("injury_impact")
    injury_trend = field("injury_trend")
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
            "context_note": None,
        }

        if normalized_date is None:
            normalized_date = date.today().isoformat()
            recovery_values["date_value"] = normalized_date
        if normalized_journal is not None:
            save_daily_journal(normalized_date, normalized_journal)
        _save_manual_context(
            normalized_date,
            validated_tags,
            injury_impact,
            injury_trend,
        )
        record_daily_checkin(normalized_date)
        local_saved = True

        if _has_recovery_values(
            {key: value for key, value in recovery_values.items() if key != "date_value"}
        ):
            result = record_recovery_checkin(**recovery_values)
            recovery_saved = True
            saved_date = result["date"]
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


def _history_days(days: int) -> int:
    return days if days in {14, 30, 60} else max(1, min(days, 90))


@app.get("/history")
def history(
    request: Request,
    days: int = 14,
    edit: str | None = None,
    message: str | None = None,
    error: str | None = None,
):
    selected_days = _history_days(days)
    today = date.today()
    local_error = None
    provider_error = None
    local = {}
    wellness = []
    coach_decisions_by_date = {}

    try:
        local = journal_history(selected_days, as_of=today)
    except Exception as error:
        local_error = str(error)
    try:
        wellness = get_recent_wellness_normalized(selected_days)
    except Exception as error:
        provider_error = str(error)
    try:
        oldest = today - timedelta(days=selected_days - 1)
        for decision in get_head_coach_decisions(limit=1000):
            if oldest <= decision.assessment_date <= today:
                coach_decisions_by_date.setdefault(
                    decision.assessment_date.isoformat(), []
                ).append(decision)
    except Exception:
        logger.exception("Head Coach decision history could not be loaded")

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
                "coach_decisions": coach_decisions_by_date.get(
                    entry_date, []
                ),
                "is_editing": entry_date == edit,
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
            "message": message,
            "error": error,
            "context_tag_labels": CONTEXT_TAG_LABELS,
        },
    )


def _save_history_edit(submitted: dict[str, list[str]]) -> RedirectResponse:
    def field(name: str) -> str:
        return submitted.get(name, [""])[-1]

    date_value = field("date")
    try:
        days = _history_days(int(field("days") or 14))
    except ValueError:
        days = 14
    local_saved = False

    try:
        journal = _journal_text(field("journal_text"))
        tags = validate_context_tags(submitted.get("context_tags", []))
        recovery_values = {
            "date_value": date_value,
            "sleep_hours": _optional_float(field("reported_sleep_hours")),
            "sleep_quality": _optional_text(field("reported_sleep_quality")),
            "fatigue": _optional_text(field("fatigue")),
            "soreness": _optional_text(field("soreness")),
            "stress": _optional_text(field("stress")),
            "mood": _optional_text(field("mood")),
            "motivation": _optional_text(field("motivation")),
            "context_note": None,
        }

        if journal is None:
            delete_daily_journal(date_value)
        else:
            save_daily_journal(date_value, journal)
        _save_manual_context(
            date_value,
            tags,
            field("injury_impact"),
            field("injury_trend"),
        )
        local_saved = True

        recovery_fields = {
            key: value
            for key, value in recovery_values.items()
            if key != "date_value"
        }
        if _has_recovery_values(recovery_fields):
            record_recovery_checkin(**recovery_values)
            message = f"{date_value}: journal context and recovery check-in saved."
        else:
            message = f"{date_value}: journal context saved locally."
        query = urlencode({"days": days, "message": message})
    except Exception as error:
        prefix = "Local journal context was saved, but " if local_saved else ""
        query = urlencode({"days": days, "error": f"{prefix}{error}"})

    return RedirectResponse(
        url=f"/history?{query}#day-{date_value}", status_code=303
    )


@app.post("/history/edit")
async def save_history_edit(request: Request):
    submitted = parse_qs(
        (await request.body()).decode("utf-8"), keep_blank_values=True
    )
    return _save_history_edit(submitted)


def main() -> None:
    uvicorn.run("athlete_os.ui.app:app", host="127.0.0.1", port=8000)
