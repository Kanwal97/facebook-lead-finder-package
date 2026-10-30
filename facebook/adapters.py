"""The only file that knows the Discord repo's function names.

Everything else in facebook/ calls these functions, so wiring the package to
the real ai.py, pipeline/db.py and keyword gate is a one-file job. Each
TODO(wire) line is a guess at a name: rename it to match, keep the return shape.
"""
from __future__ import annotations

import ai                   # local Ollama client and prompts, unchanged
from pipeline import db     # SQLite schema, leads and rejected tables
from pipeline import gate   # keyword gate, moved out of monitor/browser.py

PLATFORM = "facebook"
LEADS_TABLE = "leads"        # TODO(wire)
REJECTED_TABLE = "rejected"  # TODO(wire)


def connect():
    return db.connect()  # TODO(wire)


def passes_gate(text: str) -> bool:
    """Problem or ask phrase present, no pitch phrase, no income brag."""
    return gate.passes(text)  # TODO(wire)


def text_hash(text: str) -> str:
    """Must normalise exactly as the Discord side does, or reposts rescore."""
    return db.text_hash(text)  # TODO(wire)


def prior_score(conn, digest: str) -> dict | None:
    """Earlier result for identical text on either platform: no model call."""
    return db.find_scored(conn, digest)  # TODO(wire)


def score_post(text: str, context: str) -> dict:
    """{'score', 'intent', 'reason', 'draft'}. About 45 s on the laptop."""
    return ai.score_message(text, context=context)  # TODO(wire)


def score_group(name: str, about: str) -> dict:
    """{'score', 'reason'}: the server-scoring prompt, reused for groups."""
    return ai.score_server(name=name, description=about)  # TODO(wire)


def save_lead(conn, *, digest: str, post, group_name: str, result: dict) -> None:
    db.save_lead(  # TODO(wire): map onto the real column names
        conn, platform=PLATFORM, text_hash=digest, source=group_name,
        message_id=post.post_id, link=post.url, author=post.author_name,
        author_url=post.author_url, text=post.text, posted_at=post.created_at,
        score=result["score"], intent=result.get("intent"),
        reason=result.get("reason"), draft=result.get("draft"),
    )


def save_rejected(conn, *, digest: str, post, group_name: str, result: dict) -> None:
    # Hash and score only: nothing personal is kept for posts that are not leads.
    db.save_rejected(conn, platform=PLATFORM, text_hash=digest,
                     score=result["score"])  # TODO(wire)
