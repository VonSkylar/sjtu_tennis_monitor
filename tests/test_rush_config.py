import datetime as dt
import unittest

from sjtu_tennis_monitor.config import (
    court_attempt_order,
    is_rush_start_allowed,
    parse_rush_config,
    rush_target_date,
    rush_time_options,
)


class RushConfigTest(unittest.TestCase):
    def test_target_date_is_seven_days_after_today(self) -> None:
        now = dt.datetime(2026, 6, 6, 11, 30)
        self.assertEqual(rush_target_date(now), dt.date(2026, 6, 13))

    def test_start_is_allowed_before_1201_only(self) -> None:
        self.assertTrue(is_rush_start_allowed(dt.datetime(2026, 6, 6, 12, 0, 59)))
        self.assertFalse(is_rush_start_allowed(dt.datetime(2026, 6, 6, 12, 1, 0)))

    def test_time_options_are_one_hour_slots(self) -> None:
        options = rush_time_options()
        self.assertEqual(options[0], "07:00-08:00")
        self.assertEqual(options[-1], "21:00-22:00")
        self.assertEqual(len(options), 15)

    def test_court_attempt_order_uses_preferred_then_ascending(self) -> None:
        self.assertEqual(court_attempt_order(3), (3, 1, 2, 4, 5, 6, 7, 8))

    def test_parse_rush_config(self) -> None:
        now = dt.datetime(2026, 6, 6, 10, 0)
        config = parse_rush_config("21:00-22:00", "east", "1", now)
        self.assertEqual(config.target_date, dt.date(2026, 6, 13))
        self.assertEqual(config.start_hour, 21)
        self.assertEqual(config.end_hour, 22)
        self.assertEqual(config.preferred_court, 1)
        self.assertEqual(config.venue.key, "east")


if __name__ == "__main__":
    unittest.main()
