"""RushBooker - timed browser-based booking at the 12:00 release."""

from __future__ import annotations

import datetime as dt
import queue
import threading
import time

from sjtu_tennis_monitor.browser.monitor import VenueMonitor
from sjtu_tennis_monitor.config import (
    court_attempt_order,
    is_rush_start_allowed,
    rush_config_label,
    rush_deadline_datetime,
    rush_release_datetime,
    target_date_labels,
)
from sjtu_tennis_monitor.constants import USER_DATA_DIR
from sjtu_tennis_monitor.exceptions import BookingPageNotReady, RequestRateLimited
from sjtu_tennis_monitor.models import RushConfig, Slot


DATE_TAB_READY_COUNT = 7


def date_bar_ready(date_count: int) -> bool:
    return date_count >= DATE_TAB_READY_COUNT


def date_bar_action(date_bar_ready: bool, target_found: bool) -> str:
    if not date_bar_ready:
        return "wait"
    if not target_found:
        return "reload"
    return "select"


def slot_cell_can_submit(state: str) -> bool:
    return state in {"available", "selected"}


def slot_cell_needs_click(state: str, selected_order_matches: bool = False) -> bool:
    if state == "available":
        return True
    if state == "selected":
        return not selected_order_matches
    return False


def point_inside_viewport(x: float, y: float, width: float, height: float, margin: float = 4.0) -> bool:
    return margin <= x <= width - margin and margin <= y <= height - margin


