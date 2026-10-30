"""Browser session: persistent profile, login wait, block detection, cooldown.

Same pattern as the Discord browse mode: a visible Chromium on a profile you
logged into by hand. Nothing here types a password or answers a challenge;
if Facebook asks for anything, the run stops and you deal with it yourself.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

HOME = "https://www.facebook.com/"


class Blocked(RuntimeError):
    """Facebook pushed back. The run stops; `cooldown` says whether to rest."""

    def __init__(self, reason: str, cooldown: bool = True):
        super().__init__(reason)
        self.reason, self.cooldown = reason, cooldown


def cooldown_until(cfg: dict) -> dt.datetime | None:
    path: Path = cfg["cooldown_file"]
    if not path.exists():
        return None
    until = dt.datetime.fromisoformat(path.read_text().splitlines()[0].strip())
    return until if until > dt.datetime.now() else None


def set_cooldown(cfg: dict, reason: str) -> dt.datetime:
    until = dt.datetime.now() + dt.timedelta(hours=cfg["pacing"]["cooldown_hours"])
    path: Path = cfg["cooldown_file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{until.isoformat(timespec='seconds')}\n{reason}\n")
    return until


def clear_cooldown(cfg: dict) -> None:
    cfg["cooldown_file"].unlink(missing_ok=True)


def launch(pw, cfg: dict):
    cfg["profile_dir"].mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        str(cfg["profile_dir"]),
        headless=False,      # a visible window, as a person would have
        no_viewport=True,    # the real window size, not a fixed 1280x720
        args=["--start-maximized"],
    )
    page = context.pages[0] if context.pages else context.new_page()  # one tab, always
    return context, page


def check(page, cfg: dict) -> None:
    """Raise Blocked if the page shows any block signal."""
    signals = cfg["block_signals"]
    for fragment in signals["url"]:
        if fragment in page.url:
            # Being logged out is not a penalty: stop, but no cooldown.
            raise Blocked(f"url:{fragment}", cooldown=fragment != "/login")
    # Only dialogs are searched: a post in the feed may quote "going too fast",
    # while Facebook's own block notices arrive as dialogs.
    try:
        texts = page.locator('[role="dialog"], [role="alertdialog"]').all_inner_texts()
    except Exception:
        return
    blob = " ".join(texts).lower()
    for phrase in signals["text"]:
        if phrase.lower() in blob:
            raise Blocked(f"text:{phrase}")


def check_codes(codes, cfg: dict) -> None:
    for code in codes:
        if code in cfg["block_signals"]["graphql_codes"]:
            raise Blocked(f"graphql:{code}")


def logged_in(page) -> bool:
    return "/login" not in page.url and page.locator('input[name="email"]').count() == 0


def ensure_logged_in(page, cfg: dict) -> None:
    page.goto(HOME, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if not logged_in(page):
        input("Not logged in. Log in by hand in the browser window, finish any check "
              "Facebook shows you, then press Enter here... ")
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if not logged_in(page):
            raise Blocked("not_logged_in", cooldown=False)
    check(page, cfg)


def screenshot(page, cfg: dict, label: str) -> Path | None:
    cfg["debug_dir"].mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]+", "_", label)[:60]
    path = cfg["debug_dir"] / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{safe}.png"
    try:
        page.screenshot(path=str(path))
        return path
    except Exception:
        return None
