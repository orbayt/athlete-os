import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


CONTEXT_TAG_LABELS = {
    "travel": "Travel",
    "illness": "Illness",
    "injury_niggle": "Injury / niggle",
    "work_stress": "Work stress",
    "family_load": "Family load",
    "environmental_stress": "Environmental stress",
    "sleep_disruption": "Sleep disruption",
    "unusual_time_on_feet": "Unusual time on feet",
    "alcohol": "Alcohol / hangover",
}
CONTEXT_TAGS = set(CONTEXT_TAG_LABELS)
JOURNAL_SOURCES = {"manual", "migrated_legacy"}
INJURY_IMPACTS = {"training_only", "daily_noticeable", "daily_limiting"}
INJURY_TRENDS = {"better", "same", "worse"}


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
            source TEXT NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'migrated_legacy')),
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
            injury_impact TEXT NULL,
            injury_trend TEXT NULL,
            PRIMARY KEY (date, tag, source)
        );

        CREATE TABLE IF NOT EXISTS daily_checkin (
            date TEXT PRIMARY KEY,
            submitted_at TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('manual'))
        );
        """
    )
    journal_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(daily_journal)")
    }
    if "source" not in journal_columns:
        connection.execute(
            """
            ALTER TABLE daily_journal
            ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'migrated_legacy'))
            """
        )
    context_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(daily_context)")
    }
    if "injury_impact" not in context_columns:
        connection.execute("ALTER TABLE daily_context ADD COLUMN injury_impact TEXT NULL")
    if "injury_trend" not in context_columns:
        connection.execute("ALTER TABLE daily_context ADD COLUMN injury_trend TEXT NULL")
    return connection


def record_daily_checkin(date_value: str, source: str = "manual") -> None:
    _validated_date(date_value)
    if source != "manual":
        raise ValueError("invalid check-in source")
    submitted_at = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """INSERT INTO daily_checkin (date, submitted_at, source)
                   VALUES (?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       submitted_at = excluded.submitted_at,
                       source = excluded.source""",
                (date_value, submitted_at, source),
            )


def delete_daily_checkin(date_value: str) -> None:
    _validated_date(date_value)
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM daily_checkin WHERE date = ?", (date_value,)
            )


def daily_checkin_exists(date_value: str) -> bool:
    _validated_date(date_value)
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT 1 FROM daily_checkin WHERE date = ?", (date_value,)
        ).fetchone()
    return row is not None


def save_daily_journal(
    date_value: str, text: str, source: str = "manual"
) -> None:
    _validated_date(date_value)
    if not isinstance(text, str) or not text:
        raise ValueError("journal text must not be empty")
    if source not in JOURNAL_SOURCES:
        raise ValueError("invalid journal source")

    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO daily_journal
                    (date, text, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    text = excluded.text,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (date_value, text, source, now, now),
            )


def delete_daily_journal(date_value: str) -> None:
    _validated_date(date_value)
    with closing(_connect()) as connection:
        with connection:
            connection.execute(
                "DELETE FROM daily_journal WHERE date = ?", (date_value,)
            )


def replace_manual_context(
    date_value: str,
    tags: Iterable[str],
    injury_impact: str | None = None,
    injury_trend: str | None = None,
) -> None:
    _validated_date(date_value)
    validated_tags = validate_context_tags(tags)
    if "injury_niggle" not in validated_tags:
        injury_impact = None
        injury_trend = None
    if injury_impact is not None and injury_impact not in INJURY_IMPACTS:
        raise ValueError("invalid injury impact")
    if injury_trend is not None and injury_trend not in INJURY_TRENDS:
        raise ValueError("invalid injury trend")
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
                    (date, tag, source, confidence, confirmed, created_at,
                     injury_impact, injury_trend)
                VALUES (?, ?, 'manual', NULL, 1, ?, ?, ?)
                """,
                [
                    (
                        date_value,
                        tag,
                        now,
                        injury_impact if tag == "injury_niggle" else None,
                        injury_trend if tag == "injury_niggle" else None,
                    )
                    for tag in validated_tags
                ],
            )


