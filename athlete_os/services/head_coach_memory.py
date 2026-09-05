import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from athlete_os.services.journal_store import database_path
from athlete_os.tools.head_coach import (
    HEAD_COACH_POLICY_VERSION,
    HeadCoachAssessment,
    HeadCoachSignals,
)


class HeadCoachDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    assessment_date: date
    created_at: datetime
    policy_version: str
    signals: HeadCoachSignals
    assessment: HeadCoachAssessment
    supersedes_decision_id: int | None = None


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS head_coach_decision (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            state TEXT NOT NULL,
            reality TEXT NOT NULL,
            interpretation TEXT NOT NULL,
            session_guidance TEXT NOT NULL,
            watch_for_json TEXT NOT NULL,
            next_decision TEXT NOT NULL,
            why_json TEXT NOT NULL,
            confidence TEXT NOT NULL,
            signals_json TEXT NOT NULL,
            supersedes_decision_id INTEGER NULL
                REFERENCES head_coach_decision(id)
        )
        """
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_head_coach_decision_date
           ON head_coach_decision (assessment_date, id DESC)"""
    )
    return connection


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _serialized(
    signals: HeadCoachSignals,
    assessment: HeadCoachAssessment,
    policy_version: str,
) -> dict[str, str]:
    return {
        "policy_version": policy_version,
        "state": assessment.state,
        "reality": assessment.reality,
        "interpretation": assessment.interpretation,
        "session_guidance": assessment.session_guidance,
        "watch_for_json": _canonical_json(assessment.watch_for),
        "next_decision": assessment.next_decision,
        "why_json": _canonical_json(assessment.why),
        "confidence": assessment.confidence,
        "signals_json": _canonical_json(signals.model_dump(mode="json")),
    }


def _row_to_record(row: sqlite3.Row) -> HeadCoachDecisionRecord:
    signals = HeadCoachSignals.model_validate_json(row["signals_json"])
    assessment = HeadCoachAssessment(
        date=date.fromisoformat(row["assessment_date"]),
        state=row["state"],
        reality=row["reality"],
        interpretation=row["interpretation"],
        session_guidance=row["session_guidance"],
        watch_for=json.loads(row["watch_for_json"]),
        next_decision=row["next_decision"],
        why=json.loads(row["why_json"]),
        confidence=row["confidence"],
    )
    return HeadCoachDecisionRecord(
        id=row["id"],
        assessment_date=row["assessment_date"],
        created_at=row["created_at"],
        policy_version=row["policy_version"],
        signals=signals,
        assessment=assessment,
        supersedes_decision_id=row["supersedes_decision_id"],
    )


def _same_snapshot(row: sqlite3.Row, values: dict[str, str]) -> bool:
    return all(row[field] == value for field, value in values.items())


def record_head_coach_decision(
    *,
    signals: HeadCoachSignals,
    assessment: HeadCoachAssessment,
    policy_version: str = HEAD_COACH_POLICY_VERSION,
) -> HeadCoachDecisionRecord:
    """Append a decision unless it exactly matches that date's latest event."""

    if assessment.date != signals.as_of:
        raise ValueError("assessment date must match signals as_of")
    if not policy_version:
        raise ValueError("policy_version must not be empty")

    values = _serialized(signals, assessment, policy_version)
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        latest = connection.execute(
            """SELECT * FROM head_coach_decision
               WHERE assessment_date = ? ORDER BY id DESC LIMIT 1""",
            (assessment.date.isoformat(),),
        ).fetchone()
        if latest is not None and _same_snapshot(latest, values):
            connection.commit()
            return _row_to_record(latest)

        created_at = datetime.now(timezone.utc).isoformat()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = connection.execute(
            f"""INSERT INTO head_coach_decision
                (assessment_date, created_at, {columns}, supersedes_decision_id)
                VALUES (?, ?, {placeholders}, ?)""",
            (
                assessment.date.isoformat(),
                created_at,
                *values.values(),
                latest["id"] if latest is not None else None,
            ),
        )
        row = connection.execute(
            "SELECT * FROM head_coach_decision WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        connection.commit()
    return _row_to_record(row)


def get_latest_head_coach_decision(
    *, assessment_date: date
) -> HeadCoachDecisionRecord | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            """SELECT * FROM head_coach_decision
               WHERE assessment_date = ? ORDER BY id DESC LIMIT 1""",
            (assessment_date.isoformat(),),
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_head_coach_decisions(
    *, assessment_date: date | None = None, limit: int = 100
) -> list[HeadCoachDecisionRecord]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    query = "SELECT * FROM head_coach_decision"
    parameters: list[object] = []
    if assessment_date is not None:
        query += " WHERE assessment_date = ?"
        parameters.append(assessment_date.isoformat())
    query += " ORDER BY assessment_date DESC, id DESC LIMIT ?"
    parameters.append(limit)
    with closing(_connect()) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_record(row) for row in rows]