class RushBooker(VenueMonitor):
    """Open the booking page early, then make a timed single-slot order."""

    def __init__(self, config_provider, events: queue.Queue) -> None:
        super().__init__(config_provider, events)
        self._last_ready_log_at = 0.0
        self._last_attempt_log_at = 0.0
        self._user_stop_requested = False
        self._close_browser_event = threading.Event()

    def stop(self) -> None:
        self._user_stop_requested = True
        self._signal_close_browser()
        super().stop()

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

        if not is_rush_start_allowed(release_time=config.release_time):
            deadline = rush_deadline_datetime(release_time=config.release_time)
            self.events.put(("failed", f"抢场时间已过。本次抢场截止时间为 {deadline.strftime('%H:%M:%S')}。"))
            self.events.put(("stopped", "抢场已停止。"))
            return

        self.events.put(("log", f"抢场条件已生效：{rush_config_label(config)}"))
        self.events.put(
            ("log", f"正在打开浏览器。若尚未登录，请先完成交我办登录，程序会等待到 "
            f"{config.release_time.strftime('%H:%M:%S')} 自动抢场。")
        )

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

                self._wait_for_release_time(config)
                if self.stop_event.is_set():
                    context.close()
                    return

                self.events.put(("status", "抢场中"))
                ordered_slot = self._rush_until_deadline(page, PlaywrightTimeoutError, config)

                if ordered_slot:
                    self.events.put(("ordered", ordered_slot))
                    self.events.put(("log", "已停止自动操作，浏览器会保持打开供你确认订单。"))
                    self._hold_browser_open()
                elif self.stop_event.is_set():
                    if self._user_stop_requested:
                        self.events.put(("log", "抢场已手动停止。"))
                    else:
                        self.events.put(("failed", "抢场自动操作已停止。浏览器页面会保留打开供你查看。"))
                        self.events.put(("log", "浏览器页面已保留；需要关闭时请点击“停止抢场”。"))
                        self._hold_browser_open()
                else:
                    self.events.put(("failed", "没有抢到目标时间段的场地，抢场已停止。浏览器页面会保留打开供你查看。"))
                    self.events.put(("log", "浏览器页面已保留；需要关闭时请点击“停止抢场”。"))
                    self._hold_browser_open()

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
        while not self.stop_event.is_set() and is_rush_start_allowed(release_time=config.release_time):
            try:
                self._ensure_rush_venue_page(page, config)
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

    def _ensure_rush_venue_page(self, page, config: RushConfig) -> None:
        self._ensure_booking_page(page, config.venue)
        if self._looks_like_venue_url(page.url, config.venue):
            return
        self.events.put(("log", f"当前页面不是 {config.venue.name} 详情页，正在跳转到具体预约页。"))
        page.goto(config.venue.url, wait_until="domcontentloaded")
        raise BookingPageNotReady(f"已跳转到 {config.venue.name} 预约页，等待页面加载完成。")

    def _wait_for_release_time(self, config: RushConfig) -> None:
        release_at = rush_release_datetime(release_time=config.release_time)
        if dt.datetime.now() >= release_at:
            return

        self.events.put(("status", f"等待 {config.release_time.strftime('%H:%M:%S')}"))
        self.events.put(("log", f"预约页已打开，等待 {release_at.strftime('%H:%M:%S')} 刷新抢场。"))
        while not self.stop_event.is_set():
            remaining = (release_at - dt.datetime.now()).total_seconds()
            if remaining <= 0:
                break
            self._wait_interruptibly(min(remaining, 0.1))

    def _rush_until_deadline(self, page, timeout_error_type, config: RushConfig) -> Slot | None:
        deadline = rush_deadline_datetime(release_time=config.release_time)
        needs_reload = True
        target_date_selected = False
        reload_count = 0

        while not self.stop_event.is_set() and dt.datetime.now() < deadline:
            try:
                if needs_reload:
                    if reload_count == 0:
                        self.events.put(("log", f"{config.release_time.strftime('%H:%M:%S')} 到，正在刷新预约页。"))
                    else:
                        self.events.put(("log", "目标日期还没出现，再刷新一次预约页。"))
                    self._reload_booking_page(page, timeout_error_type)
                    reload_count += 1
                    needs_reload = False
                    target_date_selected = False

                if not self._wait_for_refreshed_booking_page(page, timeout_error_type, config, deadline):
                    return None

                if not target_date_selected:
                    date_state = self._date_tab_state(page, config.target_date)
                    action = date_bar_action(date_state["ready"], date_state["target_found"])
                    if action == "wait":
                        self._log_attempt_wait(
                            f"刷新后日期标签还没加载完整（当前 {date_state['date_count']} 个），继续等待当前页面。"
                        )
                        self._wait_interruptibly(0.3)
                        continue
                    if action == "reload":
                        self.events.put(("log", f"日期条已出现，但没有 {config.target_date.isoformat()}，立即刷新预约页。"))
                        needs_reload = True
                        continue

                    try:
                        self._select_target_date(page, timeout_error_type, config.target_date, config.venue)
                    except RuntimeError as exc:
                        self._log_attempt_wait(f"目标日期已出现但暂时点不到，继续等待当前页面：{exc}")
                        self._wait_interruptibly(0.2)
                        continue
                    target_date_selected = True
                    page.wait_for_timeout(300)

                self._raise_if_rate_limited(page)

                if not self._wait_for_target_grid_ready(page, config, deadline):
                    return None

                ordered_slot = self._try_order_current_grid(page, config)
                return ordered_slot
            except RequestRateLimited:
                raise
            except Exception as exc:
                if self._is_page_closed_error(exc):
                    self.events.put(("log", "浏览器页面已关闭，抢场停止。"))
                    self._stop_automatically(keep_browser_open=False)
                    return None
                self._log_attempt_wait(f"本轮抢场未完成：{exc}")

            self._wait_interruptibly(0.2)

        return None

    def _reload_booking_page(self, page, timeout_error_type) -> None:
        if page.is_closed():
            raise RuntimeError("浏览器页面已关闭")
        try:
            page.reload(wait_until="domcontentloaded", timeout=12000)
        except timeout_error_type:
            self.events.put(("log", "刷新响应较慢，先等待当前页面加载，不立即重复刷新。"))

    def _wait_for_refreshed_booking_page(self, page, timeout_error_type, config: RushConfig, deadline: dt.datetime) -> bool:
        while not self.stop_event.is_set() and dt.datetime.now() < deadline:
            try:
                if page.is_closed():
                    raise RuntimeError("浏览器页面已关闭")
                self._ensure_booking_page(page, config.venue)
                return True
            except RequestRateLimited:
                raise
            except BookingPageNotReady as exc:
                self._log_attempt_wait(f"刷新后页面还在加载：{exc}")
            except timeout_error_type as exc:
                self._log_attempt_wait(f"刷新后页面还在加载：{exc}")
            except Exception as exc:
                if self._is_page_closed_error(exc):
                    self.events.put(("log", "浏览器页面已关闭，抢场停止。"))
                    self._stop_automatically(keep_browser_open=False)
                    return False
                self._log_attempt_wait(f"刷新后页面还在加载：{exc}")
            self._wait_interruptibly(0.3)
        return False

    def _date_tab_state(self, page, target_date: dt.date) -> dict[str, object]:
        labels = [self._normalize_text(label) for label in target_date_labels(target_date)]
        result = page.evaluate(
            """
            ({ labels, readyCount }) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
              const bodyText = norm(document.body.innerText);
              const texts = Array.from(document.querySelectorAll('body *'))
                .filter(visible)
                .map((el) => norm(el.innerText || el.textContent || ''))
                .filter(Boolean);
              const dateTexts = new Set(
                texts.filter((text) =>
                  /\\d{1,2}月\\d{1,2}日/.test(text) ||
                  /\\d{4}-\\d{2}-\\d{2}/.test(text)
                )
              );
              return {
                ready: dateTexts.size >= readyCount,
                targetFound: labels.some((label) => bodyText.includes(label)),
                dateCount: dateTexts.size,
              };
            }
            """,
            {"labels": labels, "readyCount": DATE_TAB_READY_COUNT},
        )
        return {
            "ready": bool(result.get("ready")),
            "target_found": bool(result.get("targetFound")),
            "date_count": int(result.get("dateCount") or 0),
        }

    def _wait_for_target_grid_ready(self, page, config: RushConfig, deadline: dt.datetime) -> bool:
        while not self.stop_event.is_set() and dt.datetime.now() < deadline:
            state = self._target_grid_state(page, config)
            if state["ready"]:
                return True
            self._log_attempt_wait(f"等待场地图加载：{state['reason']}")
            self._wait_interruptibly(0.2)
        return False

    def _target_grid_state(self, page, config: RushConfig) -> dict[str, object]:
        return page.evaluate(
            """
            ({ targetHour }) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
              const all = Array.from(document.querySelectorAll('body *')).filter(visible);
              const bodyText = norm(document.body.innerText);

              const loadingSelectors = [
                '.el-loading-mask',
                '.el-loading-spinner',
                '.ant-spin-spinning',
                '.van-loading',
                '.loading',
                '[class*="loading"]',
                '[class*="spin"]'
              ];
              const hasLoading = loadingSelectors.some((selector) =>
                Array.from(document.querySelectorAll(selector)).some(visible)
              ) || /加载中|正在加载/.test(bodyText);
              if (hasLoading) {
                return { ready: false, reason: '页面仍在加载' };
              }

              const timeNodes = all
                .map((el) => ({ text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => /^\\d{2}:00$/.test(item.text));
              const courtNodes = all
                .map((el) => ({ text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => /^场地\\d+$/.test(item.text));

              const targetTime = timeNodes.find((item) => item.text === targetHour);
              if (!targetTime) {
                return { ready: false, reason: `还没看到 ${targetHour} 时间行` };
              }

              const courtSet = new Set(courtNodes.map((item) => item.text));
              for (let court = 1; court <= 8; court += 1) {
                if (!courtSet.has(`场地${court}`)) {
                  return { ready: false, reason: `还没看到场地${court}` };
                }
              }

              const gridLeft = Math.min(...courtNodes.map((item) => item.rect.left)) - 20;
              const gridRight = Math.max(...courtNodes.map((item) => item.rect.right)) + 20;
              const rowCenterY = targetTime.rect.top + targetTime.rect.height / 2;
              const candidates = all
                .map((el) => ({ el, rect: el.getBoundingClientRect() }))
                .filter((item) =>
                  item.rect.left >= gridLeft &&
                  item.rect.right <= gridRight &&
                  Math.abs((item.rect.top + item.rect.height / 2) - rowCenterY) <= 45 &&
                  item.rect.width >= 25 &&
                  item.rect.width <= 90 &&
                  item.rect.height >= 20 &&
                  item.rect.height <= 70
                );

              return {
                ready: candidates.length >= 8,
                reason: candidates.length >= 8 ? '场地图已加载' : `目标时间行只有 ${candidates.length} 个格子`
              };
            }
            """,
            {"targetHour": f"{config.start_hour:02d}:00"},
        )

    def _normalize_text(self, text: str) -> str:
        return "".join((text or "").split())

    def _is_page_closed_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "target page" in message and "closed" in message
            or "context or browser has been closed" in message
            or "浏览器页面已关闭" in message
        )

    def _try_order_current_grid(self, page, config: RushConfig) -> Slot | None:
        for court in court_attempt_order(config.preferred_court):
            if (
                self.stop_event.is_set()
                or dt.datetime.now() >= rush_deadline_datetime(release_time=config.release_time)
            ):
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
                cell_state = self._slot_cell_state(page, slot)
                cell_state_name = str(cell_state.get("state", ""))
                if not slot_cell_can_submit(cell_state_name):
                    self.events.put(("log", f"{slot.court} 不是蓝色可选格子，跳过：{cell_state['reason']}"))
                    continue

                selected_order_matches = self._selected_order_matches_slot(page, slot)
                if slot_cell_needs_click(cell_state_name, selected_order_matches):
                    self._try_select_slot_cell(page, slot, cell_state)
                    if not self._wait_for_slot_selected(page, slot):
                        self.events.put(("log", f"{slot.court} 点击后没有确认选中，继续尝试下一个场地。"))
                        continue
                else:
                    self.events.put(("log", f"{slot.court} 已经在订单栏中确认选中，继续下单。"))

                failure = self._failure_notice_text(page)
                if failure:
                    self.events.put(("log", f"{slot.court} 已不可用：{failure}"))
                    continue

                self._click_visible_text_button(page, "立即下单", timeout=1500)
                page.wait_for_timeout(180)
                failure = self._failure_notice_text(page)
                if failure:
                    self.events.put(("log", f"{slot.court} 下单前已失败：{failure}"))
                    continue

                if not self._accept_booking_notice_fast(page):
                    self.events.put(("log", "没有确认勾选预订须知，停止自动操作以避免误提交。"))
                    self._stop_automatically()
                    return None

                self._click_visible_text_button(page, "提交订单", timeout=2000)
                result_kind, result_text = self._wait_for_order_result(page)
                if result_kind == "success":
                    self.events.put(("log", f"已确认抢场成功：{slot.venue} {slot.date.isoformat()} {slot.hour}-{config.end_hour:02d}:00 {slot.court}"))
                    return slot
                if result_kind == "failure":
                    self.events.put(("log", f"{slot.court} 提交失败：{result_text}"))
                    continue

                self.events.put(("log", f"{slot.court} 提交后没有看到明确成功或失败：{result_text}。停止自动操作以避免重复下单。"))
                self._stop_automatically()
                return None
            except Exception as exc:
                if self._is_page_closed_error(exc):
                    self.events.put(("log", "浏览器页面已关闭，抢场停止。"))
                    self._stop_automatically(keep_browser_open=False)
                    return None
                self.events.put(("log", f"{slot.court} 未抢到，继续尝试下一个场地：{exc}"))

        return None

    def _scroll_slot_into_view(self, page, slot: Slot) -> None:
        result = page.evaluate(
            """
            ({ targetHour }) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
              const timeNode = Array.from(document.querySelectorAll('body *'))
                .filter(visible)
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || '') }))
                .find((item) => item.text === targetHour);
              if (!timeNode) {
                return { error: `没有找到 ${targetHour} 时间行，不能滚动到目标位置` };
              }
              timeNode.el.scrollIntoView({ block: 'center', inline: 'nearest' });
              return { ok: true };
            }
            """,
            {"targetHour": slot.hour},
        )
        if result.get("error"):
            raise RuntimeError(result["error"])
        page.wait_for_timeout(80)

    def _slot_cell_state(self, page, slot: Slot) -> dict[str, object]:
        return page.evaluate(
            """
            ({ targetCourt, targetHour }) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };
              const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
              const all = Array.from(document.querySelectorAll('body *')).filter(visible);

              const timeNodes = all
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => /^\\d{2}:00$/.test(item.text))
                .sort((a, b) => a.rect.top - b.rect.top);
              const courtNodes = all
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => /^场地\\d+$/.test(item.text))
                .sort((a, b) => a.rect.left - b.rect.left);

              const targetTime = timeNodes.find((item) => item.text === targetHour);
              const targetCourtNode = courtNodes.find((item) => item.text === targetCourt);
              if (!targetTime || !targetCourtNode) {
                return { state: 'loading', reason: '目标时间行或场地列还没加载出来' };
              }

              const gridLeft = Math.min(...courtNodes.map((item) => item.rect.left)) - 20;
              const gridRight = Math.max(...courtNodes.map((item) => item.rect.right)) + 20;
              const gridTop = Math.min(...timeNodes.map((item) => item.rect.top)) - 20;
              const gridBottom = Math.max(...timeNodes.map((item) => item.rect.bottom)) + 80;
              const targetX = targetCourtNode.rect.left + targetCourtNode.rect.width / 2;
              const targetY = targetTime.rect.top + targetTime.rect.height / 2;

              const rgbValues = (text) => {
                const values = [];
                const pattern = /rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/g;
                let match;
                while ((match = pattern.exec(text.toLowerCase())) !== null) {
                  values.push([Number(match[1]), Number(match[2]), Number(match[3])]);
                }
                return values;
              };

              const hexValues = (text) => {
                const values = [];
                const pattern = /#([0-9a-f]{6})/gi;
                let match;
                while ((match = pattern.exec(text.toLowerCase())) !== null) {
                  const value = match[1];
                  values.push([
                    Number.parseInt(value.slice(0, 2), 16),
                    Number.parseInt(value.slice(2, 4), 16),
                    Number.parseInt(value.slice(4, 6), 16),
                  ]);
                }
                return values;
              };

              const localAncestors = (el, baseRect) => {
                const nodes = [el, ...Array.from(el.querySelectorAll('*'))];
                let current = el.parentElement;
                for (let depth = 0; current && depth < 5; depth += 1) {
                  const rect = current.getBoundingClientRect();
                  const centerX = rect.left + rect.width / 2;
                  const centerY = rect.top + rect.height / 2;
                  const baseCenterX = baseRect.left + baseRect.width / 2;
                  const baseCenterY = baseRect.top + baseRect.height / 2;
                  const sameCellArea =
                    rect.width <= 120 &&
                    rect.height <= 90 &&
                    Math.abs(centerX - baseCenterX) <= 8 &&
                    Math.abs(centerY - baseCenterY) <= 8;
                  if (!sameCellArea) {
                    break;
                  }
                  nodes.push(current);
                  current = current.parentElement;
                }
                return nodes;
              };

              const classify = (el) => {
                const baseRect = el.getBoundingClientRect();
                const related = localAncestors(el, baseRect);

                const combined = related.map((node) => {
                  const text = norm(node.innerText || node.textContent || '');
                  const title = norm(node.getAttribute('title'));
                  const aria = norm(node.getAttribute('aria-label'));
                  const ariaChecked = norm(node.getAttribute('aria-checked'));
                  const klass = norm(node.className && node.className.toString());
                  const dataState = norm(node.getAttribute('data-state') || node.getAttribute('data-status'));
                  return `${text}|${title}|${aria}|${ariaChecked}|${klass}|${dataState}`;
                }).join('|');

                const colorText = related.map((node) => {
                  const style = window.getComputedStyle(node);
                  return [
                    style.backgroundColor,
                    style.borderColor,
                    style.color,
                    style.fill,
                    style.stroke,
                    style.backgroundImage,
                    node.getAttribute('fill') || '',
                    node.getAttribute('stroke') || '',
                    node.getAttribute('style') || '',
                  ].join(' ');
                }).join(' ');

                const decodedColorText = (() => {
                  try {
                    return decodeURIComponent(colorText);
                  } catch {
                    return colorText;
                  }
                })();
                const colors = [...rgbValues(decodedColorText), ...hexValues(decodedColorText)];
                const hasGreen = colors.some(([r, g, b]) => g >= 130 && r <= 120 && b <= 180 && g - r >= 45);
                if (/selected|active|checked|is-checked/i.test(combined) || hasGreen) {
                  return { state: 'selected', reason: '格子已选中' };
                }

                const hasBlue = colors.some(([r, g, b]) =>
                  b >= 185 && g >= 120 && r <= 210 && b - r >= 30 && b - g >= 8
                );
                if (/available|selectable|free|empty|enabled|optional|appointable/i.test(combined) || hasBlue) {
                  return { state: 'available', reason: '蓝色可选格子' };
                }

                if (/不可选|已约|已满|禁用|disabled|disable|unavailable|booked|sold|reserved/i.test(combined)) {
                  return { state: 'unavailable', reason: '格子标记为不可选' };
                }

                const hasGray = colors.some(([r, g, b]) =>
                  Math.abs(r - g) <= 18 && Math.abs(g - b) <= 18 && r >= 115 && r <= 245
                );
                if (hasGray) {
                  return { state: 'unavailable', reason: '灰色不可选格子' };
                }

                return { state: 'unknown', reason: '无法识别格子颜色状态' };
              };

              const candidates = all
                .map((el) => ({ el, rect: el.getBoundingClientRect() }))
                .filter((item) =>
                  item.rect.left >= gridLeft &&
                  item.rect.right <= gridRight &&
                  item.rect.top >= gridTop &&
                  item.rect.bottom <= gridBottom &&
                  item.rect.width >= 25 &&
                  item.rect.width <= 90 &&
                  item.rect.height >= 20 &&
                  item.rect.height <= 70
                )
                .map((item) => ({
                  ...item,
                  centerX: item.rect.left + item.rect.width / 2,
                  centerY: item.rect.top + item.rect.height / 2,
                }))
                .filter((item) =>
                  Math.abs(item.centerX - targetX) <= 60 &&
                  Math.abs(item.centerY - targetY) <= 45
                );
              candidates.sort((a, b) => {
                const aDistance = Math.abs(a.centerX - targetX) + Math.abs(a.centerY - targetY);
                const bDistance = Math.abs(b.centerX - targetX) + Math.abs(b.centerY - targetY);
                if (Math.abs(aDistance - bDistance) > 1) {
                  return aDistance - bDistance;
                }
                return (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height);
              });

              const best = candidates[0];
              if (!best) {
                return { state: 'not_found', reason: '没有找到目标格子' };
              }

              const classification = classify(best.el);
              return {
                state: classification.state,
                reason: classification.reason,
                x: best.centerX,
                y: best.centerY,
              };
            }
            """,
            {"targetCourt": slot.court, "targetHour": slot.hour},
        )

    def _try_select_slot_cell(self, page, slot: Slot, cell_state: dict[str, object]) -> None:
        x = cell_state.get("x")
        y = cell_state.get("y")
        if x is None or y is None:
            raise RuntimeError(f"没有 {slot.court} {slot.hour} 的点击坐标")
        x = float(x)
        y = float(y)
        if not self._is_click_point_in_viewport(page, x, y):
            self._scroll_slot_into_view(page, slot)
            refreshed_state = self._slot_cell_state(page, slot)
            refreshed_state_name = str(refreshed_state.get("state", ""))
            if not slot_cell_can_submit(refreshed_state_name):
                raise RuntimeError(f"滚动后 {slot.court} {slot.hour} 已不可下单：{refreshed_state.get('reason')}")
            x = refreshed_state.get("x")
            y = refreshed_state.get("y")
            if x is None or y is None:
                raise RuntimeError(f"滚动后仍没有 {slot.court} {slot.hour} 的点击坐标")
            x = float(x)
            y = float(y)
            if not self._is_click_point_in_viewport(page, x, y):
                raise RuntimeError(f"{slot.court} {slot.hour} 不在当前可点击范围内")
        page.mouse.click(x, y)
        page.wait_for_timeout(120)

    def _is_click_point_in_viewport(self, page, x: float, y: float) -> bool:
        viewport = page.evaluate(
            """
            () => ({ width: window.innerWidth, height: window.innerHeight })
            """
        )
        return point_inside_viewport(x, y, float(viewport["width"]), float(viewport["height"]))

    def _wait_for_slot_selected(self, page, slot: Slot) -> bool:
        end_at = time.monotonic() + 1.2
        while time.monotonic() < end_at and not self.stop_event.is_set():
            if self._selected_order_matches_slot(page, slot):
                return True
            cell_state = self._slot_cell_state(page, slot)
            if cell_state["state"] == "selected" and self._selected_order_matches_slot(page, slot):
                return True
            if self._failure_notice_text(page):
                return False
            page.wait_for_timeout(100)
        return False

    def _selected_order_matches_slot(self, page, slot: Slot) -> bool:
        try:
            body_text = page.evaluate(
                """
                () => (document.body.innerText || document.body.textContent || '')
                """
            )
        except Exception:
            return False
        compact = "".join(str(body_text or "").split())
        try:
            end_hour = int(slot.hour.split(":", 1)[0]) + 1
        except ValueError:
            end_hour = 0
        time_range = f"{slot.hour}-{end_hour:02d}:00" if end_hour else slot.hour
        return slot.court in compact and time_range in compact

    def _click_visible_text_button(self, page, text: str, timeout: int) -> None:
        end_at = time.monotonic() + timeout / 1000
        last_error = ""
        while time.monotonic() < end_at and not self.stop_event.is_set():
            result = page.evaluate(
                """
                ({ text }) => {
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
                  };
                  const norm = (value) => (value || '').replace(/\\s+/g, '').trim();
                  const targetText = norm(text);
                  const candidates = Array.from(document.querySelectorAll('button,[role="button"],.el-button,.ant-btn'))
                    .filter(visible)
                    .filter((el) => norm(el.innerText || el.textContent || '').includes(targetText))
                    .filter((el) => !el.disabled && !/disabled|is-disabled/.test(String(el.className || '')));
                  const target = candidates[candidates.length - 1];
                  if (!target) {
                    return { error: `没有找到可见按钮：${text}` };
                  }
                  target.scrollIntoView({ block: 'center', inline: 'center' });
                  const rect = target.getBoundingClientRect();
                  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                }
                """,
                {"text": text},
            )
            if not result.get("error"):
                page.mouse.click(result["x"], result["y"])
                return
            last_error = result["error"]
            page.wait_for_timeout(100)
        raise RuntimeError(last_error or f"没有找到可见按钮：{text}")

    def _accept_booking_notice_fast(self, page) -> bool:
        end_at = time.monotonic() + 2.0
        while time.monotonic() < end_at and not self.stop_event.is_set():
            result = page.evaluate(
                """
                () => {
                  const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
                  const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
                  };
                  const bodyText = norm(document.body.innerText);
                  if (!/预订须知|本人已认真阅读|自愿接受|同意/.test(bodyText)) {
                    return { ok: false, reason: '预订须知弹窗还没出现' };
                  }

                  const checkedByClass = Array.from(document.querySelectorAll('.is-checked,.el-checkbox__input.is-checked,[aria-checked="true"]'))
                    .some(visible);
                  const checkedInput = Array.from(document.querySelectorAll('input[type="checkbox"]'))
                    .some((input) => input.checked);
                  if (checkedByClass || checkedInput) {
                    return { ok: true, reason: '须知已勾选' };
                  }

                  const labels = Array.from(document.querySelectorAll('label')).filter(visible);
                  const label = labels.find((item) => /本人已认真阅读|自愿接受|同意/.test(norm(item.innerText)));
                  if (label) {
                    label.click();
                    return { ok: false, reason: '已点击须知文字，等待勾选生效' };
                  }

                  const checkbox = Array.from(document.querySelectorAll('input[type="checkbox"]')).find((item) => !item.checked);
                  if (checkbox) {
                    checkbox.click();
                    return { ok: false, reason: '已点击复选框，等待勾选生效' };
                  }

                  const box = Array.from(document.querySelectorAll('.el-checkbox,.el-checkbox__input,.ant-checkbox,.ant-checkbox-wrapper'))
                    .find(visible);
                  if (box) {
                    box.click();
                    return { ok: false, reason: '已点击复选框区域，等待勾选生效' };
                  }

                  return { ok: false, reason: '没有找到须知勾选框' };
                }
                """
            )
            if result.get("ok"):
                return True
            self._log_attempt_wait(str(result.get("reason", "正在勾选预订须知")))
            page.wait_for_timeout(100)

        return False

    def _wait_for_order_result(self, page) -> tuple[str, str]:
        end_at = time.monotonic() + 3.0
        last_text = ""
        while time.monotonic() < end_at and not self.stop_event.is_set():
            failure = self._failure_notice_text(page)
            if failure:
                return "failure", failure
            success = self._success_notice_text(page)
            if success:
                return "success", success
            page.wait_for_timeout(120)
        return "unknown", last_text or "提交后没有看到明确成功或失败提示"

    def _success_notice_text(self, page) -> str:
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
        success_patterns = (
            "预约成功",
            "预订成功",
            "下单成功",
            "提交成功",
            "订单提交成功",
        )
        for pattern in success_patterns:
            if pattern in compact:
                return compact
        return ""

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
            "请选择场地",
            "请先选择场地",
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
        while not self._close_browser_event.wait(0.2):
            pass

    def _stop_automatically(self, keep_browser_open: bool = True) -> None:
        if not keep_browser_open:
            self._signal_close_browser()
        self.stop_event.set()
        self.wake_event.set()

    def _signal_close_browser(self) -> None:
        self._close_browser_event.set()

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
