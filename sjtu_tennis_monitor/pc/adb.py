"""ADB controller for communicating with the Androws emulator."""

from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from sjtu_tennis_monitor.constants import ANDROID_PACKAGE
from sjtu_tennis_monitor.exceptions import PCAppNotReady
from sjtu_tennis_monitor.models import UiNode

# On Windows, prevent each subprocess (adb.exe) from flashing a console window.
_NO_WINDOW: int = 0x08000000 if sys.platform == "win32" else 0


def _subprocess_kwargs() -> dict:
    """Return extra kwargs for subprocess calls to hide console windows on Windows."""
    if sys.platform == "win32":
        return {"creationflags": _NO_WINDOW}
    return {}


class ADBController:
    def __init__(self, adb_path: Path, serial: str, events: queue.Queue, stop_event: threading.Event) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self.events = events
        self.stop_event = stop_event
        self.mode_label = "Androws ADB/UI树"
        self._screen_size: tuple[int, int] | None = None
        self._display_id: int | None = None
        self._screencap_display_id: str | None = None

    @classmethod
    def discover(cls, events: queue.Queue, stop_event: threading.Event) -> "ADBController | None":
        adb_path = cls._find_adb_path()
        if not adb_path:
            events.put(("log", "没有找到 Androws 自带 adb.exe。"))
            return None

        # 主动尝试连接 Androws 的多个可能端口（15118 是交我办专属容器，5555 是大后台）
        for port in ("15118", "5555"):
            try:
                subprocess.run(
                    [str(adb_path), "connect", f"127.0.0.1:{port}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    **_subprocess_kwargs(),
                )
            except Exception:
                pass

        try:
            output = subprocess.check_output(
                [str(adb_path), "devices"],
                text=True, encoding="utf-8", errors="ignore", timeout=8,
                **_subprocess_kwargs(),
            )
        except Exception as exc:
            events.put(("log", f"ADB 探测失败：{exc}"))
            return None

        offline = []
        online: list[str] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] != "List":
                if parts[1] == "device":
                    online.append(parts[0])
                if parts[1] == "offline":
                    offline.append(parts[0])

        if online:
            preferred_order = ("127.0.0.1:15118", "127.0.0.1:5555", "emulator-5554")
            serial = next((item for item in preferred_order if item in online), online[0])
            events.put(("log", f"ADB 已在线，使用设备：{serial}。"))
            return cls(adb_path, serial, events, stop_event)

        if offline:
            events.put(("log", "ADB 设备处于 offline，当前轮将使用窗口控制。"))
        else:
            events.put(("log", "ADB 未发现在线 Androws 设备，当前轮将使用窗口控制。"))
        return None

    @staticmethod
    def _find_adb_path() -> Path | None:
        base = Path("E:/Program Files/Tencent/Androws/Application")
        candidates = sorted(base.glob("*/adb.exe"), reverse=True) if base.exists() else []
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def is_ready(self) -> bool:
        try:
            output = self._adb(["get-state"], timeout=5).strip()
        except Exception:
            return False
        return output == "device"

    def prepare(self) -> None:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            self._refresh_display_context()
            if self._display_id is not None:
                self._focus_display_for_uiautomator()
                nodes = self.nodes()
                if nodes:
                    width, height = self.screen_size()
                    self.events.put(
                        (
                            "log",
                            f"ADB 已定位交我办显示层：display {self._display_id}，页面尺寸 {width}x{height}。",
                        )
                    )
                    return
            time.sleep(0.6)
        raise PCAppNotReady("ADB 已在线，但没有找到交我办所在的 Androws 显示层。请保持交我办窗口打开后重试。")

    def bring_to_front(self) -> None:
        return

    def back(self) -> None:
        self._adb([*self._input_prefix(), "keyevent", "BACK"], timeout=5)

    def tap_text(self, text: str, exact: bool = False) -> bool:
        nodes = self.nodes()
        for node in nodes:
            normalized = node.text.replace(" ", "")
            if (exact and normalized == text) or (not exact and text in normalized):
                x, y = node.center
                self.tap(x, y)
                return True
        return False

    def tap_ratio(self, x_ratio: float, y_ratio: float) -> None:
        width, height = self.screen_size()
        self.tap(int(width * x_ratio), int(height * y_ratio))

    def swipe_ratio(self, x1: float, y1: float, x2: float, y2: float) -> None:
        width, height = self.screen_size()
        self.swipe(int(width * x1), int(height * y1), int(width * x2), int(height * y2))

    def tap(self, x: int, y: int) -> None:
        self._adb([*self._input_prefix(), "tap", str(x), str(y)], timeout=5)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 450) -> None:
        self._adb(
            [*self._input_prefix(), "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
            timeout=8,
        )

    def screen_text(self) -> str:
        return " ".join(node.text for node in self.nodes())

    def nodes(self) -> list[UiNode]:
        xml = self._dump_ui_xml()
        if ANDROID_PACKAGE not in xml:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        nodes = []
        for item in root.iter("node"):
            text = (item.attrib.get("text") or item.attrib.get("content-desc") or "").strip()
            bounds_text = item.attrib.get("bounds", "")
            bounds = self._parse_bounds(bounds_text)
            if not bounds:
                continue
            nodes.append(
                UiNode(
                    text=text,
                    bounds=bounds,
                    clickable=item.attrib.get("clickable") == "true",
                    enabled=item.attrib.get("enabled") != "false",
                    selected=item.attrib.get("selected") == "true",
                )
            )
        self._update_screen_size_from_nodes(nodes)
        return nodes

    def screenshot(self) -> "Image.Image":
        from PIL import Image
        from io import BytesIO

        args = ["exec-out", "screencap", "-p"]
        screencap_display_id = self._find_screencap_display_id()
        if screencap_display_id:
            args.extend(["-d", screencap_display_id])
        raw = self._adb_bytes(args, timeout=10)
        return Image.open(BytesIO(raw)).convert("RGB")

    def screen_size(self) -> tuple[int, int]:
        if not self._screen_size:
            self._refresh_display_context()
        if not self._screen_size:
            self._screen_size = self._read_default_screen_size()
        return self._screen_size

    def _dump_ui_xml(self) -> str:
        self._adb(["shell", "uiautomator", "dump", "/sdcard/window.xml"], timeout=8)
        return self._adb(["exec-out", "cat", "/sdcard/window.xml"], timeout=8)

    def _input_prefix(self) -> list[str]:
        if self._display_id is None:
            self._refresh_display_context()
        if self._display_id is None:
            raise PCAppNotReady("ADB 在线，但没有找到交我办显示层，无法发送点击。")
        return ["shell", "input", "-d", str(self._display_id)]

    def _focus_display_for_uiautomator(self) -> None:
        try:
            self._adb([*self._input_prefix(), "tap", "2", "2"], timeout=5)
            time.sleep(0.15)
        except Exception:
            pass

    def _refresh_display_context(self) -> None:
        try:
            output = self._adb(["shell", "dumpsys", "window", "displays"], timeout=8)
        except Exception:
            return

        context = self._find_app_display_context(output)
        if not context:
            return
        display_id, size = context
        self._display_id = display_id
        if size:
            self._screen_size = size

    def _find_app_display_context(self, output: str) -> tuple[int, tuple[int, int] | None] | None:
        matches = list(re.finditer(r"Display:\s+mDisplayId=(\d+)", output))
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
            block = output[start:end]
            if ANDROID_PACKAGE not in block:
                continue

            display_id = int(match.group(1))
            size_match = re.search(r"\bcur=(\d+)x(\d+)", block)
            if not size_match:
                size_match = re.search(r"\binit=(\d+)x(\d+)", block)
            size = (int(size_match.group(1)), int(size_match.group(2))) if size_match else None
            return display_id, size
        return None

    def _update_screen_size_from_nodes(self, nodes: list[UiNode]) -> None:
        if not nodes:
            return
        max_right = max(node.bounds[2] for node in nodes)
        max_bottom = max(node.bounds[3] for node in nodes)
        if max_right > 0 and max_bottom > 0:
            self._screen_size = (max_right, max_bottom)

    def _find_screencap_display_id(self) -> str | None:
        if self._screencap_display_id:
            return self._screencap_display_id
        try:
            output = self._adb(["shell", "dumpsys", "SurfaceFlinger", "--display-id"], timeout=5)
        except Exception:
            return None

        displays: list[tuple[str, str]] = []
        for line in output.splitlines():
            match = re.search(r"Display\s+(\d+)\s+\(HWC display\s+(\d+)\)", line)
            if match:
                displays.append((match.group(1), match.group(2)))
        if not displays:
            return None

        if self._display_id and self._display_id != 0:
            chosen = next((display_id for display_id, hwc in displays if hwc != "0"), displays[-1][0])
        else:
            chosen = next((display_id for display_id, hwc in displays if hwc == "0"), displays[0][0])
        self._screencap_display_id = chosen
        return chosen

    def _read_default_screen_size(self) -> tuple[int, int]:
        try:
            output = self._adb(["shell", "wm", "size"], timeout=5)
            match = re.search(r"(\d+)x(\d+)", output)
            if match:
                return int(match.group(1)), int(match.group(2))
        except Exception:
            pass
        image = self.screenshot()
        return image.size

    def _adb(self, args: list[str], timeout: int) -> str:
        if self.stop_event.is_set():
            raise PCAppNotReady("监控已停止。")
        command = [str(self.adb_path), "-s", self.serial, *args]
        try:
            return subprocess.check_output(
                command, text=True, encoding="utf-8", errors="ignore",
                timeout=timeout, **_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise PCAppNotReady(f"ADB 命令超时 ({timeout}s)：{' '.join(args)}") from exc
        except subprocess.CalledProcessError as exc:
            raise PCAppNotReady("ADB 连接或命令失败，请确认 Androws 虚拟机及交我办已正常开启。") from exc

    def _adb_bytes(self, args: list[str], timeout: int) -> bytes:
        if self.stop_event.is_set():
            raise PCAppNotReady("监控已停止。")
        command = [str(self.adb_path), "-s", self.serial, *args]
        try:
            return subprocess.check_output(
                command, timeout=timeout, **_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise PCAppNotReady(f"ADB 命令超时 ({timeout}s)：{' '.join(args)}") from exc
        except subprocess.CalledProcessError as exc:
            raise PCAppNotReady("ADB 连接或命令失败，请确认 Androws 虚拟机及交我办已正常开启。") from exc

    def _parse_bounds(self, text: str) -> tuple[int, int, int, int] | None:
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text)
        if not match:
            return None
        return tuple(int(part) for part in match.groups())  # type: ignore[return-value]
