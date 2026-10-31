"""Find candidate groups through Facebook's own group search.

Search is the riskiest step (bursts of searches look scripted), so it gets
the tightest budget, long gaps between queries, and the browser is closed
before any model call. Groups are only ever candidates here: you approve
them, and you join private ones by hand.
"""
from __future__ import annotations

import datetime as dt
import random
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from . import adapters, groups, pacing, session, store

CARDS_JS = r"""
() => {
  const seen = new Set(), out = [];
  for (const a of document.querySelectorAll('a[href*="/groups/"]')) {
    const m = a.href.match(/\/groups\/([^\/?#]+)/);
    const name = (a.innerText || '').trim();
    if (!m || !name || seen.has(m[1])) continue;
    // Climb to the smallest ancestor that holds the "· N members" line.
    let card = a;
    while (card && !/members/i.test(card.innerText || '')) card = card.parentElement;
    if (!card || card.innerText.length > 600) continue;  // climbed past one result
    seen.add(m[1]);
    out.push({slug: m[1], name, text: card.innerText});
  }
  return out;
}
"""


def todays_queries(cfg: dict, n: int) -> list[str]:
    """Rotate through the list by date, so every query gets a turn without
    storing anything and no query is searched twice in a row."""
    queries = cfg["discovery"]["queries"]
    if not queries or n <= 0:
        return []
    offset = (dt.date.today().toordinal() * n) % len(queries)
    return [queries[(offset + i) % len(queries)] for i in range(min(n, len(queries)))]


def keep_card(card: dict, cfg: dict) -> tuple[bool, str | None]:
    """(keep, reason). 'unparsed' cards are never stored: the member-count
    line did not match, which usually means the card layout changed, and a
    group must not be ignored for good over a parsing miss."""
    d = cfg["discovery"]
    hit = groups.junk_hit(f"{card['name']} {card['text']}", d["junk_keywords"])
    if hit:
        return False, f"junk:{hit}"
    if card.get("member_count") is None:
        return False, "unparsed"
    if card["member_count"] < d["min_members"]:
        return False, "small"
    return True, None


def run(cfg: dict, dry_run: bool = False) -> str:
    conn = adapters.connect(cfg)
    store.migrate(conn)
    usage = store.usage_today(conn)
    reason = pacing.guard(cfg, usage, "discover", session.cooldown_until(cfg))
    if reason:
        return f"Not searching: {reason}."
    if not cfg["discovery"]["queries"]:
        return "No search queries. Add some under facebook.discovery.queries in config.yaml."

    budget = cfg["discovery"]["searches_per_day"] - usage["searches"]
    run_id = store.start_run(conn, "discover")
    found, stop = [], "done"
    with sync_playwright() as pw:
        context, page = session.launch(pw, cfg)
        try:
            session.ensure_logged_in(page, cfg)
            for i, query in enumerate(todays_queries(cfg, budget)):
                if i:
                    page.wait_for_timeout(pacing.seconds(cfg["discovery"]["search_pause_s"]) * 1000)
                print(f"searching: {query}")
                found += search_once(page, conn, run_id, query, cfg)
        except session.Blocked as blocked:
            stop = f"blocked:{blocked.reason}"
            session.screenshot(page, cfg, stop)
            if blocked.cooldown:
                until = session.set_cooldown(cfg, blocked.reason)
                print(f"Stopped: Facebook signal '{blocked.reason}'. No runs until {until:%a %d %b %H:%M}.")
        except KeyboardInterrupt:
            stop = "user"
        finally:
            try:
                context.close()
            except Exception:
                pass

    new = 0
    for card in found:
        keep, why = keep_card(card, cfg)
        print(f"{'+' if keep else '-'} {card['name']} ({card.get('member_count')}) {why or ''}")
        if not dry_run and why != "unparsed":
            # Junk and small groups are stored as ignored so later searches never reconsider them.
            new += store.upsert_group(conn, card, status="candidate" if keep else "ignored")

    if not dry_run:
        score_candidates(conn, cfg)
    store.end_run(conn, run_id, stop)
    return f"Discovery ended: {stop}. {len(found)} groups seen, {new} new."


def search_once(page, conn, run_id: int, query: str, cfg: dict) -> list[dict]:
    page.goto(f"https://www.facebook.com/search/groups/?q={quote_plus(query)}",
              wait_until="domcontentloaded")
    store.bump(conn, run_id, "searches")
    store.bump(conn, run_id, "page_loads")
    session.check(page, cfg)
    page.wait_for_timeout(random.uniform(3000, 6000))
    scroller = pacing.Scroller(page, cfg["pacing"])
    for _ in range(cfg["discovery"]["scrolls_per_search"]):
        scroller.scroll()
        session.check(page, cfg)
    cards = []
    for raw in page.evaluate(CARDS_JS):
        gid = groups.group_id_from_url(f"/groups/{raw['slug']}/")
        if not gid:
            continue
        cards.append({"group_id": gid, "name": raw["name"],
                      "url": f"https://www.facebook.com/groups/{gid}/",
                      "text": raw["text"], "source_query": query,
                      **groups.parse_card(raw["text"])})
    return cards


def score_candidates(conn, cfg: dict) -> None:
    """Model calls are rare and slow (about 45 s), so only unscored
    candidates that passed the filters, capped per run, biggest first.
    Unscored candidates stay unscored and are picked up by the next run."""
    d = cfg["discovery"]
    pending = store.unscored_candidates(conn, d["group_scores_per_run"])
    if not pending:
        return
    problem = adapters.model_ready(cfg)
    if problem:
        print(f"Skipping group scoring: {problem}. Run discover again once it is up.")
        return
    for group in pending:
        about = f"{group.get('privacy') or ''} group, {group.get('member_count') or '?'} members, " \
                f"{group.get('activity') or 'activity unknown'}. Found by searching '{group.get('source_query')}'."
        try:
            result = adapters.score_group(group["name"], about, cfg)
        except Exception as exc:
            print(f"  scoring failed for {group['name']}: {exc}")
            continue
        store.update_group(conn, group["group_id"], score=result["score"],
                           score_reason=result.get("reason"))
        print(f"  {result['score']:>3}  {group['name']}")
