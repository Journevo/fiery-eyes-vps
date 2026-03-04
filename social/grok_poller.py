"""Grok API polling engine — fetches and parses smart money X account tweets.

Polls smart money X accounts via the Grok Responses API (api.x.ai/v1/responses)
with x_search tool and stores parsed signals in the x_intelligence table.

Tiered polling (181 accounts from grok_monitor_config.csv):
  - 4 specialized accounts: individual calls, 30min interval (~192 calls/day)
  - 74 HIGH generic: batches of 10, 30min interval (~384 calls/day)
  - 97 MEDIUM generic: batches of 15, 2hr interval (~84 calls/day)
  - Total: ~660 calls/day, ~20K/month, well under $3/month budget
"""

import csv
import hashlib
import json
import os
import time

import requests

from config import GROK_API_KEY, get_logger
from db.connection import execute, execute_one
from monitoring.degraded import record_api_call
from social.smart_money_parsers import PARSER_MAP

log = get_logger("social.grok_poller")

GROK_RESPONSES_URL = "https://api.x.ai/v1/responses"
GROK_MODEL = "grok-4-1-fast"


class GrokRateLimited(Exception):
    """Raised when Grok API returns 429 — signals callers to stop polling."""
    pass

# Original 4 specialized accounts (backward compat)
SMART_MONEY_ACCOUNTS = [
    {"handle": "StalkHQ",       "interval_min": 30, "parser": "stalk"},
    {"handle": "kolscan_io",    "interval_min": 30, "parser": "kolscan"},
    {"handle": "SunFlowSolana", "interval_min": 30, "parser": "sunflow"},
    {"handle": "gmaborabot",    "interval_min": 60, "parser": "gmgn"},
]

# Specialized handles → their parser keys (all others use "generic")
_SPECIALIZED_PARSERS = {
    "StalkHQ": "stalk",
    "kolscan_io": "kolscan",
    "SunFlowSolana": "sunflow",
    "gmaborabot": "gmgn",
}

# In-process last-poll timestamps per handle/batch for interval enforcement
_last_poll: dict[str, float] = {}

# CSV config path
_CSV_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "grok_monitor_config.csv")


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
        if '429' in str(e):
            log.warning("Grok rate-limited for @%s — skipping rest of cycle", handle)
            raise GrokRateLimited(str(e))
        log.error("Grok API request error for @%s: %s", handle, e)
        record_api_call("grok", False)
        return []
    except GrokRateLimited:
        raise
    except Exception as e:
        log.error("Grok API error for @%s: %s", handle, e)
        record_api_call("grok", False)
        return []


