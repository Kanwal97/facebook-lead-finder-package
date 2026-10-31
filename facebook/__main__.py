"""python -m facebook <command>

  login                 open the browsing profile so you can log in by hand
  test-ai               check the local model answers and score one sample post
  discover [--dry-run]  search for candidate groups and score them
  groups [list|add|approve|ignore|approve-above]
  browse [--mode assist|auto] [--group ID] [--force]
  leads [--new] [--min-score N] [--limit N]
  mark <lead id> <new|replied|won|lost|ignored>
  status [--clear-cooldown]
"""
from __future__ import annotations

import argparse
import datetime as dt

from . import adapters, config, session, store


def _db(cfg):
    conn = adapters.connect(cfg)
    store.migrate(conn)
    return conn


def cmd_login(cfg, args) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        context, page = session.launch(pw, cfg)
        try:
            session.ensure_logged_in(page, cfg)
            input("Logged in. Set Facebook's language to English (US) if needed, then press Enter... ")
        except session.Blocked as blocked:
            return f"Facebook signal '{blocked.reason}'. Finish the check in the browser, then run login again."
        finally:
            context.close()
    return "Profile saved."


def cmd_test_ai(cfg, args) -> str:
    from . import ai
    problem = adapters.model_ready(cfg)
    if problem:
        return f"Model not ready: {problem}"
    print(f"Model {ai.settings(cfg)['model']} at {ai.settings(cfg)['base_url']} answers. Scoring a sample post...")
    sample = ("Hi all, we run a small Shopify store and every evening I copy the day's orders "
              "into Xero by hand. It takes about an hour and I keep making mistakes. "
              "Is there any way to automate this? Happy to pay someone who knows how.")
    result = adapters.score_post(sample, "Facebook group 'Shopify Store Owners' (12000 members)", cfg)
    return "\n".join([f"score:  {result['score']}", f"intent: {result['intent']}",
                      f"reason: {result['reason']}", f"draft:  {result['draft']}"])


def cmd_discover(cfg, args) -> str:
    from . import discover
    return discover.run(cfg, dry_run=args.dry_run)


def cmd_browse(cfg, args) -> str:
    from . import browse
    return browse.run(cfg, args.mode or cfg["mode"], only_group=args.group, force=args.force)


def cmd_groups(cfg, args) -> str:
    from . import groups
    conn = _db(cfg)
    if args.action == "add":
        for url in args.targets:
            gid = groups.group_id_from_url(url)
            if not gid:
                print(f"not a group URL: {url}")
                continue
            store.upsert_group(conn, {"group_id": gid, "name": gid,
                                      "url": f"https://www.facebook.com/groups/{gid}/"},
                               status="approved")
            store.update_group(conn, gid, status="approved")
        return "Added. Names fill in on the first browse."
    if args.action in ("approve", "ignore"):
        status = "approved" if args.action == "approve" else "ignored"
        for gid in args.targets:
            store.update_group(conn, gid, status=status)
        return f"{len(args.targets)} group(s) {status}."
    if args.action == "approve-above":
        floor = int(args.targets[0]) if args.targets else cfg["discovery"]["min_group_score"]
        rows = [g for g in store.list_groups(conn, "candidate") if (g["score"] or 0) >= floor]
        for g in rows:
            store.update_group(conn, g["group_id"], status="approved")
        return f"{len(rows)} candidate(s) scoring {floor}+ approved. Join private ones by hand."
    rows = store.list_groups(conn, args.status)
    if not rows:
        return "No groups yet. Run: python -m facebook discover   or   python -m facebook groups add <url>"
    print(f"{'status':<9} {'membership':<15} {'score':>5} {'members':>8}  {'name':<50} url")
    for g in rows:
        print(f"{g['status']:<9} {g['membership']:<15} {g['score'] if g['score'] is not None else '-':>5} "
              f"{(g['member_count'] or 0):>8}  {g['name'][:50]:<50} {g['url']}")
    return ""