def journal_history(days: int = 14, as_of: date | None = None) -> dict[str, dict]:
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    as_of = as_of or date.today()
    oldest = as_of - timedelta(days=days - 1)

    with closing(_connect()) as connection:
        journals = connection.execute(
            """
            SELECT date, text, source, created_at, updated_at
            FROM daily_journal
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC
            """,
            (oldest.isoformat(), as_of.isoformat()),
        ).fetchall()
        contexts = connection.execute(
            """
            SELECT date, tag, source, confidence, confirmed, created_at,
                   injury_impact, injury_trend
            FROM daily_context
            WHERE date BETWEEN ? AND ?
            ORDER BY date DESC, tag
            """,
            (oldest.isoformat(), as_of.isoformat()),
        ).fetchall()
        checkins = connection.execute(
            """SELECT date, submitted_at, source FROM daily_checkin
               WHERE date BETWEEN ? AND ? ORDER BY date DESC""",
            (oldest.isoformat(), as_of.isoformat()),
        ).fetchall()

    history: dict[str, dict] = {}
    for row in journals:
        history[row["date"]] = {
            "journal_text": row["text"],
            "journal_source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "context": [],
            "checkin_submitted": False,
        }
    for row in contexts:
        entry = history.setdefault(
            row["date"],
            {
                "journal_text": None,
                "journal_source": None,
                "created_at": None,
                "updated_at": None,
                "context": [],
                "checkin_submitted": False,
            },
        )
        entry["context"].append(
            {
                "tag": row["tag"],
                "source": row["source"],
                "confidence": row["confidence"],
                "confirmed": bool(row["confirmed"]),
                "created_at": row["created_at"],
                "injury_impact": row["injury_impact"],
                "injury_trend": row["injury_trend"],
            }
        )
    for row in checkins:
        entry = history.setdefault(
            row["date"],
            {
                "journal_text": None,
                "journal_source": None,
                "created_at": None,
                "updated_at": None,
                "context": [],
                "checkin_submitted": False,
            },
        )
        entry["checkin_submitted"] = True
        entry["checkin_submitted_at"] = row["submitted_at"]
        entry["checkin_source"] = row["source"]
    return history


def latest_daily_journal(as_of: str | None = None) -> dict | None:
    as_of_date = _validated_date(as_of) if as_of is not None else date.today()
    with closing(_connect()) as connection:
        row = connection.execute(
            """
            SELECT date, text
            FROM daily_journal
            WHERE date <= ?
            ORDER BY date DESC
            LIMIT 1
            """,
            (as_of_date.isoformat(),),
        ).fetchone()
    if row is None:
        return None
    return {"date": row["date"], "text": row["text"]}


def migrate_legacy_context_notes(wellness: list[dict]) -> dict:
    summary = {
        "scanned": len(wellness),
        "migrated": 0,
        "skipped_existing": 0,
        "skipped_empty": 0,
    }
    now = datetime.now(timezone.utc).isoformat()

    with closing(_connect()) as connection:
        with connection:
            for record in wellness:
                text = record.get("context_note")
                if not isinstance(text, str) or not text.strip():
                    summary["skipped_empty"] += 1
                    continue

                date_value = record.get("date")
                _validated_date(date_value)
                exists = connection.execute(
                    "SELECT 1 FROM daily_journal WHERE date = ?",
                    (date_value,),
                ).fetchone()
                if exists:
                    summary["skipped_existing"] += 1
                    continue

                connection.execute(
                    """
                    INSERT INTO daily_journal
                        (date, text, source, created_at, updated_at)
                    VALUES (?, ?, 'migrated_legacy', ?, ?)
                    """,
                    (date_value, text, now, now),
                )
                summary["migrated"] += 1

    return summary
