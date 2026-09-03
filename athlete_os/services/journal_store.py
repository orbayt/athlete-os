import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


CONTEXT_TAGS = {
    "travel",
    "illness",
    "injury_niggle",
    "work_stress",
    "family_load",
    "environmental_stress",
    "sleep_disruption",
    "alcohol",
    "unusual_time_on_feet",
}


def database_path() -> Path:
    override = os.getenv("ATHLETE_OS_DB_PATH")
    if override:
        return Path(override).expanduser()

    data_home = os.getenv("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local/share"
    return base / "athlete-os" / "athlete_os.db"


def _validated_date(date_value: str) -> date:
    try:
        parsed = date.fromisoformat(date_value)
    except (TypeError, ValueError) as error:
        raise ValueError("date_value must use YYYY-MM-DD format") from error
    if parsed.isoformat() != date_value:
        raise ValueError("date_value must use YYYY-MM-DD format")
    if parsed > date.today():
        raise ValueError("date_value must not be in the future")
    return parsed


def validate_context_tags(tags: Iterable[str]) -> list[str]:
    unique_tags = list(dict.fromkeys(tags))
    invalid = sorted(set(unique_tags) - CONTEXT_TAGS)
    if invalid:
        raise ValueError(f"invalid context tag: {', '.join(invalid)}")
    return unique_tags


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_journal (
            date TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_context (
            date TEXT NOT NULL,
            tag TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('manual', 'ai')),
            confidence REAL NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (date, tag, source)
        );
        """
    )
    return connection


def save_daily_journal(date_value: str, text: str) -> None:
    _validated_date(date_value)
    if not isinstance(text, str) or not text:
        raise ValueError("journal text must not be empty")

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO daily_journal (date, text, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    text = excluded.text,
                    updated_at = excluded.updated_at
                """,
                (date_value, text, now, now),
            )


def replace_manual_context(date_value: str, tags: Iterable[str]) -> None:
    _validated_date(date_value)
    validated_tags = validate_context_tags(tags)
    now = datetime.now(timezone.utc).isoformat()

    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM daily_context WHERE date = ? AND source = 'manual'",
                (date_value,),
            )
            connection.executemany(
                """
                INSERT INTO daily_context
                    (date, tag, source, confidence, confirmed, created_at)
                VALUES (?, ?, 'manual', NULL, 1, ?)
                """,
                [(date_value, tag, now) for tag in validated_tags],
            )


def journal_history(days: int = 14, as_of: date | None = None) -> dict[str, dict]:
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    as_of = as_of or date.today()
    oldest = as_of - timedelta(days=days - 1)

    with closing(_connect()) as connection:
        journals = connection.execute(
            """
            SELECT date, text, created_at, updated_at
            FROM daily_journal
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            """,
            (oldest.isoformat(), as_of.isoformat()),
        ).fetchall()
        contexts = connection.execute(
            """
            SELECT date, tag, source, confidence, confirmed, created_at
            FROM daily_context
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, tag
            """,
            (oldest.isoformat(), as_of.isoformat()),
        ).fetchall()

    history: dict[str, dict] = {}
    for row in journals:
        history[row["date"]] = {
            "journal_text": row["text"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "context": [],
        }
    for row in contexts:
        entry = history.setdefault(
            row["date"],
            {"journal_text": None, "created_at": None, "updated_at": None, "context": []},
        )
        entry["context"].append(
            {
                "tag": row["tag"],
                "source": row["source"],
                "confidence": row["confidence"],
                "confirmed": bool(row["confirmed"]),
                "created_at": row["created_at"],
            }
        )
    return history
