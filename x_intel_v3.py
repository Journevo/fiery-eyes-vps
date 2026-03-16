"""X Intelligence v3 — Grok polls 173 accounts, Haiku summarises.

Flow:
1. Grok polls all accounts as before (raw tweets stored in x_intelligence)
2. Every 4h, batch ALL tweets from x_intelligence into ONE Haiku call
3. Haiku produces a research briefing grouped by watchlist token
4. ALSO: 3-5 targeted Grok research queries for broader context
5. Combined briefing sent to Telegram

Cost: ~$0.005/Haiku batch × 6/day + ~$0.02 Grok queries = ~$1/month total
"""

import json
import time
import requests
import anthropic
from datetime import datetime, timezone
from config import GROK_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("x_intel")

GROK_URL = "https://api.x.ai/v1/responses"
GROK_MODEL = "grok-4-1-fast"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

WATCHLIST = {"BTC", "SOL", "SUI", "HYPE", "RENDER", "JUP", "BONK", "PUMP", "PENGU", "FARTCOIN", "DEEP"}

# Targeted research queries for extra context beyond account polling
RESEARCH_QUERIES = [
    "Latest whale movements and large buys/sells for RENDER HYPE JUP SOL SUI BONK in the last 4 hours",
    "Any major news affecting Solana ecosystem tokens or Bitcoin in the last 4 hours",
]

_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _get_recent_tweets(hours: int = 4) -> str:
    """Get all tweets from x_intelligence DB from last N hours, formatted for Haiku."""
    try:
        rows = execute("""
            SELECT source_handle, token_symbol, parsed_type, tweet_text, amount_usd,
                   signal_strength
            FROM x_intelligence
            WHERE detected_at > NOW() - INTERVAL '%s hours'
            ORDER BY detected_at DESC
            LIMIT 200
        """ % int(hours), fetch=True)

        if not rows:
            return ""

        lines = []
        seen_texts = set()
        for source, symbol, ptype, text, amount, strength in rows:
            # Deduplicate by tweet text (first 100 chars)
            text_key = (text or "")[:100]
            if text_key in seen_texts:
                continue
            seen_texts.add(text_key)

            tweet_text = (text or "").strip()
            if not tweet_text or len(tweet_text) < 20:
                continue

            lines.append("%s: %s" % (source or "unknown", tweet_text[:300]))

        return "\n\n".join(lines[:150])

    except Exception as e:
        log.error("Failed to get recent tweets: %s", e)
        return ""


