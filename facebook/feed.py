"""Pure decisions about a group's feed: when to stop, which posts to keep.

Kept free of browser and database imports so it is unit-tested directly.
"""
from __future__ import annotations

import time

from .graphql import Post

PINNED_SLOTS = 3  # Facebook shows at most a few featured posts above the feed


def _floor(group: dict, cfg: dict, now: float | None = None) -> float:
    oldest_allowed = (now or time.time()) - cfg["read"]["max_post_age_days"] * 86400
    return max(group.get("last_seen_time") or 0, oldest_allowed)


def reached_known(posts: dict[str, Post], group: dict, cfg: dict,
                  now: float | None = None) -> bool:
    """True once the feed is back among posts read on an earlier visit.

    Pinned and featured posts sit at the top of a chronological feed whatever
    their age, so the first few posts loaded are ignored here; an old pinned
    post must not end the scroll before it starts. Below them, two old posts
    in a row mean the rest of the feed is old too.
    """
    below_pins = list(posts.values())[PINNED_SLOTS:]
    if any(p.post_id == group.get("last_seen_post_id") for p in below_pins):
        return True
    floor = _floor(group, cfg, now)
    dated = [p for p in below_pins if p.created_at][-2:]
    return len(dated) == 2 and all(p.created_at <= floor for p in dated)


def select_new(posts: list[Post], group: dict, cfg: dict, now: float | None = None) -> list[Post]:
    floor = _floor(group, cfg, now)
    keep = [p for p in posts
            if len(p.text.strip()) >= cfg["read"]["min_text_chars"]
            and (p.created_at is None or p.created_at > floor)]
    keep.sort(key=lambda p: p.created_at or 0, reverse=True)
    return keep[: cfg["pacing"]["posts_per_group"]]
