import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from athlete_os.services.behavior_assessment import (
    BEHAVIOR_ASSESSMENT_POLICY_VERSION,
    RECOMMENDATION_RESPONSE_POLICY_VERSION,
    build_behavior_assessment,
    build_recommendation_response,
    get_daily_behavior_review,
)
from athlete_os.services.execution_store import DailyExecution
from athlete_os.services.head_coach_memory import (
    get_head_coach_decisions,
    record_head_coach_decision,
)
from athlete_os.tools.head_coach import (
    HeadCoachAssessment,
    HeadCoachSignals,
)


DAY = date(2026, 9, 6)


def execution(execution_type, source="manual"):
    return DailyExecution(
        date=DAY, execution_type=execution_type, source=source
    )


def assessment(state):
    return HeadCoachAssessment(
        date=DAY,
        state=state,
        reality="Deterministic reality.",
        interpretation="Deterministic interpretation.",
        session_guidance="Deterministic guidance.",
        why=["Known fact."],
        watch_for=["Actual response."],
        next_decision="Use the next state.",
        confidence="medium",
    )


def behavior(execution_type, source="manual", activities=None):
    return build_behavior_assessment(
        assessment_date=DAY,
        execution=execution(execution_type, source),
        activities=activities or [],
    )


def activity(activity_type="Walk", **overrides):
    values = {
        "id": "activity-1",
        "date": "2026-09-06T07:00:00",
        "name": "Morning activity",
        "type": activity_type,
        "distance_km": 5.0,
        "moving_time_min": 60.0,
        "elevation_gain_m": 80.0,
        "training_load": 2,
        "average_hr": 80,
        "max_hr": 105,
    }
    values.update(overrides)
    return values


