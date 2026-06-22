"""PCVenueMonitor — monitoring loop for the PC/ADB version."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import time

from sjtu_tennis_toolkit.constants import DEFAULT_CHECK_INTERVAL_SECONDS, SETUP_SCAN_SECONDS
from sjtu_tennis_toolkit.config import (
    config_label,
    next_pc_rate_limit_retry_time,
    save_pc_rate_limit_cooldown,
)
from sjtu_tennis_toolkit.exceptions import PCAppNotReady, PCRateLimited
from sjtu_tennis_toolkit.models import MonitorConfig, Slot, Venue
from sjtu_tennis_toolkit.pc.driver import PCBookingDriver


class PCVenueMonitor:
    def __init__(self, config_provider, events: queue.Queue) -> None:
        self.config_provider = config_provider
        self.events = events
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._last_config: MonitorConfig | None = None
        self._next_date_index_by_venue: dict[str, int] = {}
        self._driver: PCBookingDriver | None = None

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self._driver:
            self._driver.stop()

    def wake(self) -> None:
        self.wake_event.set()

    def run(self) -> None:
        self.events.put(("log", "正在启动交我办 PC 版。"))
        try:
            self._driver = PCBookingDriver(self.events, self.stop_event)
            self._driver.start()
            self.events.put(("log", f"PC 版控制方式：{self._driver.mode_label}。"))

            while not self.stop_event.is_set():
                wait_seconds = self._current_check_interval()
                try:
                    config = self.config_provider()
                    wait_seconds = config.check_interval_seconds
                    if config != self._last_config:
                        self._last_config = config
                        self._next_date_index_by_venue = {venue.key: 0 for venue in config.venues}
                        self.events.put(("log", f"PC 版监控条件已生效：{config_label(config)}"))

                    all_slots: list[Slot] = []
                    checked_dates: list[tuple[Venue, dt.date]] = []
                    skipped: list[str] = []
                    errors: list[str] = []

                    for venue in config.venues:
                        if self.stop_event.is_set():
                            break
                        try:
                            slots, checked_date = self._check_next_date(config, venue)
                        except PCRateLimited:
                            raise
                        except PCAppNotReady as exc:
                            if self.stop_event.is_set():
                                break
                            skipped.append(f"{venue.name}：{exc}")
                            continue
                        except Exception as exc:
                            if self.stop_event.is_set():
                                break
                            errors.append(f"{venue.name}：{exc}")
                            continue
                        checked_dates.append((venue, checked_date))
                        all_slots.extend(slots)

                    if self.stop_event.is_set():
                        break

                    if all_slots:
                        self.events.put(("available", all_slots))
                    elif checked_dates:
                        checked_at = dt.datetime.now().strftime("%H:%M:%S")
                        checked = "；".join(f"{venue.name} {checked_date.isoformat()}" for venue, checked_date in checked_dates)
                        self.events.put(("log", f"{checked_at} PC版 {checked} 未发现目标时段空场，{config.check_interval_seconds} 秒后检查下一轮。"))
                        if skipped or errors:
                            self.events.put(("log", "部分场馆本轮未完成：" + "；".join(skipped + errors)))
                    elif skipped and not errors:
                        raise PCAppNotReady("；".join(skipped))
                    elif errors:
                        raise RuntimeError("；".join(errors))
                except ValueError as exc:
                    if self.stop_event.is_set():
                        break
                    wait_seconds = SETUP_SCAN_SECONDS
                    self._log(f"监控条件暂未生效：{exc}")
                except PCAppNotReady as exc:
                    if self.stop_event.is_set():
                        break
                    wait_seconds = SETUP_SCAN_SECONDS
                    self._log(str(exc))
                except PCRateLimited as exc:
                    retry_at = next_pc_rate_limit_retry_time()
                    save_pc_rate_limit_cooldown(retry_at)
                    self.events.put(("error", f"{exc}\n\nPC版已停止监控，可在 {retry_at.strftime('%Y-%m-%d %H:%M')} 后再试。"))
                    self.stop_event.set()
                    break
                except Exception as exc:
                    if self.stop_event.is_set():
                        break
                    self.events.put(("log", f"PC版本轮检查未完成：{exc}"))
                    wait_seconds = self._current_check_interval()

                self._sleep_interruptibly(wait_seconds)
        except Exception as exc:
            self.events.put(("error", f"PC版监控启动失败：{exc}"))
        finally:
            self.events.put(("stopped", "PC版监控已停止。"))

    def _check_next_date(self, config: MonitorConfig, venue: Venue) -> tuple[list[Slot], dt.date]:
        assert self._driver is not None
        next_date_index = self._next_date_index_by_venue.get(venue.key, 0) % len(config.dates)
        last_error: Exception | None = None

        for offset in range(len(config.dates)):
            if self.stop_event.is_set():
                raise PCAppNotReady("监控已停止。")
            index = (next_date_index + offset) % len(config.dates)
            target_date = config.dates[index]
            try:
                slots = self._driver.check_venue_date(venue, target_date, config)
            except PCAppNotReady as exc:
                raise exc
            except RuntimeError as exc:
                last_error = exc
                self.events.put(("log", f"PC版 {venue.name} {target_date.isoformat()} 暂未检查：{exc}"))
                continue
            self._next_date_index_by_venue[venue.key] = (index + 1) % len(config.dates)
            return slots, target_date

        if last_error:
            raise last_error
        raise RuntimeError("没有任何目标日期在 PC 版页面中开放。")

    def _sleep_interruptibly(self, seconds: int) -> None:
        end_at = time.monotonic() + seconds
        while not self.stop_event.is_set() and time.monotonic() < end_at:
            if self.wake_event.wait(0.2):
                self.wake_event.clear()
                break

    def _current_check_interval(self) -> int:
        if self._last_config:
            return self._last_config.check_interval_seconds
        return DEFAULT_CHECK_INTERVAL_SECONDS

    def _log(self, message: str) -> None:
        self.events.put(("log", message))
