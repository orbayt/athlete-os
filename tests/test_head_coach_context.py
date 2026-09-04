import unittest
from datetime import date, timedelta
from unittest.mock import patch

from athlete_os.services.head_coach_context import build_head_coach_signals
from athlete_os.tools.head_coach import build_head_coach_assessment


AS_OF = date(2026, 9, 4)


def day(days_ago):
    return (AS_OF - timedelta(days=days_ago)).isoformat()


def activity(days_ago, activity_type="Run", load=40):
    return {
        "date": f"{day(days_ago)}T07:00:00",
        "type": activity_type,
        "training_load": load,
    }


def wellness(days_ago, **values):
    record = {
        "date": day(days_ago),
        "resting_hr": None,
        "hrv_rmssd": None,
        "sleep_hours": None,
        "sleep_score": None,
        "reported_sleep_hours": None,
        "reported_sleep_quality": None,
        "reported_fatigue": None,
        "reported_soreness": None,
        "reported_stress": None,
        "reported_mood": None,
        "reported_motivation": None,
    }
    record.update(values)
    return record


class HeadCoachContextTests(unittest.TestCase):
    def setUp(self):
        patchers = [
            patch("athlete_os.services.head_coach_context.get_activities_normalized"),
            patch("athlete_os.services.head_coach_context.get_wellness_normalized"),
            patch("athlete_os.services.head_coach_context.journal_history"),
        ]
        self.addCleanup(patch.stopall)
        self.get_activities, self.get_wellness, self.get_history = [
            patcher.start() for patcher in patchers
        ]
        self.get_history.return_value = {}

    def test_healthy_normal_case(self):
        self.get_activities.return_value = [
            activity(1, load=35),
            activity(3, load=35),
            activity(8, load=35),
            activity(10, load=35),
        ]
        self.get_wellness.return_value = [
            wellness(
                0,
                resting_hr=55,
                hrv_rmssd=50,
                sleep_hours=7.5,
                sleep_score=80,
                reported_fatigue="low",
                reported_soreness="none",
                reported_stress="low",
                reported_mood="good",
                reported_motivation="good",
            )
        ]

        signals = build_head_coach_signals(as_of=AS_OF)

        self.assertEqual(signals.days_since_run, 1)
        self.assertEqual(signals.recent_activity_days, 2)
        self.assertEqual(signals.recovery_state, "good")
        self.assertEqual(signals.sleep_state, "good")
        self.assertEqual(signals.constraints, [])
        self.assertEqual(build_head_coach_assessment(signals).state, "normal")

    def test_forced_inactivity_and_resolving_back_issue(self):
        self.get_activities.return_value = [
            activity(10, load=50),
            activity(20, load=50),
            activity(30, load=50),
        ]
        self.get_wellness.return_value = [
            wellness(
                0,
                sleep_hours=7.5,
                reported_fatigue="low",
                reported_stress="low",
            )
        ]
        self.get_history.return_value = {
            day(2): {
                "journal_text": "Back is improving.",
                "context": [{"tag": "injury_niggle", "source": "manual"}],
            }
        }

        signals = build_head_coach_signals(as_of=AS_OF)
        constraint = next(
            item for item in signals.constraints if item.type == "musculoskeletal"
        )

        self.assertEqual(signals.recent_training_load, "low")
        self.assertEqual(signals.days_since_run, 10)
        self.assertEqual(constraint.status, "resolving")
        self.assertEqual(build_head_coach_assessment(signals).state, "test_load")

    def test_injury_impact_and_trend_map_to_constraint(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = []
        self.get_history.return_value = {
            day(0): {
                "context": [{
                    "tag": "injury_niggle", "source": "manual",
                    "injury_impact": "daily_noticeable", "injury_trend": "better",
                }]
            }
        }

        constraint = build_head_coach_signals(as_of=AS_OF).constraints[0]

        self.assertEqual(constraint.severity, "moderate")
        self.assertEqual(constraint.injury_impact, "daily_noticeable")
        self.assertEqual(constraint.injury_trend, "better")
        self.assertEqual(
            build_head_coach_assessment(
                build_head_coach_signals(as_of=AS_OF)
            ).state,
            "active_recovery",
        )

    def test_subjective_sleep_fills_objective_gap_without_inflating_coverage(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = [
            wellness(
                0,
                reported_sleep_hours=4.0,
                reported_sleep_quality="poor",
            )
        ]
        self.get_history.return_value = {
            day(0): {"journal_text": "Poor sleep.", "context": []}
        }

        signals = build_head_coach_signals(as_of=AS_OF)
        assessment = build_head_coach_assessment(signals)

        self.assertEqual(signals.sleep_state, "poor")
        self.assertTrue(signals.subjective_data_available)
        self.assertEqual(signals.objective_data_coverage, 0)
        self.assertNotEqual(assessment.confidence, "high")

    def test_high_soreness_does_not_create_musculoskeletal_constraint(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = [
            wellness(0, reported_soreness="high")
        ]

        signals = build_head_coach_signals(as_of=AS_OF)

        self.assertEqual(signals.recovery_state, "strained")
        self.assertFalse(
            any(item.type == "musculoskeletal" for item in signals.constraints)
        )

    def test_journal_prose_alone_is_not_subjective_assessment_data(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = []
        self.get_history.return_value = {
            day(0): {"journal_text": "A detailed but unparsed day.", "context": []}
        }

        signals = build_head_coach_signals(as_of=AS_OF)

        self.assertFalse(signals.subjective_data_available)

    def test_missing_data_stays_unknown_and_creates_no_constraints(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = []

        signals = build_head_coach_signals(as_of=AS_OF)

        self.assertEqual(signals.recovery_state, "unknown")
        self.assertEqual(signals.sleep_state, "unknown")
        self.assertEqual(signals.recent_training_load, "unknown")
        self.assertEqual(signals.motivation_state, "unknown")
        self.assertEqual(signals.objective_data_coverage, 0)
        self.assertFalse(signals.subjective_data_available)
        self.assertEqual(signals.constraints, [])

    def test_old_context_is_omitted_instead_of_remaining_active(self):
        self.get_activities.return_value = []
        self.get_wellness.return_value = []
        self.get_history.return_value = {
            day(8): {
                "journal_text": "Old trip and pain.",
                "context": [
                    {"tag": "travel", "source": "manual"},
                    {"tag": "injury_niggle", "source": "manual"},
                ],
            }
        }

        signals = build_head_coach_signals(as_of=AS_OF)

        self.assertEqual(signals.constraints, [])
        self.assertFalse(signals.subjective_data_available)


if __name__ == "__main__":
    unittest.main()
