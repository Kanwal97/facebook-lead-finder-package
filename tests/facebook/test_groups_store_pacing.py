import datetime as dt
import sqlite3

from facebook import groups, pacing, store
from facebook.graphql import Post


def test_parse_card():
    card = groups.parse_card("Shopify Sellers Hub\nPublic · 12K members · 10+ posts a day\nJoin")
    assert card == {"privacy": "public", "member_count": 12000, "activity": "10+ posts a day"}
    assert groups.parse_card("Private · 1,204 members")["member_count"] == 1204
    assert groups.parse_card("nothing here")["privacy"] is None


def test_group_ids_and_titles():
    assert groups.group_id_from_url("https://www.facebook.com/groups/shopifysellers/?ref=x") == "shopifysellers"
    assert groups.group_id_from_url("https://www.facebook.com/groups/feed/") is None
    assert groups.name_from_title("(3) Shopify Store Owners | Facebook") == "Shopify Store Owners"
    assert groups.junk_hit("Make Money Online Crew", ["make money online"]) == "make money online"


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("CREATE TABLE rejected (hash TEXT PRIMARY KEY)")
    store.migrate(conn)
    store.migrate(conn)  # second run must be a no-op
    return conn


def test_migration_adds_platform_once():
    conn = make_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")]
    assert cols.count("platform") == 1


def test_groups_and_budget():
    conn = make_db()
    assert store.upsert_group(conn, {"group_id": "42", "name": "G", "url": "u"}, status="approved")
    assert not store.upsert_group(conn, {"group_id": "42", "name": "G", "url": "u"})
    store.update_group(conn, "42", membership="member")
    assert [g["group_id"] for g in store.groups_to_browse(conn, 6)] == ["42"]
    store.mark_browsed(conn, "42", Post("9", "42", "t", None, None, 1761800000, None))
    assert store.get_group(conn, "42")["last_seen_time"] == 1761800000
    run = store.start_run(conn, "browse", "auto")
    store.bump(conn, run, "page_loads", 3)
    usage = store.usage_today(conn)
    assert usage["page_loads"] == 3 and usage["browse_runs"] == 1


CFG = {"pacing": {"active_hours": [9, 22], "page_loads_per_day": 25, "runs_per_day": 3,
                  "min_hours_between_runs": 3},
       "discovery": {"searches_per_day": 4}}
USAGE = {"page_loads": 0, "searches": 0, "browse_runs": 0, "last_browse_start": None}


def test_guard():
    noon = dt.datetime(2026, 10, 30, 12, 0)
    assert pacing.guard(CFG, USAGE, "browse", None, noon) is None
    assert "active hours" in pacing.guard(CFG, USAGE, "browse", None, noon.replace(hour=23))
    assert "cooling" in pacing.guard(CFG, USAGE, "browse", noon, noon)
    recent = dict(USAGE, browse_runs=1, last_browse_start=noon.timestamp() - 3600)
    assert "since the last run" in pacing.guard(CFG, recent, "browse", None, noon)
    assert "search budget" in pacing.guard(CFG, dict(USAGE, searches=4), "discover", None, noon)
