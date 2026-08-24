import unittest
from datetime import date, timedelta
from unittest.mock import Mock, patch

from athlete_os.services.intervals_client import update_wellness
from athlete_os.tools.recovery_checkin import record_recovery_checkin


class RecoveryCheckinTests(unittest.TestCase):
    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_all_fields_are_normalized_encoded_and_confirmed(self, update):
        result = record_recovery_checkin(
            sleep_hours=7.0,
            sleep_quality=" GOOD ",
            fatigue=" Low ",
            soreness="AVERAGE",
            stress=" low",
            mood="Good ",
            motivation=" NONE ",
            date_value="2026-08-20",
        )

        update.assert_called_once_with(
            date(2026, 8, 20),
            {
                "AthleteOSReportedSleepHours": 7.0,
                "AthleteOSReportedSleepQuality": 3,
                "AthleteOSReportedFatigue": 1,
                "AthleteOSReportedSoreness": 2,
                "AthleteOSReportedStress": 1,
                "AthleteOSReportedMood": 3,
                "AthleteOSReportedMotivation": 0,
            },
        )
        self.assertEqual(
            result,
            {
                "date": "2026-08-20",
                "recorded": {
                    "sleep_hours": 7.0,
                    "sleep_quality": "good",
                    "fatigue": "low",
                    "soreness": "average",
                    "stress": "low",
                    "mood": "good",
                    "motivation": "none",
                },
            },
        )

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_partial_checkin_sends_only_supplied_fields(self, update):
        record_recovery_checkin(
            sleep_hours=6.5,
            sleep_quality="good",
            fatigue="high",
            date_value="2026-08-20",
        )

        fields = update.call_args.args[1]
        self.assertEqual(
            fields,
            {
                "AthleteOSReportedSleepHours": 6.5,
                "AthleteOSReportedSleepQuality": 3,
                "AthleteOSReportedFatigue": 3,
            },
        )
        self.assertTrue(
            {"restingHR", "hrv", "sleepSecs", "sleepScore"}.isdisjoint(fields)
        )

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_zero_values_are_preserved(self, update):
        result = record_recovery_checkin(
            sleep_hours=0,
            sleep_quality="none",
            motivation="none",
            date_value="2026-08-20",
        )

        self.assertEqual(
            update.call_args.args[1],
            {
                "AthleteOSReportedSleepHours": 0,
                "AthleteOSReportedSleepQuality": 0,
                "AthleteOSReportedMotivation": 0,
            },
        )
        self.assertEqual(
            result["recorded"],
            {
                "sleep_hours": 0,
                "sleep_quality": "none",
                "motivation": "none",
            },
        )

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_context_note_is_trimmed_passed_through_and_valid_alone(self, update):
        text = "Mild runny nose and throat irritation today."

        result = record_recovery_checkin(
            context_note=f"  {text}  ",
            date_value="2026-08-20",
        )

        update.assert_called_once_with(
            date(2026, 8, 20),
            {"AthleteOSContextNote": text},
        )
        self.assertEqual(result["recorded"], {"context_note": text})
        self.assertNotIn("AthleteOSContextNote", result["recorded"])

    def test_blank_context_note_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "context_note must not be empty"):
            record_recovery_checkin(context_note="   ")

    def test_invalid_label_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mood must be one of"):
            record_recovery_checkin(mood="none")

    def test_invalid_sleep_hours_are_rejected(self):
        for value in (-0.1, 24.1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "between 0 and 24"):
                    record_recovery_checkin(sleep_hours=value)

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_invalid_sleep_duration_and_quality_pairs_are_rejected(self, update):
        invalid_values = (
            {"sleep_hours": 7},
            {"sleep_quality": "good"},
            {"sleep_hours": 0, "sleep_quality": "good"},
            {"sleep_hours": 7, "sleep_quality": "none"},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    record_recovery_checkin(**values)

        update.assert_not_called()

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_consistent_sleep_duration_and_quality_pairs_are_accepted(self, update):
        valid_values = (
            {"sleep_hours": 0, "sleep_quality": "none"},
            {"sleep_hours": 7, "sleep_quality": "good"},
        )

        for values in valid_values:
            with self.subTest(values=values):
                record_recovery_checkin(**values)

        self.assertEqual(update.call_count, 2)

    @patch("athlete_os.tools.recovery_checkin.update_wellness")
    def test_omitted_date_uses_today(self, update):
        result = record_recovery_checkin(fatigue="none")

        self.assertEqual(update.call_args.args[0], date.today())
        self.assertEqual(result["date"], date.today().isoformat())

    def test_invalid_and_future_dates_are_rejected(self):
        for value in ("08/20/2026", "20260820"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                    record_recovery_checkin(fatigue="low", date_value=value)

        future = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "must not be in the future"):
            record_recovery_checkin(fatigue="low", date_value=future)

    def test_empty_checkin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one recovery value"):
            record_recovery_checkin(date_value="2026-08-20")


class UpdateWellnessTests(unittest.TestCase):
    @patch("athlete_os.services.intervals_client.httpx.put")
    @patch("athlete_os.services.intervals_client.API_KEY", "secret")
    def test_update_wellness_sends_partial_put_and_returns_json(self, put):
        response = Mock()
        response.json.return_value = {"id": "2026-08-20"}
        put.return_value = response
        fields = {"AthleteOSReportedFatigue": 3}

        result = update_wellness(date(2026, 8, 20), fields)

        put.assert_called_once_with(
            "https://intervals.icu/api/v1/athlete/0/wellness/2026-08-20",
            json=fields,
            auth=("API_KEY", "secret"),
            timeout=30.0,
        )
        response.raise_for_status.assert_called_once_with()
        self.assertEqual(result, {"id": "2026-08-20"})

    @patch("athlete_os.services.intervals_client.API_KEY", None)
    def test_update_wellness_requires_api_key(self):
        with self.assertRaisesRegex(RuntimeError, "INTERVALS_API_KEY"):
            update_wellness(date(2026, 8, 20), {})


if __name__ == "__main__":
    unittest.main()
