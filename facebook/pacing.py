"""Human pacing: active hours, the run guard, uneven scrolling, reading pauses.

All waits go through page.wait_for_timeout rather than time.sleep, because
Playwright's sync API only delivers network events while the script is
inside one of its calls. A time.sleep would starve the GraphQL listener.
"""
from __future__ import annotations

import datetime as dt
import random


def seconds(lo_hi) -> float:
    return random.uniform(*lo_hi)


def in_active_hours(cfg: dict, now: dt.datetime | None = None) -> bool:
    start, end = cfg["pacing"]["active_hours"]
    return start <= (now or dt.datetime.now()).hour < end


def guard(cfg: dict, usage: dict, kind: str, cooldown: dt.datetime | None,
          now: dt.datetime | None = None) -> str | None:
    """Why a run may not start now, or None if it may."""
    now = now or dt.datetime.now()
    p = cfg["pacing"]
    if cooldown:
        return f"cooling down until {cooldown:%a %d %b %H:%M} after a block signal"
    if not in_active_hours(cfg, now):
        return f"outside active hours ({p['active_hours'][0]}:00 to {p['active_hours'][1]}:00)"
    if usage["page_loads"] >= p["page_loads_per_day"]:
        return "today's page-load budget is used"
    if kind == "browse":
        if usage["browse_runs"] >= p["runs_per_day"]:
            return "today's run budget is used"
        last = usage["last_browse_start"]
        if last and now.timestamp() - last < p["min_hours_between_runs"] * 3600:
            return f"less than {p['min_hours_between_runs']} h since the last run"
    if kind == "discover" and usage["searches"] >= cfg["discovery"]["searches_per_day"]:
        return "today's search budget is used"
    return None


class Scroller:
    """Scrolls one feed the way a reader does: uneven steps, pauses to read."""

    def __init__(self, page, pacing: dict):
        self.page, self.p = page, pacing
        self.since_long = 0
        self.next_long = random.randint(*pacing["long_pause_every"])
        self.parked = False

    def _park_pointer(self) -> None:
        # Wheel events go to whatever is under the pointer: put it over the feed.
        width, height = self.page.evaluate("() => [innerWidth, innerHeight]")
        self.page.mouse.move(width * random.uniform(0.4, 0.6), height * random.uniform(0.4, 0.7),
                             steps=random.randint(8, 20))
        self.parked = True

    def wait(self, lo_hi) -> None:
        self.page.wait_for_timeout(seconds(lo_hi) * 1000)

    def scroll(self) -> None:
        if not self.parked:
            self._park_pointer()
        total = random.randint(*self.p["scroll_px"])
        ticks = random.randint(*self.p["scroll_ticks"])
        for _ in range(ticks):  # several small ticks, like a wheel, not one jump
            self.page.mouse.wheel(0, total / ticks)
            self.page.wait_for_timeout(random.uniform(40, 180))
        self.wait(self.p["scroll_pause_s"])
        self.since_long += 1
        if self.since_long >= self.next_long:
            self.wait(self.p["long_pause_s"])
            self.since_long = 0
            self.next_long = random.randint(*self.p["long_pause_every"])
