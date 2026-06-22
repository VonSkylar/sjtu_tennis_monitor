import datetime as dt
import unittest

from sjtu_tennis_toolkit.config import (
    court_attempt_order,
    is_rush_start_allowed,
    parse_rush_config,
    parse_rush_start_time,
    rush_deadline_datetime,
    rush_release_datetime,
    rush_target_date,
    rush_time_options,
)
from sjtu_tennis_toolkit.browser.rusher import (
    date_bar_action,
    date_bar_ready,
    point_inside_viewport,
    slot_cell_can_submit,
    slot_cell_needs_click,
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

    def test_default_release_time_starts_at_noon(self) -> None:
        now = dt.datetime(2026, 6, 13, 9, 30)
        self.assertEqual(rush_release_datetime(now), dt.datetime(2026, 6, 13, 12, 0, 0))

    def test_custom_release_time_has_one_minute_attempt_window(self) -> None:
        now = dt.datetime(2026, 6, 13, 9, 30)
        release_time = parse_rush_start_time("12:00:02")
        self.assertEqual(
            rush_release_datetime(now, release_time),
            dt.datetime(2026, 6, 13, 12, 0, 2),
        )
        self.assertEqual(
            rush_deadline_datetime(now, release_time),
            dt.datetime(2026, 6, 13, 12, 1, 2),
        )
        self.assertTrue(is_rush_start_allowed(dt.datetime(2026, 6, 13, 12, 1, 1), release_time))
        self.assertFalse(is_rush_start_allowed(dt.datetime(2026, 6, 13, 12, 1, 2), release_time))

    def test_date_bar_action_reloads_immediately_when_target_missing(self) -> None:
        self.assertEqual(date_bar_action(date_bar_ready=False, target_found=False), "wait")
        self.assertEqual(date_bar_action(date_bar_ready=True, target_found=False), "reload")
        self.assertEqual(date_bar_action(date_bar_ready=True, target_found=True), "select")

    def test_date_bar_ready_requires_real_date_labels(self) -> None:
        self.assertFalse(date_bar_ready(0))
        self.assertFalse(date_bar_ready(1))
        self.assertFalse(date_bar_ready(6))
        self.assertTrue(date_bar_ready(7))
        self.assertTrue(date_bar_ready(8))

    def test_slot_click_decision_requires_order_summary_for_selected_state(self) -> None:
        self.assertTrue(slot_cell_can_submit("available"))
        self.assertTrue(slot_cell_can_submit("selected"))
        self.assertFalse(slot_cell_can_submit("unavailable"))
        self.assertTrue(slot_cell_needs_click("available"))
        self.assertTrue(slot_cell_needs_click("selected", selected_order_matches=False))
        self.assertFalse(slot_cell_needs_click("selected", selected_order_matches=True))

    def test_click_point_must_be_inside_viewport(self) -> None:
        self.assertTrue(point_inside_viewport(200, 500, 1400, 950))
        self.assertFalse(point_inside_viewport(200, 1200, 1400, 950))
        self.assertFalse(point_inside_viewport(0, 500, 1400, 950))

    def test_parse_rush_config(self) -> None:
        now = dt.datetime(2026, 6, 6, 10, 0)
        config = parse_rush_config(
            "21:00-22:00",
            "east",
            "1",
            now,
            release_time_text="12:00:02",
        )
        self.assertEqual(config.target_date, dt.date(2026, 6, 13))
        self.assertEqual(config.start_hour, 21)
        self.assertEqual(config.end_hour, 22)
        self.assertEqual(config.preferred_court, 1)
        self.assertEqual(config.venue.key, "east")
        self.assertEqual(config.release_time, dt.time(12, 0, 2))


if __name__ == "__main__":
    unittest.main()
