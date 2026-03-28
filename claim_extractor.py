"""claim_extractor.py — Use Haiku to auto-extract testable predictions from YouTube summaries."""

import json
import os
import requests
from datetime import datetime, timezone

from config import get_logger
from db.connection import execute, execute_one

log = get_logger("claim_extractor")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_daily = {"date": "", "count": 0}
MAX_DAILY = 100


def _should_extract():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily["date"] != today:
        _daily["date"] = today
        _daily["count"] = 0
    return _daily["count"] < MAX_DAILY


def extract_and_store(summary_text: str, voice_name: str, video_date) -> int:
    """Extract claims from summary via Haiku and store in voice_claims. Returns count stored."""
    if not ANTHROPIC_API_KEY or not _should_extract():
        return 0
    if not summary_text or len(summary_text) < 100:
        return 0

    prompt = (
        "Extract SPECIFIC testable predictions from this video summary.\n"
        "Only extract claims with concrete numbers, dates, or clear directional calls.\n"
        "Do NOT extract vague opinions like \"I'm bullish\" or \"markets uncertain\".\n\n"
        "Return ONLY a JSON array. If no specific claims, return [].\n\n"
        "Each claim: {\"claim\": \"max 100 chars\", \"target_token\": \"SYM\" or null, \"direction\": \"bullish\"|\"bearish\"|\"neutral\"}\n\n"
        "SUMMARY BY %s:\n%s\n\nJSON array:" % (voice_name, summary_text[:3000])
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning("Haiku API error: %d", resp.status_code)
            return 0

        text = resp.json()["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()

        claims = json.loads(text)
        if not isinstance(claims, list):
            return 0

        _daily["count"] += 1
    except Exception as e:
        log.warning("Claim extraction failed: %s", e)
        return 0

    # Store claims
    stored = 0
    date_str = str(video_date)[:10] if video_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    voice = voice_name.lower().strip()

    for c in claims:
        claim_text = (c.get("claim") or "").strip()
        target = c.get("target_token")
        direction = c.get("direction", "neutral")
        if not claim_text or len(claim_text) < 5:
            continue
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"

        existing = execute_one(
            "SELECT id FROM voice_claims WHERE voice = %s AND claim = %s",
            (voice, claim_text))
        if existing:
            execute(
                "UPDATE voice_claims SET times_repeated = times_repeated + 1, last_seen = CURRENT_DATE WHERE id = %s",
                (existing[0],))
        else:
            execute(
                """INSERT INTO voice_claims (voice, claim, target_token, direction, first_seen, last_seen)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (voice, claim_text, target, direction, date_str, date_str))
            # Update voice_accuracy
            execute(
                """INSERT INTO voice_accuracy (voice, total_claims) VALUES (%s, 1)
                   ON CONFLICT (voice) DO UPDATE SET total_claims = voice_accuracy.total_claims + 1""",
                (voice,))
            stored += 1

    if stored:
        log.info("Claims: %d new from %s (daily: %d/%d)", stored, voice_name, _daily["count"], MAX_DAILY)
    return stored
