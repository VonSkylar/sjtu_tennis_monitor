"""Data models for the SJTU Tennis Court Booking Monitor."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    key: str
    name: str
    url: str


VENUES = (
    Venue(
        key="east",
        name="东区网球场",
        url="https://sports.sjtu.edu.cn/pc/#/apointmentDetails/1/3466293b-a7d8-45be-a918-8526e3bed4c5/%25E5%2585%25A8%25E9%2583%25A8/0",
    ),
    Venue(
        key="huxiaoming",
        name="胡晓明网球场",
        url="https://sports.sjtu.edu.cn/pc/?locale=zh#/apointmentDetails/1/0c6edc93-87ac-41b0-9895-6b66fda93fe5/%25E5%2585%25A8%25E9%2583%25A8/0",
    ),
)
VENUES_BY_KEY = {venue.key: venue for venue in VENUES}


@dataclass(frozen=True)
class MonitorConfig:
    venues: tuple[Venue, ...]
    dates: tuple[dt.date, ...]
    start_hour: int
    end_hour: int
    check_interval_seconds: int
    auto_order_enabled: bool


@dataclass(frozen=True)
class RushConfig:
    venue: Venue
    target_date: dt.date
    start_hour: int
    end_hour: int
    preferred_court: int


@dataclass(frozen=True)
class Slot:
    venue_key: str
    venue: str
    date: dt.date
    court: str
    hour: str
    label: str


@dataclass(frozen=True)
class UiNode:
    """A single node from an Android UI Automator XML dump."""
    text: str
    bounds: tuple[int, int, int, int]
    clickable: bool
    enabled: bool
    selected: bool

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)
