"""RushBooker - timed browser-based booking at the 12:00 release."""

from __future__ import annotations

import datetime as dt
import queue
import time

from sjtu_tennis_monitor.browser.monitor import VenueMonitor
from sjtu_tennis_monitor.config import (
    court_attempt_order,
    is_rush_start_allowed,
    rush_config_label,
    rush_deadline_datetime,
    rush_release_datetime,
)
from sjtu_tennis_monitor.constants import USER_DATA_DIR
from sjtu_tennis_monitor.exceptions import BookingPageNotReady, RequestRateLimited
from sjtu_tennis_monitor.models import RushConfig, Slot


class RushBooker(VenueMonitor):
    """Open the booking page early, then make a fast single-slot order at noon."""

    def __init__(self, config_provider, events: queue.Queue) -> None:
        super().__init__(config_provider, events)
        self._last_ready_log_at = 0.0
        self._last_attempt_log_at = 0.0

    def run(self) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.events.put(("error", "缺少 Playwright。请先运行：pip install -r requirements.txt，然后运行：python -m playwright install chromium"))
            return

        try:
            config = self.config_provider()
        except Exception as exc:
            self.events.put(("error", f"抢场条件有误：{exc}"))
            self.events.put(("stopped", "抢场已停止。"))
            return

        if not is_rush_start_allowed():
            self.events.put(("failed", "抢场时间已过。每天 12:01 前点击开始抢场才会工作。"))
            self.events.put(("stopped", "抢场已停止。"))
            return

        self.events.put(("log", f"抢场条件已生效：{rush_config_label(config)}"))
        self.events.put(("log", "正在打开浏览器。若尚未登录，请先完成交我办登录，程序会等待到 12:00 自动抢场。"))

        ordered_slot: Slot | None = None
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    USER_DATA_DIR,
                    headless=False,
                    viewport={"width": 1400, "height": 950},
                )
                page = self._find_or_open_venue_page(context, config.venue)
                self._wait_for_booking_page(page, PlaywrightTimeoutError, config)

                if self.stop_event.is_set():
                    context.close()
                    return

                self._wait_for_release_time()
                if self.stop_event.is_set():
                    context.close()
                    return

                self.events.put(("status", "抢场中"))
                ordered_slot = self._rush_until_deadline(page, PlaywrightTimeoutError, config)

                if ordered_slot:
                    self.events.put(("ordered", ordered_slot))
                    self.events.put(("log", "已停止自动操作，浏览器会保持打开供你确认订单。"))
                    self._hold_browser_open()
                else:
                    self.events.put(("failed", "12:01 前没有抢到目标时间段的场地，抢场已停止。"))

                context.close()
        except RequestRateLimited as exc:
            self.events.put(("failed", str(exc)))
        except Exception as exc:
            self.events.put(("error", f"抢场器启动或执行失败：{exc}"))
        finally:
            if ordered_slot:
                self.events.put(("stopped", "抢场器已停止，浏览器窗口已关闭。"))
            else:
                self.events.put(("stopped", "抢场已停止。"))

    def _wait_for_booking_page(self, page, timeout_error_type, config: RushConfig) -> None:
        while not self.stop_event.is_set() and is_rush_start_allowed():
            try:
                self._ensure_booking_page(page, config.venue)
                self.events.put(("log", "预约页已就绪。"))
                return
            except RequestRateLimited:
                raise
            except BookingPageNotReady as exc:
                self._log_ready_wait(str(exc))
            except timeout_error_type as exc:
                self._log_ready_wait(f"正在等待预约页加载：{exc}")
            except Exception as exc:
                self._log_ready_wait(f"正在等待预约页就绪：{exc}")
            self._wait_interruptibly(0.5)

        if not self.stop_event.is_set():
            raise RuntimeError("抢场时间已过，预约页仍未就绪。")

    def _wait_for_release_time(self) -> None:
        release_at = rush_release_datetime()
        if dt.datetime.now() >= release_at:
            return

        self.events.put(("status", "等待 12:00"))
        self.events.put(("log", f"预约页已打开，等待 {release_at.strftime('%H:%M:%S')} 刷新抢场。"))
        while not self.stop_event.is_set():
            remaining = (release_at - dt.datetime.now()).total_seconds()
            if remaining <= 0:
                break
            self._wait_interruptibly(min(remaining, 0.1))

    def _rush_until_deadline(self, page, timeout_error_type, config: RushConfig) -> Slot | None:
        deadline = rush_deadline_datetime()
        first_attempt = True

        while not self.stop_event.is_set() and dt.datetime.now() < deadline:
            try:
                if first_attempt:
                    self.events.put(("log", "12:00 到，正在刷新预约页。"))
                    first_attempt = False
                page.reload(wait_until="domcontentloaded", timeout=5000)
                self._ensure_booking_page(page, config.venue)
                self._select_target_date(page, timeout_error_type, config.target_date, config.venue)
                page.wait_for_timeout(150)
                self._raise_if_rate_limited(page)

                ordered_slot = self._try_order_current_grid(page, config)
                if ordered_slot:
                    return ordered_slot
            except RequestRateLimited:
                raise
            except Exception as exc:
                self._log_attempt_wait(f"本轮抢场未完成：{exc}")

            self._wait_interruptibly(0.08)

        return None

    def _try_order_current_grid(self, page, config: RushConfig) -> Slot | None:
        for court in court_attempt_order(config.preferred_court):
            if self.stop_event.is_set() or dt.datetime.now() >= rush_deadline_datetime():
                return None

            slot = Slot(
                venue_key=config.venue.key,
                venue=config.venue.name,
                date=config.target_date,
                court=f"场地{court}",
                hour=f"{config.start_hour:02d}:00",
                label=f"场地{court}-{config.start_hour:02d}:00",
            )

            try:
                self._clear_transient_notices(page)
                self.events.put(("log", f"尝试下单：{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court}"))
                self._click_slot_cell(page, slot)
                page.wait_for_timeout(80)

                failure = self._failure_notice_text(page)
                if failure:
                    self.events.put(("log", f"{slot.court} 已不可用：{failure}"))
                    continue

                self._click_text_button(page, "立即下单", timeout=1200)
                page.wait_for_timeout(180)
                failure = self._failure_notice_text(page)
                if failure:
                    self.events.put(("log", f"{slot.court} 下单前已失败：{failure}"))
                    continue

                self._accept_booking_notice_fast(page)
                page.wait_for_timeout(80)
                self._click_text_button(page, "提交订单", timeout=1500)
                page.wait_for_timeout(350)
                failure = self._failure_notice_text(page)
                if failure:
                    self.events.put(("log", f"{slot.court} 提交失败：{failure}"))
                    continue

                self.events.put(("log", f"已自动提交订单：{slot.venue} {slot.date.isoformat()} {slot.hour}-{config.end_hour:02d}:00 {slot.court}"))
                return slot
            except Exception as exc:
                self.events.put(("log", f"{slot.court} 未抢到，继续尝试下一个场地：{exc}"))

        return None

    def _accept_booking_notice_fast(self, page) -> None:
        end_at = time.monotonic() + 1.5
        last_error = ""
        while time.monotonic() < end_at and not self.stop_event.is_set():
            result = page.evaluate(
                """
                () => {
                  const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
                  const labels = Array.from(document.querySelectorAll('label'));
                  const label = labels.find((item) => /本人已认真阅读|自愿接受|同意/.test(norm(item.innerText)));
                  if (label) {
                    label.click();
                    return { ok: true };
                  }

                  const textNode = Array.from(document.querySelectorAll('body *'))
                    .find((item) => /本人已认真阅读|自愿接受|同意/.test(norm(item.innerText)));
                  if (textNode) {
                    const target = textNode.closest('label') || textNode;
                    target.click();
                    return { ok: true };
                  }

                  const checkbox = Array.from(document.querySelectorAll('input[type="checkbox"]')).find((item) => !item.checked);
                  if (checkbox) {
                    checkbox.click();
                    return { ok: true };
                  }

                  return { error: '没有找到须知勾选框。' };
                }
                """
            )
            if result.get("ok"):
                return
            last_error = result.get("error", "")
            page.wait_for_timeout(100)

        if last_error:
            self.events.put(("log", last_error))

    def _failure_notice_text(self, page) -> str:
        text = page.evaluate(
            """
            () => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const selectors = [
                '.el-message',
                '.ant-message',
                '.van-toast',
                '.toast',
                '[role="alert"]',
                '.message',
                '.notice'
              ];
              const nodes = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
              const texts = nodes.filter(visible).map((node) => node.innerText || node.textContent || '');
              return texts.join('\\n');
            }
            """
        )
        compact = "".join(str(text or "").split())
        if not compact:
            return ""
        failure_patterns = (
            "已被预定",
            "请重新选择时段",
            "预约失败",
            "下单失败",
            "提交失败",
            "无法预约",
            "已被预约",
            "库存不足",
        )
        for pattern in failure_patterns:
            if pattern in compact:
                return compact
        return ""

    def _clear_transient_notices(self, page) -> None:
        try:
            page.evaluate(
                """
                () => {
                  const selectors = [
                    '.el-message',
                    '.ant-message',
                    '.van-toast',
                    '.toast',
                    '[role="alert"]'
                  ];
                  for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                      node.remove();
                    }
                  }
                }
                """
            )
        except Exception:
            pass

    def _hold_browser_open(self) -> None:
        while not self.stop_event.is_set():
            self._wait_interruptibly(0.2)

    def _wait_interruptibly(self, seconds: float) -> None:
        if self.wake_event.wait(seconds):
            self.wake_event.clear()

    def _log_ready_wait(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_ready_log_at >= 5:
            self._last_ready_log_at = now
            self.events.put(("log", message))

    def _log_attempt_wait(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_attempt_log_at >= 0.8:
            self._last_attempt_log_at = now
            self.events.put(("log", message))
