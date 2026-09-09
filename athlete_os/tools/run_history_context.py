from athlete_os.services.run_history_context import get_run_history_context


def run_history_context(activity_id: str) -> dict:
    """Return factual, date-relative running-history context for an activity."""

    return get_run_history_context(activity_id)
