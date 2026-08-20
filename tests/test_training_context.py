import unittest
from datetime import date, timedelta
from math import exp
from unittest.mock import patch

from athlete_os.tools.training_context import (
    build_training_context,
    training_context,
)


AS_OF = date(2026, 8, 20)


def activity(days_ago: int, load, activity_id=None, **values):
    activity_date = AS_OF - timedelta(days=days_ago)
    return {
        "id": activity_id if activity_id is not None else days_ago,
        "date": f"{activity_date.isoformat()}T07:00:00",
        "name": values.get("name", f"Activity {days_ago}"),
        "type": values.get("type", "Ride"),
        "distance_km": values.get("distance_km", 10),
        "moving_time_min": values.get("moving_time_min", 60),
        "elevation_gain_m": values.get("elevation_gain_m", 100),
        "training_load": load,
    }


class TrainingContextTests(unittest.TestCase):
    def test_sums_same_day_and_calculates_window_totals(self):
        activities = [
            activity(0, 10, 1),
            activity(0, 20, 2),
            activity(2, 3),
            activity(6, 7),
            activity(27, 28),
            activity(83, 84),
            activity(84, 1000),
        ]

        result = build_training_context(activities, AS_OF)

        self.assertEqual(
            result["load"]["totals"],
            {"3d": 33, "7d": 40, "28d": 68, "84d": 152},
        )
        self.assertEqual(result["data_coverage"]["activity_count"], 7)
        self.assertEqual(result["data_coverage"]["activity_days"], 6)

    def test_rest_days_decay_ewma_using_zero_load(self):
        context_on_load_day = build_training_context([activity(0, 70)], AS_OF)
        context_after_rest_day = build_training_context(
            [activity(1, 70)], AS_OF
        )

        alpha = 1 - exp(-1 / 7)
        self.assertEqual(
            context_on_load_day["load"]["recent"]["observed_value"],
            round(alpha * 70, 2),
        )
        self.assertEqual(
            context_after_rest_day["load"]["recent"]["observed_value"],
            round(alpha * 70 * (1 - alpha), 2),
        )

    def test_missing_and_known_loads_are_reported_without_imputation(self):
        result = build_training_context(
            [activity(0, None, 1), activity(0, 12, 2), activity(2, None, 3)],
            AS_OF,
        )

        self.assertEqual(result["load"]["totals"]["3d"], 12)
        self.assertEqual(result["data_coverage"]["activities_with_training_load"], 1)
        self.assertEqual(result["data_coverage"]["activities_missing_training_load"], 2)
        self.assertEqual(result["data_coverage"]["days_with_missing_training_load"], 2)
        self.assertEqual(len(result["highest_load_activities_28d"]), 1)
        self.assertFalse(result["load"]["recent"]["complete"])
        self.assertFalse(
            result["load"]["recent"]["recent_window_complete"]
        )
        self.assertFalse(result["load"]["background"]["complete"])

        alpha = 1 - exp(-1 / 7)
        self.assertEqual(
            result["load"]["recent"]["observed_value"],
            round(alpha * 12, 2),
        )

    def test_state_completeness_uses_state_specific_windows(self):
        result = build_training_context(
            [activity(6, None, 1), activity(150, None, 2)],
            AS_OF,
        )

        self.assertFalse(result["load"]["recent"]["complete"])
        self.assertFalse(
            result["load"]["recent"]["recent_window_complete"]
        )
        self.assertFalse(result["load"]["background"]["complete"])

        old_missing_only = build_training_context(
            [activity(150, None, 2)],
            AS_OF,
        )

        self.assertFalse(old_missing_only["load"]["recent"]["complete"])
        self.assertTrue(
            old_missing_only["load"]["recent"]["recent_window_complete"]
        )
        self.assertFalse(old_missing_only["load"]["background"]["complete"])

    def test_exact_exponential_time_constant_is_used(self):
        result = build_training_context([activity(0, 70)], AS_OF)

        recent_alpha = 1 - exp(-1 / 7)
        background_alpha = 1 - exp(-1 / 42)
        self.assertEqual(
            result["load"]["recent"]["observed_value"],
            round(recent_alpha * 70, 2),
        )
        self.assertEqual(
            result["load"]["background"]["observed_value"],
            round(background_alpha * 70, 2),
        )
        self.assertEqual(
            result["model"]["missing_load_policy"],
            "exclude_and_report",
        )

    def test_weekly_trajectory_has_twelve_weeks_and_partial_current_week(self):
        result = build_training_context([activity(0, 10)], AS_OF)
        weeks = result["load"]["trajectory"]["weekly_training_load"]

        self.assertEqual(len(weeks), 12)
        self.assertTrue(all(week["is_complete_week"] for week in weeks[:-1]))
        self.assertFalse(weeks[-1]["is_complete_week"])
        self.assertEqual(weeks[-1]["week_start"], "2026-08-17")
        self.assertEqual(weeks[-1]["training_load"], 10)

    def test_highest_load_activities_are_ranked_limited_and_normalized(self):
        activities = [
            activity(index, load, index, name=f"Load {load}")
            for index, load in enumerate([10, 60, 30, 50, 20, 40, 70])
        ]
        activities.append(activity(28, 1000, 1000))

        result = build_training_context(activities, AS_OF)
        highest = result["highest_load_activities_28d"]

        self.assertEqual([item["training_load"] for item in highest], [70, 60, 50, 40, 30])
        self.assertEqual(
            set(highest[0]),
            {
                "id",
                "date",
                "name",
                "type",
                "distance_km",
                "moving_time_min",
                "elevation_gain_m",
                "training_load",
            },
        )

    def test_empty_history_produces_valid_zero_load_context(self):
        result = build_training_context([], AS_OF)

        self.assertEqual(result["load"]["recent"]["observed_value"], 0)
        self.assertEqual(result["load"]["background"]["observed_value"], 0)
        self.assertTrue(result["load"]["recent"]["complete"])
        self.assertTrue(
            result["load"]["recent"]["recent_window_complete"]
        )
        self.assertTrue(result["load"]["background"]["complete"])
        self.assertEqual(
            result["load"]["totals"],
            {"3d": 0, "7d": 0, "28d": 0, "84d": 0},
        )
        self.assertEqual(result["highest_load_activities_28d"], [])
        self.assertEqual(result["data_coverage"]["activity_count"], 0)
        self.assertEqual(len(result["load"]["trajectory"]["weekly_training_load"]), 12)

    def test_activities_outside_history_do_not_affect_context(self):
        result = build_training_context(
            [activity(209, 42), activity(210, 1000), activity(-1, 1000)],
            AS_OF,
        )

        self.assertEqual(result["data_coverage"]["activity_count"], 1)
        self.assertEqual(result["data_coverage"]["activities_with_training_load"], 1)
        self.assertGreater(result["load"]["background"]["observed_value"], 0)
        self.assertLess(result["load"]["background"]["observed_value"], 1)

    @patch("athlete_os.tools.training_context.get_recent_activities_normalized")
    def test_tool_fetches_210_days_of_normalized_activities(self, get_activities):
        get_activities.return_value = []

        result = training_context()

        get_activities.assert_called_once_with(210)
        self.assertEqual(result["model"]["history_days"], 210)


if __name__ == "__main__":
    unittest.main()
