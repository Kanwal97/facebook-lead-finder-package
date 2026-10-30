"""Walker tests. The fixture mimics the documented 2026 shape (JSON lines,
nested comet_sections, a shared post inside the story). Replace it with
real responses saved by a first assist run, then keep both."""
import json

from facebook.graphql import FeedCapture, error_codes, extract_post, iter_stories, split_payload


def story(post_id, text, ts, author="Dana Ruiz", attached=None):
    inner = {"__typename": "Story", "comet_sections": {
        "message": {"story": {"message": {"text": text}}}}}
    node = {
        "__typename": "Story",
        "id": f"S:{post_id}",
        "comet_sections": {
            "content": {"story": inner},
            "context_layout": {"story": {"comet_sections": {
                "actor_photo": {"story": {"actors": [
                    {"__typename": "User", "name": author, "id": "100", "url": "https://www.facebook.com/dana"}]}},
                "metadata": [{"story": {"creation_time": ts,
                                        "url": f"https://www.facebook.com/groups/42/posts/{post_id}/"}}]}}},
            "feedback": {"story": {"post_id": post_id}},
        },
    }
    if attached:
        node["attached_story"] = attached
    return node


def test_split_payload_handles_prefix_and_lines():
    body = "for (;;);" + json.dumps({"data": {"a": 1}}) + "\n" + json.dumps({"label": "x", "data": {}}) + "\n\n"
    assert len(split_payload(body)) == 2


def test_split_payload_single_pretty_object():
    assert split_payload(json.dumps({"data": {"a": 1}}, indent=2)) == [{"data": {"a": 1}}]


def test_extract_post_fields():
    post = extract_post(story("111", "Need someone to sync Shopify orders to Xero", 1761800000), "42")
    assert post.post_id == "111"
    assert post.text.startswith("Need someone")
    assert post.author_name == "Dana Ruiz"
    assert post.created_at == 1761800000
    assert post.url.endswith("/groups/42/posts/111/")


def test_shared_post_does_not_leak_its_text_or_id():
    shared = story("999", "Buy my course", 1700000000, author="Spammer")
    post = extract_post(story("222", "Our Zapier flow keeps failing on refunds", 1761800500,
                              attached=shared), "42")
    assert post.post_id == "222"
    assert "Zapier" in post.text


def test_url_built_when_missing():
    node = story("333", "Any devs here who build Make.com scenarios?", 1761801000)
    node["comet_sections"]["context_layout"]["story"]["comet_sections"]["metadata"][0]["story"].pop("url")
    assert extract_post(node, "42").url == "https://www.facebook.com/groups/42/posts/333/"


def test_capture_dedupes_across_pages():
    page1 = {"data": {"node": {"group_feed": {"edges": [
        {"node": story("1", "first post text long enough", 1761800001)},
        {"node": story("2", "second post text long enough", 1761800002)}]}}}}
    page2 = {"label": "stream", "data": story("2", "second post text long enough", 1761800002)}
    cap = FeedCapture(["GroupsCometFeedRegularStoriesPaginationQuery"])
    cap.reset("42")
    assert cap.add(page1) == 2
    assert cap.add(page2) == 0
    assert sorted(cap.posts) == ["1", "2"]


def test_iter_stories_yields_outermost_only():
    assert len(list(iter_stories({"data": story("5", "x", 1)}))) == 1


def test_error_codes():
    assert error_codes({"errors": [{"code": 368, "message": "blocked"}]}) == [368]
    assert error_codes({"error": 1357004}) == [1357004]
