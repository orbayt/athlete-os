import unittest
from datetime import date, timedelta
from unittest.mock import patch

from starlette.requests import Request

from athlete_os.tools.recovery_context import build_recovery_context
from athlete_os.tools.training_context import build_training_context
from athlete_os.ui.app import (
    _save_checkin,
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
    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_renders_domain_context(
        self, training, recovery, latest_journal
    ):
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

    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_notes_mixed_resting_hr_context(
        self, training, recovery, latest_journal
    ):
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

    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_checkin_passes_canonical_values_and_none_for_blanks(
        self, record, save_journal, replace_context
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

    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch(
        "athlete_os.ui.app.record_recovery_checkin",
        side_effect=RuntimeError("Intervals unavailable"),
    )
    def test_local_journal_survives_provider_failure(
        self, record, save_journal, replace_context
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
        self.assertIn("saved+locally", response.headers["location"])
        self.assertIn("Intervals+unavailable", response.headers["location"])

    @patch("athlete_os.ui.app.record_recovery_checkin")
    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.save_daily_journal")
    def test_context_tags_only_is_valid(
        self, save_journal, replace_context, record
    ):
        response = _save_checkin(form_data(
                {"date": "2026-09-02", "context_tags": "family_load"}
            ))

        self.assertEqual(response.status_code, 303)
        save_journal.assert_not_called()
        replace_context.assert_called_once_with("2026-09-02", ["family_load"])
        record.assert_not_called()

    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_history_renders_migrated_journal_without_legacy_ui(
        self, local_history, get_wellness
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

    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_history_edit_mode_prefills_manual_fields_and_current_tags(
        self, local_history, get_wellness
    ):
        today = date.today().isoformat()
        local_history.return_value = {
            today: {
                "journal_text": "Canonical journal",
                "context": [
                    {"tag": "travel", "source": "manual"},
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

    @patch("athlete_os.ui.app.get_recent_wellness_normalized")
    @patch("athlete_os.ui.app.journal_history")
    def test_migrated_context_prefills_the_single_journal_field(
        self, local_history, get_wellness
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

    @patch("athlete_os.ui.app.replace_manual_context")
    @patch("athlete_os.ui.app.delete_daily_journal")
    @patch("athlete_os.ui.app.save_daily_journal")
    @patch("athlete_os.ui.app.record_recovery_checkin")
    def test_history_edit_saves_manual_recovery_journal_and_replaces_tags(
        self, record, save_journal, delete_journal, replace_context
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

    @patch("athlete_os.ui.app.latest_daily_journal", return_value=None)
    @patch("athlete_os.ui.app.recovery_context", side_effect=RuntimeError("offline"))
    @patch("athlete_os.ui.app.training_context", side_effect=RuntimeError("offline"))
    def test_dashboard_displays_provider_errors(
        self, training, recovery, latest_journal
    ):
        response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Training data: offline", body)
        self.assertIn("Recovery data: offline", body)


if __name__ == "__main__":
    unittest.main()
