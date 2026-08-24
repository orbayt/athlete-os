import unittest
from datetime import date, timedelta
from math import exp, log
from unittest.mock import patch

from athlete_os.services.intervals_client import normalize_wellness
from athlete_os.tools.recovery_context import (
    build_recovery_context,
    recovery_context,
)


AS_OF = date(2026, 8, 24)


def wellness(days_ago: int, **values):
    return {
        "date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        **values,
    }


class WellnessNormalizationTests(unittest.TestCase):
    def test_custom_fields_decode_without_exposing_provider_names(self):
        normalized = normalize_wellness(
            {
                "id": "2026-08-24",
                "AthleteOSReportedSleepHours": 0,
                "AthleteOSReportedSleepQuality": 0,
                "AthleteOSReportedFatigue": 1,
                "AthleteOSReportedSoreness": 2,
                "AthleteOSReportedStress": 3,
                "AthleteOSReportedMood": 4,
                "AthleteOSReportedMotivation": 0,
            }
        )

        self.assertEqual(normalized["reported_sleep_hours"], 0)
        self.assertEqual(normalized["reported_sleep_quality"], "none")
        self.assertEqual(normalized["reported_fatigue"], "low")
        self.assertEqual(normalized["reported_soreness"], "average")
        self.assertEqual(normalized["reported_stress"], "high")
        self.assertEqual(normalized["reported_mood"], "excellent")
        self.assertEqual(normalized["reported_motivation"], "none")
        self.assertFalse(
            any(key.startswith("AthleteOS") for key in normalized)
        )

    def test_missing_and_unknown_custom_values_normalize_to_none(self):
        normalized = normalize_wellness(
            {"id": "2026-08-24", "AthleteOSReportedFatigue": 99}
        )

        self.assertIsNone(normalized["reported_sleep_hours"])
        self.assertIsNone(normalized["reported_fatigue"])


class RecoveryContextTests(unittest.TestCase):
    def test_objective_metrics_use_observations_without_zero_fill(self):
        records = [
            wellness(0, resting_hr=66, hrv_rmssd=64, sleep_hours=7, sleep_score=80),
            wellness(1, resting_hr=None, hrv_rmssd=0, sleep_hours=None),
            wellness(2, resting_hr=60, hrv_rmssd=16, sleep_hours=5, sleep_score=70),
            wellness(10, resting_hr=54, hrv_rmssd=-5, sleep_hours=8, sleep_score=90),
            wellness(41, resting_hr=50, hrv_rmssd=None),
        ]

        result = build_recovery_context(records, AS_OF)
        resting_hr = result["objective"]["resting_hr"]

        self.assertEqual(resting_hr["latest"], {"date": "2026-08-24", "value": 66})
        self.assertEqual(resting_hr["recent_7d"]["mean"], 63)
        self.assertEqual(resting_hr["recent_7d"]["observed_days"], 2)
        self.assertEqual(resting_hr["recent_7d"]["coverage_pct"], 28.6)
        self.assertEqual(resting_hr["background_42d"]["mean"], 57.5)
        self.assertEqual(resting_hr["background_42d"]["observed_days"], 4)

        hrv = result["objective"]["hrv"]
        expected_ln = (log(64) + log(16)) / 2
        self.assertEqual(hrv["latest"], {"date": "2026-08-24", "rmssd_ms": 64})
        self.assertEqual(hrv["recent_7d"]["mean_ln_rmssd"], round(expected_ln, 2))
        self.assertEqual(
            hrv["recent_7d"]["geometric_mean_rmssd"],
            round(exp(expected_ln), 2),
        )
        self.assertEqual(hrv["recent_7d"]["observed_days"], 2)
        self.assertNotIn("complete", hrv["recent_7d"])

    def test_objective_and_reported_sleep_remain_separate_and_keep_zero(self):
        result = build_recovery_context(
            [wellness(0, sleep_hours=7.5, reported_sleep_hours=0)],
            AS_OF,
        )

        objective = result["objective"]["sleep"]["duration_hours"]
        subjective = result["subjective"]["sleep_duration_hours"]
        self.assertEqual(objective["latest"]["value"], 7.5)
        self.assertEqual(subjective["latest"]["value"], 0)
        self.assertEqual(subjective["recent_7d"]["mean"], 0)
        self.assertEqual(subjective["recent_7d"]["observed_days"], 1)

    def test_ordinal_metrics_score_labels_and_exclude_missing_values(self):
        records = [
            wellness(0, reported_fatigue="none", reported_mood="excellent"),
            wellness(1, reported_fatigue=None),
            wellness(2, reported_fatigue="high", reported_mood="poor"),
            wellness(10, reported_fatigue="average"),
        ]

        result = build_recovery_context(records, AS_OF)
        fatigue = result["subjective"]["fatigue"]

        self.assertEqual(
            fatigue["latest"],
            {"date": "2026-08-24", "label": "none", "score": 0},
        )
        self.assertEqual(fatigue["recent_7d"]["mean_score"], 1.5)
        self.assertEqual(fatigue["recent_7d"]["observed_days"], 2)
        self.assertEqual(fatigue["background_42d"]["mean_score"], 1.67)
        self.assertEqual(fatigue["background_42d"]["observed_days"], 3)
        self.assertEqual(
            result["subjective"]["mood"]["recent_7d"]["mean_score"],
            2.5,
        )

    def test_history_boundaries_future_filter_and_duplicate_last_wins(self):
        records = [
            wellness(42, resting_hr=10),
            wellness(-1, resting_hr=20),
            wellness(41, resting_hr=50),
            wellness(0, resting_hr=60),
            wellness(0, resting_hr=70),
        ]

        result = build_recovery_context(records, AS_OF)
        resting_hr = result["objective"]["resting_hr"]

        self.assertEqual(result["data_coverage"]["wellness_records"], 2)
        self.assertEqual(resting_hr["latest"]["value"], 70)
        self.assertEqual(resting_hr["background_42d"]["mean"], 60)

    def test_empty_history_has_none_aggregates_and_zero_coverage(self):
        result = build_recovery_context([], AS_OF)

        resting_hr = result["objective"]["resting_hr"]
        self.assertIsNone(resting_hr["latest"])
        self.assertIsNone(resting_hr["recent_7d"]["mean"])
        self.assertEqual(resting_hr["recent_7d"]["observed_days"], 0)
        self.assertEqual(resting_hr["recent_7d"]["coverage_pct"], 0)
        self.assertIsNone(
            result["objective"]["hrv"]["background_42d"][
                "geometric_mean_rmssd"
            ]
        )
        self.assertIsNone(result["subjective"]["motivation"]["latest"])
        self.assertEqual(result["data_coverage"]["wellness_records"], 0)

    @patch("athlete_os.tools.recovery_context.get_recent_wellness_normalized")
    def test_tool_fetches_42_days_of_normalized_wellness(self, get_wellness):
        get_wellness.return_value = []

        result = recovery_context()

        get_wellness.assert_called_once_with(42)
        self.assertEqual(result["model"]["background_window_days"], 42)


if __name__ == "__main__":
    unittest.main()
