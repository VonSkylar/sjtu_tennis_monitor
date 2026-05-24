"""Shared constants for the SJTU Tennis Court Booking Monitor."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Monitoring defaults
# ---------------------------------------------------------------------------
DEFAULT_CHECK_INTERVAL_SECONDS = 20
MIN_CHECK_INTERVAL_SECONDS = 10
SETUP_SCAN_SECONDS = 1

# ---------------------------------------------------------------------------
# Court hours
# ---------------------------------------------------------------------------
OPEN_HOUR = 7
CLOSE_HOUR = 22

# ---------------------------------------------------------------------------
# Date visibility
# ---------------------------------------------------------------------------
EIGHTH_DAY_RELEASE_HOUR = 12

# ---------------------------------------------------------------------------
# Browser version
# ---------------------------------------------------------------------------
USER_DATA_DIR = "browser_profile"
DEBUG_PAGE_LIST_SECONDS = 60

# ---------------------------------------------------------------------------
# PC / ADB version
# ---------------------------------------------------------------------------
PC_SHORTCUT_NAME = "交我办.lnk"
ANDROID_PACKAGE = "edu.sjtu.infoplus.taskcenter"
REFERENCE_WINDOW_SIZE = (795, 1337)
DATE_CARD_RATIOS = (0.122, 0.351, 0.586, 0.815)
DATE_CARD_Y_RATIO = 0.698
DATE_CARD_STATUS_BOX = (0.08, 0.66, 0.16, 0.76)
