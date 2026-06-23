import datetime as dt
import unittest

from sjtu_tennis_toolkit.config import (
    HUXIAOMING_COURT_SCOPE_ALL,
    HUXIAOMING_COURT_SCOPE_INDOOR,
    HUXIAOMING_COURT_SCOPE_OUTDOOR,
    court_matches_monitor_scope,
    parse_config,
)


class MonitorCourtScopeTest(unittest.TestCase):
    def _config(self, scope: str):
        return parse_config(
            "2026-06-23",
            "17:00",
            "22:00",
            ("east", "huxiaoming"),
            "20",
            False,
            scope,
        )

    def test_huxiaoming_indoor_only_keeps_courts_6_and_7(self) -> None:
        config = self._config("只要室内场")
        self.assertEqual(config.huxiaoming_court_scope, HUXIAOMING_COURT_SCOPE_INDOOR)
        self.assertFalse(court_matches_monitor_scope("huxiaoming", "场地5", config))
        self.assertTrue(court_matches_monitor_scope("huxiaoming", "场地6", config))
        self.assertTrue(court_matches_monitor_scope("huxiaoming", "场地7", config))
        self.assertFalse(court_matches_monitor_scope("huxiaoming", "场地8", config))

    def test_huxiaoming_outdoor_only_keeps_1_to_5_and_8(self) -> None:
        config = self._config("只要室外场")
        self.assertEqual(config.huxiaoming_court_scope, HUXIAOMING_COURT_SCOPE_OUTDOOR)
        for court in (1, 2, 3, 4, 5, 8):
            self.assertTrue(court_matches_monitor_scope("huxiaoming", f"场地{court}", config))
        for court in (6, 7):
            self.assertFalse(court_matches_monitor_scope("huxiaoming", f"场地{court}", config))

    def test_all_scope_keeps_every_huxiaoming_court(self) -> None:
        config = self._config("全部都要")
        self.assertEqual(config.huxiaoming_court_scope, HUXIAOMING_COURT_SCOPE_ALL)
        for court in range(1, 9):
            self.assertTrue(court_matches_monitor_scope("huxiaoming", f"场地{court}", config))

    def test_east_court_is_never_filtered_by_huxiaoming_scope(self) -> None:
        config = self._config("只要室内场")
        self.assertTrue(court_matches_monitor_scope("east", "场地1", config))
        self.assertTrue(court_matches_monitor_scope("east", "场地8", config))

    def test_unknown_huxiaoming_court_is_rejected_for_partial_scope(self) -> None:
        config = self._config("只要室内场")
        self.assertFalse(court_matches_monitor_scope("huxiaoming", "页面文字显示可预约", config))


if __name__ == "__main__":
    unittest.main()