import unittest
from datetime import date, timedelta
from unittest.mock import patch

from athlete_os.services.run_history_context import (
    RUN_HISTORY_POLICY_VERSION,
    build_run_history_context,
    get_run_history_context,
    is_running_activity,
)


DAY = date(2026, 9, 8)


def activity(days_ago=0, activity_type="Run", activity_id=None, **overrides):
    values = {
        "id": activity_id or f"activity-{days_ago}-{activity_type}",
        "date": f"{(DAY - timedelta(days=days_ago)).isoformat()}T07:00:00",
        "name": "Recorded activity",
        "type": activity_type,
        "distance_km": 10.0,
        "moving_time_min": 60.0,
        "elevation_gain_m": 100.0,
        "training_load": 50,
        "average_hr": 140,
        "max_hr": 160,
    }
    values.update(overrides)
    return values


def build(
    history, selected=None, oldest=None, newest=None, request_succeeded=True
):
    selected = selected or activity(activity_id="selected")
    return build_run_history_context(
        selected_activity=selected,
        normalized_history=history,
        selected_date=DAY,
        retrieved_oldest=oldest or DAY - timedelta(days=210),
        retrieved_newest=newest or DAY,
        provider_request_succeeded=request_succeeded,
    )


class RunHistoryContextTests(unittest.TestCase):
    def test_running_family_is_explicit(self):
        self.assertTrue(is_running_activity(activity(activity_type="Run")))
        self.assertTrue(is_running_activity(activity(activity_type="TrailRun")))
        self.assertFalse(is_running_activity(activity(activity_type="Walk")))
        self.assertFalse(is_running_activity(activity(activity_type="VirtualRun")))

    def test_run_and_trail_run_share_the_same_reference(self):
        selected = activity(activity_id="selected", activity_type="TrailRun")
        history = [selected, activity(1, "Run"), activity(2, "TrailRun")]

        result = build(history, selected)
        reference = result["shared_running_history"]["prior_84d"]

        self.assertEqual(reference["session_count"], 2)
        self.assertEqual(reference["distinct_running_days"], 2)
        self.assertEqual(
            result["selected_activity"]["original_type"], "TrailRun"
        )

    def test_exact_reference_boundaries_and_future_exclusion(self):
        selected = activity(activity_id="selected")
        history = [
            selected,
            activity(84, activity_id="day-84"),
            activity(85, activity_id="day-85"),
            activity(210, activity_id="day-210"),
            activity(211, activity_id="day-211"),
            activity(-1, activity_id="future"),
        ]

        result = build(history, selected)

        self.assertEqual(
            result["shared_running_history"]["prior_84d"]["session_count"],
            1,
        )
        self.assertEqual(
            result["shared_running_history"]["prior_210d"]["session_count"],
            3,
        )
        self.assertEqual(
            result["shared_running_history"]["prior_210d"][
                "requested_range"
            ],
            {"oldest": "2026-02-10", "newest": "2026-09-07"},
        )

    @patch(
        "athlete_os.services.run_history_context.get_activities_normalized"
    )
    @patch("athlete_os.services.run_history_context.get_activity_normalized")
    def test_historical_target_fetch_is_relative_to_selected_date(
        self, get_activity, get_activities
    ):
        selected = activity(
            activity_id="historic",
            date="2026-05-01T07:00:00",
        )
        get_activity.return_value = selected
        get_activities.return_value = [selected]

        result = get_run_history_context("historic")

        get_activities.assert_called_once_with(
            date(2025, 10, 3), date(2026, 5, 1)
        )
        self.assertEqual(
            result["retrieval"]["requested_range"],
            {"oldest": "2025-10-03", "newest": "2026-05-01"},
        )

    def test_walk_is_exposure_but_not_running_history(self):
        selected = activity(activity_id="selected")
        walk = activity(
            1,
            "Walk",
            distance_km=5,
            moving_time_min=70,
            elevation_gain_m=80,
            training_load=3,
        )

        result = build([selected, walk])
        surrounding = result["surrounding_activity"]["prior_7_complete_days"]

        self.assertEqual(
            result["shared_running_history"]["observed_prior_session_count"],
            0,
        )
        self.assertEqual(surrounding["by_original_type"]["Walk"]["activity_count"], 1)
        self.assertEqual(
            surrounding["by_original_type"]["Walk"]["metrics"][
                "moving_time_min"
            ]["observed_subtotal"],
            70,
        )
        self.assertEqual(surrounding["running_family"]["activity_count"], 0)

    def test_multiple_runs_on_one_date_are_sessions_on_one_day(self):
        selected = activity(activity_id="selected")
        history = [
            selected,
            activity(1, "Run", "run-1"),
            activity(1, "TrailRun", "run-2"),
        ]

        reference = build(history)["shared_running_history"]["prior_84d"]

        self.assertEqual(reference["session_count"], 2)
        self.assertEqual(reference["distinct_running_days"], 1)

    def test_missing_values_and_measured_zero_remain_distinct(self):
        selected = activity(activity_id="selected")
        history = [
            selected,
            activity(1, training_load=None, distance_km=None),
            activity(2, training_load=0, distance_km=0),
        ]

        metrics = build(history)["shared_running_history"]["prior_84d"][
            "metrics"
        ]

        self.assertEqual(
            metrics["training_load"],
            {
                "known_count": 1,
                "missing_count": 1,
                "median": 0,
                "minimum": 0,
                "maximum": 0,
            },
        )
        self.assertEqual(metrics["distance_km"]["minimum"], 0)

    def test_incomplete_and_empty_history_report_limitations(self):
        selected = activity(activity_id="selected")
        result = build(
            [selected],
            oldest=DAY - timedelta(days=30),
            newest=DAY,
        )
        reference = result["shared_running_history"]["prior_84d"]

        self.assertFalse(reference["requested_range_retrieved"])
        self.assertIsNotNone(reference["retrieval_limitation"])
        self.assertEqual(reference["session_count"], 0)
        self.assertIsNone(reference["metrics"]["distance_km"]["median"])
        self.assertIsNotNone(
            result["shared_running_history"]["previous_run_limitation"]
        )

    def test_three_of_seven_surrounding_days_are_retrieved(self):
        selected = activity(activity_id="selected")
        result = build(
            [selected, activity(1), activity(3), activity(6)],
            oldest=DAY - timedelta(days=3),
            newest=DAY,
        )
        surrounding = result["surrounding_activity"][
            "prior_7_complete_days"
        ]
        coverage = surrounding["retrieval_coverage"]

        self.assertFalse(coverage["requested_range_retrieved"])
        self.assertTrue(coverage["unobserved_dates_may_have_activity"])
        self.assertIn("must not be interpreted", coverage["retrieval_limitation"])
        self.assertEqual(surrounding["all_activities"]["activity_count"], 2)
        self.assertEqual(
            surrounding["all_activities"]["metrics"]["distance_km"][
                "observed_subtotal"
            ],
            20,
        )

    def test_selected_day_outside_retrieved_range_is_not_assumed_empty(self):
        selected = activity(activity_id="selected")
        result = build(
            [selected, activity(1)],
            oldest=DAY - timedelta(days=7),
            newest=DAY - timedelta(days=1),
        )
        selected_day = result["surrounding_activity"]["selected_day"]
        coverage = selected_day["retrieval_coverage"]

        self.assertFalse(coverage["requested_range_retrieved"])
        self.assertTrue(coverage["unobserved_dates_may_have_activity"])
        self.assertEqual(selected_day["all_activities"]["activity_count"], 0)
        self.assertIsNone(
            selected_day["all_activities"]["metrics"]["distance_km"][
                "observed_subtotal"
            ]
        )

    def test_failed_request_overrides_matching_date_boundaries(self):
        selected = activity(activity_id="selected")
        result = build(
            [selected, activity(1)], request_succeeded=False
        )

        for reference_name in ("prior_84d", "prior_210d"):
            reference = result["shared_running_history"][reference_name]
            self.assertFalse(reference["requested_range_retrieved"])
            self.assertEqual(
                reference["retrieval_limitation"],
                "The provider request did not succeed.",
            )
        for window_name in ("selected_day", "prior_7_complete_days"):
            coverage = result["surrounding_activity"][window_name][
                "retrieval_coverage"
            ]
            self.assertFalse(coverage["requested_range_retrieved"])
            self.assertTrue(coverage["unobserved_dates_may_have_activity"])

    def test_per_type_exposure_is_quantitative_and_preserves_partial_sums(self):
        selected = activity(activity_id="selected")
        history = [
            selected,
            activity(1, "Walk", "walk-1", distance_km=5),
            activity(2, "Walk", "walk-2", distance_km=None),
            activity(3, "VirtualRide", "ride", distance_km=20),
        ]

        exposure = build(history)["surrounding_activity"][
            "prior_7_complete_days"
        ]
        walking = exposure["by_original_type"]["Walk"]

        self.assertEqual(set(exposure["by_original_type"]), {"Walk", "VirtualRide"})
        self.assertEqual(walking["activity_count"], 2)
        self.assertEqual(
            walking["metrics"]["distance_km"]["observed_subtotal"], 5
        )
        self.assertFalse(walking["metrics"]["distance_km"]["complete"])

    def test_output_is_unclassified_and_has_no_persistence_contract(self):
        result = build([activity(activity_id="selected")])

        self.assertEqual(result["policy_version"], RUN_HISTORY_POLICY_VERSION)
        output_keys = set()

        def collect_keys(value):
            if isinstance(value, dict):
                output_keys.update(value)
                for nested in value.values():
                    collect_keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect_keys(nested)

        collect_keys(result)
        for field in (
            "classification",
            "head_coach_decision",
            "recommendation_response",
            "readiness_score",
        ):
            self.assertNotIn(field, output_keys)
        self.assertIn(
            "No activity intensity classification is produced.",
            result["unsupported_evidence"],
        )

    def test_non_running_target_and_invalid_id_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Run or TrailRun"):
            build([activity(activity_type="Walk")], activity(activity_type="Walk"))
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            get_run_history_context(" ")
        with self.assertRaisesRegex(ValueError, "invalid"):
            get_run_history_context("bad/id")


if __name__ == "__main__":
    unittest.main()
