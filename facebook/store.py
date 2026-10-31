"""SQLite: groups, leads, rejected posts and the run ledger.

Daily budgets are counted from fb_runs rather than kept in memory, so they
hold across crashes, restarts and several runs in one day.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS fb_groups (
  group_id          TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  url               TEXT NOT NULL,
  privacy           TEXT,
  member_count      INTEGER,
  activity          TEXT,
  membership        TEXT DEFAULT 'unknown',
  source_query      TEXT,
  score             INTEGER,
  score_reason      TEXT,
  status            TEXT DEFAULT 'candidate',
  last_seen_post_id TEXT,
  last_seen_time    INTEGER,
  last_browsed_at   INTEGER,
  created_at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS fb_runs (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  mode        TEXT,
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER,
  page_loads  INTEGER DEFAULT 0,
  searches    INTEGER DEFAULT 0,
  posts_seen  INTEGER DEFAULT 0,
  stop_reason TEXT
);
CREATE TABLE IF NOT EXISTS leads (
  id          INTEGER PRIMARY KEY,
  platform    TEXT NOT NULL DEFAULT 'facebook',
  post_id     TEXT UNIQUE,
  text_hash   TEXT,
  group_name  TEXT,
  author_name TEXT,
  author_url  TEXT,
  text        TEXT,
  url         TEXT,
  posted_at   INTEGER,
  score       INTEGER,
  intent      TEXT,
  reason      TEXT,
  draft       TEXT,
  status      TEXT DEFAULT 'new',   -- new | replied | won | lost | ignored
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER
);
-- Posts the model scored below the lead threshold. Only the hash and the
-- score are kept, so the same text is never scored twice and nothing
-- personal is stored for posts that are not leads.
CREATE TABLE IF NOT EXISTS rejected (
  text_hash   TEXT PRIMARY KEY,
  post_id     TEXT,
  score       INTEGER,
  intent      TEXT,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_hash ON leads(text_hash);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""
RUN_COUNTERS = {"page_loads", "searches", "posts_seen"}
LEAD_STATUSES = ("new", "replied", "won", "lost", "ignored")


def now() -> int:
    return int(time.time())


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))


def _rows(conn, sql: str, args=()) -> list[dict]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return [dict(row) for row in cur.execute(sql, args)]


def migrate(conn) -> None:
    """Safe to run every start: every statement is IF NOT EXISTS."""
    conn.executescript(SCHEMA)
    conn.commit()


# ---- groups ---------------------------------------------------------------

def upsert_group(conn, group: dict, status: str = "candidate") -> bool:
    """Insert a found group. Existing rows keep your status and the score."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO fb_groups (group_id, name, url, privacy, member_count, "
        "activity, source_query, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (group["group_id"], group["name"], group["url"], group.get("privacy"),
         group.get("member_count"), group.get("activity"), group.get("source_query"),
         status, now()))
    conn.commit()
    return cur.rowcount == 1


def get_group(conn, group_id: str) -> dict | None:
    rows = _rows(conn, "SELECT * FROM fb_groups WHERE group_id = ?", (group_id,))
    return rows[0] if rows else None


def list_groups(conn, status: str | None = None) -> list[dict]:
    if status:
        return _rows(conn, "SELECT * FROM fb_groups WHERE status = ? "
                           "ORDER BY score DESC NULLS LAST", (status,))
    return _rows(conn, "SELECT * FROM fb_groups ORDER BY status, score DESC NULLS LAST")


def groups_to_browse(conn, limit: int) -> list[dict]:
    # Least recently browsed first, so every approved group gets its turn.
    return _rows(conn,
                 "SELECT * FROM fb_groups WHERE status = 'approved' "
                 "AND membership IN ('member', 'public_readable', 'unknown') "
                 "ORDER BY COALESCE(last_browsed_at, 0) LIMIT ?", (limit,))


def unscored_candidates(conn, limit: int) -> list[dict]:
    return _rows(conn, "SELECT * FROM fb_groups WHERE status = 'candidate' AND score IS NULL "
                       "ORDER BY member_count DESC LIMIT ?", (limit,))


