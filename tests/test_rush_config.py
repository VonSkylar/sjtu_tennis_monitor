import datetime as dt
import queue
import unittest
from unittest.mock import Mock

from sjtu_tennis_toolkit.config import (
    HUXIAOMING_COURT_SCOPE_ALL,
    HUXIAOMING_COURT_SCOPE_INDOOR,
    HUXIAOMING_COURT_SCOPE_OUTDOOR,
    MAX_RUSH_TIME_SLOTS,
    court_attempt_order,
    is_rush_start_allowed,
    parse_rush_config,
    parse_rush_start_time,
    parse_rush_time_slots,
    rush_allowed_courts,
    rush_attempt_plan,
    rush_court_attempt_order,
    rush_deadline_datetime,
    rush_release_datetime,
    rush_target_date,
    rush_time_options,
)
from sjtu_tennis_toolkit.browser.rusher import (
    RushBooker,
    date_bar_action,
    date_bar_ready,
    point_inside_viewport,
    slot_cell_can_submit,
    slot_cell_needs_click,
)
from sjtu_tennis_toolkit.models import Slot


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

    def test_dynamic_time_slots_preserve_added_order(self) -> None:
        slots = parse_rush_time_slots(
            "19:00-20:00",
            "20:00-21:00",
            "18:00-19:00",
            "17:00-18:00",
        )
        self.assertEqual(
            tuple((slot.start_hour, slot.end_hour) for slot in slots),
            ((19, 20), (20, 21), (18, 19), (17, 18)),
        )

    def test_legacy_empty_time_gap_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "连续添加"):
            parse_rush_time_slots("19:00-20:00", "不选择", "21:00-22:00")

    def test_first_time_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "第一时间必须选择"):
            parse_rush_time_slots("不选择")

    def test_at_most_seven_time_slots(self) -> None:
        seven_times = tuple(f"{hour:02d}:00-{hour + 1:02d}:00" for hour in range(7, 14))
        self.assertEqual(len(parse_rush_time_slots(*seven_times)), MAX_RUSH_TIME_SLOTS)
        with self.assertRaisesRegex(ValueError, "最多只能设置"):
            parse_rush_time_slots(*seven_times, "14:00-15:00")

    def test_rush_times_cannot_repeat(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能重复"):
            parse_rush_time_slots("07:00-08:00", "7:00-8:00", "不选择")

    def test_rush_allowed_courts_follow_venue_scope(self) -> None:
        self.assertEqual(rush_allowed_courts("east"), tuple(range(1, 9)))
        self.assertEqual(
            rush_allowed_courts("huxiaoming", HUXIAOMING_COURT_SCOPE_OUTDOOR),
            (1, 2, 3, 4, 5, 8),
        )
        self.assertEqual(
            rush_allowed_courts("huxiaoming", HUXIAOMING_COURT_SCOPE_INDOOR),
            (6, 7),
        )
        self.assertEqual(
            rush_allowed_courts("huxiaoming", HUXIAOMING_COURT_SCOPE_ALL),
            tuple(range(1, 9)),
        )

    def test_rush_court_order_stays_inside_allowed_scope(self) -> None:
        self.assertEqual(
            rush_court_attempt_order(3, (1, 2, 3, 4, 5, 8)),
            (3, 1, 2, 4, 5, 8),
        )
        self.assertEqual(rush_court_attempt_order(7, (6, 7)), (7, 6))

    def test_preferred_court_must_match_huxiaoming_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "当前场地范围"):
            parse_rush_config(
                "19:00-20:00",
                "huxiaoming",
                "6",
                huxiaoming_court_scope="只要室外场",
            )

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
            "huxiaoming",
            "3",
            now,
            release_time_text="12:00:02",
            huxiaoming_court_scope="只要室外场",
            time_range_texts=(
                "21:00-22:00",
                "19:00-20:00",
                "20:00-21:00",
            ),
        )
        self.assertEqual(config.target_date, dt.date(2026, 6, 13))
        self.assertEqual(
            tuple((slot.start_hour, slot.end_hour) for slot in config.time_slots),
            ((21, 22), (19, 20), (20, 21)),
        )
        self.assertEqual(config.preferred_court, 3)
        self.assertEqual(config.venue.key, "huxiaoming")
        self.assertEqual(config.release_time, dt.time(12, 0, 2))
        self.assertEqual(config.huxiaoming_court_scope, HUXIAOMING_COURT_SCOPE_OUTDOOR)

    def test_attempt_plan_is_time_first_then_court(self) -> None:
        config = parse_rush_config(
            "19:00-20:00",
            "huxiaoming",
            "3",
            dt.datetime(2026, 6, 6, 10, 0),
            second_time_range_text="20:00-21:00",
            huxiaoming_court_scope="只要室外场",
        )
        plan = rush_attempt_plan(config)
        self.assertEqual(
            tuple((slot.start_hour, court) for slot, court in plan),
            (
                (19, 3), (19, 1), (19, 2), (19, 4), (19, 5), (19, 8),
                (20, 3), (20, 1), (20, 2), (20, 4), (20, 5), (20, 8),
            ),
        )

    def test_success_stops_before_later_time_slots(self) -> None:
        config = parse_rush_config(
            "19:00-20:00",
            "east",
            "1",
            second_time_range_text="20:00-21:00",
        )
        ordered = Slot(
            "east",
            "东区网球场",
            config.target_date,
            "场地1",
            "19:00",
            "场地1-19:00",
        )
        booker = RushBooker(lambda: config, queue.Queue())
        booker._wait_for_target_grid_ready = Mock(return_value=True)
        booker._try_order_current_grid = Mock(return_value=ordered)

        result = booker._try_configured_time_slots(
            object(),
            config,
            dt.datetime.now() + dt.timedelta(minutes=1),
        )

        self.assertEqual(result, ordered)
        self.assertEqual(booker._try_order_current_grid.call_count, 1)

    def test_failed_first_time_switches_to_second_time(self) -> None:
        config = parse_rush_config(
            "19:00-20:00",
            "east",
            "1",
            second_time_range_text="20:00-21:00",
        )
        ordered = Slot(
            "east",
            "东区网球场",
            config.target_date,
            "场地2",
            "20:00",
            "场地2-20:00",
        )
        booker = RushBooker(lambda: config, queue.Queue())
        booker._wait_for_target_grid_ready = Mock(return_value=True)
        booker._try_order_current_grid = Mock(side_effect=(None, ordered))

        result = booker._try_configured_time_slots(
            object(),
            config,
            dt.datetime.now() + dt.timedelta(minutes=1),
        )

        self.assertEqual(result, ordered)
        attempted_slots = [
            call.args[2]
            for call in booker._try_order_current_grid.call_args_list
        ]
        self.assertEqual(
            tuple(slot.start_hour for slot in attempted_slots),
            (19, 20),
        )

    def test_worker_supports_seven_time_priorities(self) -> None:
        time_ranges = tuple(f"{hour:02d}:00-{hour + 1:02d}:00" for hour in range(7, 14))
        config = parse_rush_config(
            time_ranges[0],
            "east",
            "1",
            time_range_texts=time_ranges,
        )
        booker = RushBooker(lambda: config, queue.Queue())
        booker._wait_for_target_grid_ready = Mock(return_value=True)
        booker._try_order_current_grid = Mock(return_value=None)

        result = booker._try_configured_time_slots(
            object(),
            config,
            dt.datetime.now() + dt.timedelta(minutes=1),
        )

        self.assertIsNone(result)
        attempted_slots = [
            call.args[2]
            for call in booker._try_order_current_grid.call_args_list
        ]
        self.assertEqual(
            tuple(slot.start_hour for slot in attempted_slots),
            tuple(range(7, 14)),
        )


if __name__ == "__main__":
    unittest.main()
