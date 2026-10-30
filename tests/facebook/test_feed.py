from facebook.feed import reached_known, select_new
from facebook.graphql import Post

NOW = 1_761_800_000
CFG = {"read": {"max_post_age_days": 14, "min_text_chars": 10}, "pacing": {"posts_per_group": 40}}


def post(pid, hours_ago, text="a long enough post"):
    return Post(pid, "42", text, None, None, NOW - hours_ago * 3600, None)


def feed(*posts):
    return {p.post_id: p for p in posts}


def test_old_pinned_post_does_not_stop_the_scroll():
    group = {"last_seen_time": NOW - 48 * 3600}
    posts = feed(post("pinned", 24 * 90), post("a", 1), post("b", 2))
    assert not reached_known(posts, group, CFG, NOW)


def test_stops_when_recent_posts_are_already_read():
    group = {"last_seen_time": NOW - 48 * 3600}
    posts = feed(post("pinned", 24 * 90), post("a", 1), post("b", 50), post("c", 60), post("d", 70))
    assert reached_known(posts, group, CFG, NOW)


def test_first_visit_stops_at_age_limit():
    posts = feed(post("a", 1), post("b", 2), post("c", 3), post("d", 24 * 15), post("e", 24 * 16))
    assert reached_known(posts, {}, CFG, NOW)


def test_two_old_pins_alone_do_not_stop():
    posts = feed(post("pin1", 24 * 90), post("pin2", 24 * 60))
    assert not reached_known(posts, {}, CFG, NOW)


def test_stops_at_last_seen_post_below_the_pins():
    posts = feed(post("pin", 24 * 90), post("a", 1), post("b", 2), post("seen", 5))
    assert reached_known(posts, {"last_seen_post_id": "seen"}, CFG, NOW)


def test_select_new_filters_old_short_and_sorts_newest_first():
    group = {"last_seen_time": NOW - 10 * 3600}
    kept = select_new([post("old", 20), post("short", 1, "hi"), post("x", 3), post("y", 1)], group, CFG, NOW)
    assert [p.post_id for p in kept] == ["y", "x"]