def update_group(conn, group_id: str, **fields) -> None:
    allowed = {"name", "status", "membership", "score", "score_reason", "privacy",
               "member_count", "activity"}
    keys = [k for k in fields if k in allowed]
    if keys:
        conn.execute(f"UPDATE fb_groups SET {', '.join(f'{k} = ?' for k in keys)} "
                     "WHERE group_id = ?", [fields[k] for k in keys] + [group_id])
        conn.commit()


def mark_browsed(conn, group_id: str, newest) -> None:
    """Record the visit; move the watermark forward if a dated post was read.

    Called for skipped groups too (newest=None), so a group whose probe fails
    does not stay at the front of the rotation forever.
    """
    conn.execute("UPDATE fb_groups SET last_browsed_at = ? WHERE group_id = ?", (now(), group_id))
    if newest is not None and newest.created_at:
        conn.execute(
            "UPDATE fb_groups SET last_seen_post_id = ?, last_seen_time = ? "
            "WHERE group_id = ? AND COALESCE(last_seen_time, 0) < ?",
            (newest.post_id, newest.created_at, group_id, newest.created_at))
    conn.commit()


# ---- leads ----------------------------------------------------------------

def find_scored(conn, digest: str) -> dict | None:
    """Earlier result for identical text, as a lead or a reject, else None."""
    for table in ("leads", "rejected"):
        rows = _rows(conn, f"SELECT score, intent FROM {table} WHERE text_hash = ? LIMIT 1", (digest,))
        if rows:
            return rows[0]
    return None


def save_lead(conn, *, digest: str, post, group_name: str, result: dict,
              platform: str = "facebook") -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO leads (platform, post_id, text_hash, group_name, author_name, "
        "author_url, text, url, posted_at, score, intent, reason, draft, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (platform, post.post_id, digest, group_name, post.author_name, post.author_url,
         post.text, post.url, post.created_at, result["score"], result.get("intent"),
         result.get("reason"), result.get("draft"), now(), now()))
    conn.commit()
    return cur.rowcount == 1


def save_rejected(conn, *, digest: str, post, result: dict) -> None:
    conn.execute("INSERT OR IGNORE INTO rejected (text_hash, post_id, score, intent, created_at) "
                 "VALUES (?,?,?,?,?)",
                 (digest, post.post_id, result["score"], result.get("intent"), now()))
    conn.commit()


def list_leads(conn, new_only: bool = False, min_score: int = 0, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM leads WHERE score >= ?"
    args: list = [min_score]
    if new_only:
        sql += " AND status = 'new'"
    sql += " ORDER BY score DESC, created_at DESC LIMIT ?"
    args.append(limit)
    return _rows(conn, sql, args)


def set_lead_status(conn, lead_id: int, status: str) -> bool:
    if status not in LEAD_STATUSES:
        raise ValueError(f"status must be one of {', '.join(LEAD_STATUSES)}")
    cur = conn.execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
                       (status, now(), lead_id))
    conn.commit()
    return cur.rowcount == 1


# ---- run ledger -----------------------------------------------------------

def start_run(conn, kind: str, mode: str | None = None) -> int:
    cur = conn.execute("INSERT INTO fb_runs (kind, mode, started_at) VALUES (?,?,?)",
                       (kind, mode, now()))
    conn.commit()
    return cur.lastrowid


def bump(conn, run_id: int, field: str, n: int = 1) -> None:
    assert field in RUN_COUNTERS, field
    conn.execute(f"UPDATE fb_runs SET {field} = {field} + ? WHERE id = ?", (n, run_id))
    conn.commit()


def end_run(conn, run_id: int, reason: str) -> None:
    conn.execute("UPDATE fb_runs SET ended_at = ?, stop_reason = ? WHERE id = ?",
                 (now(), reason, run_id))
    conn.commit()


def usage_today(conn) -> dict:
    midnight = int(dt.datetime.combine(dt.date.today(), dt.time()).timestamp())
    row = _rows(conn,
                "SELECT COALESCE(SUM(page_loads), 0) AS page_loads, "
                "COALESCE(SUM(searches), 0) AS searches, "
                "SUM(kind = 'browse') AS browse_runs, "
                "MAX(CASE WHEN kind = 'browse' THEN started_at END) AS last_browse_start "
                "FROM fb_runs WHERE started_at >= ?", (midnight,))[0]
    row["browse_runs"] = row["browse_runs"] or 0
    return row
