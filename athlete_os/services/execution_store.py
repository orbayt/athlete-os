import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict

from athlete_os.services.journal_store import database_path


ExecutionType = Literal["REST", "ACTIVE_RECOVERY", "ACTIVITY", "UNKNOWN"]
ExecutionSource = Literal["provider", "manual", "derived"]
MANUAL_EXECUTION_TYPES = {"REST", "ACTIVE_RECOVERY", "ACTIVITY"}


class DailyExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    execution_type: ExecutionType
    source: ExecutionSource
    activity_id: str | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    existing = connection.execute(
        """SELECT sql FROM sqlite_master
           WHERE type = 'table' AND name = 'daily_execution'"""
    ).fetchone()
    if existing is not None and "'TRAINING'" in existing["sql"]:
        connection.executescript(
            """
            BEGIN;
            CREATE TABLE daily_execution_v01 (
                date TEXT PRIMARY KEY,
                execution_type TEXT NOT NULL
                    CHECK (execution_type IN
                        ('REST', 'ACTIVE_RECOVERY', 'ACTIVITY', 'UNKNOWN')),
                source TEXT NOT NULL
                    CHECK (source IN ('provider', 'manual', 'derived')),
                activity_id TEXT NULL,
                notes TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO daily_execution_v01
                (date, execution_type, source, activity_id, notes,
                 created_at, updated_at)
            SELECT date,
                   CASE execution_type
                       WHEN 'TRAINING' THEN 'ACTIVITY'
                       ELSE execution_type
                   END,
                   source, activity_id, notes, created_at, updated_at
            FROM daily_execution;
            DROP TABLE daily_execution;
            ALTER TABLE daily_execution_v01 RENAME TO daily_execution;
            COMMIT;
            """
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_execution (
            date TEXT PRIMARY KEY,
            execution_type TEXT NOT NULL
                CHECK (execution_type IN
                    ('REST', 'ACTIVE_RECOVERY', 'ACTIVITY', 'UNKNOWN')),
            source TEXT NOT NULL
                CHECK (source IN ('provider', 'manual', 'derived')),
            activity_id TEXT NULL,
            notes TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return connection


def _row_to_execution(row: sqlite3.Row) -> DailyExecution:
    return DailyExecution(**dict(row))


def get_daily_execution(date_value: str) -> DailyExecution | None:
    _validated_date(date_value)
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM daily_execution WHERE date = ?", (date_value,)
        ).fetchone()
    return _row_to_execution(row) if row is not None else None


def upsert_manual_execution(
    date_value: str, execution_type: str, notes: str | None = None
) -> DailyExecution:
    _validated_date(date_value)
    if execution_type not in MANUAL_EXECUTION_TYPES:
        raise ValueError("invalid manual execution type")
    normalized_notes = notes.strip() if notes and notes.strip() else None
    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO daily_execution
                    (date, execution_type, source, activity_id, notes,
                     created_at, updated_at)
                VALUES (?, ?, 'manual', NULL, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    execution_type = excluded.execution_type,
                    source = 'manual',
                    activity_id = NULL,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (date_value, execution_type, normalized_notes, now, now),
            )
            row = connection.execute(
                "SELECT * FROM daily_execution WHERE date = ?", (date_value,)
            ).fetchone()
    return _row_to_execution(row)


def delete_manual_execution(date_value: str) -> bool:
    _validated_date(date_value)
    with closing(_connect()) as connection:
        with connection:
            cursor = connection.execute(
                """DELETE FROM daily_execution
                   WHERE date = ? AND source = 'manual'""",
                (date_value,),
            )
    return cursor.rowcount > 0


def resolve_daily_execution(
    date_value: str, activities: list[dict]
) -> DailyExecution:
    """Resolve factual execution, with provider activity as strongest evidence."""

    execution_date = _validated_date(date_value)
    dated_activities = [
        activity
        for activity in activities
        if isinstance(activity.get("date"), str)
        and activity["date"][:10] == date_value
    ]
    if dated_activities:
        activity_id = next(
            (
                str(activity["id"])
                for activity in dated_activities
                if activity.get("id") is not None
            ),
            None,
        )
        return DailyExecution(
            date=execution_date,
            execution_type="ACTIVITY",
            source="provider",
            activity_id=activity_id,
        )

    stored = get_daily_execution(date_value)
    if stored is not None:
        return stored
    return DailyExecution(
        date=execution_date,
        execution_type="UNKNOWN",
        source="derived",
    )
