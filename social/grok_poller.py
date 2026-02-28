"""Grok API polling engine — fetches and parses smart money X account tweets.

Polls 4 smart money X accounts via the Grok Responses API (api.x.ai/v1/responses)
with x_search tool and stores parsed signals in the x_intelligence table.

Accounts:
    @StalkHQ        — KOL accumulation alerts, cabal finder       (every 30min)
    @kolscan_io     — Real-time KOL transactions, PnL leaderboard (every 30min)
    @SunFlowSolana  — Whale flow alerts with entry timing         (every 30min)
    @gmaborabot     — Smart money trending, top traders            (every 60min)
"""

import hashlib
import json
import time

import requests

from config import GROK_API_KEY, get_logger
from db.connection import execute, execute_one
from monitoring.degraded import record_api_call
from social.smart_money_parsers import PARSER_MAP

log = get_logger("social.grok_poller")

GROK_RESPONSES_URL = "https://api.x.ai/v1/responses"
GROK_MODEL = "grok-4-1-fast"

SMART_MONEY_ACCOUNTS = [
    {"handle": "StalkHQ",       "interval_min": 30, "parser": "stalk"},
    {"handle": "kolscan_io",    "interval_min": 30, "parser": "kolscan"},
    {"handle": "SunFlowSolana", "interval_min": 30, "parser": "sunflow"},
    {"handle": "gmaborabot",    "interval_min": 60, "parser": "gmgn"},
]

# In-process last-poll timestamps per handle for interval enforcement
_last_poll: dict[str, float] = {}


