"""SunFlow Alpha Telegram Parser — Primary free smart money source.

Monitors SunFlow Alpha channel for whale inflow/outflow rankings.
Parses: token name, rank, dollar amount, timeframe, direction.
Cross-references against watchlist for conviction scoring.

Multi-timeframe conviction: 1 TF = 1x, 2 = 2x, 3 = 3x, all 4 = 4x weight.
"""

import re
import json
import asyncio
import requests
from datetime import datetime, timezone
from telethon import TelegramClient, events
from config import (TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE,
                    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger)
from db.connection import execute, execute_one

log = get_logger("sunflow_telegram")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "💼 Portfolio", "📈 Market", "🔧 Tools"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# SunFlow Alpha channel — find by username or ID
SUNFLOW_CHANNEL = "SunFlowAlpha"  # Will resolve on first connect

# Watchlist tokens for cross-referencing
WATCHLIST = {"BTC", "SOL", "JUP", "HYPE", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN"}

# Session file
SESSION_FILE = "/opt/fiery-eyes/fiery_eyes_session"

# Timeframe keywords
TIMEFRAME_PATTERNS = {
    "daily": ["daily", "24h", "today", "1d"],
    "weekly": ["weekly", "7d", "week", "1w"],
    "monthly": ["monthly", "30d", "month", "1m"],
    "3month": ["3 month", "3m", "90d", "3-month", "quarterly"],
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS sunflow_signals (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            rank INTEGER,
            amount_usd REAL,
            timeframe TEXT,
            direction TEXT NOT NULL,
            conviction_weight INTEGER DEFAULT 1,
            raw_text TEXT,
            message_date TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS sunflow_conviction (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            timeframes_present INTEGER DEFAULT 0,
            timeframe_list TEXT,
            total_inflow_usd REAL DEFAULT 0,
            total_outflow_usd REAL DEFAULT 0,
            net_flow_usd REAL DEFAULT 0,
            conviction_score REAL DEFAULT 0,
            is_watchlist BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (token)
        )
    """)


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------
def detect_timeframe(text: str) -> str:
    """Detect timeframe from message text."""
    text_lower = text.lower()
    for tf, keywords in TIMEFRAME_PATTERNS.items():
        for kw in keywords:
            if kw in text_lower:
                return tf
    return "daily"  # Default


def detect_direction(text: str) -> str:
    """Detect inflow or outflow from message text."""
    text_lower = text.lower()
    if "outflow" in text_lower or "selling" in text_lower or "sold" in text_lower:
        return "outflow"
    if "inflow" in text_lower or "buying" in text_lower or "bought" in text_lower:
        return "inflow"
    # Check for emoji cues (green = inflow, red = outflow)
    if "🔴" in text or "📉" in text:
        return "outflow"
    if "🟢" in text or "📈" in text:
        return "inflow"
    return "inflow"  # Default


def parse_sunflow_message(text: str) -> list:
    """Parse a SunFlow Alpha message into structured signals.

    Expected format:
    #1 PENGU $2,355,576
    #2 RENDER $988,993
    #3 BONK $235,719
    """
    timeframe = detect_timeframe(text)
    direction = detect_direction(text)

    # Pattern: #N TOKEN $AMOUNT or #N $TOKEN $AMOUNT
    # Also handles: 1. TOKEN $AMOUNT, TOKEN — $AMOUNT
    patterns = [
        # #1 PENGU $2,355,576
        re.compile(r'#?(\d{1,2})\s+\$?([A-Z][A-Z0-9]{1,14})\s+\$?([\d,]+(?:\.\d+)?)', re.MULTILINE),
        # 1) PENGU $2,355,576
        re.compile(r'(\d{1,2})\)\s+\$?([A-Z][A-Z0-9]{1,14})\s+\$?([\d,]+(?:\.\d+)?)', re.MULTILINE),
        # PENGU — $2,355,576 (no rank)
        re.compile(r'\$?([A-Z][A-Z0-9]{1,14})\s+[\-—]\s+\$?([\d,]+(?:\.\d+)?)', re.MULTILINE),
    ]

    signals = []
    seen_tokens = set()

    for pattern in patterns:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) == 3:
                rank, token, amount_str = groups
                rank = int(rank)
            elif len(groups) == 2:
                token, amount_str = groups
                rank = len(signals) + 1
            else:
                continue

            token = token.upper()
            amount = float(amount_str.replace(",", ""))

            if token in seen_tokens:
                continue
            seen_tokens.add(token)

            # Skip obviously wrong tokens
            if token in {"USD", "USDT", "USDC", "TOP", "NET"}:
                continue

            signals.append({
                "token": token,
                "rank": rank,
                "amount_usd": amount,
                "timeframe": timeframe,
                "direction": direction,
                "is_watchlist": token in WATCHLIST,
            })

    return signals


# ---------------------------------------------------------------------------
# Storage and conviction scoring
# ---------------------------------------------------------------------------
def store_signals(signals: list, raw_text: str, message_date=None):
    """Store parsed signals and update conviction scores."""
    ensure_table()

    for s in signals:
        execute("""
            INSERT INTO sunflow_signals (token, rank, amount_usd, timeframe, direction,
                                         conviction_weight, raw_text, message_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (s["token"], s["rank"], s["amount_usd"], s["timeframe"],
              s["direction"], 1, raw_text[:500], message_date))

    log.info("Stored %d SunFlow signals (%s, %s)",
             len(signals),
             signals[0]["direction"] if signals else "?",
             signals[0]["timeframe"] if signals else "?")


def update_conviction_scores():
    """Recalculate multi-timeframe conviction for all tokens.

    Conviction = number of timeframes present × weight.
    1 TF = 1x, 2 = 2x, 3 = 3x, all 4 = 4x.
    """
    # Get latest signals per token per timeframe (last 7 days for daily, 30 for others)
    rows = execute("""
        WITH latest AS (
            SELECT DISTINCT ON (token, timeframe, direction)
                token, timeframe, direction, amount_usd
            FROM sunflow_signals
            WHERE created_at > NOW() - INTERVAL '30 days'
            ORDER BY token, timeframe, direction, created_at DESC
        )
        SELECT token,
               array_agg(DISTINCT timeframe) as timeframes,
               SUM(CASE WHEN direction = 'inflow' THEN amount_usd ELSE 0 END) as total_inflow,
               SUM(CASE WHEN direction = 'outflow' THEN amount_usd ELSE 0 END) as total_outflow
        FROM latest
        WHERE direction = 'inflow'
        GROUP BY token
    """, fetch=True)

    if not rows:
        return

    for token, timeframes, total_inflow, total_outflow in rows:
        tf_count = len(timeframes) if timeframes else 0
        # Multi-timeframe conviction multiplier
        conviction = tf_count * tf_count  # 1=1, 2=4, 3=9, 4=16 (quadratic)
        net_flow = (total_inflow or 0) - (total_outflow or 0)
        is_watchlist = token.upper() in WATCHLIST

        execute("""
            INSERT INTO sunflow_conviction (token, timeframes_present, timeframe_list,
                total_inflow_usd, total_outflow_usd, net_flow_usd, conviction_score, is_watchlist, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (token) DO UPDATE SET
                timeframes_present = EXCLUDED.timeframes_present,
                timeframe_list = EXCLUDED.timeframe_list,
                total_inflow_usd = EXCLUDED.total_inflow_usd,
                total_outflow_usd = EXCLUDED.total_outflow_usd,
                net_flow_usd = EXCLUDED.net_flow_usd,
                conviction_score = EXCLUDED.conviction_score,
                is_watchlist = EXCLUDED.is_watchlist,
                updated_at = NOW()
        """, (token, tf_count, ",".join(timeframes) if timeframes else "",
              total_inflow or 0, total_outflow or 0, net_flow,
              conviction, is_watchlist))

    log.info("Updated conviction scores for %d tokens", len(rows))


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
def check_watchlist_alerts(signals: list) -> list:
    """Check if any watchlist tokens appear in the signals."""
    alerts = []
    for s in signals:
        if s["is_watchlist"]:
            emoji = "🟢" if s["direction"] == "inflow" else "🔴"
            alerts.append({
                "token": s["token"],
                "direction": s["direction"],
                "amount": s["amount_usd"],
                "rank": s["rank"],
                "timeframe": s["timeframe"],
                "message": (
                    f"{emoji} <b>SUNFLOW — ${s['token']}</b>\n"
                    f"  #{s['rank']} {s['direction']} ${s['amount_usd']:,.0f} ({s['timeframe']})\n"
                    f"  Watchlist match — whale {'accumulation' if s['direction'] == 'inflow' else 'distribution'}"
                ),
            })
    return alerts


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def get_conviction_summary() -> str:
    """Get formatted conviction summary for watchlist tokens."""
    ensure_table()
    rows = execute("""
        SELECT token, timeframes_present, timeframe_list,
               total_inflow_usd, total_outflow_usd, net_flow_usd, conviction_score
        FROM sunflow_conviction
        WHERE is_watchlist = TRUE
        ORDER BY conviction_score DESC
    """, fetch=True)

    if not rows:
        return "🐋 <b>SUNFLOW CONVICTION</b>\nNo watchlist data yet"

    lines = ["🐋 <b>SUNFLOW CONVICTION</b>", ""]
    for token, tf_count, tf_list, inflow, outflow, net, score in rows:
        arrow = "🟢" if net > 0 else "🔴"
        tf_str = f"{tf_count}/4 TF"
        net_str = f"${abs(net):,.0f}"
        flow_dir = "net inflow" if net > 0 else "net outflow"
        tfs = tf_list.replace(",", " ") if tf_list else ""
        lines.append(
            f"{arrow} <b>${token}</b>: {tf_str} ({tfs}) | {flow_dir} {net_str} | conv: {score:.0f}"
        )

    return "\n".join(lines)


def format_for_report() -> str | None:
    """One-line per watchlist token for daily report."""
    rows = execute("""
        SELECT token, timeframes_present, net_flow_usd, conviction_score
        FROM sunflow_conviction
        WHERE is_watchlist = TRUE AND conviction_score > 0
        ORDER BY conviction_score DESC
    """, fetch=True)

    if not rows:
        return None

    lines = []
    for token, tf_count, net, score in rows:
        arrow = "🟢" if net > 0 else "🔴"
        lines.append(f"  {arrow} {token}: {tf_count}/4 TF, net ${abs(net):,.0f} {'in' if net > 0 else 'out'}, conv {score:.0f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telethon listener
# ---------------------------------------------------------------------------
async def start_listener():
    """Start listening to SunFlow Alpha channel."""
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        log.error("Telegram API credentials not set")
        return

    client = TelegramClient(SESSION_FILE, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
    try:
        # Try connecting with existing session — don't prompt for code
        await client.connect()
        if not await client.is_user_authorized():
            log.warning("Telethon session not authorized. Run manually: python sunflow_telegram.py --auth")
            await client.disconnect()
            return
    except Exception as e:
        log.warning("Telethon connection failed: %s. Run --auth manually.", e)
        return

    # Resolve channel
    try:
        channel = await client.get_entity(SUNFLOW_CHANNEL)
        log.info("Connected to SunFlow Alpha channel: %s (ID: %s)", channel.title, channel.id)
    except Exception as e:
        log.error("Could not find SunFlow Alpha channel: %s", e)
        await client.disconnect()
        return

    @client.on(events.NewMessage(chats=channel))
    async def handler(event):
        text = event.message.text or ""
        if not text or len(text) < 20:
            return

        # Parse the message
        signals = parse_sunflow_message(text)
        if not signals:
            log.debug("No signals parsed from SunFlow message")
            return

        log.info("SunFlow: %d signals (%s %s)", len(signals),
                 signals[0]["direction"], signals[0]["timeframe"])

        # Store
        store_signals(signals, text, event.message.date)

        # Update conviction scores
        update_conviction_scores()

        # Check for watchlist alerts
        alerts = check_watchlist_alerts(signals)
        for alert in alerts:
            log.info("SunFlow watchlist alert: %s %s", alert["token"], alert["direction"])
            send_telegram(alert["message"])

    log.info("SunFlow listener started — waiting for messages...")
    await client.run_until_disconnected()


def run_listener():
    """Synchronous entry point for the listener."""
    asyncio.run(start_listener())


# ---------------------------------------------------------------------------
# Manual message processing (for backfilling or testing)
# ---------------------------------------------------------------------------
def process_manual_message(text: str, direction: str = None, timeframe: str = None):
    """Manually process a SunFlow-format message."""
    ensure_table()
    signals = parse_sunflow_message(text)

    # Override direction/timeframe if specified
    if direction:
        for s in signals:
            s["direction"] = direction
    if timeframe:
        for s in signals:
            s["timeframe"] = timeframe

    if signals:
        store_signals(signals, text)
        update_conviction_scores()

        alerts = check_watchlist_alerts(signals)
        for a in alerts:
            print(a["message"])

    return signals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ensure_table()

    if "--auth" in sys.argv:
        # Interactive auth — run this manually once
        async def auth():
            client = TelegramClient(SESSION_FILE, int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
            await client.start(phone=TELEGRAM_PHONE)
            print("Authenticated successfully!")
            me = await client.get_me()
            print(f"Logged in as: {me.first_name} ({me.phone})")
            # Test channel access
            try:
                channel = await client.get_entity(SUNFLOW_CHANNEL)
                print(f"SunFlow channel found: {channel.title}")
                # Get last 3 messages
                async for msg in client.iter_messages(channel, limit=3):
                    print(f"  [{msg.date}] {(msg.text or '')[:100]}")
            except Exception as e:
                print(f"Channel access failed: {e}")
            await client.disconnect()
        asyncio.run(auth())
    elif "--listen" in sys.argv:
        run_listener()
    elif "--conviction" in sys.argv:
        print(get_conviction_summary())
    elif "--test" in sys.argv:
        # Test with sample data from the user's description
        print("=== Testing daily inflows ===")
        test_inflow = """🟢 Top 16 whale USD Net Inflows (Daily)
#1 PENGU $2,355,576
#2 RENDER $988,993
#3 BONK $235,719
#4 JUP $180,000
#5 WIF $150,000"""
        signals = process_manual_message(test_inflow, direction="inflow", timeframe="daily")
        for s in signals:
            wl = "⭐" if s["is_watchlist"] else ""
            print(f"  #{s['rank']} {s['token']} ${s['amount_usd']:,.0f} {s['direction']} {wl}")

        print("\n=== Testing monthly inflows ===")
        test_monthly = """🟢 Top 16 whale USD Net Inflows (Monthly)
#1 RENDER $4,200,000
#2 HYPE $1,500,000
#3 BONK $800,000"""
        signals2 = process_manual_message(test_monthly, direction="inflow", timeframe="monthly")
        for s in signals2:
            wl = "⭐" if s["is_watchlist"] else ""
            print(f"  #{s['rank']} {s['token']} ${s['amount_usd']:,.0f} {s['direction']} {wl}")

        print("\n=== Testing 3-month inflows ===")
        test_3m = """🟢 Top 16 whale USD Net Inflows (3-Month)
#1 RENDER $9,100,000
#2 HYPE $3,200,000"""
        signals3 = process_manual_message(test_3m, direction="inflow", timeframe="3month")
        for s in signals3:
            wl = "⭐" if s["is_watchlist"] else ""
            print(f"  #{s['rank']} {s['token']} ${s['amount_usd']:,.0f} {s['direction']} {wl}")

        print("\n=== Testing daily outflows ===")
        test_outflow = """🔴 Top 16 whale USD Net Outflows (Daily)
#1 FARTCOIN $500,000
#2 PENGU $350,000"""
        signals4 = process_manual_message(test_outflow, direction="outflow", timeframe="daily")
        for s in signals4:
            wl = "⭐" if s["is_watchlist"] else ""
            print(f"  #{s['rank']} {s['token']} ${s['amount_usd']:,.0f} {s['direction']} {wl}")

        print("\n=== Conviction Summary ===")
        print(get_conviction_summary())
    else:
        print("Usage:")
        print("  python sunflow_telegram.py --listen    # Start live listener")
        print("  python sunflow_telegram.py --conviction # Show conviction scores")
        print("  python sunflow_telegram.py --test      # Run test with sample data")
