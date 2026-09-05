import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from athlete_os.services.head_coach_memory import (
    get_head_coach_decisions,
    get_latest_head_coach_decision,
    record_head_coach_decision,
)
from athlete_os.services.head_coach_context import get_daily_head_coach
from athlete_os.services.journal_store import record_daily_checkin
from athlete_os.tools.head_coach import (
    HEAD_COACH_POLICY_VERSION,
    HeadCoachConstraint,
    HeadCoachSignals,
    build_head_coach_assessment,
)


AS_OF = date(2026, 9, 4)


def signals(as_of=AS_OF, impact="training_only"):
    return HeadCoachSignals(
        as_of=as_of,
        recovery_state="good",
        sleep_state="good",
        recent_training_load="low",
        recent_activity_days=3,
        days_since_run=12,
        motivation_state="good",
        constraints=[
            HeadCoachConstraint(
                type="musculoskeletal",
                detail="Back issue",
                status="active",
                severity=(
                    "moderate" if impact == "daily_noticeable" else "low"
                ),
                injury_impact=impact,
                injury_trend="better",
            )
        ],
        objective_data_coverage=1.0,
        subjective_data_available=True,
    )


class HeadCoachMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        db_path = Path(self.temporary_directory.name) / "memory.sqlite3"
        self.environment = patch.dict(
            os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def record(self, current_signals=None, policy_version=HEAD_COACH_POLICY_VERSION):
        current_signals = current_signals or signals()
        return record_head_coach_decision(
            signals=current_signals,
            assessment=build_head_coach_assessment(current_signals),
            policy_version=policy_version,
        )

    def test_first_decision_persists_and_round_trips(self):
        stored = self.record()
        latest = get_latest_head_coach_decision(assessment_date=AS_OF)

        self.assertEqual(stored.id, latest.id)
        self.assertEqual(latest.policy_version, HEAD_COACH_POLICY_VERSION)
        self.assertEqual(latest.signals, signals())
        self.assertEqual(
            latest.signals.constraints[0].injury_impact, "training_only"
        )
        self.assertEqual(latest.signals.constraints[0].injury_trend, "better")
        self.assertEqual(
            latest.assessment, build_head_coach_assessment(signals())
        )

    def test_exact_duplicate_reuses_latest_row(self):
        first = self.record()
        duplicate = self.record()

        self.assertEqual(duplicate.id, first.id)
        self.assertEqual(len(get_head_coach_decisions()), 1)

    def test_changed_signals_append_and_supersede(self):
        first = self.record(signals(impact="training_only"))
        second = self.record(signals(impact="daily_noticeable"))
        decisions = get_head_coach_decisions(assessment_date=AS_OF)

        self.assertEqual(len(decisions), 2)
        self.assertEqual(first.assessment.state, "test_load")
        self.assertEqual(second.assessment.state, "active_recovery")
        self.assertEqual(second.supersedes_decision_id, first.id)
        self.assertIsNone(first.supersedes_decision_id)

    def test_explicit_injury_resolution_appends_easy_decision(self):
        first_signals = signals(impact="daily_noticeable")
        first = self.record(first_signals)
        cleared_signals = first_signals.model_copy(
            update={"constraints": []}
        )
        second = self.record(cleared_signals)

        self.assertEqual(first.assessment.state, "active_recovery")
        self.assertEqual(second.assessment.state, "easy")
        self.assertEqual(second.supersedes_decision_id, first.id)
        self.assertEqual(len(get_head_coach_decisions(assessment_date=AS_OF)), 2)

    def test_policy_version_change_creates_new_event(self):
        first = self.record(policy_version="daily_head_coach_v0.1")
        second = self.record(policy_version="daily_head_coach_v0.2")

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.supersedes_decision_id, first.id)
        self.assertEqual(
            get_head_coach_decisions(assessment_date=AS_OF)[1].policy_version,
            "daily_head_coach_v0.1",
        )

    def test_identical_snapshots_on_different_dates_are_distinct(self):
        first = self.record(signals(as_of=AS_OF))
        tomorrow = AS_OF + timedelta(days=1)
        second = self.record(signals(as_of=tomorrow))

        self.assertNotEqual(first.id, second.id)
        self.assertIsNone(second.supersedes_decision_id)
        self.assertEqual(len(get_head_coach_decisions()), 2)

    @patch("athlete_os.services.head_coach_context.build_head_coach_signals")
    def test_checkin_enables_decision_and_duplicate_refresh_reuses_it(
        self, build_signals
    ):
        record_daily_checkin(AS_OF.isoformat())
        build_signals.return_value = signals()

        first = get_daily_head_coach(as_of=AS_OF)
        second = get_daily_head_coach(as_of=AS_OF)

        self.assertEqual(first, second)
        self.assertEqual(len(get_head_coach_decisions(assessment_date=AS_OF)), 1)

    @patch("athlete_os.services.head_coach_context.build_head_coach_signals")
    def test_same_day_changed_reality_appends_and_supersedes(self, build_signals):
        record_daily_checkin(AS_OF.isoformat())
        initial = signals(impact="daily_noticeable")
        updated = initial.model_copy(update={"constraints": []})
        build_signals.side_effect = [initial, updated]

        get_daily_head_coach(as_of=AS_OF)
        get_daily_head_coach(as_of=AS_OF)
        decisions = get_head_coach_decisions(assessment_date=AS_OF)

        self.assertEqual([row.assessment.state for row in decisions], ["easy", "active_recovery"])
        self.assertEqual(decisions[0].supersedes_decision_id, decisions[1].id)


if __name__ == "__main__":
    unittest.main()
