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
    store.migrate(conn)
    store.migrate(conn)  # second run must be a no-op
    return conn


def test_migration_is_idempotent():
    conn = make_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)")]
    assert cols.count("platform") == 1
    assert "text_hash" in [r[1] for r in conn.execute("PRAGMA table_info(rejected)")]


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


def test_skipped_group_moves_to_the_back_of_the_rotation():
    conn = make_db()
    for gid in ("a", "b"):
        store.upsert_group(conn, {"group_id": gid, "name": gid, "url": "u"}, status="approved")
    store.mark_browsed(conn, "a", None)  # probe failed: visited, no watermark
    assert [g["group_id"] for g in store.groups_to_browse(conn, 2)] == ["b", "a"]
    assert store.get_group(conn, "a")["last_seen_time"] is None


def test_leads_and_rejects():
    conn = make_db()
    post = Post("111", "42", "Need someone to sync Shopify orders to Xero", "Dana", None, 1761800000,
                "https://www.facebook.com/groups/42/posts/111/")
    assert store.find_scored(conn, "h1") is None
    assert store.save_lead(conn, digest="h1", post=post, group_name="G",
                           result={"score": 80, "intent": "problem", "reason": "r", "draft": "d"})
    assert not store.save_lead(conn, digest="h1", post=post, group_name="G", result={"score": 80})
    assert store.find_scored(conn, "h1")["score"] == 80
    store.save_rejected(conn, digest="h2", post=Post("222", "42", "x", None, None, None, None),
                        result={"score": 10, "intent": "spam"})
    assert store.find_scored(conn, "h2") == {"score": 10, "intent": "spam"}

    leads = store.list_leads(conn, new_only=True)
    assert [l["id"] for l in leads] == [1] and leads[0]["draft"] == "d"
    assert store.set_lead_status(conn, 1, "replied")
    assert store.list_leads(conn, new_only=True) == []
    assert not store.set_lead_status(conn, 99, "won")


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
