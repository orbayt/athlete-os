import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from athlete_os.services.execution_store import (
    delete_manual_execution,
    get_daily_execution,
    resolve_daily_execution,
    upsert_manual_execution,
)


class ExecutionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "execution.sqlite3"
        self.environment = patch.dict(
            os.environ, {"ATHLETE_OS_DB_PATH": str(self.db_path)}
        )
        self.environment.start()
        self.day = (date.today() - timedelta(days=1)).isoformat()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_manual_execution_types_round_trip(self):
        for execution_type in ("REST", "ACTIVE_RECOVERY", "ACTIVITY"):
            with self.subTest(execution_type=execution_type):
                stored = upsert_manual_execution(
                    self.day, execution_type, "  Athlete note  "
                )
                loaded = get_daily_execution(self.day)

                self.assertEqual(stored, loaded)
                self.assertEqual(loaded.execution_type, execution_type)
                self.assertEqual(loaded.source, "manual")
                self.assertEqual(loaded.notes, "Athlete note")
                self.assertIsNone(loaded.activity_id)

    def test_provider_activity_is_neutral_activity_without_detail_duplication(self):
        for activity_type in ("Walk", "Run"):
            with self.subTest(activity_type=activity_type):
                activity = {
                    "id": 812,
                    "date": f"{self.day}T07:00:00",
                    "type": activity_type,
                    "distance_km": 8.2,
                    "moving_time_min": 45,
                    "training_load": 62,
                    "average_hr": 148,
                }

                execution = resolve_daily_execution(self.day, [activity])

                self.assertEqual(execution.execution_type, "ACTIVITY")
                self.assertNotIn(
                    execution.execution_type, {"TRAINING", "ACTIVE_RECOVERY"}
                )
                self.assertEqual(execution.source, "provider")
                self.assertEqual(execution.activity_id, "812")
                self.assertIsNone(get_daily_execution(self.day))
                self.assertEqual(
                    set(execution.model_dump()),
                    {
                        "date",
                        "execution_type",
                        "source",
                        "activity_id",
                        "notes",
                        "created_at",
                        "updated_at",
                    },
                )

    def test_no_activity_is_unknown_not_rest(self):
        execution = resolve_daily_execution(self.day, [])

        self.assertEqual(execution.execution_type, "UNKNOWN")
        self.assertEqual(execution.source, "derived")
        self.assertIsNone(get_daily_execution(self.day))

    def test_provider_evidence_takes_precedence_without_mutating_manual_row(self):
        manual = upsert_manual_execution(self.day, "REST", "Planned rest")

        resolved = resolve_daily_execution(
            self.day, [{"id": "run-1", "date": self.day, "type": "Run"}]
        )

        self.assertEqual(resolved.source, "provider")
        self.assertEqual(resolved.execution_type, "ACTIVITY")
        self.assertEqual(get_daily_execution(self.day), manual)

    def test_delete_only_removes_manual_execution(self):
        upsert_manual_execution(self.day, "ACTIVE_RECOVERY")

        self.assertTrue(delete_manual_execution(self.day))
        self.assertIsNone(get_daily_execution(self.day))
        self.assertFalse(delete_manual_execution(self.day))

    def test_schema_contains_only_normalized_execution_fields(self):
        upsert_manual_execution(self.day, "ACTIVITY")
        with closing(sqlite3.connect(self.db_path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(daily_execution)")
            }

        self.assertEqual(
            columns,
            {
                "date",
                "execution_type",
                "source",
                "activity_id",
                "notes",
                "created_at",
                "updated_at",
            },
        )

    def test_legacy_training_row_migrates_to_activity(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE daily_execution (
                    date TEXT PRIMARY KEY,
                    execution_type TEXT NOT NULL CHECK (execution_type IN
                        ('REST', 'ACTIVE_RECOVERY', 'TRAINING', 'UNKNOWN')),
                    source TEXT NOT NULL,
                    activity_id TEXT NULL,
                    notes TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """INSERT INTO daily_execution
                   VALUES (?, 'TRAINING', 'manual', NULL, NULL,
                           '2026-08-01T08:00:00+00:00',
                           '2026-08-01T08:00:00+00:00')""",
                (self.day,),
            )
            connection.commit()

        migrated = get_daily_execution(self.day)

        self.assertEqual(migrated.execution_type, "ACTIVITY")
        self.assertEqual(migrated.source, "manual")


if __name__ == "__main__":
    unittest.main()
