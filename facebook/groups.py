"""What kind of group is this? Membership probe and search-card parsing.

The probe matches English labels, so the browsing profile must have its
Facebook language set to English (US).
"""
from __future__ import annotations

import re

READABLE = {"member", "public_readable"}

# Path segments under /groups/ that are Facebook pages, not groups.
NOT_GROUPS = {"feed", "discover", "joins", "create", "notifications", "search", "you"}

CARD_RE = re.compile(
    r"\b(Public|Private)\b\s*·\s*([\d.,]+\s*[KkMm]?)\s+members?(?:\s*·\s*([^\n·]+))?")


def group_id_from_url(url: str) -> str | None:
    match = re.search(r"/groups/([^/?#]+)", url or "")
    if not match or match.group(1).lower() in NOT_GROUPS:
        return None
    return match.group(1)


def feed_url(group_id: str, sorting: str) -> str:
    return f"https://www.facebook.com/groups/{group_id}/?sorting_setting={sorting}"


def name_from_title(title: str) -> str | None:
    """'(3) Shopify Store Owners | Facebook' -> 'Shopify Store Owners'."""
    name = re.sub(r"^\(\d+\+?\)\s*", "", title or "").split(" | ")[0].strip()
    return name if name and name.lower() != "facebook" else None


def parse_count(text: str) -> int | None:
    text = text.replace(",", "").strip().upper()
    scale = 1
    if text.endswith("K"):
        scale, text = 1_000, text[:-1]
    elif text.endswith("M"):
        scale, text = 1_000_000, text[:-1]
    try:
        return int(float(text.strip()) * scale)
    except ValueError:
        return None


def parse_card(text: str) -> dict:
    """'Public · 12K members · 10+ posts a day' -> privacy, members, activity."""
    match = CARD_RE.search(text or "")
    if not match:
        return {"privacy": None, "member_count": None, "activity": None}
    return {
        "privacy": match.group(1).lower(),
        "member_count": parse_count(match.group(2)),
        "activity": (match.group(3) or "").strip() or None,
    }


def junk_hit(text: str, junk_keywords) -> str | None:
    low = (text or "").lower()
    return next((word for word in junk_keywords if word in low), None)


def probe(page) -> str:
    """member | public_readable | join_required | pending | unknown."""
    try:
        page.wait_for_selector('div[role="feed"] div[role="article"]', timeout=10_000)
        has_feed = True
    except Exception:
        has_feed = False
    join = page.get_by_role("button", name=re.compile(r"^join group$", re.I)).count() > 0
    pending = page.get_by_role("button", name=re.compile(r"cancel request", re.I)).count() > 0
    private = page.get_by_text(re.compile(r"^private group$", re.I)).count() > 0
    if pending:
        return "pending"
    if has_feed:
        return "public_readable" if join else "member"
    if private or join:
        return "join_required"
    return "unknown"
