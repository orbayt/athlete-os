import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from athlete_os.services.checkin_completion import (
    daily_checkin_is_complete,
    record_daily_checkin_if_complete,
)
from athlete_os.services.journal_store import (
    daily_checkin_exists,
    record_daily_checkin,
)


def complete_values():
    return {
        "sleep_hours": 8.0,
        "sleep_quality": "excellent",
        "fatigue": "none",
        "soreness": "none",
        "stress": "none",
        "mood": "good",
        "motivation": "excellent",
    }


class CheckinCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        db_path = Path(self.temporary_directory.name) / "checkin.sqlite3"
        self.environment = patch.dict(
            os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}
        )
        self.environment.start()
        self.day = (date.today() - timedelta(days=1)).isoformat()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_complete_structured_checkin_records_marker(self):
        values = complete_values()

        self.assertTrue(daily_checkin_is_complete(values))
        self.assertTrue(record_daily_checkin_if_complete(self.day, values))
        self.assertTrue(daily_checkin_exists(self.day))

    def test_partial_values_do_not_record_marker(self):
        values = complete_values()
        values["motivation"] = None

        self.assertFalse(daily_checkin_is_complete(values))
        self.assertFalse(record_daily_checkin_if_complete(self.day, values))
        self.assertFalse(daily_checkin_exists(self.day))

    def test_partial_edit_preserves_existing_completion(self):
        record_daily_checkin(self.day)

        self.assertFalse(
            record_daily_checkin_if_complete(self.day, {"fatigue": "low"})
        )
        self.assertTrue(daily_checkin_exists(self.day))


if __name__ == "__main__":
    unittest.main()