def _load_monitor_config() -> dict:
    """Load tiered account config from grok_monitor_config.csv.

    Returns:
        {"high": [handle, ...], "medium": [handle, ...]}
    Excludes the 4 specialized accounts (they're polled individually).
    """
    high = []
    medium = []

    try:
        with open(_CSV_CONFIG_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                handle_raw = row.get("handle", "").strip()
                if not handle_raw:
                    continue
                # Strip leading @ if present
                handle = handle_raw.lstrip("@")
                priority = (row.get("priority") or "").strip().upper()

                # Skip specialized accounts — polled individually
                if handle in _SPECIALIZED_PARSERS:
                    continue

                if priority == "HIGH":
                    high.append(handle)
                else:
                    medium.append(handle)

        log.info("Loaded monitor config: %d HIGH + %d MEDIUM generic accounts",
                 len(high), len(medium))
    except FileNotFoundError:
        log.warning("CSV config not found at %s — falling back to specialized only",
                    _CSV_CONFIG_PATH)
    except Exception as e:
        log.error("Failed to load monitor config: %s — falling back", e)

    return {"high": high, "medium": medium}


def _grok_fetch_tweets_batch(handles: list[str], interval_min: int = 30,
                              max_tweets: int = 5) -> list[dict]:
    """Call Grok Responses API with multiple handles in allowed_x_handles.

    Returns list of {"handle": str, "text": str} dicts, empty on error.
    Uses a longer timeout (90s) since batch queries take more time.
    """
    if not GROK_API_KEY or not handles:
        return []

    handles_str = ", ".join(f"@{h}" for h in handles)
    user_prompt = (
        f"Find the most recent posts from these X accounts: {handles_str}. "
        f"Return up to {max_tweets} posts per account. "
        f'Return ONLY a JSON object: {{"tweets": [{{"handle": "account_name", "text": "full post text"}}, ...]}}. '
        f'If no posts found, return {{"tweets": []}}. '
        f"Include each post's full text verbatim including dollar signs, "
        f"contract addresses, and numbers. Do not summarize or paraphrase. "
        f"The handle field should NOT include the @ symbol."
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
                        "allowed_x_handles": handles,
                    }
                ],
                "temperature": 0,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        record_api_call("grok", True)

        # Extract text content from response
        output = data.get("output", [])
        content = ""
        for item in output:
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")

        if not content:
            log.debug("Grok batch returned no content for %d handles", len(handles))
            return []

        # Strip markdown fences
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)

        parsed = json.loads(content)
        tweets = parsed.get("tweets", [])

        if not isinstance(tweets, list):
            log.warning("Grok batch returned non-list tweets: %s", type(tweets).__name__)
            return []

        # Normalize: ensure each entry has handle + text
        results = []
        for t in tweets:
            if isinstance(t, dict) and t.get("text"):
                handle = t.get("handle", "").lstrip("@")
                results.append({"handle": handle, "text": str(t["text"])})
            elif isinstance(t, str) and t:
                # Fallback: if Grok returns plain strings, attribute to first handle
                results.append({"handle": handles[0], "text": t})

        log.info("Grok batch returned %d tweets for %d handles", len(results), len(handles))
        return results

    except json.JSONDecodeError as e:
        log.error("Grok batch JSON parse error: %s (content: %.200s)",
                  e, content if 'content' in dir() else "N/A")
        record_api_call("grok", False)
        return []
    except requests.RequestException as e:
        if '429' in str(e):
            log.warning("Grok batch rate-limited — skipping rest of cycle")
            raise GrokRateLimited(str(e))
        log.error("Grok batch API request error: %s", e)
        record_api_call("grok", False)
        return []
    except GrokRateLimited:
        raise
    except Exception as e:
        log.error("Grok batch API error: %s", e)
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
    # Resolve symbol → address if missing
    if parsed.get("token_symbol") and not parsed.get("token_address"):
        try:
            from social.token_resolver import resolve_token
            result = resolve_token(parsed["token_symbol"])
            if result:
                parsed["token_address"] = result["address"]
        except Exception as e:
            log.debug("Token resolve failed for $%s: %s",
                      parsed.get("token_symbol"), e)

    # Compute signal category (macro/ecosystem/risk/infra/meme/info)
    try:
        from social.smart_money_parsers import categorize_signal
        category = categorize_signal(tweet_text, parsed)
    except Exception:
        category = "info"

    try:
        execute(
            """INSERT INTO x_intelligence
               (source_handle, tweet_id, tweet_text, parsed_type,
                token_address, token_symbol, wallet_address, amount_usd,
                signal_strength, raw_data, signal_category)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tweet_id) DO NOTHING""",
            (f"@{handle}", tweet_id, tweet_text,
             parsed.get("parsed_type"),
             parsed.get("token_address"),
             parsed.get("token_symbol"),
             parsed.get("wallet_address"),
             parsed.get("amount_usd"),
             parsed.get("signal_strength"),
             json.dumps(parsed.get("extra", {})),
             category),
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

            ca = parsed.get("token_address") or ""
            ca_line = f"\n📋 CA: <code>{ca}</code>" if ca else ""
            msg = (f"📡 <b>X SMART MONEY</b>\n"
                   f"@{handle} — {parsed_type}\n"
                   f"🪙 ${symbol}{extra_info}\n"
                   f"Strength: {strength}{ca_line}")
            route_alert(2, msg)

        elif strength == "medium":
            amount_str = f" ${parsed['amount_usd']:,.0f}" if parsed.get("amount_usd") else ""
            ca = parsed.get("token_address") or ""
            ca_str = f" | CA: {ca}" if ca else ""
            msg = f"@{handle}: {parsed_type} ${symbol}{amount_str} [{strength}]{ca_str}"
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


def _poll_specialized_accounts() -> dict:
    """Poll the 4 original specialized accounts individually.

    Returns:
        {"total_signals": int, "per_account": {handle: count}, "errors": [str]}
    """
    total = 0
    per_account = {}
    errors = []

    for config in SMART_MONEY_ACCOUNTS:
        handle = config["handle"]
        try:
            count = poll_account(config)
            per_account[handle] = count
            total += count
        except GrokRateLimited:
            log.warning("Grok rate-limited — aborting specialized poll cycle")
            return {"total_signals": total, "per_account": per_account,
                    "errors": errors, "rate_limited": True}
        except Exception as e:
            log.error("Poll failed for @%s: %s", handle, e)
            errors.append(handle)
            per_account[handle] = 0

        # 2s sleep between accounts to avoid rate-limiting
        time.sleep(2)

    return {"total_signals": total, "per_account": per_account, "errors": errors}


def _poll_batch_tier(accounts: list[str], batch_size: int) -> dict:
    """Poll generic accounts in batches via _grok_fetch_tweets_batch.

    Returns:
        {"total_signals": int, "per_account": {handle: count}, "errors": [str]}
    """
    total = 0
    per_account = {}
    errors = []

    # Interval gate for the batch tier (keyed by tier name)
    batch_key = f"batch_{batch_size}_{len(accounts)}"
    now = time.time()

    for i in range(0, len(accounts), batch_size):
        batch = accounts[i:i + batch_size]
        batch_id = f"batch_{i // batch_size}"

        try:
            tweet_items = _grok_fetch_tweets_batch(batch, max_tweets=5)

            for item in tweet_items:
                handle = item["handle"]
                tweet_text = item["text"]
                tweet_id = _compute_tweet_id(handle, tweet_text)

                if _is_already_processed(tweet_id):
                    continue

                # Use specialized parser if available, else generic
                parser_key = _SPECIALIZED_PARSERS.get(handle, "generic")
                parser_fn = PARSER_MAP.get(parser_key)
                if not parser_fn:
                    parser_fn = PARSER_MAP.get("generic")

                try:
                    parsed = parser_fn(tweet_text)
                except Exception as e:
                    log.error("Parser error for @%s tweet: %s", handle, e)
                    continue

                if _store_signal(handle, tweet_id, tweet_text, parsed):
                    _route_signal_alert(handle, parsed, tweet_text)
                    per_account[handle] = per_account.get(handle, 0) + 1
                    total += 1
                    log.info("New signal: @%s %s $%s [%s]",
                             handle, parsed.get("parsed_type"),
                             parsed.get("token_symbol") or "?",
                             parsed.get("signal_strength"))

        except GrokRateLimited:
            log.warning("Grok rate-limited — aborting batch poll cycle")
            break
        except Exception as e:
            log.error("Batch poll failed (%s): %s", batch_id, e)
            errors.extend(batch)

        # 3s sleep between batches to avoid rate-limiting
        time.sleep(3)

    return {"total_signals": total, "per_account": per_account, "errors": errors}


def run_smart_money_poll_high() -> dict:
    """Poll HIGH tier: 4 specialized accounts + HIGH generic (batches of 10).

    Called every 30 minutes.

    Returns:
        {"total_signals": int, "per_account": {handle: count}, "errors": [str]}
    """
    if not GROK_API_KEY:
        log.warning("GROK_API_KEY not set — smart money polling disabled")
        return {"total_signals": 0, "per_account": {}, "errors": []}

    log.info("=== Smart money HIGH tier poll starting ===")

    # 1. Poll specialized accounts individually
    result = _poll_specialized_accounts()

    # 2. Poll HIGH generic accounts in batches (skip if already rate-limited)
    if result.get("rate_limited"):
        log.warning("Skipping HIGH generic poll — Grok rate-limited")
    else:
        config = _load_monitor_config()
        high_accounts = config.get("high", [])

        if high_accounts:
            batch_result = _poll_batch_tier(high_accounts, batch_size=10)
            result["total_signals"] += batch_result["total_signals"]
            result["per_account"].update(batch_result["per_account"])
            result["errors"].extend(batch_result["errors"])

    log.info("Smart money HIGH tier poll complete: %d new signals",
             result["total_signals"])

    return result


def run_smart_money_poll_medium() -> dict:
    """Poll MEDIUM tier: MEDIUM generic accounts only (batches of 15).

    Called every 2 hours.

    Returns:
        {"total_signals": int, "per_account": {handle: count}, "errors": [str]}
    """
    if not GROK_API_KEY:
        log.warning("GROK_API_KEY not set — smart money polling disabled")
        return {"total_signals": 0, "per_account": {}, "errors": []}

    log.info("=== Smart money MEDIUM tier poll starting ===")

    config = _load_monitor_config()
    medium_accounts = config.get("medium", [])

    if not medium_accounts:
        log.info("No MEDIUM tier accounts to poll")
        return {"total_signals": 0, "per_account": {}, "errors": []}

    result = _poll_batch_tier(medium_accounts, batch_size=15)

    log.info("Smart money MEDIUM tier poll complete: %d new signals (%d accounts)",
             result["total_signals"], len(medium_accounts))

    return result


def run_smart_money_poll() -> dict:
    """Backward-compatible entry point: calls run_smart_money_poll_high().

    Returns:
        {"total_signals": int, "per_account": {handle: count}, "errors": [str]}
    """
    return run_smart_money_poll_high()


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
                       amount_usd, signal_strength, raw_data, detected_at,
                       COALESCE(signal_category, 'info') as signal_category
                FROM x_intelligence
                WHERE detected_at > NOW() - INTERVAL '{int(hours)} hours'
                  AND signal_strength IN ({placeholders})
                ORDER BY detected_at DESC
                LIMIT 15""",
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
                "signal_category": r[8],
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
