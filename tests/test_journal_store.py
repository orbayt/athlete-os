import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from athlete_os.services.journal_store import (
    database_path,
    journal_history,
    replace_manual_context,
    save_daily_journal,
)


class JournalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "journal.sqlite3"
        self.environment = patch.dict(
            os.environ,
            {"ATHLETE_OS_DB_PATH": str(self.db_path)},
        )
        self.environment.start()
        self.day = (date.today() - timedelta(days=1)).isoformat()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_override_path_and_raw_journal_are_persisted(self):
        raw_text = "Line one.\n  Meaningful inner spacing stays."

        save_daily_journal(self.day, raw_text)

        self.assertEqual(database_path(), self.db_path)
        self.assertTrue(self.db_path.exists())
        self.assertEqual(journal_history()[self.day]["journal_text"], raw_text)

    def test_saving_same_date_updates_text_and_preserves_created_at(self):
        save_daily_journal(self.day, "First entry")
        first = journal_history()[self.day]

        save_daily_journal(self.day, "Updated entry")
        updated = journal_history()[self.day]

        self.assertEqual(updated["journal_text"], "Updated entry")
        self.assertEqual(updated["created_at"], first["created_at"])

    def test_manual_context_tags_are_persisted_with_manual_metadata(self):
        replace_manual_context(self.day, ["travel", "alcohol"])

        contexts = journal_history()[self.day]["context"]
        self.assertEqual(
            {context["tag"] for context in contexts},
            {"travel", "alcohol"},
        )
        self.assertTrue(all(context["source"] == "manual" for context in contexts))
        self.assertTrue(all(context["confirmed"] for context in contexts))
        self.assertTrue(all(context["confidence"] is None for context in contexts))

    def test_manual_context_tags_can_be_replaced_without_touching_ai(self):
        replace_manual_context(self.day, ["travel", "work_stress"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO daily_context
                    (date, tag, source, confidence, confirmed, created_at)
                VALUES (?, ?, 'ai', ?, 0, ?)
                """,
                (self.day, "illness", 0.84, "2026-01-01T00:00:00+00:00"),
            )
            connection.commit()

        replace_manual_context(self.day, ["family_load"])

        contexts = journal_history()[self.day]["context"]
        self.assertEqual(
            {(context["tag"], context["source"]) for context in contexts},
            {("family_load", "manual"), ("illness", "ai")},
        )

    def test_invalid_context_tag_is_rejected_without_modifying_existing_tags(self):
        replace_manual_context(self.day, ["travel"])

        with self.assertRaisesRegex(ValueError, "invalid context tag"):
            replace_manual_context(self.day, ["made_up"])

        contexts = journal_history()[self.day]["context"]
        self.assertEqual([context["tag"] for context in contexts], ["travel"])

    def test_event_support_is_rejected_for_new_entries(self):
        with self.assertRaisesRegex(ValueError, "invalid context tag"):
            replace_manual_context(self.day, ["event_support"])

    def test_future_dates_are_rejected(self):
        future = (date.today() + timedelta(days=1)).isoformat()

        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            save_daily_journal(future, "Tomorrow")
        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            replace_manual_context(future, ["travel"])


if __name__ == "__main__":
    unittest.main()
