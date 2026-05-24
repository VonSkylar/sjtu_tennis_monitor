"""VenueMonitor — Playwright browser-based booking page monitor."""

from __future__ import annotations

import datetime as dt
import queue
import re
import threading
import time

from sjtu_tennis_booking.constants import (
    DEFAULT_CHECK_INTERVAL_SECONDS,
    DEBUG_PAGE_LIST_SECONDS,
    SETUP_SCAN_SECONDS,
    USER_DATA_DIR,
)
from sjtu_tennis_booking.config import (
    config_label,
    next_rate_limit_retry_time,
    save_rate_limit_cooldown,
    target_date_labels,
)
from sjtu_tennis_booking.exceptions import BookingPageNotReady, RequestRateLimited
from sjtu_tennis_booking.models import MonitorConfig, Slot, Venue, VENUES


class VenueMonitor:
    def __init__(self, config_provider, events: queue.Queue) -> None:
        self.config_provider = config_provider
        self.events = events
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._last_page_debug_at = 0.0
        self._last_not_ready_log_at = 0.0
        self._last_config: MonitorConfig | None = None
        self._next_date_index_by_venue: dict[str, int] = {}
        self._pages_by_venue: dict[str, object] = {}
        self._submitted_order_keys: set[tuple[str, str, str, str]] = set()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def wake(self) -> None:
        self.wake_event.set()

    def run(self) -> None:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.events.put(("error", "缺少 Playwright。请先运行：pip install -r requirements.txt，然后运行：python -m playwright install chromium"))
            return

        self.events.put(("log", "正在打开浏览器。若尚未登录，请先完成交我办登录。程序会按所选场馆打开网球场预约页。"))

        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    USER_DATA_DIR,
                    headless=False,
                    viewport={"width": 1400, "height": 950},
                )
                while not self.stop_event.is_set():
                    wait_seconds = self._current_check_interval()
                    try:
                        config = self.config_provider()
                        wait_seconds = config.check_interval_seconds
                        if config != self._last_config:
                            self._last_config = config
                            self._next_date_index_by_venue = {venue.key: 0 for venue in config.venues}
                            self.events.put(("log", f"监控条件已生效：{config_label(config)}"))

                        all_slots = []
                        checked_dates = []
                        venue_not_ready = []
                        venue_errors = []
                        for venue in config.venues:
                            try:
                                page = self._find_or_open_venue_page(context, venue)
                                slots, checked_date = self._check_once(page, PlaywrightTimeoutError, config, venue)
                            except RequestRateLimited:
                                raise
                            except BookingPageNotReady as exc:
                                venue_not_ready.append(f"{venue.name}：{exc}")
                                continue
                            except Exception as exc:
                                venue_errors.append(f"{venue.name}：{exc}")
                                continue
                            checked_dates.append((venue, checked_date))
                            all_slots.extend(slots)

                        if all_slots:
                            self.events.put(("available", all_slots))
                            self._maybe_auto_order_unique_slot(config, all_slots)
                        elif checked_dates:
                            checked_at = dt.datetime.now().strftime("%H:%M:%S")
                            checked = "；".join(f"{venue.name} {checked_date.isoformat()}" for venue, checked_date in checked_dates)
                            self.events.put(("log", f"{checked_at} {checked} 未发现目标时段空场，{config.check_interval_seconds} 秒后检查下一轮。"))
                            if venue_not_ready or venue_errors:
                                self.events.put(("log", "部分场馆本轮未完成：" + "；".join(venue_not_ready + venue_errors)))
                        elif venue_not_ready and not venue_errors:
                            raise BookingPageNotReady("；".join(venue_not_ready))
                        elif venue_errors:
                            raise RuntimeError("；".join(venue_errors))
                    except ValueError as exc:
                        wait_seconds = SETUP_SCAN_SECONDS
                        self._log_not_ready(f"监控条件暂未生效：{exc}")
                    except BookingPageNotReady as exc:
                        wait_seconds = SETUP_SCAN_SECONDS
                        self._log_not_ready(str(exc))
                    except RequestRateLimited as exc:
                        retry_at = next_rate_limit_retry_time()
                        save_rate_limit_cooldown(retry_at)
                        self.events.put(("error", f"{exc}\n\n今天请求额度已经用完，建议不要继续刷新。程序已停止监控，可在 {retry_at.strftime('%Y-%m-%d %H:%M')} 后再试。"))
                        self.stop_event.set()
                        break
                    except Exception as exc:  # Keep polling through transient page changes.
                        self.events.put(("log", f"本轮检查未完成：{exc}"))
                        wait_seconds = self._current_check_interval()

                    self._sleep_interruptibly(wait_seconds)

                context.close()
        except Exception as exc:
            self.events.put(("error", f"浏览器监控启动失败：{exc}"))
        finally:
            self.events.put(("stopped", "监控已停止。"))

    def _find_or_open_venue_page(self, context, venue: Venue):
        cached = self._pages_by_venue.get(venue.key)
        if cached and not cached.is_closed():
            matched_venue = self._venue_from_url(cached.url)
            if matched_venue and matched_venue.key != venue.key:
                self._pages_by_venue.pop(venue.key, None)
            else:
                if self._is_blank_page(cached):
                    cached.goto(venue.url, wait_until="domcontentloaded")
                self.events.put(("log", f"正在监控 {venue.name} 标签页：{self._page_label(cached)}"))
                return cached

        page = self._find_existing_venue_page(context, venue)
        if page:
            self._pages_by_venue[venue.key] = page
            return page

        page = self._find_blank_page(context) or context.new_page()
        self._pages_by_venue[venue.key] = page
        self.events.put(("log", f"正在打开 {venue.name} 预约页。"))
        page.goto(venue.url, wait_until="domcontentloaded")
        return page

    def _is_blank_page(self, page) -> bool:
        url = (page.url or "").lower()
        return url in {"", "about:blank"} or url.startswith("chrome://newtab")

    def _find_blank_page(self, context):
        for page in context.pages:
            if not page.is_closed() and self._is_blank_page(page):
                return page
        return None

    def _find_existing_venue_page(self, context, venue: Venue):
        candidates = list(context.pages)

        self._log_visible_pages(candidates)

        url_matches = []
        for candidate in candidates:
            if candidate.is_closed():
                continue
            url = candidate.url.lower()
            if url in {"", "about:blank"} or url.startswith("chrome://newtab"):
                continue
            if self._looks_like_venue_url(url, venue):
                url_matches.append(candidate)

        for candidate in url_matches:
            self.events.put(("log", f"正在监控 {venue.name} 标签页：{self._page_label(candidate)}"))
            return candidate

        return None

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

    def _maybe_auto_order_unique_slot(self, config: MonitorConfig, slots: list[Slot]) -> None:
        if not config.auto_order_enabled:
            return
        if len(slots) != 1:
            self.events.put(("log", f"本轮发现 {len(slots)} 个符合条件的空场，按设置不自动下单，只报警。"))
            return

        slot = slots[0]
        order_key = (slot.venue_key, slot.date.isoformat(), slot.hour, slot.court)
        if order_key in self._submitted_order_keys:
            self.events.put(("log", f"{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court} 已尝试提交过订单，本轮不重复下单。"))
            return

        try:
            self._auto_order_slot(slot)
        except Exception as exc:
            self.events.put(("log", f"自动下单未完成：{exc}"))
            return

        self._submitted_order_keys.add(order_key)
        self.events.put(("log", f"已自动提交订单：{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court}"))

    def _auto_order_slot(self, slot: Slot) -> None:
        page = self._pages_by_venue.get(slot.venue_key)
        if not page or page.is_closed():
            raise RuntimeError(f"找不到 {slot.venue} 的预约标签页")

        self.events.put(("log", f"唯一符合条件空场，开始自动下单：{slot.venue} {slot.date.isoformat()} {slot.hour} {slot.court}"))
        self._click_slot_cell(page, slot)
        page.wait_for_timeout(500)
        self._click_text_button(page, "立即下单", timeout=5000)
        page.wait_for_timeout(800)
        self._accept_booking_notice(page)
        page.wait_for_timeout(300)
        self._click_text_button(page, "提交订单", timeout=5000)

    def _click_slot_cell(self, page, slot: Slot) -> None:
        click_point = page.evaluate(
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
                .map((el) => ({ el, text: norm(el.innerText), rect: el.getBoundingClientRect() }))
                .filter((item) => /^\\d{2}:00$/.test(item.text))
                .sort((a, b) => a.rect.top - b.rect.top);
              const courtNodes = all
                .map((el) => ({ el, text: norm(el.innerText), rect: el.getBoundingClientRect() }))
                .filter((item) => /^场地\\d+$/.test(item.text))
                .sort((a, b) => a.rect.left - b.rect.left);

              if (timeNodes.length === 0 || courtNodes.length === 0) {
                return { error: '没有识别到时间行或场地列。' };
              }

              const gridLeft = Math.min(...courtNodes.map((item) => item.rect.left)) - 20;
              const gridRight = Math.max(...courtNodes.map((item) => item.rect.right)) + 20;
              const gridTop = Math.min(...timeNodes.map((item) => item.rect.top)) - 20;
              const gridBottom = Math.max(...timeNodes.map((item) => item.rect.bottom)) + 80;

              const isAvailable = (el) => {
                const chain = [];
                let current = el;
                for (let depth = 0; current && depth < 4; depth += 1) {
                  chain.push(current);
                  current = current.parentElement;
                }
                const combined = chain.map((node) => {
                  const text = norm(node.innerText);
                  const title = norm(node.getAttribute('title'));
                  const aria = norm(node.getAttribute('aria-label'));
                  const klass = norm(node.className && node.className.toString());
                  const dataState = norm(node.getAttribute('data-state') || node.getAttribute('data-status'));
                  return `${text}|${title}|${aria}|${klass}|${dataState}`;
                }).join('|');

                if (/不可选|已约|已满|禁用|disabled|disable|unavailable|booked|sold|reserved/i.test(combined)) {
                  return false;
                }
                if (/可选|available|selectable|free|empty|enabled|optional|appointable/i.test(combined)) {
                  return true;
                }

                const colorText = chain.map((node) => {
                  const style = window.getComputedStyle(node);
                  return `${style.backgroundColor} ${style.borderColor} ${style.backgroundImage}`;
                }).join(' ').toLowerCase();
                const blueish = /rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/g;
                let match;
                while ((match = blueish.exec(colorText)) !== null) {
                  const r = Number(match[1]);
                  const g = Number(match[2]);
                  const b = Number(match[3]);
                  if (b >= 185 && g >= 120 && r <= 210 && b - r >= 30 && b - g >= 8) {
                    return true;
                  }
                }
                return false;
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
                .filter((item) => isAvailable(item.el));

              for (const item of candidates) {
                const centerX = item.rect.left + item.rect.width / 2;
                const centerY = item.rect.top + item.rect.height / 2;
                const hour = timeNodes.reduce((best, node) => {
                  const y = node.rect.top + node.rect.height / 2;
                  const distance = Math.abs(centerY - y);
                  return !best || distance < best.distance ? { node, distance } : best;
                }, null);
                const court = courtNodes.reduce((best, node) => {
                  const x = node.rect.left + node.rect.width / 2;
                  const distance = Math.abs(centerX - x);
                  return !best || distance < best.distance ? { node, distance } : best;
                }, null);

                if (
                  hour &&
                  court &&
                  hour.distance <= 45 &&
                  court.distance <= 60 &&
                  hour.node.text === targetHour &&
                  court.node.text === targetCourt
                ) {
                  item.el.scrollIntoView({ block: 'center', inline: 'center' });
                  const rect = item.el.getBoundingClientRect();
                  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                }
              }

              return { error: `没有找到 ${targetCourt} ${targetHour} 对应的可选格子。` };
            }
            """,
            {"targetCourt": slot.court, "targetHour": slot.hour},
        )
        if click_point.get("error"):
            raise RuntimeError(click_point["error"])
        page.mouse.click(click_point["x"], click_point["y"])

    def _click_text_button(self, page, text: str, timeout: int) -> None:
        try:
            page.get_by_role("button", name=re.compile(text)).last.click(timeout=timeout)
            return
        except Exception:
            pass
        page.get_by_text(text, exact=True).last.click(timeout=timeout)

    def _accept_booking_notice(self, page) -> None:
        try:
            page.get_by_text("预订须知", exact=False).first.wait_for(timeout=5000)
        except Exception:
            pass

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
        if result.get("error"):
            raise RuntimeError(result["error"])

    def _log_not_ready(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_not_ready_log_at >= 5:
            self._last_not_ready_log_at = now
            self.events.put(("log", message))

    def _looks_like_booking_url(self, url: str) -> bool:
        normalized = url.lower()
        return (
            "sports.sjtu.edu.cn" in normalized
            or "appointmentdetails" in normalized
            or "apointmentdetails" in normalized
            or "appointment" in normalized
        )

    def _looks_like_venue_url(self, url: str, venue: Venue) -> bool:
        venue_id = self._appointment_id_from_url(venue.url)
        return bool(venue_id and venue_id in (url or "").lower())

    def _venue_from_url(self, url: str) -> Venue | None:
        for venue in VENUES:
            if self._looks_like_venue_url(url, venue):
                return venue
        return None

    def _appointment_id_from_url(self, url: str) -> str:
        match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", url.lower())
        return match.group(0) if match else ""

    def _has_booking_grid(self, page) -> bool:
        try:
            return bool(
                page.locator("text=场地1").count() > 0
                and (page.locator("text=07:00").count() > 0 or page.locator("text=08:00").count() > 0)
            )
        except Exception:
            return False

    def _looks_like_booking_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        has_venue = any(venue.name in compact for venue in VENUES) or "网球场地" in compact
        has_grid = ("场地1" in compact or "场地2" in compact) and ("07:00" in compact or "08:00" in compact)
        has_system = "场馆预约系统" in compact or "VENUERESERVATIONSYSTEM" in compact
        return has_venue or has_grid or (has_system and has_grid)

    def _log_visible_pages(self, pages) -> None:
        now = time.monotonic()
        if now - self._last_page_debug_at < DEBUG_PAGE_LIST_SECONDS:
            return
        self._last_page_debug_at = now
        labels = [self._page_label(page) for page in pages if not page.is_closed()]
        if labels:
            self.events.put(("log", "程序当前能看到的标签页：" + "；".join(labels)))
        else:
            self.events.put(("log", "程序当前没有看到任何浏览器标签页。"))

    def _page_label(self, page) -> str:
        try:
            title = page.title(timeout=800).strip()
        except Exception:
            title = ""
        url = (page.url or "").strip()
        if len(url) > 90:
            url = url[:87] + "..."
        return f"\u201c{title or '无标题'}\u201d {url or '无地址'}"

    def _check_once(self, page, timeout_error_type, config: MonitorConfig, venue: Venue) -> tuple[list[Slot], dt.date]:
        self._ensure_booking_page(page, venue)
        next_date_index = self._next_date_index_by_venue.get(venue.key, 0) % len(config.dates)
        for offset in range(len(config.dates)):
            index = (next_date_index + offset) % len(config.dates)
            target_date = config.dates[index]
            try:
                self._select_target_date(page, timeout_error_type, target_date, venue)
            except RuntimeError as exc:
                self.events.put(("log", f"{venue.name} {target_date.isoformat()} 暂未检查：{exc}"))
                continue

            self._next_date_index_by_venue[venue.key] = (index + 1) % len(config.dates)
            page.wait_for_timeout(900)
            self._raise_if_rate_limited(page)
            slots = self._extract_available_slots(page, config, target_date, venue)
            return slots, target_date

        raise RuntimeError("没有任何目标日期在页面日期标签中开放。")

    def _ensure_booking_page(self, page, venue: Venue) -> None:
        text = page.locator("body").inner_text(timeout=3000)
        self._handle_login_page(page, text)
        if not (self._looks_like_booking_url(page.url) or self._looks_like_booking_text(text) or self._has_booking_grid(page)):
            raise BookingPageNotReady("标签页不像网球场预约页。请确认能看到日期、时间和场地表格。")
        if "每日请求超过限制" in text or "请求超过限制" in text:
            raise RequestRateLimited("学校系统提示\u201c每日请求超过限制，无法获取\u201d。")

    def _handle_login_page(self, page, text: str) -> None:
        compact = re.sub(r"\s+", "", text or "")
        url = (page.url or "").lower()

        if "jaccount.sjtu.edu.cn" in url or "统一身份认证" in compact or "登录jAccount" in compact:
            raise BookingPageNotReady("正在等待 jAccount 登录。请扫码或输入验证码完成登录；登录成功后程序会继续监控，并复用本地浏览器会话。")

        if "校内人员登录" in compact:
            try:
                page.get_by_text("校内人员登录", exact=True).click(timeout=1500)
            except Exception:
                try:
                    page.get_by_role("button", name=re.compile("校内人员登录")).click(timeout=1500)
                except Exception as exc:
                    raise BookingPageNotReady(f"检测到未登录，但没有点到\u201c校内人员登录\u201d：{exc}") from exc
            raise BookingPageNotReady("检测到未登录，已自动点击\u201c校内人员登录\u201d。如进入 jAccount 页面，请手动完成一次登录。")

    def _raise_if_rate_limited(self, page) -> None:
        text = page.locator("body").inner_text(timeout=3000)
        if "每日请求超过限制" in text or "请求超过限制" in text:
            raise RequestRateLimited("学校系统提示\u201c每日请求超过限制，无法获取\u201d。")

    def _select_target_date(self, page, timeout_error_type, target_date: dt.date, venue: Venue) -> None:
        for label in target_date_labels(target_date):
            locator = page.get_by_text(label, exact=False).first
            try:
                if locator.count() > 0:
                    locator.click(timeout=1200)
                    self.events.put(("log", f"{venue.name} 已切换到目标日期：{label}"))
                    return
            except timeout_error_type:
                continue
        raise RuntimeError("目标日期暂未开放预约，或页面日期标签中未找到该日期。")

    def _extract_available_slots(self, page, config: MonitorConfig, target_date: dt.date, venue: Venue) -> list[Slot]:
        target_hours = [f"{hour:02d}:00" for hour in range(config.start_hour, config.end_hour)]
        raw_slots = page.evaluate(
            """
            ({ targetHours }) => {
              const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 4 && rect.height > 4 && style.visibility !== 'hidden' && style.display !== 'none';
              };

              const norm = (text) => (text || '').replace(/\\s+/g, '').trim();
              const all = Array.from(document.querySelectorAll('body *')).filter(visible);
              const bodyText = norm(document.body.innerText);

              const timeNodes = all
                .map((el) => ({ el, text: norm(el.innerText), rect: el.getBoundingClientRect() }))
                .filter((item) => /^\\d{2}:00$/.test(item.text) && targetHours.includes(item.text))
                .sort((a, b) => a.rect.top - b.rect.top);

              const courtNodes = all
                .map((el) => ({ el, text: norm(el.innerText), rect: el.getBoundingClientRect() }))
                .filter((item) => /^场地\\d+$/.test(item.text))
                .sort((a, b) => a.rect.left - b.rect.left);

              if (timeNodes.length === 0 || courtNodes.length === 0) {
                return { error: '没有识别到时间行或场地列。请确认页面停留在网球场预约表格。' };
              }

              const gridLeft = Math.min(...courtNodes.map((item) => item.rect.left)) - 20;
              const gridRight = Math.max(...courtNodes.map((item) => item.rect.right)) + 20;
              const gridTop = Math.min(...timeNodes.map((item) => item.rect.top)) - 20;
              const gridBottom = Math.max(...timeNodes.map((item) => item.rect.bottom)) + 80;

              const isAvailable = (el) => {
                const chain = [];
                let current = el;
                for (let depth = 0; current && depth < 4; depth += 1) {
                  chain.push(current);
                  current = current.parentElement;
                }

                const combined = chain.map((node) => {
                  const text = norm(node.innerText);
                  const title = norm(node.getAttribute('title'));
                  const aria = norm(node.getAttribute('aria-label'));
                  const klass = norm(node.className && node.className.toString());
                  const dataState = norm(node.getAttribute('data-state') || node.getAttribute('data-status'));
                  return `${text}|${title}|${aria}|${klass}|${dataState}`;
                }).join('|');

                if (/不可选|已约|已满|禁用|disabled|disable|unavailable|booked|sold|reserved/i.test(combined)) {
                  return false;
                }
                if (/可选|available|selectable|free|empty|enabled|optional|appointable/i.test(combined)) {
                  return true;
                }

                const colorText = chain.map((node) => {
                  const style = window.getComputedStyle(node);
                  return `${style.backgroundColor} ${style.borderColor} ${style.backgroundImage}`;
                }).join(' ').toLowerCase();
                const blueish = /rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/g;
                let match;
                while ((match = blueish.exec(colorText)) !== null) {
                  const r = Number(match[1]);
                  const g = Number(match[2]);
                  const b = Number(match[3]);
                  const lightSelectableBlue =
                    b >= 185 &&
                    g >= 120 &&
                    r <= 210 &&
                    b - r >= 30 &&
                    b - g >= 8;
                  if (lightSelectableBlue) {
                    return true;
                  }
                }

                return false;
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
                .filter((item) => isAvailable(item.el));

              const results = [];
              const seen = new Set();

              for (const item of candidates) {
                const centerX = item.rect.left + item.rect.width / 2;
                const centerY = item.rect.top + item.rect.height / 2;
                const hour = timeNodes.reduce((best, node) => {
                  const y = node.rect.top + node.rect.height / 2;
                  const distance = Math.abs(centerY - y);
                  return !best || distance < best.distance ? { node, distance } : best;
                }, null);
                const court = courtNodes.reduce((best, node) => {
                  const x = node.rect.left + node.rect.width / 2;
                  const distance = Math.abs(centerX - x);
                  return !best || distance < best.distance ? { node, distance } : best;
                }, null);

                if (!hour || !court || hour.distance > 45 || court.distance > 60) {
                  continue;
                }

                const key = `${court.node.text}-${hour.node.text}`;
                if (!seen.has(key)) {
                  seen.add(key);
                  results.push({ court: court.node.text, hour: hour.node.text, label: key });
                }
              }

              return {
                slots: results.sort((a, b) => a.hour.localeCompare(b.hour) || a.court.localeCompare(b.court)),
                candidateCount: candidates.length,
              };
            }
            """,
            {"targetHours": target_hours},
        )

        if raw_slots.get("error"):
            raise RuntimeError(raw_slots["error"])

        candidate_count = raw_slots.get("candidateCount", 0)
        slot_items = raw_slots.get("slots", [])
        if candidate_count and not slot_items:
            self.events.put(("log", f"识别到 {candidate_count} 个蓝色候选，但没有匹配到目标时间行，请把页面滚动到目标时间段附近。"))

        return [
            Slot(
                venue_key=venue.key,
                venue=venue.name,
                date=target_date,
                court=item["court"],
                hour=item["hour"],
                label=item["label"],
            )
            for item in slot_items
        ]
