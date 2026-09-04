import unittest
from datetime import date

from pydantic import ValidationError

from athlete_os.tools.head_coach import (
    HeadCoachConstraint,
    HeadCoachSignals,
    build_head_coach_assessment,
)


def signals(**overrides):
    values = {
        "as_of": date(2026, 9, 4),
        "recovery_state": "good",
        "sleep_state": "good",
        "recent_training_load": "normal",
        "recent_activity_days": 4,
        "days_since_run": 1,
        "motivation_state": "good",
        "constraints": [],
        "objective_data_coverage": 0.8,
        "subjective_data_available": True,
    }
    values.update(overrides)
    return HeadCoachSignals(**values)


class HeadCoachTests(unittest.TestCase):
    def test_severe_active_constraint_requires_rest(self):
        assessment = build_head_coach_assessment(
            signals(
                constraints=[
                    HeadCoachConstraint(
                        type="musculoskeletal",
                        detail="Back soreness",
                        severity="high",
                    )
                ]
            )
        )

        self.assertEqual(assessment.state, "rest")
        self.assertEqual(assessment.session_guidance, "No structured training today.")
        self.assertIn(
            "Back soreness: active, high severity.", assessment.why
        )

    def test_active_illness_requires_rest_at_low_severity(self):
        assessment = build_head_coach_assessment(
            signals(
                constraints=[
                    HeadCoachConstraint(type="illness", detail="Sore throat")
                ]
            )
        )

        self.assertEqual(assessment.state, "rest")

    def test_systemic_strain_uses_active_recovery(self):
        assessment = build_head_coach_assessment(
            signals(recovery_state="strained", sleep_state="poor")
        )

        self.assertEqual(assessment.state, "active_recovery")

    def test_systemic_strain_with_active_musculoskeletal_constraint_rests(self):
        assessment = build_head_coach_assessment(
            signals(
                recovery_state="strained",
                constraints=[
                    HeadCoachConstraint(
                        type="musculoskeletal", detail="Back soreness"
                    )
                ],
            )
        )

        self.assertEqual(assessment.state, "rest")

    def test_resolving_musculoskeletal_constraint_prioritizes_test_load(self):
        assessment = build_head_coach_assessment(
            signals(
                days_since_run=10,
                constraints=[
                    HeadCoachConstraint(
                        type="musculoskeletal",
                        detail="Back soreness",
                        status="resolving",
                    )
                ],
            )
        )

        self.assertEqual(assessment.state, "test_load")
        self.assertIn("test the system, not train it", assessment.session_guidance)
        self.assertIn("Symptoms during activity.", assessment.watch_for)
        self.assertIn("Symptoms later after activity.", assessment.watch_for)
        self.assertIn(
            "resolving low-severity musculoskeletal constraint",
            assessment.reality,
        )

    def test_long_break_from_running_uses_easy(self):
        assessment = build_head_coach_assessment(signals(days_since_run=8))

        self.assertEqual(assessment.state, "easy")

    def test_mixed_recovery_or_sleep_uses_easy(self):
        assessment = build_head_coach_assessment(
            signals(recovery_state="mixed")
        )

        self.assertEqual(assessment.state, "easy")

    def test_good_recovery_and_sleep_allow_normal_with_high_confidence(self):
        assessment = build_head_coach_assessment(signals())

        self.assertEqual(assessment.state, "normal")
        self.assertEqual(assessment.confidence, "high")
        self.assertIn("Recovery state is good.", assessment.why)
        self.assertIn("Sleep state is good.", assessment.why)
        self.assertNotIn("constraint", assessment.reality)
        self.assertEqual(
            assessment.reality,
            "Recovery is good; sleep is good; recent training load is normal; "
            "days since last run: 1.",
        )

    def test_incomplete_picture_falls_back_to_easy_with_low_confidence(self):
        assessment = build_head_coach_assessment(
            signals(
                recovery_state="unknown",
                sleep_state="unknown",
                recent_training_load="unknown",
                recent_activity_days=0,
                days_since_run=None,
                motivation_state="unknown",
                objective_data_coverage=0,
                subjective_data_available=False,
            )
        )

        self.assertEqual(assessment.state, "easy")
        self.assertEqual(assessment.confidence, "low")
        self.assertEqual(
            assessment.why,
            ["Available data is insufficient for a strong readiness conclusion."],
        )

    def test_resolved_constraints_are_ignored(self):
        assessment = build_head_coach_assessment(
            signals(
                constraints=[
                    HeadCoachConstraint(
                        type="illness",
                        detail="Resolved cold",
                        status="resolved",
                        severity="high",
                    )
                ]
            )
        )

        self.assertEqual(assessment.state, "normal")
        self.assertFalse(
            any("Resolved cold" in reason for reason in assessment.why)
        )

    def test_signal_ranges_are_validated(self):
        with self.assertRaises(ValidationError):
            signals(recent_activity_days=-1)
        with self.assertRaises(ValidationError):
            signals(days_since_run=-1)
        with self.assertRaises(ValidationError):
            signals(objective_data_coverage=1.1)


if __name__ == "__main__":
    unittest.main()
