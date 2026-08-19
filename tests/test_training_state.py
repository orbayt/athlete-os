import unittest
from unittest.mock import patch

from athlete_os.tools.training_state import training_state


class TrainingStateTests(unittest.TestCase):
    @patch("athlete_os.tools.training_state.get_recent_wellness_normalized")
    @patch("athlete_os.tools.training_state.get_recent_activities_normalized")
    def test_breaks_down_training_and_finds_latest_recovery_values(
        self, get_activities, get_wellness
    ):
        get_activities.return_value = [
            {
                "date": "2026-08-18T07:00:00",
                "type": "Ride",
                "distance_km": 40.125,
                "moving_time_min": 90,
                "elevation_gain_m": 500.4,
                "training_load": 80.25,
            },
            {
                "date": "2026-08-18T18:00:00",
                "type": "Run",
                "distance_km": 10.555,
                "moving_time_min": 60,
                "elevation_gain_m": 100.2,
                "training_load": 50.25,
            },
            {
                "date": "2026-08-17T07:00:00",
                "type": "Ride",
                "distance_km": None,
                "moving_time_min": 30,
                "elevation_gain_m": None,
                "training_load": None,
            },
        ]
        get_wellness.return_value = [
            {
                "date": "2026-08-19",
                "fitness_ctl": 52,
                "fatigue_atl": 61,
                "ramp_rate": 2,
                "resting_hr": 48,
                "hrv_rmssd": None,
                "sleep_hours": None,
                "sleep_score": 82,
                "steps": 1000,
            },
            {
                "date": "2026-08-17",
                "hrv_rmssd": 47,
                "sleep_hours": 7.5,
                "sleep_score": 75,
            },
            {
                "date": "2026-08-18",
                "hrv_rmssd": 51,
                "sleep_hours": 6.75,
                "sleep_score": None,
            },
        ]

        result = training_state(7)

        self.assertEqual(
            result["training"],
            {
                "activity_count": 3,
                "distance_km": 50.68,
                "moving_time_hours": 3.0,
                "elevation_gain_m": 601,
                "training_load": 130.5,
                "by_type": {
                    "Ride": {
                        "activity_count": 2,
                        "distance_km": 40.12,
                        "moving_time_hours": 2.0,
                        "elevation_gain_m": 500,
                        "training_load": 80.2,
                    },
                    "Run": {
                        "activity_count": 1,
                        "distance_km": 10.55,
                        "moving_time_hours": 1.0,
                        "elevation_gain_m": 100,
                        "training_load": 50.2,
                    },
                },
            },
        )
        self.assertEqual(result["current_state"]["date"], "2026-08-19")
        self.assertEqual(result["current_state"]["form"], -9)
        self.assertEqual(
            result["latest_recovery"],
            {
                "hrv_rmssd": {"date": "2026-08-18", "value": 51},
                "sleep_hours": {"date": "2026-08-18", "value": 6.75},
                "sleep_score": {"date": "2026-08-19", "value": 82},
            },
        )
        self.assertEqual(
            result["data_coverage"],
            {
                "activity_days": 2,
                "wellness_days": 3,
                "hrv_days": 2,
                "sleep_days": 2,
            },
        )

    @patch("athlete_os.tools.training_state.get_recent_wellness_normalized")
    @patch("athlete_os.tools.training_state.get_recent_activities_normalized")
    def test_empty_data_has_empty_breakdown_and_null_recovery(
        self, get_activities, get_wellness
    ):
        get_activities.return_value = []
        get_wellness.return_value = []

        result = training_state()

        self.assertEqual(result["training"]["by_type"], {})
        self.assertIsNone(result["current_state"])
        self.assertEqual(
            result["latest_recovery"],
            {"hrv_rmssd": None, "sleep_hours": None, "sleep_score": None},
        )

    def test_rejects_non_positive_period(self):
        with self.assertRaisesRegex(ValueError, "days must be at least 1"):
            training_state(0)


if __name__ == "__main__":
    unittest.main()