def cmd_leads(cfg, args) -> str:
    conn = _db(cfg)
    floor = cfg["lead"]["min_score"] if args.min_score is None else args.min_score
    rows = store.list_leads(conn, new_only=args.new, min_score=floor, limit=args.limit)
    if not rows:
        return "No leads yet." if not args.new else "No new leads."
    for r in rows:
        when = dt.datetime.fromtimestamp(r["posted_at"]).strftime("%d %b") if r["posted_at"] else "?"
        print(f"#{r['id']:<4} {r['score']:>3} {r['intent'] or '':<14} {r['status']:<8} "
              f"{(r['group_name'] or '')[:30]:<30} {r['author_name'] or 'unknown'}  {when}")
        print(f"      {(r['text'] or '').strip()[:300]}")
        if r["url"]:
            print(f"      {r['url']}")
        if r["draft"]:
            print(f"      draft: {r['draft']}")
        print()
    return f"{len(rows)} lead(s). Mark one with: python -m facebook mark <id> replied|won|lost|ignored"


def cmd_mark(cfg, args) -> str:
    conn = _db(cfg)
    if not store.set_lead_status(conn, args.lead_id, args.status):
        return f"No lead with id {args.lead_id}."
    return f"Lead {args.lead_id} marked {args.status}."


def cmd_status(cfg, args) -> str:
    if args.clear_cooldown:
        session.clear_cooldown(cfg)
        print("Cooldown cleared. Only do this after checking the account by hand.")
    conn = _db(cfg)
    u, p = store.usage_today(conn), cfg["pacing"]
    until = session.cooldown_until(cfg)
    last = (dt.datetime.fromtimestamp(u["last_browse_start"]).strftime("%H:%M")
            if u["last_browse_start"] else "none")
    return "\n".join([
        f"cooldown:     {until:%a %d %b %H:%M}" if until else "cooldown:     none",
        f"runs today:   {u['browse_runs']}/{p['runs_per_day']} (last {last})",
        f"page loads:   {u['page_loads']}/{p['page_loads_per_day']}",
        f"searches:     {u['searches']}/{cfg['discovery']['searches_per_day']}",
        f"active hours: {p['active_hours'][0]}:00 to {p['active_hours'][1]}:00",
        f"database:     {cfg['db_path']}",
    ])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m facebook")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    sub.add_parser("test-ai").set_defaults(fn=cmd_test_ai)
    d = sub.add_parser("discover")
    d.add_argument("--dry-run", action="store_true", help="print what would be stored")
    d.set_defaults(fn=cmd_discover)
    g = sub.add_parser("groups")
    g.add_argument("action", nargs="?", default="list",
                   choices=["list", "add", "approve", "ignore", "approve-above"])
    g.add_argument("targets", nargs="*", help="group URLs, ids, or a score floor")
    g.add_argument("--status", choices=["candidate", "approved", "ignored"])
    g.set_defaults(fn=cmd_groups)
    b = sub.add_parser("browse")
    b.add_argument("--mode", choices=["assist", "auto"])
    b.add_argument("--group", help="browse just this group id")
    b.add_argument("--force", action="store_true",
                   help="ignore active hours and run spacing (never a cooldown)")
    b.set_defaults(fn=cmd_browse)
    le = sub.add_parser("leads")
    le.add_argument("--new", action="store_true", help="only leads not yet marked")
    le.add_argument("--min-score", type=int, default=None, help="default: lead.min_score")
    le.add_argument("--limit", type=int, default=50)
    le.set_defaults(fn=cmd_leads)
    m = sub.add_parser("mark")
    m.add_argument("lead_id", type=int)
    m.add_argument("status", choices=store.LEAD_STATUSES)
    m.set_defaults(fn=cmd_mark)
    s = sub.add_parser("status")
    s.add_argument("--clear-cooldown", action="store_true")
    s.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    message = args.fn(config.load(), args)
    if message:
        print(message)


if __name__ == "__main__":
    main()
