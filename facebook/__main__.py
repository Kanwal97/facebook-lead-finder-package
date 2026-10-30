"""python -m facebook <command>

  login                 open the browsing profile so you can log in by hand
  discover [--dry-run]  search for candidate groups and score them
  groups [list|add|approve|ignore|approve-above]
  browse [--mode assist|auto] [--group ID] [--force]
  status [--clear-cooldown]

Leads land in the shared tables: use the existing `leads --new --platform facebook`.
"""
from __future__ import annotations

import argparse
import datetime as dt

from . import config, session, store


def cmd_login(cfg, args) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        context, page = session.launch(pw, cfg)
        session.ensure_logged_in(page, cfg)
        input("Logged in. Set Facebook's language to English (US) if needed, then press Enter... ")
        context.close()
    return "Profile saved."


def cmd_discover(cfg, args) -> str:
    from . import discover
    return discover.run(cfg, dry_run=args.dry_run)


def cmd_browse(cfg, args) -> str:
    from . import browse
    return browse.run(cfg, args.mode or cfg["mode"], only_group=args.group, force=args.force)


def cmd_groups(cfg, args) -> str:
    from . import adapters, groups
    conn = adapters.connect()
    store.migrate(conn, (adapters.LEADS_TABLE, adapters.REJECTED_TABLE))
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
    for g in store.list_groups(conn, args.status):
        print(f"{g['status']:<9} {g['membership']:<15} {g['score'] if g['score'] is not None else '-':>3} "
              f"{(g['member_count'] or 0):>8}  {g['name'][:50]:<50} {g['url']}")
    return ""


def cmd_status(cfg, args) -> str:
    from . import adapters
    if args.clear_cooldown:
        session.clear_cooldown(cfg)
        print("Cooldown cleared. Only do this after checking the account by hand.")
    conn = adapters.connect()
    store.migrate(conn, (adapters.LEADS_TABLE, adapters.REJECTED_TABLE))
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
    ])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m facebook")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
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
    s = sub.add_parser("status")
    s.add_argument("--clear-cooldown", action="store_true")
    s.set_defaults(fn=cmd_status)

    args = parser.parse_args(argv)
    message = args.fn(config.load(), args)
    if message:
        print(message)


if __name__ == "__main__":
    main()
