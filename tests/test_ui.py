import unittest
from datetime import date, timedelta
from unittest.mock import patch

from starlette.requests import Request

from athlete_os.tools.recovery_context import build_recovery_context
from athlete_os.tools.training_context import build_training_context
from athlete_os.ui.app import _save_checkin, dashboard, history


def form_data(values):
    return {key: [value] for key, value in values.items()}


def get_request(path):
    return Request(
        {"type": "http", "method": "GET", "path": path, "headers": []}
    )


class UiTests(unittest.TestCase):
    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_renders_domain_context(self, training, recovery):
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
        self.assertIn("Alcohol / hangover", body)
        self.assertIn("0.0%", body)

    @patch("athlete_os.ui.app.recovery_context")
    @patch("athlete_os.ui.app.training_context")
    def test_dashboard_notes_mixed_resting_hr_context(self, training, recovery):
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
            context_note="Travel day",
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
                {"date": "2026-09-02", "journal_text": "A long day."}
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
    def test_history_renders_legacy_event_support_and_legacy_note(
        self, local_history, get_wellness
    ):
        today = date.today().isoformat()
        local_history.return_value = {
            today: {
                "journal_text": "Raw journal text",
                "context": [
                    {"tag": "event_support", "source": "manual"}
                ],
            }
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
                "context_note": "Legacy context",
            },
        ]

        response = history(get_request("/history"), days=14)
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body.count('<article class="day">'), 14)
        self.assertIn("Raw journal text", body)
        self.assertIn("Event Support", body)
        self.assertIn("Legacy context", body)
        self.assertNotIn("Provider copy", body)

    @patch("athlete_os.ui.app.recovery_context", side_effect=RuntimeError("offline"))
    @patch("athlete_os.ui.app.training_context", side_effect=RuntimeError("offline"))
    def test_dashboard_displays_provider_errors(self, training, recovery):
        response = dashboard(get_request("/"))
        body = response.body.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Training data: offline", body)
        self.assertIn("Recovery data: offline", body)


if __name__ == "__main__":
    unittest.main()
