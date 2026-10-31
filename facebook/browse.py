"""Browse approved groups, newest posts first, and turn posts into leads.

Per group: open the chronological feed, probe membership, collect posts
(from GraphQL, DOM as fallback) until reaching one already read, then gate
and score. Scoring runs in the pause between groups, so the 45-second model
calls cost no extra time on the page.
"""
from __future__ import annotations

import random
import sys
import threading
import time

from playwright.sync_api import sync_playwright

from . import adapters, dom, groups, pacing, session, store
from .feed import reached_known, select_new
from .graphql import FeedCapture, Post


class EnterWatcher:
    """Lets the Playwright loop poll for Enter without blocking.

    select() on stdin only works on Unix and input() would stall the event
    pump that delivers GraphQL responses, so a daemon thread reads stdin and
    raises a flag. Start it only after the last input() prompt of the run.
    """

    def __init__(self):
        self._pressed = threading.Event()
        self._thread: threading.Thread | None = None

    def _read(self) -> None:
        while sys.stdin.readline():
            self._pressed.set()

    def arm(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._read, daemon=True)
            self._thread.start()
        self._pressed.clear()

    def pressed(self) -> bool:
        return self._pressed.is_set()


_enter = EnterWatcher()


def run(cfg: dict, mode: str, only_group: str | None = None, force: bool = False) -> str:
    conn = adapters.connect(cfg)
    store.migrate(conn)
    reason = pacing.guard(cfg, store.usage_today(conn), "browse", session.cooldown_until(cfg))
    if reason and not (force and "cooling" not in reason):  # --force never skips a cooldown
        return f"Not browsing: {reason}."

    targets = ([store.get_group(conn, only_group)] if only_group
               else store.groups_to_browse(conn, cfg["pacing"]["groups_per_run"]))
    targets = [g for g in targets if g]
    if not targets:
        return "No approved groups to browse. Add some with: python -m facebook groups add <url>"

    # Posts are scored during the run and the watermark moves on, so a dead
    # model would mean posts read now are never scored. Check before opening.
    problem = adapters.model_ready(cfg)
    if problem:
        return f"Not browsing: {problem}."

    run_id = store.start_run(conn, "browse", mode)
    backlog: list[tuple[dict, Post]] = []
    stop = "done"
    with sync_playwright() as pw:
        context, page = session.launch(pw, cfg)
        capture = FeedCapture(cfg["read"]["feed_queries"])
        page.on("response", capture.on_response)
        try:
            session.ensure_logged_in(page, cfg)
            for i, group in enumerate(targets):
                if store.usage_today(conn)["page_loads"] >= cfg["pacing"]["page_loads_per_day"]:
                    stop = "budget"
                    break
                backlog += read_group(page, capture, conn, run_id, group, cfg, mode)
                if i < len(targets) - 1:
                    backlog = pause_and_score(page, conn, backlog, cfg)
        except session.Blocked as blocked:
            stop = f"blocked:{blocked.reason}"
            shot = session.screenshot(page, cfg, stop)
            print(f"Stopped: Facebook signal '{blocked.reason}'. Screenshot: {shot}")
            if blocked.cooldown:
                until = session.set_cooldown(cfg, blocked.reason)
                print(f"No runs until {until:%a %d %b %H:%M}. Check the account by hand.")
        except KeyboardInterrupt:
            stop = "user"
        finally:
            try:
                context.close()
            except Exception:
                pass  # Ctrl+C may already have taken the browser down

    if backlog:
        print(f"Browser closed. Scoring {len(backlog)} post(s) left over...")
    for group, post in backlog:
        score_safely(conn, group, post, cfg)
    store.end_run(conn, run_id, stop)
    return f"Browse run ended: {stop}. See leads with: python -m facebook leads --new"


