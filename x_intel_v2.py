"""X Intelligence v2 — Research-style Grok queries + Haiku summarisation.

Replaces the 173-account timeline polling with:
1. 3-5 targeted Grok x_search queries every 4 hours
2. 10 key accounts polled every 2 hours
3. ALL results fed to Claude Haiku for a research briefing
4. Output reads like a portfolio manager's morning note

Cost: ~15-20 Grok calls/day + 6 Haiku calls/day = ~$1/month total.
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

# Watchlist tokens — only these appear in the briefing unless huge move
WATCHLIST = {"BTC", "SOL", "SUI", "HYPE", "RENDER", "JUP", "BONK", "PUMP", "PENGU", "FARTCOIN"}

# 10 key accounts for direct monitoring
KEY_ACCOUNTS = [
    "aixbt_agent", "lookonchain", "StalkHQ", "nansen_ai",
    "KaitoAI", "MarioNawfal", "EmberCN",
    "DefiLlama", "whale_alert", "ColdBloodShill",
]

# Targeted research queries
RESEARCH_QUERIES = [
    "Latest whale movements and large buys/sells for RENDER HYPE JUP SOL SUI BONK in the last 4 hours on crypto Twitter",
    "What are top crypto analysts saying about current Bitcoin and crypto market conditions in the last 4 hours",
    "Any major news affecting Solana ecosystem tokens in the last 4 hours including DeFi, memecoins, and protocol updates",
]

# Persistent keyboard for Telegram
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def _grok_search(query: str, handles: list[str] | None = None) -> str:
    """Run a Grok x_search query. Returns the raw text response."""
    if not GROK_API_KEY:
        return ""

    tools = [{"type": "x_search"}]
    if handles:
        tools = [{"type": "x_search", "allowed_x_handles": handles}]

    try:
        resp = requests.post(
            GROK_URL,
            headers={"Authorization": "Bearer " + GROK_API_KEY, "Content-Type": "application/json"},
            json={
                "model": GROK_MODEL,
                "instructions": "You are a crypto research assistant. Return detailed, specific information with numbers and facts. Include the source handle for each piece of information.",
                "input": [{"role": "user", "content": query}],
                "tools": tools,
                "temperature": 0,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()

        from monitoring.degraded import record_api_call
        record_api_call("grok", True)

        # Extract text from response
        content = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")

        return content.strip()

    except Exception as e:
        log.error("Grok search failed: %s", e)
        from monitoring.degraded import record_api_call
        record_api_call("grok", False)
        return ""


def _grok_fetch_key_accounts() -> str:
    """Fetch recent tweets from 10 key accounts via Grok."""
    query = (
        "Find the most recent posts from these accounts: "
        + ", ".join("@" + h for h in KEY_ACCOUNTS)
        + ". Return the full text of each post with the author's handle. "
        "Focus on posts about crypto markets, whale activity, token analysis, "
        "and macro events. Skip retweets and promotional content."
    )
    return _grok_search(query, handles=KEY_ACCOUNTS)


def _haiku_summarise(raw_intel: str) -> str | None:
    """Send all raw Grok output to Claude Haiku for a research briefing."""
    if not ANTHROPIC_API_KEY or not raw_intel.strip():
        return None

    # Get SunFlow conviction for context
    sf_context = ""
    try:
        rows = execute("""
            SELECT token, conviction_score, net_flow_usd, timeframes_present
            FROM sunflow_conviction WHERE is_watchlist = TRUE
            ORDER BY conviction_score DESC
        """, fetch=True)
        if rows:
            sf_lines = ["%s: conviction %s, %s/4 TF, net $%s" %
                        (r[0], r[1], r[3], "{:,.0f}".format(r[2]) if r[2] else "0")
                        for r in rows]
            sf_context = "\nSunFlow whale conviction: " + " | ".join(sf_lines)
    except Exception:
        pass

    prompt = """You are a crypto research analyst writing a 4-hourly intelligence briefing.
Your reader holds positions in JUP, HYPE, RENDER, BONK on Solana, with BTC/SOL as benchmarks.

WATCHLIST: BTC, SOL, SUI, HYPE, RENDER, JUP, BONK, PUMP, PENGU, FARTCOIN%s

Below is raw intelligence gathered from crypto Twitter in the last 4 hours.
Read ALL of it. For each watchlist token mentioned, extract:

1. Any specific claims with numbers (revenue, TVL, whale buys, price targets)
2. Any narrative shifts or new information
3. Any warnings or risks flagged
4. Any non-watchlist tokens getting unusual attention (potential additions)

