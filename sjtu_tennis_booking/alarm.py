"""Alarm sound generation and playback."""

from __future__ import annotations

import math
import tempfile
import threading
import wave
from pathlib import Path

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None  # type: ignore[assignment]


class Alarm:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._playing_async_sound = False
        self._sound_file = Path(tempfile.gettempdir()) / "sjtu_venue_alarm.wav"

    def start(self) -> None:
        if self._playing_async_sound:
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        if winsound:
            try:
                sound_file = self._ensure_alarm_sound()
                winsound.PlaySound(
                    str(sound_file),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
                self._playing_async_sound = True
                return
            except RuntimeError:
                pass
            except OSError:
                pass

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._playing_async_sound = False
        if winsound:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except RuntimeError:
                try:
                    winsound.PlaySound(None, 0)
                except RuntimeError:
                    pass

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if winsound:
                winsound.Beep(1400, 450)
                if self._stop_event.wait(0.15):
                    break
                winsound.Beep(950, 450)
                self._stop_event.wait(0.45)
            else:
                print("\a", end="", flush=True)
                self._stop_event.wait(1)

    def _ensure_alarm_sound(self) -> Path:
        if self._sound_file.exists() and self._sound_file.stat().st_size > 0:
            return self._sound_file

        sample_rate = 44100
        amplitude = 18000
        pattern = (
            (1400, 0.45),
            (0, 0.15),
            (950, 0.45),
            (0, 0.45),
        )

        frames = bytearray()
        with wave.open(str(self._sound_file), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(sample_rate)
            for frequency, duration in pattern:
                sample_count = int(sample_rate * duration)
                for index in range(sample_count):
                    if frequency:
                        value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
                    else:
                        value = 0
                    frames.extend(value.to_bytes(2, byteorder="little", signed=True))
            sound.writeframes(bytes(frames))

        return self._sound_file