def _grok_search(query: str) -> str:
    """Run a Grok x_search query for broader context."""
    if not GROK_API_KEY:
        return ""
    try:
        resp = requests.post(
            GROK_URL,
            headers={"Authorization": "Bearer " + GROK_API_KEY, "Content-Type": "application/json"},
            json={
                "model": GROK_MODEL,
                "instructions": "You are a crypto research assistant. Return detailed, specific information with numbers and source attribution.",
                "input": [{"role": "user", "content": query}],
                "tools": [{"type": "x_search"}],
                "temperature": 0,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()

        from monitoring.degraded import record_api_call
        record_api_call("grok", True)

        content = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")
        return content.strip()
    except Exception as e:
        log.error("Grok research query failed: %s", e)
        from monitoring.degraded import record_api_call
        record_api_call("grok", False)
        return ""


def _haiku_briefing(tweet_dump: str, grok_research: str) -> str | None:
    """Send all collected intelligence to Haiku for a research briefing."""
    if not ANTHROPIC_API_KEY:
        return None

    # Get SunFlow context
    sf_context = ""
    try:
        rows = execute("""
            SELECT token, conviction_score, net_flow_usd, timeframes_present
            FROM sunflow_conviction WHERE is_watchlist = TRUE
            ORDER BY conviction_score DESC
        """, fetch=True)
        if rows:
            sf_lines = ["%s: conviction %s, %s/4 TF" % (r[0], r[1], r[3]) for r in rows]
            sf_context = "\nSunFlow whale conviction: " + " | ".join(sf_lines)
    except Exception:
        pass

    # Build combined intel block
    intel_parts = []
    if tweet_dump:
        intel_parts.append("=== ACCOUNT TWEETS (last 4h) ===\n" + tweet_dump)
    if grok_research:
        intel_parts.append("=== RESEARCH QUERIES ===\n" + grok_research)

    combined = "\n\n".join(intel_parts)
    if not combined.strip():
        return None

    prompt = """You are a crypto research analyst writing a 4-hourly intelligence briefing.
Your reader holds positions in JUP, HYPE, RENDER, BONK on Solana, with BTC/SOL as benchmarks.

WATCHLIST: BTC, SOL, SUI, HYPE, RENDER, JUP, BONK, PUMP, PENGU, FARTCOIN, DEEP%s

Below are tweets from 173 crypto accounts plus research queries from the last 4 hours.
Read ALL of them. Produce a research briefing:

1. WATCHLIST: For each watchlist token mentioned, summarise what was said, by whom, with specific numbers. Flag BULLISH/BEARISH/NEUTRAL. Only include tokens with actual new information.

2. MACRO: Any macro or geopolitical developments affecting crypto.

3. SMART MONEY: Any whale movements, KOL positioning changes, or accumulation patterns.

4. NON-WATCHLIST: Any token getting attention from 3+ accounts NOT on watchlist. Flag for research.

5. NARRATIVES: Any emerging or dying narratives across multiple accounts.

Rules:
- Be specific — cite numbers, sources, claims
- If data is thin for a token, skip it
- Under 500 words total
- Skip tokens under $5M MCap unless on watchlist
- If a tweet mentions MCap (e.g. "$198M MCap"), that's NOT a buy amount
- Attribution format: "aixbt notes..." or "lookonchain reports..."

INTELLIGENCE:
%s""" % (sf_context, combined[:12000])

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        cost = (response.usage.input_tokens * 1 / 1e6) + (response.usage.output_tokens * 5 / 1e6)
        log.info("Haiku briefing: %d in / %d out, $%.4f",
                 response.usage.input_tokens, response.usage.output_tokens, cost)
        return text.strip()
    except Exception as e:
        log.error("Haiku briefing failed: %s", e)
        return None


def _store_briefing(raw_intel: str, summary: str):
    """Store briefing in DB."""
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS x_briefings (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                raw_intel TEXT,
                summary TEXT,
                word_count INTEGER
            )
        """)
        execute("INSERT INTO x_briefings (raw_intel, summary, word_count) VALUES (%s, %s, %s)",
                (raw_intel[:10000], summary, len(summary.split())))
    except Exception as e:
        log.error("Store briefing failed: %s", e)


def _send_telegram(text: str):
    """Send with persistent keyboard."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    max_len = 4000
    chunks = [text] if len(text) <= max_len else []
    if not chunks:
        current = ""
        for para in text.split("\n\n"):
            if current and len(current) + len(para) + 2 > max_len:
                chunks.append(current)
                current = ""
            current = current + "\n\n" + para if current else para
        if current:
            chunks.append(current)
    for chunk in chunks:
        try:
            requests.post(
                "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True, "reply_markup": _KEYBOARD_JSON},
                timeout=15)
        except Exception as e:
            log.error("Telegram send error: %s", e)


def run_x_intel_batch(send_to_telegram: bool = True) -> dict:
    """Full X Intel pipeline: DB tweets + Grok research → Haiku → Telegram."""
    log.info("=== X Intel Briefing starting ===")

    # 1. Get tweets from DB (collected by Grok polling)
    tweet_dump = _get_recent_tweets(hours=4)
    log.info("Tweet dump: %d chars from DB", len(tweet_dump))

    # 2. Targeted Grok research queries
    research_parts = []
    for i, query in enumerate(RESEARCH_QUERIES):
        log.info("Research query %d/%d...", i + 1, len(RESEARCH_QUERIES))
        result = _grok_search(query)
        if result:
            research_parts.append(result)
        time.sleep(2)
    grok_research = "\n\n".join(research_parts)
    log.info("Grok research: %d chars", len(grok_research))

    if not tweet_dump and not grok_research:
        log.warning("No intelligence available — skipping")
        return {"status": "empty"}

    # 3. Haiku summarisation
    log.info("Running Haiku briefing...")
    summary = _haiku_briefing(tweet_dump, grok_research)

    if not summary:
        log.error("Haiku failed")
        return {"status": "haiku_failed"}

    # 4. Format and send
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    message = "\U0001f4e1 <b>X INTEL \u2014 %s</b>\n\n%s" % (now, summary)

    _store_briefing(tweet_dump[:5000] + "\n---\n" + grok_research[:5000], summary)

    if send_to_telegram:
        _send_telegram(message)
        log.info("X Intel briefing sent (%d words)", len(summary.split()))

    return {"status": "ok", "tweet_chars": len(tweet_dump),
            "research_chars": len(grok_research), "summary_words": len(summary.split())}


def get_latest_briefing() -> str | None:
    """Get most recent briefing from DB."""
    try:
        row = execute("SELECT summary FROM x_briefings ORDER BY created_at DESC LIMIT 1", fetch=True)
        return row[0][0] if row else None
    except Exception:
        return None


if __name__ == "__main__":
    result = run_x_intel_batch(send_to_telegram=False)
    print("Result:", result)
    briefing = get_latest_briefing()
    if briefing:
        print("\n" + briefing)