FORMAT your output EXACTLY like this:

WATCHLIST:
[TOKEN]: [2-3 sentence summary of what was said, citing sources]. Signal: [BULLISH/BEARISH/NEUTRAL] — [one line reason].

NON-WATCHLIST NOTABLE:
[TOKEN] ([brief reason for attention]). Consider /deepdive.

MACRO:
[Any macro/geopolitical signals in 1-2 sentences]

Rules:
- Only include tokens with ACTUAL information (not just price mentions)
- Be specific — cite numbers, sources, and claims
- If a token has no new information, DON'T include it
- If the raw data is thin, say so — don't pad
- Keep total under 400 words
- Skip tokens with MCap under $5M unless on watchlist

RAW INTELLIGENCE:
%s""" % (sf_context, raw_intel[:8000])

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        cost = (response.usage.input_tokens * 1 / 1e6) + (response.usage.output_tokens * 5 / 1e6)
        log.info("Haiku summary: %d in / %d out tokens, $%.4f",
                 response.usage.input_tokens, response.usage.output_tokens, cost)
        return text.strip()

    except Exception as e:
        log.error("Haiku summarisation failed: %s", e)
        return None


def _store_briefing(raw_intel: str, summary: str):
    """Store the briefing in DB for historical reference."""
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
        execute("""
            INSERT INTO x_briefings (raw_intel, summary, word_count)
            VALUES (%s, %s, %s)
        """, (raw_intel[:10000], summary, len(summary.split())))
    except Exception as e:
        log.error("Failed to store briefing: %s", e)


def _send_telegram(text: str):
    """Send to Telegram with persistent keyboard."""
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
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": _KEYBOARD_JSON,
                },
                timeout=15,
            )
        except Exception as e:
            log.error("Telegram send error: %s", e)


def run_x_intel_batch(send_to_telegram: bool = True) -> dict:
    """Run the full X Intel pipeline: Grok research → Haiku summary → Telegram.

    Called every 4 hours at 06, 10, 14, 18, 22 UTC.
    """
    log.info("=== X Intel Briefing starting ===")

    # Step 1: Gather raw intelligence from Grok
    raw_parts = []

    # 1a: Targeted research queries
    for i, query in enumerate(RESEARCH_QUERIES):
        log.info("Research query %d/%d...", i + 1, len(RESEARCH_QUERIES))
        result = _grok_search(query)
        if result:
            raw_parts.append("=== QUERY: %s ===\n%s" % (query[:80], result))
        time.sleep(2)

    # 1b: Key account tweets
    log.info("Fetching key accounts...")
    key_tweets = _grok_fetch_key_accounts()
    if key_tweets:
        raw_parts.append("=== KEY ACCOUNTS ===\n%s" % key_tweets)

    raw_intel = "\n\n".join(raw_parts)
    log.info("Raw intel gathered: %d chars from %d sources", len(raw_intel), len(raw_parts))

    if not raw_intel.strip():
        log.warning("No raw intelligence gathered — skipping briefing")
        return {"status": "empty", "raw_chars": 0}

    # Step 2: Haiku summarisation
    log.info("Running Haiku summary...")
    summary = _haiku_summarise(raw_intel)

    if not summary:
        log.error("Haiku summary failed — sending raw fallback")
        # Fallback: send truncated raw
        summary = "Raw intel (Haiku failed):\n" + raw_intel[:2000]

    # Step 3: Format and send
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    message = "\U0001f4e1 <b>X INTEL \u2014 %s</b>\n\n%s" % (now, summary)

    _store_briefing(raw_intel, summary)

    if send_to_telegram:
        _send_telegram(message)
        log.info("X Intel briefing sent (%d chars)", len(message))

    return {
        "status": "ok",
        "raw_chars": len(raw_intel),
        "summary_words": len(summary.split()),
    }


def get_latest_briefing() -> str | None:
    """Get the most recent X Intel briefing from DB."""
    try:
        row = execute("""
            SELECT summary, created_at FROM x_briefings
            ORDER BY created_at DESC LIMIT 1
        """, fetch=True)
        if row:
            return row[0][0]
    except Exception:
        pass
    return None


if __name__ == "__main__":
    result = run_x_intel_batch(send_to_telegram=False)
    print("Result:", result)
    if result.get("status") == "ok":
        briefing = get_latest_briefing()
        if briefing:
            print("\n" + briefing)