class BehaviorAssessmentTests(unittest.TestCase):
    def test_manual_rest_has_no_invented_activity_load(self):
        result = behavior("REST")

        self.assertEqual(result.execution.type, "REST")
        self.assertIsNone(result.activity_evidence)
        self.assertEqual(result.load_classification, "UNKNOWN")

    def test_manual_active_recovery_preserves_reported_execution(self):
        result = behavior("ACTIVE_RECOVERY")

        self.assertEqual(result.execution.type, "ACTIVE_RECOVERY")
        self.assertEqual(result.execution.source, "manual")
        self.assertIsNone(result.activity_evidence)

    def test_manual_activity_remains_unclassified(self):
        result = behavior("ACTIVITY")

        self.assertEqual(result.execution.type, "ACTIVITY")
        self.assertEqual(result.load_classification, "UNKNOWN")
        self.assertIsNone(result.activity_evidence)

    def test_missing_execution_remains_unknown(self):
        result = behavior("UNKNOWN", "derived")

        self.assertEqual(result.execution.type, "UNKNOWN")
        self.assertEqual(result.load_classification, "UNKNOWN")
        self.assertIsNone(result.activity_evidence)

    def test_provider_walk_and_run_remain_unclassified(self):
        for activity_type in ("Walk", "Run"):
            with self.subTest(activity_type=activity_type):
                item = activity(activity_type)
                result = behavior("ACTIVITY", "provider", [item])

                self.assertEqual(result.activity_evidence.types, [activity_type])
                self.assertEqual(result.load_classification, "UNKNOWN")

    def test_missing_training_load_is_explicitly_incomplete(self):
        result = behavior(
            "ACTIVITY", "provider", [activity(training_load=None)]
        )

        self.assertIsNone(result.activity_evidence.training_load)
        self.assertFalse(result.activity_evidence.training_load_complete)

    def test_partial_daily_metrics_remain_missing_instead_of_partial_totals(self):
        evidence = behavior(
            "ACTIVITY",
            "provider",
            [
                activity("Walk"),
                activity(
                    "Ride", id="activity-2", moving_time_min=None,
                    distance_km=None, elevation_gain_m=None,
                    average_hr=None, max_hr=None,
                ),
            ],
        ).activity_evidence

        self.assertIsNone(evidence.moving_time_min)
        self.assertIsNone(evidence.distance_km)
        self.assertIsNone(evidence.elevation_gain_m)
        self.assertIsNone(evidence.average_hr)
        self.assertIsNone(evidence.max_hr)

    def test_multiple_activities_aggregate_only_factual_metrics(self):
        activities = [
            activity(
                "Walk", id="walk", distance_km=5, moving_time_min=60,
                elevation_gain_m=80, training_load=2, average_hr=80,
                max_hr=105,
            ),
            activity(
                "Ride", id="ride", distance_km=10, moving_time_min=30,
                elevation_gain_m=20, training_load=8, average_hr=100,
                max_hr=130,
            ),
        ]

        evidence = behavior(
            "ACTIVITY", "provider", activities
        ).activity_evidence

        self.assertEqual(evidence.activity_count, 2)
        self.assertEqual(evidence.activity_ids, ["walk", "ride"])
        self.assertEqual(evidence.types, ["Walk", "Ride"])
        self.assertEqual(evidence.moving_time_min, 90)
        self.assertEqual(evidence.distance_km, 15)
        self.assertEqual(evidence.elevation_gain_m, 100)
        self.assertEqual(evidence.training_load, 10)
        self.assertTrue(evidence.training_load_complete)
        self.assertEqual(evidence.average_hr, 86.7)
        self.assertEqual(evidence.max_hr, 130)

    def test_v0_deterministic_response_matrix(self):
        expected = {
            ("rest", "REST"): "WITHIN_GUIDANCE",
            ("active_recovery", "ACTIVE_RECOVERY"): "WITHIN_GUIDANCE",
            ("active_recovery", "REST"): "MORE_CONSERVATIVE",
            ("test_load", "REST"): "MORE_CONSERVATIVE",
            ("test_load", "ACTIVE_RECOVERY"): "MORE_CONSERVATIVE",
            ("easy", "REST"): "MORE_CONSERVATIVE",
            ("normal", "REST"): "MORE_CONSERVATIVE",
        }
        for (coach_state, execution_type), response_state in expected.items():
            with self.subTest(
                coach_state=coach_state, execution_type=execution_type
            ):
                response = build_recommendation_response(
                    assessment=assessment(coach_state),
                    behavior=behavior(execution_type),
                )
                self.assertEqual(response.response, response_state)

    def test_any_activity_is_unknown_and_never_above_guidance(self):
        provider_behavior = behavior(
            "ACTIVITY", "provider", [activity("Run")]
        )
        for coach_state in (
            "rest", "active_recovery", "test_load", "easy", "normal"
        ):
            with self.subTest(coach_state=coach_state):
                response = build_recommendation_response(
                    assessment=assessment(coach_state),
                    behavior=provider_behavior,
                )
                self.assertEqual(response.response, "UNKNOWN")
                self.assertNotEqual(response.response, "ABOVE_GUIDANCE")

    def test_sep_6_shaped_walk_remains_unknown_response(self):
        walk = activity(
            "Walk",
            id="i183767947",
            distance_km=5.21,
            moving_time_min=64.4,
            elevation_gain_m=89.0,
            training_load=2,
            average_hr=77,
            max_hr=103,
        )
        assessed = behavior("ACTIVITY", "provider", [walk])
        response = build_recommendation_response(
            assessment=assessment("active_recovery"), behavior=assessed
        )

        self.assertEqual(assessed.load_classification, "UNKNOWN")
        self.assertEqual(response.response, "UNKNOWN")
        self.assertIn("No athlete-specific", " ".join(response.reasons))

    def test_policy_versions_are_explicit(self):
        assessed = behavior("REST")
        response = build_recommendation_response(
            assessment=assessment("rest"), behavior=assessed
        )

        self.assertEqual(
            assessed.policy_version, BEHAVIOR_ASSESSMENT_POLICY_VERSION
        )
        self.assertEqual(
            response.policy_version, RECOMMENDATION_RESPONSE_POLICY_VERSION
        )
        self.assertEqual(
            response.behavior_policy_version,
            BEHAVIOR_ASSESSMENT_POLICY_VERSION,
        )

    @patch("athlete_os.services.behavior_assessment.resolve_daily_execution")
    @patch("athlete_os.services.behavior_assessment.get_activities_normalized")
    @patch(
        "athlete_os.services.behavior_assessment.get_latest_head_coach_decision"
    )
    def test_service_orchestrates_existing_sources(
        self, get_decision, get_activities, resolve_execution
    ):
        get_decision.return_value = type(
            "Decision", (), {"id": 41, "assessment": assessment("easy")}
        )()
        get_activities.return_value = [activity("Run")]
        resolve_execution.return_value = execution("ACTIVITY", "provider")

        review = get_daily_behavior_review(assessment_date=DAY)

        get_activities.assert_called_once_with(DAY, DAY)
        resolve_execution.assert_called_once_with(
            DAY.isoformat(), get_activities.return_value
        )
        self.assertEqual(review.head_coach_decision_id, 41)
        self.assertEqual(review.behavior.load_classification, "UNKNOWN")
        self.assertEqual(review.recommendation_response.response, "UNKNOWN")

    def test_recomputation_does_not_modify_stored_decision(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "behavior.sqlite3"
            with patch.dict(os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}):
                signals = HeadCoachSignals(
                    as_of=DAY,
                    recovery_state="good",
                    sleep_state="good",
                    recent_training_load="normal",
                    recent_activity_days=2,
                    days_since_run=1,
                    motivation_state="good",
                    constraints=[],
                    objective_data_coverage=1,
                    subjective_data_available=True,
                )
                stored = record_head_coach_decision(
                    signals=signals, assessment=assessment("normal")
                )
                before = get_head_coach_decisions()

                assessed = behavior("REST")
                build_recommendation_response(
                    assessment=stored.assessment, behavior=assessed
                )

                self.assertEqual(get_head_coach_decisions(), before)


if __name__ == "__main__":
    unittest.main()
