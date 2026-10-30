"""Read posts from the GraphQL responses the group page already fetches.

The tool never calls /api/graphql/ itself. It only reads responses the page
asked for while you (or the scroller) moved down the feed, so rotating
doc_ids and request signing are Facebook's problem, not ours.

Field paths are deep and change between deploys, so nothing here uses a
fixed path. The walker finds each Story node and searches inside it,
shallowest match first, for the handful of fields we need.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from urllib.parse import parse_qs

# Keys whose contents belong to some other post (a shared post, a link
# preview) and must not be mistaken for this post's text or id.
SKIP_KEYS = {"attached_story", "all_subattachments", "attachments"}
URL_KEYS = ("wwwURL", "url", "permalink_url")


@dataclass
class Post:
    post_id: str
    group_id: str | None
    text: str
    author_name: str | None
    author_url: str | None
    created_at: int | None  # unix seconds; None when only the DOM was readable
    url: str | None


def split_payload(body: str) -> list[dict]:
    """One response holds several JSON objects, one per line (@stream/@defer)."""
    body = body.strip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    if not out:  # a single pretty-printed object spread over lines
        try:
            obj = json.loads(body)
            out = [obj] if isinstance(obj, dict) else []
        except ValueError:
            pass
    return out


def _is_story(node: dict) -> bool:
    return node.get("__typename") == "Story" or "comet_sections" in node


def iter_stories(obj):
    """Yield the outermost Story nodes. Inner `story` dicts repeat the same
    post at deeper levels, so the walk does not descend into a Story."""
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if _is_story(node):
                yield node
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(reversed(node))


def _first(node, keys, kind, accept=lambda v: True):
    """Breadth-first search for the shallowest value under one of `keys`."""
    keys = (keys,) if isinstance(keys, str) else keys
    queue = deque([node])
    while queue:
        cur = queue.popleft()
        if isinstance(cur, dict):
            for key, value in cur.items():
                if key in keys and isinstance(value, kind) and accept(value):
                    return value
            for key, value in cur.items():
                if key not in SKIP_KEYS and isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(cur, list):
            queue.extend(v for v in cur if isinstance(v, (dict, list)))
    return None


def extract_post(story: dict, group_id: str | None) -> Post | None:
    post_id = _first(story, "post_id", (str, int))
    if post_id is None:
        return None
    message = _first(story, "message", dict, lambda d: isinstance(d.get("text"), str))
    actors = _first(story, "actors", list,
                    lambda v: bool(v) and isinstance(v[0], dict) and "name" in v[0])
    created = _first(story, "creation_time", int)
    url = _first(story, URL_KEYS, str,
                 lambda s: ("/posts/" in s or "/permalink/" in s) and "comment_id" not in s)
    if not url and group_id:
        url = f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/"
    actor = actors[0] if actors else {}
    return Post(
        post_id=str(post_id),
        group_id=group_id,
        text=(message or {}).get("text", ""),
        author_name=actor.get("name"),
        author_url=actor.get("url") or actor.get("profile_url"),
        created_at=created,
        url=url,
    )


def error_codes(obj: dict) -> list[int]:
    codes = []
    for err in obj.get("errors") or []:
        code = err.get("code") if isinstance(err, dict) else None
        if isinstance(code, int):
            codes.append(code)
    if isinstance(obj.get("error"), int):  # older style: {"error": 1357004, ...}
        codes.append(obj["error"])
    return codes


def friendly_name(request) -> str | None:
    headers = request.headers or {}
    name = headers.get("x-fb-friendly-name")
    if name:
        return name
    form = parse_qs(request.post_data or "")
    return (form.get("fb_api_req_friendly_name") or [None])[0]


class FeedCapture:
    """Collects posts for the group currently open.

    on_response only queues the response. Reading a body inside a Playwright
    sync-API event handler can deadlock, so drain() reads the queue from the
    main loop after each scroll.
    """

    def __init__(self, feed_queries):
        self.feed_queries = set(feed_queries)
        self.group_id: str | None = None
        self.posts: dict[str, Post] = {}
        self.codes: list[int] = []
        self._queue = []

    def reset(self, group_id: str) -> None:
        self.group_id, self.posts, self.codes, self._queue = group_id, {}, [], []

    def on_response(self, response) -> None:
        if "/api/graphql/" in response.url:
            self._queue.append(response)

    def drain(self) -> int:
        queue, self._queue = self._queue, []
        added = 0
        for response in queue:
            try:
                name = friendly_name(response.request)
                body = response.text()
            except Exception:
                continue  # body discarded after a navigation; nothing to read
            for obj in split_payload(body):
                self.codes += error_codes(obj)
                if name in self.feed_queries:
                    added += self.add(obj)
        return added

    def ingest_html(self, page) -> int:
        """The first page of posts is often preloaded in JSON script tags."""
        texts = page.eval_on_selector_all(
            'script[type="application/json"]', "els => els.map(e => e.textContent)")
        added = 0
        for text in texts:
            if "creation_time" not in text:
                continue
            try:
                added += self.add(json.loads(text))
            except ValueError:
                continue
        return added

    def add(self, obj) -> int:
        added = 0
        for story in iter_stories(obj):
            post = extract_post(story, self.group_id)
            if post and post.post_id not in self.posts:
                self.posts[post.post_id] = post
                added += 1
        return added
