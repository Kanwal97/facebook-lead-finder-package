"""The parts that are not about Facebook: the lead database, the keyword
gate, text hashing and the model calls.

Everything else in facebook/ goes through these functions, so swapping the
database or the model backend touches this one file.
"""
from __future__ import annotations

import hashlib
import re

import requests

from . import ai, store

PLATFORM = "facebook"

# Coaches and course sellers bragging about results: "my students made $57K",
# "$10k in 30 days", "$32.8k so far". Selling, not asking.
_BRAG_RE = re.compile(
    r"\b(my|our)\s+(students?|mentees?|clients?|members?|guys|community)\b.{0,80}\$\s?\d"
    r"|\$\s?\d[\d,.]*\s?k?\+?\s*(in|within|per|every|a|/)\s*(\d+\s*)?(days?|weeks?|months?|year|hours?|hrs)\b"
    r"|\$\s?\d[\d,.]*\s?k?\+?\s+(so far|total sales|yesterday|today|this week)\b",
    re.I | re.S)


def connect(cfg: dict):
    return store.connect(cfg["db_path"])


def text_hash(text: str) -> str:
    """Same hash for the same words, whatever the punctuation, case or spacing."""
    normalised = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return hashlib.sha1(normalised.encode()).hexdigest()


def passes_gate(text: str, cfg: dict) -> bool:
    """Cheap keyword gate so the slow local model only sees plausible requests."""
    low = text.lower()
    gate = cfg["gate"]
    if any(k.lower() in low for k in gate.get("skip_if_contains") or []):
        return False
    if _BRAG_RE.search(text):
        return False
    required = gate.get("require_any") or []
    return not required or any(k.lower() in low for k in required)


def prior_score(conn, digest: str) -> dict | None:
    """Earlier result for identical text: no model call for reposts."""
    return store.find_scored(conn, digest)


def model_ready(cfg: dict) -> str | None:
    """Why the model cannot be used right now, or None if it can."""
    try:
        ai.ping(cfg)
        return None
    except requests.ConnectionError:
        return f"no LLM server at {ai.settings(cfg)['base_url']} (is Ollama running?)"
    except Exception as exc:  # wrong model, auth, odd response
        return str(exc)


def score_post(text: str, context: str, cfg: dict) -> dict:
    """{'score', 'intent', 'reason', 'draft'}. Slow: about 45 s on a CPU-only laptop."""
    return ai.score_post(cfg, text, context)


def score_group(name: str, about: str, cfg: dict) -> dict:
    """{'score', 'reason'}."""
    return ai.score_group(cfg, name, about)


def save_lead(conn, *, digest: str, post, group_name: str, result: dict) -> bool:
    return store.save_lead(conn, digest=digest, post=post, group_name=group_name,
                           result=result, platform=PLATFORM)


def save_rejected(conn, *, digest: str, post, group_name: str, result: dict) -> None:
    store.save_rejected(conn, digest=digest, post=post, result=result)
