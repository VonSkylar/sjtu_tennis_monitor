"""Configuration parsing, date utilities, and rate-limit management."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sjtu_tennis_monitor.constants import (
    CLOSE_HOUR,
    DEFAULT_CHECK_INTERVAL_SECONDS,
    EIGHTH_DAY_RELEASE_HOUR,
    MIN_CHECK_INTERVAL_SECONDS,
    OPEN_HOUR,
)
from sjtu_tennis_monitor.models import MonitorConfig, Venue, VENUES_BY_KEY


# ---------------------------------------------------------------------------
# Rate-limit cooldown files (relative to project root / CWD)
# ---------------------------------------------------------------------------
RATE_LIMIT_COOLDOWN_FILE = Path("rate_limit_until.txt")
PC_RATE_LIMIT_COOLDOWN_FILE = Path("pc_rate_limit_until.txt")


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------
def parse_config(
    date_text: str,
    start_text: str,
    end_text: str,
    venue_keys: tuple[str, ...],
    interval_text: str = str(DEFAULT_CHECK_INTERVAL_SECONDS),
    auto_order_enabled: bool = False,
) -> MonitorConfig:
    venues = parse_venues(venue_keys)
    target_dates = parse_dates(date_text)

    start_hour = parse_hour(start_text, "开始时间")
    end_hour = parse_hour(end_text, "结束时间")
    check_interval_seconds = parse_interval_seconds(interval_text)

    if not (OPEN_HOUR <= start_hour < CLOSE_HOUR):
        raise ValueError(f"开始时间必须在 {OPEN_HOUR:02d}:00 到 {CLOSE_HOUR - 1:02d}:00 之间")
    if not (OPEN_HOUR + 1 <= end_hour <= CLOSE_HOUR):
        raise ValueError(f"结束时间必须在 {OPEN_HOUR + 1:02d}:00 到 {CLOSE_HOUR:02d}:00 之间")
    if end_hour <= start_hour:
        raise ValueError("结束时间必须晚于开始时间")

    return MonitorConfig(venues, target_dates, start_hour, end_hour, check_interval_seconds, auto_order_enabled)


def parse_venues(venue_keys: tuple[str, ...]) -> tuple[Venue, ...]:
    if not venue_keys:
        raise ValueError("请至少选择一片网球场")

    venues = []
    seen: set[str] = set()
    for key in venue_keys:
        if key in seen:
            continue
        venue = VENUES_BY_KEY.get(key)
        if not venue:
            raise ValueError(f"未知场馆：{key}")
        seen.add(key)
        venues.append(venue)

    return tuple(venues)


def parse_dates(text: str) -> tuple[dt.date, ...]:
    value = text.strip()
    if not value:
        raise ValueError("请至少输入一个日期")

    range_parts = re.split(r"\s*(?:~|至|到)\s*", value)
    if len(range_parts) == 2:
        start = parse_date(range_parts[0])
        end = parse_date(range_parts[1])
        if end < start:
            raise ValueError("日期范围的结束日期必须晚于或等于开始日期")
        days = (end - start).days + 1
        if days > 14:
            raise ValueError("一次最多监控 14 天")
        return tuple(start + dt.timedelta(days=offset) for offset in range(days))
    if len(range_parts) > 2:
        raise ValueError("日期范围格式应为 2026-05-11~2026-05-18")

    parts = [part for part in re.split(r"[,，;；\s]+", value) if part]
    dates: list[dt.date] = []
    seen: set[dt.date] = set()
    for part in parts:
        date = parse_date(part)
        if date not in seen:
            seen.add(date)
            dates.append(date)

    if len(dates) > 14:
        raise ValueError("一次最多监控 14 天")
    return tuple(dates)


def parse_date(text: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD；多个日期可用逗号分隔，或用 2026-05-11~2026-05-18") from exc


def parse_hour(text: str, field_name: str) -> int:
    value = text.strip()
    match = re.fullmatch(r"(\d{1,2})(?::00)?", value)
    if not match:
        raise ValueError(f"{field_name}格式应为 HH:00，例如 18:00")
    return int(match.group(1))


def parse_interval_seconds(text: str) -> int:
    value = text.strip()
    match = re.fullmatch(r"(\d+)\s*(?:秒|s|S)?", value)
    if not match:
        raise ValueError("刷新间隔应为秒数，例如 30")
    seconds = int(match.group(1))
    if seconds < MIN_CHECK_INTERVAL_SECONDS:
        raise ValueError(f"刷新间隔最小为 {MIN_CHECK_INTERVAL_SECONDS} 秒")
    return seconds


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def config_label(config: MonitorConfig) -> str:
    venues = "、".join(venue.name for venue in config.venues)
    dates = ",".join(date.isoformat() for date in config.dates)
    auto_order = "，唯一符合条件空场自动下单" if config.auto_order_enabled else ""
    return f"{venues} {dates} {config.start_hour:02d}:00-{config.end_hour:02d}:00，每 {config.check_interval_seconds} 秒检查{auto_order}"


def target_date_labels(target_date: dt.date) -> list[str]:
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[target_date.weekday()]
    return [
        target_date.strftime("%m月%d日"),
        f"{target_date.strftime('%m月%d日')} ({weekday})",
        f"{target_date.strftime('%m月%d日')}（{weekday}）",
        target_date.isoformat(),
    ]


def default_date_range_text(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now()
    # When opened at or after 21:00, today's courts are almost over – start from tomorrow.
    if current.hour >= CLOSE_HOUR - 1:
        start_date = current.date() + dt.timedelta(days=1)
        # Tomorrow's 8th-day slot is only released at tomorrow noon, so use 7 days.
        visible_days = 7
    else:
        start_date = current.date()
        visible_days = 8 if current.hour >= EIGHTH_DAY_RELEASE_HOUR else 7
    end_date = start_date + dt.timedelta(days=visible_days - 1)
    return f"{start_date.isoformat()}~{end_date.isoformat()}"


# ---------------------------------------------------------------------------
# Rate-limit cooldown – browser version
# ---------------------------------------------------------------------------
def next_rate_limit_retry_time() -> dt.datetime:
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow, dt.time(hour=0, minute=10))


def save_rate_limit_cooldown(until: dt.datetime) -> None:
    RATE_LIMIT_COOLDOWN_FILE.write_text(until.isoformat(timespec="minutes"), encoding="utf-8")


def load_rate_limit_cooldown() -> dt.datetime | None:
    if not RATE_LIMIT_COOLDOWN_FILE.exists():
        return None
    try:
        until = dt.datetime.fromisoformat(RATE_LIMIT_COOLDOWN_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if dt.datetime.now() >= until:
        try:
            RATE_LIMIT_COOLDOWN_FILE.unlink()
        except OSError:
            pass
        return None
    return until


# ---------------------------------------------------------------------------
# Rate-limit cooldown – PC / ADB version
# ---------------------------------------------------------------------------
def next_pc_rate_limit_retry_time() -> dt.datetime:
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow, dt.time(hour=0, minute=10))


def save_pc_rate_limit_cooldown(until: dt.datetime) -> None:
    PC_RATE_LIMIT_COOLDOWN_FILE.write_text(until.isoformat(timespec="minutes"), encoding="utf-8")


def load_pc_rate_limit_cooldown() -> dt.datetime | None:
    if not PC_RATE_LIMIT_COOLDOWN_FILE.exists():
        return None
    try:
        until = dt.datetime.fromisoformat(PC_RATE_LIMIT_COOLDOWN_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if dt.datetime.now() >= until:
        try:
            PC_RATE_LIMIT_COOLDOWN_FILE.unlink()
        except OSError:
            pass
        return None
    return until
