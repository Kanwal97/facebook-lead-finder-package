"""Load config.yaml from the project root.

Defaults live here so a key missing from the YAML falls back to a slow,
safe value instead of crashing mid-run or running unthrottled.
"""
from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict = {
    "db_path": "leads.db",
    "profile_dir": "~/.local/share/lead-finder/fb-profile",
    "debug_dir": "debug/facebook",
    "cooldown_file": "~/.local/state/lead-finder/fb-cooldown",
    "mode": "assist",
    "sorting": "CHRONOLOGICAL",
    "pacing": {
        "active_hours": [9, 22],
        "groups_per_run": 6,
        "runs_per_day": 3,
        "min_hours_between_runs": 3,
        "page_loads_per_day": 25,
        "posts_per_group": 40,
        "scrolls_per_group": 15,
        "scroll_px": [400, 900],
        "scroll_ticks": [3, 6],
        "scroll_pause_s": [4, 11],
        "long_pause_every": [6, 10],
        "long_pause_s": [20, 60],
        "group_pause_min": [4, 9],
        "assist_minutes_per_group": 6,
        "warmup_until": None,
        "cooldown_hours": 72,
    },
    "read": {
        "max_post_age_days": 14,
        "min_text_chars": 40,
        "feed_queries": [
            "CometGroupDiscussionRootSuccessQuery",
            "GroupsCometFeedRegularStoriesPaginationQuery",
        ],
        "dom_fallback_after_scrolls": 4,
    },
    "discovery": {
        "searches_per_day": 4,
        "search_pause_s": [60, 120],
        "scrolls_per_search": 3,
        "min_members": 2000,
        "group_scores_per_run": 10,
        "min_group_score": 60,
        "queries": [],
        "junk_keywords": [],
    },
    "lead": {"min_score": 50},
    "block_signals": {
        "url": ["/checkpoint/", "/two_step_verification/", "/login"],
        "text": ["temporarily blocked", "going too fast", "automated behavior",
                 "account restricted", "account has been disabled"],
        "graphql_codes": [368],
    },
}

AI_DEFAULTS: dict = {
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3:4b",
    "api_key": None,
    "max_tokens": 800,
    "temperature": 0.2,
    "timeout": 300,
    "json_mode": True,
    "extra_body": {"reasoning_effort": "none"},
}

GATE_DEFAULTS: dict = {"require_any": [], "skip_if_contains": []}

# Counts halved during warm-up. Pauses are left alone: slower is never riskier.
WARMUP_HALVED = ("groups_per_run", "runs_per_day", "page_loads_per_day",
                 "posts_per_group", "scrolls_per_group")


def _merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _as_date(value) -> dt.date | None:
    if value in (None, ""):
        return None
    # YAML already turns 2026-11-15 into a date; a quoted string does not.
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def load(path: Path = CONFIG_PATH, today: dt.date | None = None) -> dict:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"No config file at {path}. Copy config.example.yaml to config.yaml "
                         "and fill in your profile.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, raw.get("facebook"))

    # Top-level sections shared by every command.
    cfg["profile"] = raw.get("profile") or {}
    cfg["ai"] = _merge(AI_DEFAULTS, raw.get("ai"))
    cfg["gate"] = _merge(GATE_DEFAULTS, raw.get("gate"))
    shared_junk = (raw.get("filters") or {}).get("junk_keywords") or []
    cfg["discovery"]["junk_keywords"] = sorted(
        {w.lower() for w in shared_junk} | {w.lower() for w in cfg["discovery"]["junk_keywords"]}
    )

    # Relative paths are relative to the project root, not the shell's directory.
    for key in ("db_path", "profile_dir", "debug_dir", "cooldown_file"):
        value = Path(cfg[key]).expanduser()
        cfg[key] = value if value.is_absolute() else path.parent / value

    warmup = _as_date(cfg["pacing"]["warmup_until"])
    if warmup and (today or dt.date.today()) < warmup:
        for key in WARMUP_HALVED:
            cfg["pacing"][key] = max(1, cfg["pacing"][key] // 2)
        cfg["discovery"]["searches_per_day"] = max(1, cfg["discovery"]["searches_per_day"] // 2)
    return cfg
