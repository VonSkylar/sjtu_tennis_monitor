"""Configuration parsing, date utilities, and rate-limit management."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from sjtu_tennis_toolkit.constants import (
    CLOSE_HOUR,
    DEFAULT_CHECK_INTERVAL_SECONDS,
    EIGHTH_DAY_RELEASE_HOUR,
    MIN_CHECK_INTERVAL_SECONDS,
    OPEN_HOUR,
)
from sjtu_tennis_toolkit.models import (
    MonitorConfig,
    RushConfig,
    RushTimeSlot,
    Venue,
    VENUES_BY_KEY,
)


# ---------------------------------------------------------------------------
# Monitor court scopes
# ---------------------------------------------------------------------------
HUXIAOMING_COURT_SCOPE_ALL = "all"
HUXIAOMING_COURT_SCOPE_OUTDOOR = "outdoor"
HUXIAOMING_COURT_SCOPE_INDOOR = "indoor"
HUXIAOMING_COURT_SCOPE_LABELS = {
    HUXIAOMING_COURT_SCOPE_OUTDOOR: "只要室外场",
    HUXIAOMING_COURT_SCOPE_INDOOR: "只要室内场",
    HUXIAOMING_COURT_SCOPE_ALL: "全部都要",
}
HUXIAOMING_COURT_SCOPE_OPTIONS = tuple(HUXIAOMING_COURT_SCOPE_LABELS.values())
RUSH_TIME_NOT_SELECTED = "不选择"
MAX_RUSH_TIME_SLOTS = 7
ALL_TENNIS_COURTS = tuple(range(1, 9))
HUXIAOMING_OUTDOOR_COURTS = (1, 2, 3, 4, 5, 8)
HUXIAOMING_INDOOR_COURTS = (6, 7)


# ---------------------------------------------------------------------------
# Rate-limit cooldown files (relative to project root / CWD)
# ---------------------------------------------------------------------------
RATE_LIMIT_COOLDOWN_FILE = Path("rate_limit_until.txt")
PC_RATE_LIMIT_COOLDOWN_FILE = Path("pc_rate_limit_until.txt")
RUSH_TARGET_DAYS_AHEAD = 7
DEFAULT_RUSH_RELEASE_TIME = dt.time(hour=12)
RUSH_ATTEMPT_WINDOW = dt.timedelta(minutes=1)


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
    huxiaoming_court_scope: str = HUXIAOMING_COURT_SCOPE_ALL,
) -> MonitorConfig:
    venues = parse_venues(venue_keys)
    target_dates = parse_dates(date_text)

    start_hour = parse_hour(start_text, "开始时间")
    end_hour = parse_hour(end_text, "结束时间")
    check_interval_seconds = parse_interval_seconds(interval_text)
    court_scope = parse_huxiaoming_court_scope(huxiaoming_court_scope)

    if not (OPEN_HOUR <= start_hour < CLOSE_HOUR):
        raise ValueError(f"开始时间必须在 {OPEN_HOUR:02d}:00 到 {CLOSE_HOUR - 1:02d}:00 之间")
    if not (OPEN_HOUR + 1 <= end_hour <= CLOSE_HOUR):
        raise ValueError(f"结束时间必须在 {OPEN_HOUR + 1:02d}:00 到 {CLOSE_HOUR:02d}:00 之间")
    if end_hour <= start_hour:
        raise ValueError("结束时间必须晚于开始时间")

    return MonitorConfig(
        venues=venues,
        dates=target_dates,
        start_hour=start_hour,
        end_hour=end_hour,
        check_interval_seconds=check_interval_seconds,
        auto_order_enabled=auto_order_enabled,
        huxiaoming_court_scope=court_scope,
    )


def parse_huxiaoming_court_scope(value: str) -> str:
    normalized = value.strip()
    if normalized in HUXIAOMING_COURT_SCOPE_LABELS:
        return normalized
    for key, label in HUXIAOMING_COURT_SCOPE_LABELS.items():
        if normalized == label:
            return key
    raise ValueError("胡晓明网球场范围必须选择：只要室外场、只要室内场或全部都要")


def huxiaoming_court_scope_label(scope: str) -> str:
    return HUXIAOMING_COURT_SCOPE_LABELS[parse_huxiaoming_court_scope(scope)]


def court_matches_monitor_scope(venue_key: str, court_text: str, config: MonitorConfig) -> bool:
    if venue_key != "huxiaoming" or config.huxiaoming_court_scope == HUXIAOMING_COURT_SCOPE_ALL:
        return True

    match = re.fullmatch(r"场地(\d+)", court_text.strip())
    if not match:
        return False

    court_number = int(match.group(1))
    if config.huxiaoming_court_scope == HUXIAOMING_COURT_SCOPE_INDOOR:
        return court_number in {6, 7}
    return court_number in {1, 2, 3, 4, 5, 8}


def parse_rush_config(
    time_range_text: str,
    venue_key: str,
    court_text: str,
    now: dt.datetime | None = None,
    release_time_text: str = "12:00:00",
    second_time_range_text: str = "",
    third_time_range_text: str = "",
    huxiaoming_court_scope: str = HUXIAOMING_COURT_SCOPE_ALL,
    time_range_texts: tuple[str, ...] | None = None,
) -> RushConfig:
    venue = parse_venues((venue_key,))[0]
    configured_times = time_range_texts or (
        time_range_text,
        second_time_range_text,
        third_time_range_text,
    )
    time_slots = parse_rush_time_slots(*configured_times)
    court_scope = parse_huxiaoming_court_scope(huxiaoming_court_scope)
    court = parse_court_number(court_text)
    allowed_courts = rush_allowed_courts(venue.key, court_scope)
    if court not in allowed_courts:
        allowed_text = ",".join(str(value) for value in allowed_courts)
        raise ValueError(f"场地号必须在当前场地范围内：{allowed_text}")
    release_time = parse_rush_start_time(release_time_text)
    return RushConfig(
        venue=venue,
        target_date=rush_target_date(now),
        time_slots=time_slots,
        preferred_court=court,
        release_time=release_time,
        huxiaoming_court_scope=court_scope,
    )


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


def parse_rush_time_range(text: str) -> tuple[int, int]:
    value = text.strip()
    match = re.fullmatch(r"(\d{1,2}):00\s*[-~]\s*(\d{1,2}):00", value)
    if not match:
        raise ValueError("抢场时间段格式应为 HH:00-HH:00，例如 21:00-22:00")

    start_hour = int(match.group(1))
    end_hour = int(match.group(2))
    if not (OPEN_HOUR <= start_hour < CLOSE_HOUR):
        raise ValueError(f"抢场开始时间必须在 {OPEN_HOUR:02d}:00 到 {CLOSE_HOUR - 1:02d}:00 之间")
    if end_hour != start_hour + 1:
        raise ValueError("抢场器每次只能抢 1 小时时段")
    if end_hour > CLOSE_HOUR:
        raise ValueError(f"抢场结束时间不能晚于 {CLOSE_HOUR:02d}:00")
    return start_hour, end_hour


def parse_rush_time_slots(
    *time_range_texts: str,
) -> tuple[RushTimeSlot, ...]:
    values = tuple(value.strip() for value in time_range_texts)
    if not values or not values[0] or values[0] == RUSH_TIME_NOT_SELECTED:
        raise ValueError("第一时间必须选择")
    if len(values) > MAX_RUSH_TIME_SLOTS:
        raise ValueError(f"最多只能设置 {MAX_RUSH_TIME_SLOTS} 个时间")

    selected_values: list[str] = []
    missing_seen = False
    for value in values:
        selected = bool(value and value != RUSH_TIME_NOT_SELECTED)
        if not selected:
            missing_seen = True
            continue
        if missing_seen:
            raise ValueError("时间必须按顺序连续添加")
        selected_values.append(value)

    time_slots = tuple(
        RushTimeSlot(*parse_rush_time_range(value))
        for value in selected_values
    )
    normalized_slots = tuple(
        (slot.start_hour, slot.end_hour)
        for slot in time_slots
    )
    if len(normalized_slots) != len(set(normalized_slots)):
        raise ValueError("第一、第二、第三时间不能重复")
    return time_slots


def parse_rush_start_time(text: str) -> dt.time:
    value = text.strip()
    try:
        return dt.datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise ValueError("开始抢场时间格式应为 HH:MM:SS，例如 12:00:00") from exc


def parse_court_number(text: str) -> int:
    value = text.strip().replace("场地", "")
    if not value.isdigit():
        raise ValueError("场地号必须是 1 到 8")
    court = int(value)
    if not (1 <= court <= 8):
        raise ValueError("场地号必须是 1 到 8")
    return court


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def config_label(config: MonitorConfig) -> str:
    venues = "、".join(venue.name for venue in config.venues)
    dates = ",".join(date.isoformat() for date in config.dates)
    auto_order = "，唯一符合条件空场自动下单" if config.auto_order_enabled else ""
    huxiaoming_scope = ""
    if any(venue.key == "huxiaoming" for venue in config.venues):
        huxiaoming_scope = f"（胡晓明：{huxiaoming_court_scope_label(config.huxiaoming_court_scope)}）"
    return (
        f"{venues}{huxiaoming_scope} {dates} "
        f"{config.start_hour:02d}:00-{config.end_hour:02d}:00，"
        f"每 {config.check_interval_seconds} 秒检查{auto_order}"
    )


def rush_config_label(config: RushConfig) -> str:
    time_ranges = "、".join(
        f"{slot.start_hour:02d}:00-{slot.end_hour:02d}:00"
        for slot in config.time_slots
    )
    scope = ""
    if config.venue.key == "huxiaoming":
        scope = f"（{huxiaoming_court_scope_label(config.huxiaoming_court_scope)}）"
    return (
        f"{config.venue.name}{scope} {config.target_date.isoformat()} "
        f"{time_ranges} 场地{config.preferred_court}，"
        f"{config.release_time.strftime('%H:%M:%S')} 开始抢场"
    )


def rush_target_date(now: dt.datetime | None = None) -> dt.date:
    current = now or dt.datetime.now()
    return current.date() + dt.timedelta(days=RUSH_TARGET_DAYS_AHEAD)


def rush_time_options() -> tuple[str, ...]:
    return tuple(f"{hour:02d}:00-{hour + 1:02d}:00" for hour in range(OPEN_HOUR, CLOSE_HOUR))


def rush_allowed_courts(
    venue_key: str,
    huxiaoming_court_scope: str = HUXIAOMING_COURT_SCOPE_ALL,
) -> tuple[int, ...]:
    if venue_key != "huxiaoming":
        return ALL_TENNIS_COURTS

    scope = parse_huxiaoming_court_scope(huxiaoming_court_scope)
    if scope == HUXIAOMING_COURT_SCOPE_OUTDOOR:
        return HUXIAOMING_OUTDOOR_COURTS
    if scope == HUXIAOMING_COURT_SCOPE_INDOOR:
        return HUXIAOMING_INDOOR_COURTS
    return ALL_TENNIS_COURTS


def rush_court_attempt_order(
    preferred_court: int,
    allowed_courts: tuple[int, ...],
) -> tuple[int, ...]:
    normalized = tuple(sorted(set(allowed_courts)))
    if not normalized or any(court not in ALL_TENNIS_COURTS for court in normalized):
        raise ValueError("允许场地号必须是 1 到 8")
    if preferred_court not in normalized:
        raise ValueError("首选场地号不在允许的场地范围内")
    return (preferred_court, *(court for court in normalized if court != preferred_court))


def court_attempt_order(preferred_court: int) -> tuple[int, ...]:
    return rush_court_attempt_order(preferred_court, ALL_TENNIS_COURTS)


def rush_attempt_plan(config: RushConfig) -> tuple[tuple[RushTimeSlot, int], ...]:
    allowed_courts = rush_allowed_courts(config.venue.key, config.huxiaoming_court_scope)
    courts = rush_court_attempt_order(config.preferred_court, allowed_courts)
    return tuple(
        (time_slot, court)
        for time_slot in config.time_slots
        for court in courts
    )


def rush_release_datetime(
    now: dt.datetime | None = None,
    release_time: dt.time | None = None,
) -> dt.datetime:
    current = now or dt.datetime.now()
    return dt.datetime.combine(current.date(), release_time or DEFAULT_RUSH_RELEASE_TIME)


def rush_deadline_datetime(
    now: dt.datetime | None = None,
    release_time: dt.time | None = None,
) -> dt.datetime:
    return rush_release_datetime(now, release_time) + RUSH_ATTEMPT_WINDOW


def is_rush_start_allowed(
    now: dt.datetime | None = None,
    release_time: dt.time | None = None,
) -> bool:
    current = now or dt.datetime.now()
    return current < rush_deadline_datetime(current, release_time)


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