def read_group(page, capture: FeedCapture, conn, run_id: int, group: dict,
               cfg: dict, mode: str) -> list[tuple[dict, Post]]:
    gid = group["group_id"]
    capture.reset(gid)
    page.goto(groups.feed_url(gid, cfg["sorting"]), wait_until="domcontentloaded")
    store.bump(conn, run_id, "page_loads")
    session.check(page, cfg)

    state = groups.probe(page)
    title = groups.name_from_title(page.title())
    store.update_group(conn, gid, membership=state,
                       **({"name": title} if title and group["name"] == gid else {}))
    if state not in groups.READABLE:
        store.mark_browsed(conn, gid, None)  # counts as a visit, so the rotation moves on
        print(f"[{group['name']}] {state}: skipped")
        return []

    capture.ingest_html(page)
    if mode == "assist":
        read_assisted(page, capture, group, cfg)
    else:
        read_auto(page, capture, group, cfg)

    fresh = select_new(list(capture.posts.values()), group, cfg)
    store.bump(conn, run_id, "posts_seen", len(fresh))
    newest = max((p for p in capture.posts.values() if p.created_at),
                 key=lambda p: p.created_at, default=None)
    store.mark_browsed(conn, gid, newest)
    print(f"[{group['name']}] {len(capture.posts)} posts read, {len(fresh)} new")
    return [(group, post) for post in fresh]


def read_auto(page, capture: FeedCapture, group: dict, cfg: dict) -> None:
    p, r = cfg["pacing"], cfg["read"]
    scroller = pacing.Scroller(page, p)
    for n in range(p["scrolls_per_group"]):
        capture.drain()
        session.check_codes(capture.codes, cfg)
        if reached_known(capture.posts, group, cfg) or len(capture.posts) >= p["posts_per_group"]:
            break
        if n == r["dom_fallback_after_scrolls"] and not capture.posts:
            # GraphQL gave nothing: a query was probably renamed. Degrade, and keep evidence.
            session.screenshot(page, cfg, f"no-graphql-{group['group_id']}")
            for post in dom.read_articles(page, group["group_id"]):
                capture.posts.setdefault(post.post_id, post)
        scroller.scroll()
        session.check(page, cfg)
    capture.drain()
    session.check_codes(capture.codes, cfg)


def read_assisted(page, capture: FeedCapture, group: dict, cfg: dict) -> None:
    """You scroll; the tool listens. No automated input reaches the page."""
    limit = cfg["pacing"]["assist_minutes_per_group"] * 60
    print(f"[{group['name']}] scroll this group yourself. Press Enter when done "
          f"(moves on by itself after {limit // 60} min or once it reaches posts already read).")
    _enter.arm()
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        page.wait_for_timeout(2000)  # keeps Playwright delivering responses
        capture.drain()
        session.check_codes(capture.codes, cfg)
        session.check(page, cfg)
        if reached_known(capture.posts, group, cfg):
            print("  reached posts already read")
            break
        if _enter.pressed():
            break


def pause_and_score(page, conn, backlog, cfg) -> list:
    """Wait between groups, using the wait to run the model."""
    target = random.uniform(*cfg["pacing"]["group_pause_min"]) * 60
    start = time.monotonic()
    while backlog and time.monotonic() - start < target:
        group, post = backlog.pop(0)
        score_safely(conn, group, post, cfg)
    left = target - (time.monotonic() - start)
    if left > 0:
        page.wait_for_timeout(left * 1000)
    return backlog


def score_safely(conn, group: dict, post: Post, cfg: dict) -> str:
    """One model failure must not drop the rest of the backlog."""
    try:
        return handle_post(conn, group, post, cfg)
    except Exception as exc:
        print(f"  scoring failed for post {post.post_id}: {exc}")
        return "error"


def handle_post(conn, group: dict, post: Post, cfg: dict) -> str:
    text = post.text.strip()
    if not adapters.passes_gate(text, cfg):
        return "gated"
    digest = adapters.text_hash(text)
    result = adapters.prior_score(conn, digest)  # reposts: no model call
    if result is not None and result["score"] < cfg["lead"]["min_score"]:
        return "repost"  # already in the rejected table; nothing new to store
    if result is None:
        age = ""
        if post.created_at:
            age = f", {max(0, int((time.time() - post.created_at) // 3600))} h ago"
        context = (f"Facebook group '{group['name']}'"
                   f" ({group.get('member_count') or '?'} members). "
                   f"Post by {post.author_name or 'unknown'}{age}")
        result = adapters.score_post(text, context, cfg)
    if result["score"] >= cfg["lead"]["min_score"]:
        adapters.save_lead(conn, digest=digest, post=post, group_name=group["name"], result=result)
        print(f"  lead {result['score']:>3} {result.get('intent', ''):<14} {text[:70]!r}")
        return "lead"
    adapters.save_rejected(conn, digest=digest, post=post, group_name=group["name"], result=result)
    return "rejected"
