"""Fallback reader: posts from div[role=article] when GraphQL yields nothing.

Weaker than GraphQL on every field: text may be cut at "See more" (we never
click it), the author link is a guess, and there is no timestamp. Used only
so a changed query name degrades the run instead of emptying it.
"""
from __future__ import annotations

import re

from .graphql import Post

ARTICLES_JS = r"""
() => Array.from(document.querySelectorAll('div[role="feed"] div[role="article"]'))
  // Comments are articles nested inside a post's article; keep top level only.
  .filter(a => !a.parentElement.closest('div[role="article"]'))
  .map(a => {
    const msg = a.querySelector('[data-ad-comet-preview="message"], [data-ad-preview="message"]');
    const link = a.querySelector('a[href*="/posts/"], a[href*="/permalink/"]');
    const who = a.querySelector('h2 a, h3 a, strong a');
    return {
      text: (msg || a).innerText,
      url: link ? link.href.split('?')[0] : null,
      author: who ? who.innerText : null,
      author_url: who ? who.href.split('?')[0] : null,
    };
  })
"""

POST_ID_RE = re.compile(r"/(?:posts|permalink)/(\d+)")


def read_articles(page, group_id: str) -> list[Post]:
    posts = []
    for row in page.evaluate(ARTICLES_JS):
        match = POST_ID_RE.search(row.get("url") or "")
        if not match:
            continue  # no id means no dedupe; skip rather than risk rescoring
        posts.append(Post(
            post_id=match.group(1), group_id=group_id, text=(row.get("text") or "").strip(),
            author_name=row.get("author"), author_url=row.get("author_url"),
            created_at=None, url=row.get("url"),
        ))
    return posts
