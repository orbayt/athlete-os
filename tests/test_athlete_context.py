import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from athlete_os.tools.athlete_context import athlete_context


AS_OF = date(2026, 9, 3)


def day(days_ago):
    return (AS_OF - timedelta(days=days_ago)).isoformat()


class AthleteContextTests(unittest.TestCase):
    @patch("athlete_os.tools.athlete_context.latest_daily_journal")
    @patch("athlete_os.tools.athlete_context.journal_history")
    @patch("athlete_os.tools.athlete_context.get_wellness_normalized")
    @patch("athlete_os.tools.athlete_context.get_activities_normalized")
    def test_unified_context_contract_and_timeline(
        self, get_activities, get_wellness, get_journal_history, get_latest_journal
    ):
        get_activities.return_value = [
            {
                "id": 1,
                "date": f"{day(0)}T07:00:00",
                "type": "Run",
                "distance_km": 10.25,
                "moving_time_min": 60,
                "training_load": 50,
            },
            {
                "id": 2,
                "date": f"{day(0)}T18:00:00",
                "type": "Ride",
                "distance_km": 20,
                "moving_time_min": 45.5,
                "training_load": 30.5,
            },
            {
                "id": 3,
                "date": f"{day(2)}T08:00:00",
                "type": "Run",
                "distance_km": None,
                "moving_time_min": 30,
                "training_load": None,
            },
        ]
        get_wellness.return_value = [
            {
                "date": day(0),
                "resting_hr": 63,
                "hrv_rmssd": None,
                "sleep_hours": 7.25,
                "sleep_score": 82,
                "reported_sleep_hours": 6.5,
                "reported_sleep_quality": "average",
                "reported_soreness": "low",
                "reported_motivation": "good",
                "context_note": "Provider copy must not replace local journal.",
                "steps": 9000,
            },
            {
                "date": day(1),
                "resting_hr": 61,
                "sleep_hours": None,
                "reported_fatigue": "high",
                "reported_mood": "average",
                "steps": None,
            },
            {
                "date": day(2),
                "hrv_rmssd": 44,
                "reported_stress": "high",
            },
        ]
        get_journal_history.return_value = {
            day(0): {
                "journal_text": "Raw journal text stays exactly this way.",
                "journal_source": "migrated_legacy",
                "context": [
                    {"tag": "work_stress", "source": "manual"},
                    {"tag": "travel", "source": "ai"},
                ],
            }
        }
        get_latest_journal.return_value = {
            "date": day(0),
            "text": "Raw journal text stays exactly this way.",
        }

        result = athlete_context(AS_OF.isoformat())

        self.assertEqual(
            set(result),
            {
                "as_of",
                "training",
                "recovery",
                "subjective",
                "life_context",
                "recent_timeline",
                "data_quality",
            },
        )
        self.assertEqual(len(result["recent_timeline"]), 7)
        self.assertEqual(
            [row["date"] for row in result["recent_timeline"]],
            [day(offset) for offset in range(7)],
        )

        today = result["recent_timeline"][0]
        self.assertEqual(
            today["training"],
            {
                "activity_count": 2,
                "training_load": 80.5,
                "distance_km": 30.25,
                "moving_time_min": 105.5,
            },
        )
        missing_day = result["recent_timeline"][3]
        self.assertEqual(
            missing_day["training"],
            {
                "activity_count": 0,
                "training_load": 0,
                "distance_km": 0,
                "moving_time_min": 0,
            },
        )
        self.assertIsNone(missing_day["steps"])
        self.assertIsNone(missing_day["resting_hr"])
        self.assertIsNone(missing_day["sleep"]["objective_hours"])

        self.assertEqual(
            result["recovery"]["sleep"]["objective_latest"],
            {"date": day(0), "hours": 7.25, "score": 82},
        )
        self.assertEqual(
            result["recovery"]["sleep"]["reported_latest"],
            {"date": day(0), "hours": 6.5, "quality": "average"},
        )
        self.assertEqual(
            result["subjective"],
            {
                "fatigue": {"date": day(1), "value": "high"},
                "soreness": {"date": day(0), "value": "low"},
                "stress": {"date": day(2), "value": "high"},
                "mood": {"date": day(1), "value": "average"},
                "motivation": {"date": day(0), "value": "good"},
            },
        )
        self.assertEqual(
            result["life_context"]["latest_journal"],
            {
                "date": day(0),
                "text": "Raw journal text stays exactly this way.",
            },
        )
        self.assertEqual(
            today["context_tags"],
            [
                {"tag": "work_stress", "source": "manual"},
                {"tag": "travel", "source": "ai"},
            ],
        )
        self.assertEqual(today["journal"], "Raw journal text stays exactly this way.")
        self.assertTrue(
            all(
                "legacy_context" not in row
                for row in result["recent_timeline"]
            )
        )
        self.assertNotIn("latest_legacy_context", result["life_context"])
        self.assertEqual(
            result["recovery"]["resting_hr"]["latest"]["measurement_context"],
            "overnight_supported",
        )
        self.assertEqual(
            result["data_quality"]["resting_hr"],
            {"recent_7d_coverage_pct": 28.6, "comparability": "mixed"},
        )
        self.assertEqual(
            result["data_quality"]["hrv"]["recent_7d_coverage_pct"], 14.3
        )

        serialized = json.dumps(result).lower()
        for forbidden in (
            "readiness",
            "recovery_score",
            "life_load",
            "risk_score",
            "nutrition",
            "recommendation",
        ):
            self.assertNotIn(forbidden, serialized)

    @patch("athlete_os.tools.athlete_context.latest_daily_journal", return_value=None)
    @patch("athlete_os.tools.athlete_context.journal_history", return_value={})
    @patch("athlete_os.tools.athlete_context.get_wellness_normalized", return_value=[])
    @patch("athlete_os.tools.athlete_context.get_activities_normalized", return_value=[])
    def test_empty_sources_preserve_nulls_and_zero_activity(
        self, get_activities, get_wellness, get_history, get_latest
    ):
        result = athlete_context(AS_OF.isoformat())

        self.assertIsNone(result["training"]["last_activity"])
        self.assertIsNone(result["recovery"]["resting_hr"]["latest"])
        self.assertIsNone(result["recovery"]["sleep"]["objective_latest"])
        self.assertIsNone(result["subjective"]["fatigue"])
        self.assertIsNone(result["life_context"]["latest_journal"])
        self.assertTrue(
            all(row["training"]["activity_count"] == 0 for row in result["recent_timeline"])
        )
        self.assertTrue(all(row["steps"] is None for row in result["recent_timeline"]))

    def test_invalid_and_future_as_of_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            athlete_context("09/03/2026")
        future = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            athlete_context(future)


if __name__ == "__main__":
    unittest.main()
