import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from athlete_os.tools.recovery_context import build_recovery_context
from athlete_os.tools.training_context import build_training_context
from athlete_os.tools.head_coach import HeadCoachAssessment
from athlete_os.services.execution_store import DailyExecution
from athlete_os.services.head_coach_memory import get_head_coach_decisions
from athlete_os.services.journal_store import (
    daily_checkin_exists,
    record_daily_checkin,
)
from athlete_os.ui.app import (
    _save_checkin,
    _save_execution,
    _save_history_edit,
    dashboard,
    history,
)


def form_data(values):
    return {
        key: value if isinstance(value, list) else [value]
        for key, value in values.items()
    }


def get_request(path):
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": []}
    )


class UiTests(unittest.TestCase):
    def setUp(self):
        self.activities_patcher = patch(
            "athlete_os.ui.app.get_activities_normalized", return_value=[]
        )
        self.execution_patcher = patch(
            "athlete_os.ui.app.resolve_daily_execution",
            side_effect=lambda date_value, activities: DailyExecution(
                date=date.fromisoformat(date_value),
                execution_type="ACTIVITY" if activities else "UNKNOWN",
                source="provider" if activities else "derived",
                activity_id=(
                    str(activities[0]["id"])
                    if activities and activities[0].get("id") is not None
                    else None
                ),
            ),
        )
        self.get_activities = self.activities_patcher.start()
        self.resolve_execution = self.execution_patcher.start()
        self.addCleanup(self.activities_patcher.stop)
        self.addCleanup(self.execution_patcher.stop)

    def head_coach(self, **overrides):
        values = {
            "date": date.today(),
            "state": "test_load",
            "reality": "Recovery is good, but an active constraint remains.",
            "interpretation": "Load tolerance remains uncertain.",
            "session_guidance": "Test the system, not train it.",
            "why": ["An injury constraint is active."],
            "watch_for": ["Symptoms during activity.", "Symptoms later."],
            "next_decision": "Progress only if symptoms do not worsen.",
            "confidence": "high",
        }
        values.update(overrides)
        return HeadCoachAssessment(**values)

    def complete_checkin_form(self, date_value):
        return form_data(
            {
                "date": date_value,
                "sleep_hours": "8",
                "sleep_quality": "excellent",
                "fatigue": "none",
                "soreness": "none",
                "stress": "none",
                "mood": "good",
                "motivation": "excellent",
                "journal_text": "Complete daily report",
            }
        )

    @patch("athlete_os.ui.app.get_daily_head_coach")
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_renders_domain_context(
        self, training, recovery, latest_journal, get_head_coach
    ):
        get_head_coach.return_value = self.head_coach()
        training.return_value = build_training_context([], date(2026, 9, 3))
        recovery.return_value = build_recovery_context([], date(2026, 9, 3))

        response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Training State", body)
        self.assertIn("Recovery", body)
        self.assertIn("Subjective state", body)
        self.assertIn("Data coverage", body)
        self.assertIn("Daily check-in", body)
        self.assertIn("Journal", body)
        self.assertIn("Alcohol / hangover", body)
        self.assertIn("0.0%", body)
        self.assertIn("HEAD COACH · TODAY", body)
        self.assertIn("TEST LOAD", body)
        self.assertIn("Recovery is good, but an active constraint remains.", body)
        self.assertIn("Test the system, not train it.", body)
        self.assertIn("Symptoms during activity.", body)
        self.assertIn("Progress only if symptoms do not worsen.", body)
        self.assertNotIn("Confidence:", body)
        details = body.split("Why this recommendation?", 1)[1].split(
            "</details>", 1
        )[0]
        self.assertIn("An injury constraint is active.", details)
        self.assertNotIn("readiness score", body.lower())

    @patch(
        "athlete_os.ui.app.get_daily_head_coach",
        side_effect=RuntimeError("assessment source offline"),
    )
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_head_coach_failure_renders_fallback(
        self, training, recovery, latest_journal, get_head_coach
    ):
        today = date.today()
        training.return_value = build_training_context([], today)
        recovery.return_value = build_recovery_context([], today)

        with self.assertLogs("athlete_os.ui.app", level="ERROR") as logs:
            response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Head Coach assessment unavailable.", body)
        self.assertNotIn("assessment source offline", body)
        self.assertNotIn("NORMAL TRAINING", body)
        self.assertIn("Head Coach assessment generation failed", logs.output[0])

    @patch("athlete_os.ui.app.get_daily_head_coach", return_value=None)
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_awaits_explicit_checkin(
        self, training, recovery, latest_journal, get_head_coach
    ):
        today = date.today()
        training.return_value = build_training_context([], today)
        recovery.return_value = build_recovery_context([], today)

        response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("AWAITING CHECK-IN", body)
        self.assertIn(
            "Complete today's check-in to get a new recommendation.", body
        )
        coach_card = body.split("HEAD COACH · TODAY", 1)[1].split(
            "Training State", 1
        )[0]
        self.assertNotIn("WATCH", coach_card)
        self.assertNotIn("NEXT", coach_card)
        self.assertNotIn("Why this recommendation?", coach_card)

    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_repeated_dashboard_refresh_without_checkin_creates_no_memory(
        self, training, recovery, latest_journal
    ):
        today = date.today()
        training.return_value = build_training_context([], today)
        recovery.return_value = build_recovery_context([], today)
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "ui.sqlite3"
            with patch.dict(os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}):
                first = dashboard(get_request("/"))
                second = dashboard(get_request("/"))
                decisions = get_head_coach_decisions(assessment_date=today)

        self.assertIn("AWAITING CHECK-IN", first.body.decode())
        self.assertIn("AWAITING CHECK-IN", second.body.decode())
        self.assertEqual(decisions, [])

    @patch("athlete_os.ui.app.get_daily_head_coach")
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_notes_mixed_resting_hr_context(
        self, training, recovery, latest_journal, get_head_coach
    ):
        get_head_coach.return_value = self.head_coach()
        today = date.today()
        training.return_value = build_training_context([], today)
        recovery.return_value = build_recovery_context(
            [
                {"date": today.isoformat(), "resting_hr": 63},
                {
                    "date": (today - timedelta(days=1)).isoformat(),
                    "resting_hr": 59,
                    "sleep_hours": 7,
                },
            ],
            today,
        )

        response = dashboard(get_request("/"))

        self.assertIn("Mixed overnight wear context", response.body.decode())

    @patch("athlete_os.ui.app.record_daily_checkin_if_complete")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_checkin_passes_canonical_values_and_none_for_blanks(
        self, record, save_journal, replace_context, record_marker
    ):
        record.return_value = {"date": "2026-09-02", "recorded": {}}

        response = _save_checkin(form_data({
                "date": "2026-09-02",
                "sleep_hours": "7.5",
                "sleep_quality": "good",
                "fatigue": "",
                "soreness": "low",
                "stress": "",
                "mood": "excellent",
                "motivation": "",
                "journal_text": "  Travel day  ",
                "context_tags": "travel,work_stress",
            }))

        self.assertEqual(response.status_code, 303)
        record.assert_called_once_with(
            date_value="2026-09-02",
            sleep_hours=7.5,
            sleep_quality="good",
            fatigue=None,
            soreness="low",
            stress=None,
            mood="excellent",
            motivation=None,
            context_note=None,
        )
        save_journal.assert_called_once_with("2026-09-02", "Travel day")
        replace_context.assert_called_once_with(
            "2026-09-02", ["travel", "work_stress"]
        )
        record_marker.assert_called_once_with(
            "2026-09-02",
            {
                "date_value": "2026-09-02",
                "sleep_hours": 7.5,
                "sleep_quality": "good",
                "fatigue": None,
                "soreness": "low",
                "stress": None,
                "mood": "excellent",
                "motivation": None,
                "context_note": None,
            },
        )

    @patch("athlete_os.ui.app.record_daily_checkin_if_complete")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch(
        "athlete_os.ui.app.record_recovery_checkin",
        side_effect=RuntimeError("Intervals unavailable"),
    )
    def test_local_journal_survives_provider_failure(
        self, record, save_journal, replace_context, record_marker
    ):
        response = _save_checkin(form_data(
                {
                    "date": "2026-09-02",
                    "fatigue": "high",
                    "journal_text": "A long day.",
                }
            ))

        self.assertEqual(response.status_code, 303)
        save_journal.assert_called_once_with("2026-09-02", "A long day.")
        replace_context.assert_called_once_with("2026-09-02", [])
        record_marker.assert_not_called()
        self.assertIn("saved+locally", response.headers["location"])
        self.assertIn("Intervals+unavailable", response.headers["location"])

    @patch("athlete_os.ui.app.record_daily_checkin_if_complete")
    @patch("athlete_os.ui.app.record_recovery_checkin")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    def test_context_tags_only_is_valid(
        self, save_journal, replace_context, record, record_marker
    ):
        response = _save_checkin(form_data(
                {"date": "2026-09-02", "context_tags": "family_load"}
            ))

        self.assertEqual(response.status_code, 303)
        save_journal.assert_not_called()
        replace_context.assert_called_once_with("2026-09-02", ["family_load"])
        record_marker.assert_not_called()
        record.assert_not_called()

    @patch("athlete_os.ui.app.record_daily_checkin_if_complete")
    @patch("athlete_os.ui.app.replace_manual_context")
    def test_checkin_passes_structured_injury_context(
        self, replace_context, record_marker
    ):
        response = _save_checkin(form_data({
            "date": "2026-09-02",
            "context_tags": "injury_niggle",
            "injury_impact": "daily_noticeable",
            "injury_trend": "better",
        }))

        self.assertEqual(response.status_code, 303)
        replace_context.assert_called_once_with(
            "2026-09-02", ["injury_niggle"],
            injury_impact="daily_noticeable", injury_trend="better",
        )
        record_marker.assert_not_called()

    def test_dashboard_and_history_share_completion_semantics(self):
        today = date.today().isoformat()
        dashboard_day = (date.today() - timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "completion.sqlite3"
            with patch.dict(os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}):
                with patch(
                    "athlete_os.ui.app.record_recovery_checkin",
                    side_effect=lambda **values: {
                        "date": values["date_value"], "recorded": values
                    },
                ):
                    _save_checkin(self.complete_checkin_form(dashboard_day))
                    history_form = self.complete_checkin_form(today)
                    history_form["reported_sleep_hours"] = history_form.pop(
                        "sleep_hours"
                    )
                    history_form["reported_sleep_quality"] = history_form.pop(
                        "sleep_quality"
                    )
                    history_response = _save_history_edit(
                        history_form
                    )

                self.assertTrue(daily_checkin_exists(dashboard_day))
                self.assertTrue(daily_checkin_exists(today))
                self.assertTrue(
                    history_response.headers["location"].endswith(
                        f"#day-{today}"
                    )
                )

                with (
                    patch("athlete_os.ui.app.training_context") as training,
                    patch("athlete_os.ui.app.recovery_context") as recovery,
                    patch(
                        "athlete_os.ui.app.get_daily_head_coach",
                        side_effect=lambda as_of: (
                            self.head_coach(date=as_of)
                            if daily_checkin_exists(as_of.isoformat())
                            else None
                        ),
                    ),
                ):
                    training.return_value = build_training_context(
                        [], date.today()
                    )
                    recovery.return_value = build_recovery_context(
                        [], date.today()
                    )
                    body = dashboard(get_request("/")).body.decode()

                self.assertNotIn("AWAITING CHECK-IN", body)
                self.assertIn("TEST LOAD", body)

    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_partial_history_edit_does_not_complete_checkin(self, record):
        record.return_value = {"date": date.today().isoformat(), "recorded": {}}
        today = date.today().isoformat()
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "partial.sqlite3"
            with patch.dict(os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}):
                _save_history_edit(
                    form_data(
                        {
                            "date": today,
                            "fatigue": "low",
                            "journal_text": "Partial edit",
                        }
                    )
                )
                completed = daily_checkin_exists(today)

        self.assertFalse(completed)

    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_partial_history_edit_preserves_completed_marker(self, record):
        day = (date.today() - timedelta(days=1)).isoformat()
        record.return_value = {"date": day, "recorded": {}}
        with tempfile.TemporaryDirectory() as temporary_directory:
            db_path = Path(temporary_directory) / "preserved.sqlite3"
            with patch.dict(os.environ, {"ATHLETE_OS_DB_PATH": str(db_path)}):
                record_daily_checkin(day)
                _save_history_edit(
                    form_data({"date": day, "fatigue": "low"})
                )
                completed = daily_checkin_exists(day)

        self.assertTrue(completed)

    @patch("athlete_os.ui.app.get_head_coach_decisions", return_value=[])
    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_history_renders_migrated_journal_without_legacy_ui(
        self, local_history, get_wellness, get_decisions
    ):
        today = date.today().isoformat()
        local_history.return_value = {
            today: {
                "journal_text": "Raw journal text",
                "context": [
                    {"tag": "event_support", "source": "manual"},
                    {"tag": "alcohol", "source": "manual"},
                ],
            },
            (date.today() - timedelta(days=1)).isoformat(): {
                "journal_text": "Migrated context",
                "journal_source": "migrated_legacy",
                "context": [],
            },
        }
        get_wellness.return_value = [
            {
                "date": today,
                "resting_hr": 50,
                "hrv_rmssd": None,
                "sleep_hours": None,
                "reported_sleep_hours": 2.0,
                "reported_fatigue": "high",
                "reported_soreness": None,
                "reported_stress": "high",
                "reported_mood": None,
                "reported_motivation": None,
                "context_note": "Provider copy",
            },
            {
                "date": (date.today() - timedelta(days=1)).isoformat(),
                "context_note": "Provider-side historical copy",
            },
        ]

        response = history(get_request("/history"), days=14)
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body.count('<article class="day" id="day-'), 14)
        for offset in range(14):
            entry_date = (date.today() - timedelta(days=offset)).isoformat()
            self.assertIn(f'id="day-{entry_date}"', body)
            self.assertIn(
                f"/history?days=14&edit={entry_date}#day-{entry_date}",
                body,
            )
        self.assertIn("Raw journal text", body)
        self.assertIn("Event Support", body)
        self.assertIn("Alcohol / hangover", body)
        self.assertIn("Migrated context", body)
        self.assertNotIn("Legacy context", body)
        self.assertNotIn("Provider-side historical copy", body)
        self.assertNotIn("Provider copy", body)

    @patch("athlete_os.ui.app.get_head_coach_decisions", return_value=[])
    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_history_edit_mode_prefills_manual_fields_and_current_tags(
        self, local_history, get_wellness, get_decisions
    ):
        today = date.today().isoformat()
        local_history.return_value = {
            today: {
                "journal_text": "Canonical journal",
                "context": [
                    {"tag": "travel", "source": "manual"},
                    {
                        "tag": "injury_niggle",
                        "source": "manual",
                        "injury_impact": "daily_noticeable",
                        "injury_trend": "better",
                    },
                    {"tag": "event_support", "source": "manual"},
                ],
            }
        }
        get_wellness.return_value = [
            {
                "date": today,
                "resting_hr": 51,
                "hrv_rmssd": 48,
                "sleep_hours": 7.2,
                "sleep_score": 80,
                "steps": 8000,
                "reported_sleep_hours": 6.5,
                "reported_sleep_quality": "average",
                "reported_fatigue": "high",
                "reported_soreness": "low",
                "reported_stress": "average",
                "reported_mood": "good",
                "reported_motivation": "average",
                "context_note": "Provider copy",
            }
        ]

        response = history(get_request("/history"), days=30, edit=today)
        body = response.body.decode()
        form = body[body.index('<form method="post" action="/history/edit">'):]

        self.assertIn(f'name="date" value="{today}"', form)
        self.assertIn('name="reported_sleep_hours"', form)
        self.assertIn('value="6.5"', form)
        self.assertIn('value="high" selected', form)
        self.assertIn("Canonical journal", form)
        self.assertIn('value="travel" checked', form)
        self.assertIn('value="daily_noticeable" selected', form)
        self.assertIn('value="better" selected', form)
        self.assertIn("How is it affecting movement?", form)
        for label in (
            "Travel",
            "Illness",
            "Injury / niggle",
            "Work stress",
            "Family load",
            "Environmental stress",
            "Sleep disruption",
            "Unusual time on feet",
            "Alcohol / hangover",
        ):
            self.assertIn(label, form)
        self.assertNotIn('value="event_support"', form)
        for objective_field in (
            "resting_hr",
            "hrv_rmssd",
            "steps",
            "sleep_hours",
            "sleep_score",
        ):
            self.assertNotIn(f'name="{objective_field}"', form)
        self.assertIn(
            f'href="/history?days=30#day-{today}">Cancel</a>', form
        )

        normal_body = history(get_request("/history"), days=30).body.decode()
        self.assertNotIn('<form method="post" action="/history/edit">', normal_body)
        self.assertIn(
            f"edit={today}#day-{today}", normal_body
        )

    @patch("athlete_os.ui.app.get_head_coach_decisions", return_value=[])
    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_migrated_context_prefills_the_single_journal_field(
        self, local_history, get_wellness, get_decisions
    ):
        today = date.today().isoformat()
        local_history.return_value = {
            today: {
                "journal_text": "Migrated journal text",
                "journal_source": "migrated_legacy",
                "context": [],
            }
        }
        get_wellness.return_value = [
            {"date": today, "context_note": "Legacy text only"}
        ]

        response = history(get_request("/history"), edit=today)
        body = response.body.decode()
        textarea = body.split('name="journal_text">', 1)[1].split(
            "</textarea>", 1
        )[0]

        self.assertEqual(textarea, "Migrated journal text")
        self.assertNotIn("Legacy context", body)
        self.assertNotIn("Legacy text only", body)

    @patch("athlete_os.ui.app.get_head_coach_decisions")
    @patch("athlete_os.ui.app.get_recent_wellness_normalized", return_value=[])
    @patch("athlete_os.ui.app.journal_history", return_value={})
    def test_history_renders_latest_and_same_day_decision_history(
        self, local_history, get_wellness, get_decisions
    ):
        current_date = date.today()
        earlier = SimpleNamespace(
            assessment_date=current_date,
            created_at=datetime(2026, 9, 5, 8, 0, tzinfo=timezone.utc),
            assessment=self.head_coach(
                date=current_date,
                state="test_load",
                reality="Load tolerance was unknown.",
                session_guidance="Test the system carefully.",
            ),
        )
        latest = SimpleNamespace(
            assessment_date=current_date,
            created_at=datetime(2026, 9, 5, 9, 30, tzinfo=timezone.utc),
            assessment=self.head_coach(
                date=current_date,
                state="active_recovery",
                reality="Daily movement is still uncomfortable.",
                session_guidance="No running today.",
            ),
        )
        get_decisions.return_value = [latest, earlier]

        body = history(get_request("/history")).body.decode()

        self.assertIn("HEAD COACH", body)
        self.assertIn("ACTIVE RECOVERY", body)
        self.assertIn("No running today.", body)
        self.assertIn("2 coach decisions", body)
        self.assertIn("TEST LOAD", body)
        self.assertLess(body.index("08:00"), body.index("09:30"))

    @patch("athlete_os.ui.app.get_head_coach_decisions", return_value=[])
    @patch("athlete_os.ui.app.get_recent_wellness_normalized", return_value=[])
    @patch("athlete_os.ui.app.journal_history", return_value={})
    def test_history_shows_provider_execution_without_manual_confirmation(
        self, local_history, get_wellness, get_decisions
    ):
        yesterday = date.today() - timedelta(days=1)
        self.get_activities.return_value = [
            {
                "id": "activity-9",
                "date": f"{yesterday.isoformat()}T08:00:00",
                "type": "Run",
                "distance_km": 5.0,
            }
        ]

        body = history(get_request("/history")).body.decode()
        card = body.split(f'id="day-{yesterday.isoformat()}"', 1)[1].split(
            "</article>", 1
        )[0]

        self.assertIn("Activity recorded", card)
        self.assertIn("Provider", card)
        self.assertNotIn("Choose outcome", card)

    @patch("athlete_os.ui.app.get_head_coach_decisions", return_value=[])
    @patch("athlete_os.ui.app.get_recent_wellness_normalized", return_value=[])
    @patch("athlete_os.ui.app.journal_history", return_value={})
    def test_history_allows_unknown_past_day_to_be_closed(
        self, local_history, get_wellness, get_decisions
    ):
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        body = history(get_request("/history")).body.decode()
        card = body.split(f'id="day-{yesterday}"', 1)[1].split(
            "</article>", 1
        )[0]

        self.assertIn('action="/history/execution"', card)
        self.assertIn(">Activity<", card)
        self.assertIn("Optional note", card)

    @patch("athlete_os.ui.app.upsert_manual_execution")
    def test_history_execution_save_preserves_range_and_anchor(self, upsert):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        upsert.return_value = DailyExecution(
            date=date.fromisoformat(yesterday),
            execution_type="REST",
            source="manual",
        )

        response = _save_execution(
            form_data(
                {
                    "date": yesterday,
                    "days": "30",
                    "execution_type": "REST",
                    "notes": "  Deliberate rest  ",
                }
            )
        )

        upsert.assert_called_once_with(yesterday, "REST", "Deliberate rest")
        self.assertEqual(response.status_code, 303)
        self.assertIn("days=30", response.headers["location"])
        self.assertTrue(
            response.headers["location"].endswith(f"#day-{yesterday}")
        )

    @patch("athlete_os.ui.app.record_daily_checkin_if_complete")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.delete_daily_journal")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_history_edit_saves_manual_recovery_journal_and_replaces_tags(
        self, record, save_journal, delete_journal, replace_context,
        record_marker,
    ):
        response = _save_history_edit(
            form_data(
                {
                    "date": "2026-09-02",
                    "days": "30",
                    "reported_sleep_hours": "7.5",
                    "reported_sleep_quality": "good",
                    "fatigue": "low",
                    "soreness": "average",
                    "stress": "high",
                    "mood": "good",
                    "motivation": "excellent",
                    "journal_text": "  Updated journal  ",
                    "context_tags": ["travel", "alcohol"],
                }
            )
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("days=30", response.headers["location"])
        self.assertTrue(
            response.headers["location"].endswith("#day-2026-09-02")
        )
        save_journal.assert_called_once_with("2026-09-02", "Updated journal")
        delete_journal.assert_not_called()
        replace_context.assert_called_once_with(
            "2026-09-02", ["travel", "alcohol"]
        )
        record.assert_called_once_with(
            date_value="2026-09-02",
            sleep_hours=7.5,
            sleep_quality="good",
            fatigue="low",
            soreness="average",
            stress="high",
            mood="good",
            motivation="excellent",
            context_note=None,
        )
        record_marker.assert_called_once()

    @patch("athlete_os.ui.app.record_recovery_checkin")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.delete_daily_journal")
    def test_history_edit_can_clear_journal_and_all_manual_tags(
        self, delete_journal, replace_context, record
    ):
        response = _save_history_edit(
            form_data(
                {
                    "date": "2026-09-02",
                    "days": "14",
                    "journal_text": "",
                }
            )
        )

        self.assertEqual(response.status_code, 303)
        delete_journal.assert_called_once_with("2026-09-02")
        replace_context.assert_called_once_with("2026-09-02", [])
        record.assert_not_called()

    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch(
        "athlete_os.ui.app.record_recovery_checkin",
        side_effect=RuntimeError("Intervals unavailable"),
    )
    def test_history_edit_preserves_local_changes_on_provider_failure(
        self, record, save_journal, replace_context
    ):
        response = _save_history_edit(
            form_data(
                {
                    "date": "2026-09-02",
                    "days": "60",
                    "fatigue": "high",
                    "journal_text": "Still locally saved",
                    "context_tags": ["work_stress"],
                }
            )
        )

        save_journal.assert_called_once_with(
            "2026-09-02", "Still locally saved"
        )
        replace_context.assert_called_once_with(
            "2026-09-02", ["work_stress"]
        )
        self.assertIn("Local+journal+context+was+saved", response.headers["location"])
        self.assertIn("Intervals+unavailable", response.headers["location"])

    def test_future_history_edit_is_rejected(self):
        future = (date.today() + timedelta(days=1)).isoformat()

        response = _save_history_edit(
            form_data({"date": future, "days": "14", "journal_text": ""})
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("must+not+be+in+the+future", response.headers["location"])

    @patch("athlete_os.ui.app.get_daily_head_coach")
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context", side_effect=RuntimeError("offline"))
    @patch("athlete_os.ui.app.training_context", side_effect=RuntimeError("offline"))
    def test_dashboard_displays_provider_errors(
        self, training, recovery, latest_journal, get_head_coach
    ):
        get_head_coach.return_value = self.head_coach()
        response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Training data: offline", body)
        self.assertIn("Recovery data: offline", body)


if __name__ == "__main__":
    unittest.main()
