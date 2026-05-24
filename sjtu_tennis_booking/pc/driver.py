"""PCBookingDriver — venue navigation and slot extraction via ADB."""

from __future__ import annotations

import datetime as dt
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Iterable

from PIL import Image

from sjtu_tennis_booking.constants import (
    ANDROID_PACKAGE,
    DATE_CARD_RATIOS,
    DATE_CARD_STATUS_BOX,
    DATE_CARD_Y_RATIO,
    PC_SHORTCUT_NAME,
)
from sjtu_tennis_booking.exceptions import PCAppNotReady, PCRateLimited
from sjtu_tennis_booking.models import MonitorConfig, Slot, UiNode, Venue
from sjtu_tennis_booking.pc.adb import ADBController


class PCBookingDriver:
    def __init__(self, events: queue.Queue, stop_event: threading.Event) -> None:
        self.events = events
        self.stop_event = stop_event
        self.controller: ADBController | None = None

    @property
    def mode_label(self) -> str:
        return self.controller.mode_label if self.controller else "未初始化"

    def start(self) -> None:
        shortcut = self._find_pc_shortcut()
        self.events.put(("log", f"正在打开桌面交我办：{shortcut}"))
        os.startfile(shortcut)  # type: ignore[attr-defined]

        # 循环等待 Androws 虚拟机及 ADB 调试服务启动，最长等待 25 秒
        self.events.put(("log", "正在等待 Androws 虚拟机及 ADB 调试服务就绪（最长等待 25 秒）..."))
        max_wait = 25
        start_time = time.monotonic()
        adb = None

        while time.monotonic() - start_time < max_wait:
            if self.stop_event.is_set():
                raise PCAppNotReady("监控已停止。")
            adb = ADBController.discover(self.events, self.stop_event)
            if adb and adb.is_ready():
                break
            time.sleep(2.0)

        if adb and adb.is_ready():
            self.controller = adb
            self.controller.prepare()
            self.events.put(("log", "已连接 Androws Android 自动化接口，开始按页面文字节点进行后台操作。"))
            return

        raise PCAppNotReady(
            "Androws Android 自动化接口当前是 offline，请确认 Androws 虚拟机及开发者模式已正常开启。"
        )

    def stop(self) -> None:
        return

    def check_venue_date(self, venue: Venue, target_date: dt.date, config: MonitorConfig) -> list[Slot]:
        if self.stop_event.is_set():
            raise PCAppNotReady("监控已停止。")
        controller = self._controller()
        controller.bring_to_front()
        self._open_venue_detail_by_adb(venue)
        if self.stop_event.is_set():
            raise PCAppNotReady("监控已停止。")
        self._select_target_date_by_adb(target_date)
        if self.stop_event.is_set():
            raise PCAppNotReady("监控已停止。")
        time.sleep(1.0)
        self._raise_if_rate_limited()
        slots = self._extract_available_slots(venue, target_date, config)
        if slots:
            self.events.put(("log", f"PC版 {venue.name} {target_date.isoformat()} 发现 {len(slots)} 个候选空场。"))
        return slots

    def _open_venue_detail_by_adb(self, venue: Venue) -> None:
        controller = self._adb()
        for _ in range(6):
            text = controller.screen_text()
            if self._looks_like_venue_detail_text(text, venue):
                return
            if self._looks_like_other_venue_detail_text(text, venue):
                self.events.put(("log", "当前在其它场馆详情页，正在返回场馆列表。"))
                controller.back()
                time.sleep(1.2)
                continue
            if self._looks_like_booking_list_text(text):
                self._tap_venue_in_list(venue)
                return
            if "场馆预约" in text and ("去跑步" in text or "体育赛事" in text):
                if controller.tap_text("场馆预约"):
                    time.sleep(1.5)
                    continue
            if "智慧体育" in text:
                if controller.tap_text("智慧体育"):
                    time.sleep(1.5)
                    continue
            controller.back()
            time.sleep(0.8)

        self._open_booking_list_from_known_path()
        self._tap_venue_in_list(venue)

    def _looks_like_venue_detail_text(self, text: str, venue: Venue) -> bool:
        return venue.name in text and "时间" in text and ("地图" in text or "场馆设施" in text)

    def _looks_like_other_venue_detail_text(self, text: str, venue: Venue) -> bool:
        return (
            venue.name not in text
            and "网球场" in text
            and "时间" in text
            and ("地图" in text or "场馆设施" in text)
        )

    def _looks_like_booking_list_text(self, text: str) -> bool:
        return (
            "场馆预约" in text
            and ("精品推荐" in text or "预订" in text)
            and ("东区网球场" in text or "胡晓明网球场" in text)
            and "场馆设施" not in text
        )

    def _open_booking_list_from_known_path(self) -> None:
        controller = self._controller()
        if not controller.tap_text("智慧体育"):
            controller.tap_ratio(0.370, 0.310)
        time.sleep(1.5)
        if not controller.tap_text("场馆预约"):
            controller.tap_ratio(0.220, 0.225)
        time.sleep(1.5)

    def _tap_venue_in_list(self, venue: Venue) -> None:
        controller = self._controller()
        for _ in range(3):
            if controller.tap_text(venue.name):
                time.sleep(2.0)
                return
            controller.swipe_ratio(0.50, 0.78, 0.50, 0.36)
            time.sleep(1.0)
        raise PCAppNotReady(f"没有在场馆列表中找到 {venue.name}")

    def _select_target_date_by_adb(self, target_date: dt.date) -> None:
        days_from_today = (target_date - dt.date.today()).days
        if days_from_today < 0:
            raise RuntimeError("PC版无法监控过去日期。")
        if days_from_today > 13:
            raise RuntimeError("PC版一次最多按页面开放情况监控未来 14 天。")

        controller = self._adb()
        label = target_date.strftime("%m-%d")
        for _ in range(4):
            if controller.tap_text(label):
                self.events.put(("log", f"PC版已切换到目标日期：{label}"))
                return
            controller.swipe_ratio(0.82, 0.70, 0.18, 0.70)
            time.sleep(0.8)

        self._select_target_date_by_ratios(days_from_today)
        self.events.put(("log", f"PC版未从文字节点找到 {label}，已改用日期卡片位置点击。"))

    def _select_target_date_by_ratios(self, days_from_today: int) -> None:
        controller = self._controller()
        group = days_from_today // 4
        index = days_from_today % 4
        for _ in range(group):
            controller.swipe_ratio(0.82, DATE_CARD_Y_RATIO, 0.18, DATE_CARD_Y_RATIO)
            time.sleep(0.7)
        controller.tap_ratio(DATE_CARD_RATIOS[index], DATE_CARD_Y_RATIO)
        self.events.put(("log", f"PC版已点击第 {days_from_today + 1} 天日期卡片。"))

    def _raise_if_rate_limited(self) -> None:
        text = self._controller().screen_text()
        if "每日请求超过限制" in text or "请求超过限制" in text:
            raise PCRateLimited("学校系统提示\u201c每日请求超过限制，无法获取\u201d。")

    def _extract_available_slots(self, venue: Venue, target_date: dt.date, config: MonitorConfig) -> list[Slot]:
        slots = self._extract_slots_from_ui_tree(venue, target_date, config)
        if slots is not None:
            return slots
        raise PCAppNotReady(
            "ADB 已连接，但当前页面没有识别到场馆详情、日期状态或时段表，已跳过本次判断以避免误报。"
        )

    def _extract_slots_from_ui_tree(self, venue: Venue, target_date: dt.date, config: MonitorConfig) -> list[Slot] | None:
        controller = self._adb()
        nodes = controller.nodes()
        text = " ".join(node.text for node in nodes)

        target_hours = {f"{hour:02d}:00" for hour in range(config.start_hour, config.end_hour)}
        time_nodes = [node for node in nodes if node.text in target_hours]
        court_nodes = [node for node in nodes if re.fullmatch(r"场地\d+", node.text)]
        available_nodes = [
            node
            for node in nodes
            if self._node_looks_available(node) and self._node_in_booking_area(node)
        ]

        slots: list[Slot] = []
        seen: set[tuple[str, str]] = set()
        for node in available_nodes:
            hour = self._nearest_node(node, time_nodes, axis="y", max_distance=70)
            court = self._nearest_node(node, court_nodes, axis="x", max_distance=90)
            if hour and court:
                key = (court.text, hour.text)
                if key not in seen:
                    seen.add(key)
                    slots.append(
                        Slot(
                            venue_key=venue.key,
                            venue=venue.name,
                            date=target_date,
                            court=court.text,
                            hour=hour.text,
                            label=f"{court.text}-{hour.text}",
                        )
                    )

        if slots or time_nodes or court_nodes:
            return sorted(slots, key=lambda item: (item.hour, item.court))

        if self._target_date_marked_full(nodes, target_date):
            self.events.put(("log", f"PC版 {venue.name} {target_date.isoformat()} 页面显示已订满。"))
            return []

        if self._has_any_available_words(text):
            return [self._summary_slot(venue, target_date, config, "页面文字显示可预约")]
        return None

    def _target_date_marked_full(self, nodes: list[UiNode], target_date: dt.date) -> bool:
        label = target_date.strftime("%m-%d")
        for node in nodes:
            if label in node.text and "已订满" in node.text:
                return True

        date_nodes = [node for node in nodes if node.text == label]
        full_nodes = [node for node in nodes if "已订满" in node.text]
        for date_node in date_nodes:
            date_x, date_y = date_node.center
            for full_node in full_nodes:
                full_x, full_y = full_node.center
                if abs(full_x - date_x) <= 95 and 0 <= full_y - date_y <= 90:
                    return True
        return False

    def _extract_slots_from_screenshot(self, venue: Venue, target_date: dt.date, config: MonitorConfig) -> list[Slot]:
        image = self._controller().screenshot()
        if not self._looks_like_venue_detail_screenshot(image):
            raise PCAppNotReady("截图未确认处于场馆详情页，已跳过空场判断以避免误报。")
        if self._selected_date_card_is_full(image, target_date):
            self.events.put(("log", f"PC版 {venue.name} {target_date.isoformat()} 日期卡片显示已订满。"))
            return []

        components = self._find_blue_available_components(image)
        if components:
            return [self._summary_slot(venue, target_date, config, f"识别到 {len(components)} 个蓝色可预约候选")]

        return [self._summary_slot(venue, target_date, config, "日期卡片未显示已订满")]

    def _looks_like_venue_detail_screenshot(self, image: Image.Image) -> bool:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()

        banner_top = int(height * 0.12)
        banner_bottom = int(height * 0.43)
        colorful = 0
        sampled = 0
        for y in range(banner_top, banner_bottom, 6):
            for x in range(int(width * 0.05), int(width * 0.90), 6):
                r, g, b = pixels[x, y]
                if max(r, g, b) - min(r, g, b) > 35 and not (b > 170 and g > 90 and r < 80):
                    colorful += 1
                sampled += 1

        card_y = int(height * DATE_CARD_Y_RATIO)
        white_cards = 0
        for ratio in DATE_CARD_RATIOS:
            cx = int(width * ratio)
            card_pixels = 0
            white = 0
            for y in range(card_y - 30, card_y + 35, 4):
                for x in range(cx - 45, cx + 45, 4):
                    if 0 <= x < width and 0 <= y < height:
                        r, g, b = pixels[x, y]
                        if r > 235 and g > 235 and b > 235:
                            white += 1
                        card_pixels += 1
            if card_pixels and white / card_pixels > 0.42:
                white_cards += 1

        return sampled > 0 and colorful / sampled > 0.08 and white_cards >= 3

    def _selected_date_card_is_full(self, image: Image.Image, target_date: dt.date) -> bool:
        days_from_today = max(0, min(13, (target_date - dt.date.today()).days))
        index = days_from_today % 4
        box = self._date_status_box(image, index)
        crop = image.crop(box).convert("RGB")
        red_pixels = 0
        total = max(1, crop.width * crop.height)
        for r, g, b in crop.getdata():
            if r >= 210 and g <= 125 and b <= 125:
                red_pixels += 1
        return red_pixels / total > 0.006

    def _date_status_box(self, image: Image.Image, index: int) -> tuple[int, int, int, int]:
        card_width = int(image.width * 0.20)
        center_x = int(image.width * DATE_CARD_RATIOS[index])
        top = int(image.height * DATE_CARD_STATUS_BOX[1])
        bottom = int(image.height * DATE_CARD_STATUS_BOX[3])
        left = max(0, center_x - card_width // 2)
        right = min(image.width, center_x + card_width // 2)
        return (left, top, right, bottom)

    def _find_blue_available_components(self, image: Image.Image) -> list[tuple[int, int, int, int]]:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()
        mask: set[tuple[int, int]] = set()
        top_limit = int(height * 0.16)
        bottom_limit = int(height * 0.92)
        right_limit = int(width * 0.92)

        step = 2
        for y in range(top_limit, bottom_limit, step):
            for x in range(0, right_limit, step):
                r, g, b = pixels[x, y]
                blue_selectable = b >= 175 and g >= 105 and r <= 165 and (b - r) >= 45 and (b - g) >= 10
                header_or_icon = y < int(height * 0.28) and b >= 170 and g >= 80
                if blue_selectable and not header_or_icon:
                    mask.add((x // step, y // step))

        components: list[tuple[int, int, int, int]] = []
        while mask:
            start = mask.pop()
            stack = [start]
            min_x = max_x = start[0]
            min_y = max_y = start[1]
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (nx, ny) in mask:
                        mask.remove((nx, ny))
                        stack.append((nx, ny))

            left, top, right, bottom = min_x * step, min_y * step, (max_x + 1) * step, (max_y + 1) * step
            comp_width = right - left
            comp_height = bottom - top
            if count >= 35 and 22 <= comp_width <= 150 and 18 <= comp_height <= 120:
                components.append((left, top, right, bottom))

        return components

    def _summary_slot(self, venue: Venue, target_date: dt.date, config: MonitorConfig, reason: str) -> Slot:
        return Slot(
            venue_key=venue.key,
            venue=venue.name,
            date=target_date,
            court=reason,
            hour=f"{config.start_hour:02d}:00-{config.end_hour:02d}:00",
            label=reason,
        )

    def _has_any_available_words(self, text: str) -> bool:
        return bool(re.search(r"可约|可预约|空闲|可选|available|free|empty", text, flags=re.I))

    def _node_looks_available(self, node: UiNode) -> bool:
        combined = node.text
        if re.search(r"不可选|已约|已满|已订满|禁用|disabled|disable|unavailable|booked|sold|reserved", combined, flags=re.I):
            return False
        if re.search(r"可约|可预约|可选|空闲|available|selectable|free|empty|enabled|optional", combined, flags=re.I):
            return True
        return node.clickable and node.enabled and not node.selected and not node.text

    def _node_in_booking_area(self, node: UiNode) -> bool:
        left, top, right, bottom = node.bounds
        width = right - left
        height = bottom - top
        return top > 200 and width >= 20 and height >= 18

    def _nearest_node(self, target: UiNode, nodes: Iterable[UiNode], axis: str, max_distance: int) -> UiNode | None:
        tx, ty = target.center
        best: tuple[int, UiNode] | None = None
        for node in nodes:
            nx, ny = node.center
            distance = abs((ty - ny) if axis == "y" else (tx - nx))
            if distance <= max_distance and (best is None or distance < best[0]):
                best = (distance, node)
        return best[1] if best else None

    def _controller(self) -> ADBController:
        if not self.controller:
            raise RuntimeError("PC版控制器尚未初始化。")
        return self.controller

    def _adb(self) -> ADBController:
        return self._controller()

    def _find_pc_shortcut(self) -> str:
        candidates = [
            Path(os.environ.get("USERPROFILE", "")) / "Desktop" / PC_SHORTCUT_NAME,
            Path("C:/Users/Public/Desktop") / PC_SHORTCUT_NAME,
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        raise FileNotFoundError(f"没有找到桌面 {PC_SHORTCUT_NAME}，请确认交我办 PC 版快捷方式仍在桌面。")