def _grok_fetch_tweets(handle: str, interval_min: int = 30,
                        max_tweets: int = 10) -> list[str]:
    """Call Grok Responses API with x_search tool to fetch recent tweets.

    Uses the /v1/responses endpoint with x_search tool and allowed_x_handles
    to search for recent posts from a specific X account.

    Returns list of tweet text strings, empty on error.
    """
    if not GROK_API_KEY:
        log.debug("GROK_API_KEY not set — skipping poll for @%s", handle)
        return []

    user_prompt = (
        f"Find the {max_tweets} most recent posts from @{handle} on X. "
        f'Return ONLY a JSON object: {{"tweets": ["full text of post 1", ...]}}. '
        f'If no posts found, return {{"tweets": []}}. '
        f"Include each post's full text verbatim including dollar signs, "
        f"contract addresses, and numbers. Do not summarize or paraphrase."
    )

    try:
        resp = requests.post(
            GROK_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {GROK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROK_MODEL,
                "instructions": (
                    "You are a data extraction assistant. "
                    "Return only valid JSON. No prose, no markdown fences."
                ),
                "input": [
                    {"role": "user", "content": user_prompt},
                ],
                "tools": [
                    {
                        "type": "x_search",
                        "allowed_x_handles": [handle],
                    }
                ],
                "temperature": 0,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        record_api_call("grok", True)

        # Responses API returns output as a list of message items
        output = data.get("output", [])
        content = ""
        for item in output:
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")

        if not content:
            log.debug("Grok returned no text content for @%s", handle)
            return []

        # Strip markdown fences if Grok adds them
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)

        parsed = json.loads(content)
        tweets = parsed.get("tweets", [])

        if not isinstance(tweets, list):
            log.warning("Grok returned non-list tweets for @%s: %s",
                         handle, type(tweets).__name__)
            return []

        log.info("Grok returned %d tweets for @%s", len(tweets), handle)
        return [str(t) for t in tweets if t]

    except json.JSONDecodeError as e:
        log.error("Grok JSON parse error for @%s: %s (content: %.200s)",
                   handle, e, content if 'content' in dir() else "N/A")
        record_api_call("grok", False)
        return []
    except requests.RequestException as e:
        log.error("Grok API request error for @%s: %s", handle, e)
        record_api_call("grok", False)
        return []
    except Exception as e:
        log.error("Grok API error for @%s: %s", handle, e)
        record_api_call("grok", False)
        return []


def _compute_tweet_id(handle: str, tweet_text: str) -> str:
    """Generate a deterministic dedup ID from handle + tweet text."""
    raw = f"{handle}:{tweet_text[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _is_already_processed(tweet_id: str) -> bool:
    """Check if a tweet_id exists in x_intelligence table."""
    try:
        row = execute_one(
            "SELECT 1 FROM x_intelligence WHERE tweet_id = %s", (tweet_id,))
        return row is not None
    except Exception:
        return False


def _store_signal(handle: str, tweet_id: str, tweet_text: str,
                   parsed: dict) -> bool:
    """Store a parsed signal in x_intelligence. Returns True if new row inserted."""
    try:
        execute(
            """INSERT INTO x_intelligence
               (source_handle, tweet_id, tweet_text, parsed_type,
                token_address, token_symbol, wallet_address, amount_usd,
                signal_strength, raw_data)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tweet_id) DO NOTHING""",
            (f"@{handle}", tweet_id, tweet_text,
             parsed.get("parsed_type"),
             parsed.get("token_address"),
             parsed.get("token_symbol"),
             parsed.get("wallet_address"),
             parsed.get("amount_usd"),
             parsed.get("signal_strength"),
             json.dumps(parsed.get("extra", {}))),
        )
        return True
    except Exception as e:
        log.error("Failed to store signal for @%s: %s", handle, e)
        return False


def _route_signal_alert(handle: str, parsed: dict, tweet_text: str):
    """Route a parsed signal through the severity system."""
    strength = parsed.get("signal_strength", "weak")
    parsed_type = parsed.get("parsed_type", "info")
    symbol = parsed.get("token_symbol") or "?"

    try:
        from telegram_bot.severity import route_alert

        if strength == "strong":
            # Map parsed_type to alert_type for severity classification
            if parsed_type in ("accumulation", "cabal_alert"):
                alert_type = "smart_money_strong_accumulation"
            elif parsed_type == "whale_flow":
                alert_type = "smart_money_strong_whale_flow"
            elif parsed_type == "multi_kol_buy":
                alert_type = "smart_money_multi_kol"
            else:
                alert_type = "smart_money_strong_accumulation"

            amount_str = f"${parsed['amount_usd']:,.0f}" if parsed.get("amount_usd") else ""
            extra_info = ""
            if parsed.get("extra", {}).get("wallet_count"):
                extra_info = f" — {parsed['extra']['wallet_count']} wallets"
            if amount_str:
                extra_info += f" ({amount_str})"

            msg = (f"📡 <b>X SMART MONEY</b>\n"
                   f"@{handle} — {parsed_type}\n"
                   f"🪙 ${symbol}{extra_info}\n"
                   f"Strength: {strength}")
            route_alert(2, msg)

        elif strength == "medium":
            amount_str = f" ${parsed['amount_usd']:,.0f}" if parsed.get("amount_usd") else ""
            msg = f"@{handle}: {parsed_type} ${symbol}{amount_str} [{strength}]"
            route_alert(3, msg)

        else:
            # Weak = Tier 4 (logged only)
            log.debug("Tier 4 (weak): @%s %s $%s", handle, parsed_type, symbol)

    except Exception as e:
        log.error("Alert routing failed for @%s: %s", handle, e)


def poll_account(handle_config: dict) -> int:
    """Poll a single smart money account. Returns count of new signals."""
    handle = handle_config["handle"]
    interval_min = handle_config["interval_min"]
    parser_key = handle_config["parser"]

    # Interval gate
    now = time.time()
    last = _last_poll.get(handle, 0)
    if now - last < interval_min * 60:
        log.debug("Skipping @%s — polled %.0fs ago (interval: %dmin)",
                   handle, now - last, interval_min)
        return 0

    parser_fn = PARSER_MAP.get(parser_key)
    if not parser_fn:
        log.error("No parser found for key '%s' (handle: @%s)", parser_key, handle)
        return 0

    tweets = _grok_fetch_tweets(handle, interval_min)
    _last_poll[handle] = now

    if not tweets:
        return 0

    new_count = 0
    for tweet_text in tweets:
        tweet_id = _compute_tweet_id(handle, tweet_text)

        if _is_already_processed(tweet_id):
            continue

        try:
            parsed = parser_fn(tweet_text)
        except Exception as e:
            log.error("Parser error for @%s tweet: %s", handle, e)
            continue

        if _store_signal(handle, tweet_id, tweet_text, parsed):
            _route_signal_alert(handle, parsed, tweet_text)
            new_count += 1
            log.info("New signal: @%s %s $%s [%s]",
                     handle, parsed.get("parsed_type"),
                     parsed.get("token_symbol") or "?",
                     parsed.get("signal_strength"))

    return new_count


def run_smart_money_poll() -> dict:
    """Main entry point: poll all 4 smart money accounts.

    Returns:
        {"total_signals": int, "per_account": {handle: count},
         "errors": [handle_that_failed]}
    """
    if not GROK_API_KEY:
        log.warning("GROK_API_KEY not set — smart money polling disabled")
        return {"total_signals": 0, "per_account": {}, "errors": []}

    log.info("=== Smart money X poll starting ===")
    total = 0
    per_account = {}
    errors = []

    for config in SMART_MONEY_ACCOUNTS:
        handle = config["handle"]
        try:
            count = poll_account(config)
            per_account[handle] = count
            total += count
        except Exception as e:
            log.error("Poll failed for @%s: %s", handle, e)
            errors.append(handle)
            per_account[handle] = 0

        # 2s sleep between accounts to avoid rate-limiting
        time.sleep(2)

    log.info("Smart money poll complete: %d new signals (%s)",
             total, ", ".join(f"@{h}={c}" for h, c in per_account.items()))

    return {"total_signals": total, "per_account": per_account, "errors": errors}


def get_recent_x_signals(hours: int = 4,
                          min_strength: str = "medium") -> list[dict]:
    """Query recent x_intelligence rows for Huoyan pulse or API consumers.

    Args:
        hours: Lookback window (default 4 to match pulse cadence)
        min_strength: Minimum signal strength filter

    Returns:
        List of signal dicts, ordered by detected_at DESC, max 8 rows.
    """
    strength_levels = {"weak": 0, "medium": 1, "strong": 2}
    min_level = strength_levels.get(min_strength, 1)

    # Build strength filter
    allowed = [s for s, l in strength_levels.items() if l >= min_level]
    if not allowed:
        allowed = ["medium", "strong"]

    placeholders = ",".join(["%s"] * len(allowed))

    try:
        rows = execute(
            f"""SELECT source_handle, parsed_type, token_symbol, token_address,
                       amount_usd, signal_strength, raw_data, detected_at
                FROM x_intelligence
                WHERE detected_at > NOW() - INTERVAL '{int(hours)} hours'
                  AND signal_strength IN ({placeholders})
                ORDER BY detected_at DESC
                LIMIT 8""",
            tuple(allowed),
            fetch=True,
        )

        return [
            {
                "source_handle": r[0],
                "parsed_type": r[1],
                "token_symbol": r[2],
                "token_address": r[3],
                "amount_usd": float(r[4]) if r[4] else None,
                "signal_strength": r[5],
                "raw_data": r[6],
                "detected_at": r[7],
            }
            for r in (rows or [])
        ]
    except Exception as e:
        log.error("get_recent_x_signals failed: %s", e)
        return []


def get_x_intelligence_summary(token_address: str | None = None) -> dict:
    """Get aggregated X intelligence summary for a token or overall.

    Used by health score Social signal and /xintel command.
    """
    try:
        if token_address:
            rows = execute(
                """SELECT COUNT(*) as total,
                          COUNT(*) FILTER (WHERE signal_strength = 'strong') as strong,
                          ARRAY_AGG(DISTINCT source_handle) as sources,
                          MAX(detected_at) as latest
                   FROM x_intelligence
                   WHERE token_address = %s
                     AND detected_at > NOW() - INTERVAL '24 hours'""",
                (token_address,),
                fetch=True,
            )
        else:
            rows = execute(
                """SELECT COUNT(*) as total,
                          COUNT(*) FILTER (WHERE signal_strength = 'strong') as strong,
                          ARRAY_AGG(DISTINCT source_handle) as sources,
                          MAX(detected_at) as latest
                   FROM x_intelligence
                   WHERE detected_at > NOW() - INTERVAL '24 hours'""",
                fetch=True,
            )

        if rows and rows[0]:
            r = rows[0]
            return {
                "signal_count": r[0] or 0,
                "strong_signals": r[1] or 0,
                "sources": [s for s in (r[2] or []) if s],
                "latest_detected": str(r[3]) if r[3] else None,
            }
    except Exception as e:
        log.error("get_x_intelligence_summary failed: %s", e)

    return {"signal_count": 0, "strong_signals": 0, "sources": [], "latest_detected": None}
