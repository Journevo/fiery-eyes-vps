"""menu_v7.py — Redesigned 6-button menu system for Fiery Eyes v7.

Handles:
- Main menu (6 buttons: Macro, Cycle, Tokens, Intel, Learn, Command)
- Sub-menus for each button
- /digest, /addtoken, /scorehistory commands
- Message logging for /digest
- Discovery alerting after YouTube scans
"""

import json
import os
import requests
from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("menu_v7")

KEYBOARD_JSON = {
    "keyboard": [
        ["🌍 Macro", "₿ Cycle", "🪙 Tokens"],
        ["🧠 Intel", "📚 Learn", "💼 Command"],
    ],
    "resize_keyboard": True, "is_persistent": True,
}

# ---------------------------------------------------------------------------
# Main Menu
# ---------------------------------------------------------------------------
MAIN_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🌍 Macro", callback_data="menu_macro"),
     InlineKeyboardButton("₿ Cycle", callback_data="menu_cycle"),
     InlineKeyboardButton("🪙 Tokens", callback_data="menu_tokens")],
    [InlineKeyboardButton("🧠 Intel", callback_data="menu_intel"),
     InlineKeyboardButton("📚 Learn", callback_data="menu_learn"),
     InlineKeyboardButton("💼 Command", callback_data="menu_command")],
])

# ---------------------------------------------------------------------------
# Sub-Menus
# ---------------------------------------------------------------------------
MACRO_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Dashboard", callback_data="m_dashboard"),
     InlineKeyboardButton("📈 Yields", callback_data="m_yields")],
    [InlineKeyboardButton("👷 Employment", callback_data="m_employment"),
     InlineKeyboardButton("🌍 Global", callback_data="m_global")],
    [InlineKeyboardButton("🛢 Commodities", callback_data="m_commodities"),
     InlineKeyboardButton("📉 Indices", callback_data="m_indices")],
    [InlineKeyboardButton("⚠️ Triggers", callback_data="m_thresholds"),
     InlineKeyboardButton("🇯🇵 Carry", callback_data="m_carry")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])

CYCLE_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Position", callback_data="c_position"),
     InlineKeyboardButton("🏗 Structure", callback_data="c_structure")],
    [InlineKeyboardButton("📡 Regime", callback_data="c_regime"),
     InlineKeyboardButton("📊 Consensus", callback_data="c_consensus")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])

TOKENS_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📋 Watchlist", callback_data="t_watchlist"),
     InlineKeyboardButton("📊 Scores", callback_data="t_scores")],
    [InlineKeyboardButton("🐋 SunFlow", callback_data="t_sunflow"),
     InlineKeyboardButton("🔍 Deep Dive", callback_data="t_deepdive")],
    [InlineKeyboardButton("📜 History", callback_data="t_scorehistory"),
     InlineKeyboardButton("🏦 ISA", callback_data="t_isa")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])

INTEL_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📺 Today", callback_data="i_today"),
     InlineKeyboardButton("📄 Digest", callback_data="i_digest")],
    [InlineKeyboardButton("🔮 Voices", callback_data="i_voices"),
     InlineKeyboardButton("🎯 Thesis", callback_data="i_thesis")],
    [InlineKeyboardButton("📓 Notebook", callback_data="i_notebook"),
     InlineKeyboardButton("🔍 Claims", callback_data="i_claims")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])

LEARN_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🌍 Macro 101", callback_data="l_macro"),
     InlineKeyboardButton("₿ Cycle 101", callback_data="l_cycle")],
    [InlineKeyboardButton("📊 TA 101", callback_data="l_ta"),
     InlineKeyboardButton("⚠️ Risk 101", callback_data="l_risk")],
    [InlineKeyboardButton("🎯 Strategies", callback_data="l_strategies")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])

COMMAND_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("💼 Portfolio", callback_data="cmd_portfolio"),
     InlineKeyboardButton("📊 Scores", callback_data="cmd_scores")],
    [InlineKeyboardButton("📝 Update", callback_data="x_update"),
     InlineKeyboardButton("➕ Add Token", callback_data="x_addtoken")],
    [InlineKeyboardButton("⚙️ System", callback_data="x_system"),
     InlineKeyboardButton("❓ Help", callback_data="tool_help")],
    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
])


# ---------------------------------------------------------------------------
# Callback handler for v7 menus
# ---------------------------------------------------------------------------
async def handle_v7_callback(query, context, data):
    """Handle Phase 2 menu callbacks. Returns True if handled, False if not."""
    chat_id = query.message.chat_id
    bot = context.bot

    # Sub-menu navigation
    if data == "menu_main":
        await bot.send_message(chat_id, "🔥 <b>FIERY EYES v7</b>", parse_mode="HTML", reply_markup=MAIN_MENU_KB)
        return True
    elif data == "menu_macro":
        await bot.send_message(chat_id, "🌍 <b>Macro Dashboard</b>", parse_mode="HTML", reply_markup=MACRO_MENU_KB)
        return True
    elif data == "menu_cycle":
        await bot.send_message(chat_id, "₿ <b>BTC Cycle</b>", parse_mode="HTML", reply_markup=CYCLE_MENU_KB)
        return True
    elif data == "menu_tokens":
        await bot.send_message(chat_id, "🪙 <b>Token Intelligence</b>", parse_mode="HTML", reply_markup=TOKENS_MENU_KB)
        return True
    elif data == "menu_intel":
        await bot.send_message(chat_id, "🧠 <b>Intelligence</b>", parse_mode="HTML", reply_markup=INTEL_MENU_KB)
        return True
    elif data == "menu_learn":
        await bot.send_message(chat_id, "📚 <b>Learn</b>", parse_mode="HTML", reply_markup=LEARN_MENU_KB)
        return True
    elif data == "menu_command":
        await bot.send_message(chat_id, "💼 <b>Commands</b>", parse_mode="HTML", reply_markup=COMMAND_MENU_KB)
        return True

    # --- MACRO sub-buttons ---
    elif data == "m_dashboard":
        try:
            from macro.dashboard_formatter import format_risk_barometer
            await bot.send_message(chat_id, format_risk_barometer(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_yields":
        try:
            from macro.dashboard_formatter import format_inflation_yields
            await bot.send_message(chat_id, format_inflation_yields(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_employment":
        try:
            from macro.dashboard_formatter import format_us_economy
            await bot.send_message(chat_id, format_us_economy(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_global":
        try:
            from macro.dashboard_formatter import format_global_comparison
            await bot.send_message(chat_id, format_global_comparison(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_commodities":
        try:
            from macro.dashboard_formatter import format_commodities_currencies
            await bot.send_message(chat_id, format_commodities_currencies(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_indices":
        try:
            from macro.dashboard_formatter import format_indices_stocks
            await bot.send_message(chat_id, format_indices_stocks(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_thresholds":
        try:
            from macro.threshold_monitor import format_thresholds_telegram
            await bot.send_message(chat_id, format_thresholds_telegram(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "m_carry":
        try:
            from macro.dashboard_formatter import format_global_comparison
            text = format_global_comparison()
            # Only send carry trade part
            if "CARRY TRADE" in text:
                carry = text[text.index("━━━ 🇯🇵 CARRY"):]
                await bot.send_message(chat_id, carry, parse_mode="HTML")
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True

    # --- CYCLE sub-buttons ---
    elif data == "c_position":
        try:
            from cycle_screen import generate_cycle_screen
            await bot.send_message(chat_id, generate_cycle_screen())
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "c_structure":
        try:
            from market_structure import run_market_structure
            ms = run_market_structure() or {}
            fg = ms.get("fear_greed", {})
            fund = ms.get("funding", {})
            oi = ms.get("open_interest", {})
            text = (
                "🏗 <b>MARKET STRUCTURE</b>\n\n"
                "F&G: %s (%s)\n"
                "BTC Funding: %s%%\n"
                "Open Interest: $%sB\n"
                "BTC Dominance: %s%%"
            ) % (
                fg.get("value", "?"), fg.get("label", "?"),
                fund.get("current_pct", "?"),
                "%.1f" % (oi.get("total_usd", 0) / 1e9) if oi.get("total_usd") else "?",
                ms.get("btc_dominance", "?"),
            )
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "c_regime":
        try:
            from nimbus_sync import format_regime_for_report
            text = format_regime_for_report() or "No regime data"
            await bot.send_message(chat_id, text)
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "c_consensus":
        try:
            from opus_feedback import query_consensus
            await bot.send_message(chat_id, query_consensus("BTC"), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True

    # --- TOKENS sub-buttons ---
    elif data == "t_watchlist":
        try:
            from watchlist import run_watchlist, format_watchlist_telegram
            prices = run_watchlist(send_to_telegram=False)
            if prices:
                await bot.send_message(chat_id, format_watchlist_telegram(prices), parse_mode="HTML")
            else:
                await bot.send_message(chat_id, "Watchlist unavailable")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "t_scores":
        try:
            from token_scores import run_score_update, format_scores_telegram
            scores = run_score_update(send_to_telegram=False)
            await bot.send_message(chat_id, format_scores_telegram(scores), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "t_sunflow":
        try:
            rows = execute("""
                SELECT token, conviction_score, timeframes_present, net_flow_usd
                FROM sunflow_conviction ORDER BY conviction_score DESC
            """, fetch=True)
            if rows:
                lines = ["🐋 <b>SUNFLOW WHALE CONVICTION</b>\n"]
                for r in rows:
                    flow = "$%s" % "{:,.0f}".format(r[3]) if r[3] else "?"
                    lines.append("  %s: conviction %s, %s/4 TF, net %s" % (r[0], r[1], r[2], flow))
                await bot.send_message(chat_id, "\n".join(lines), parse_mode="HTML")
            else:
                await bot.send_message(chat_id, "No SunFlow data")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "t_deepdive":
        await bot.send_message(chat_id, "Send /deepdive TOKEN for full analysis\nExample: /deepdive JUP")
        return True
    elif data == "t_scorehistory":
        text = format_score_history()
        await bot.send_message(chat_id, text, parse_mode="HTML")
        return True
    elif data == "t_isa":
        try:
            row_mstr = execute_one("SELECT price FROM market_prices WHERE ticker = 'MSTR' ORDER BY date DESC LIMIT 1")
            row_coin = execute_one("SELECT price FROM market_prices WHERE ticker = 'COIN' ORDER BY date DESC LIMIT 1")
            text = "🏦 <b>ISA PROXIES</b>\n\n"
            text += "MSTR: $%.2f\n" % float(row_mstr[0]) if row_mstr else "MSTR: ?\n"
            text += "COIN: $%.2f\n" % float(row_coin[0]) if row_coin else "COIN: ?\n"
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True

    # --- INTEL sub-buttons ---
    elif data == "i_today":
        text = format_today_intel()
        await bot.send_message(chat_id, text, parse_mode="HTML")
        return True
    elif data == "i_digest":
        await bot.send_message(chat_id, "Generating digest...")
        try:
            filepath, count = generate_digest()
            if filepath:
                with open(filepath, "rb") as f:
                    await bot.send_document(chat_id, f,
                        caption="📄 Digest: %d items from last 24h" % count)
            else:
                await bot.send_message(chat_id, "No messages in last 24h")
        except Exception as e:
            await bot.send_message(chat_id, "Digest error: %s" % e)
        return True
    elif data == "i_voices":
        try:
            from opus_feedback import query_voices
            await bot.send_message(chat_id, query_voices(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "i_thesis":
        try:
            from opus_feedback import query_thesis
            await bot.send_message(chat_id, query_thesis(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "i_claims":
        try:
            from opus_feedback import query_thesis
            await bot.send_message(chat_id, query_thesis(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "i_notebook":
        await bot.send_message(chat_id, "Generating notebook...")
        try:
            from notebook import send_notebook
            send_notebook()
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True

    # --- LEARN sub-buttons ---
    elif data in ("l_macro", "l_cycle", "l_ta", "l_risk"):
        module = data[2:]
        try:
            text, kb = show_lesson_list(module)
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await bot.send_message(chat_id, "📚 Coming in Phase 3 — 40 structured lessons")
        return True
    elif data == "l_strategies":
        try:
            from opus_feedback import query_learn
            await bot.send_message(chat_id, query_learn(), parse_mode="HTML")
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data.startswith("lesson_"):
        parts = data.split("_")
        if len(parts) == 3:
            module, num = parts[1], int(parts[2])
            try:
                text, kb = show_lesson(module, num)
                await bot.send_message(chat_id, text, reply_markup=kb)
            except Exception as e:
                await bot.send_message(chat_id, "Lesson not found: %s" % e)
        return True

    # --- COMMAND sub-buttons ---
    elif data == "x_update":
        text = (
            "📝 <b>OPUS UPDATE FORMAT</b>\n\n"
            "Paste after daily Opus synthesis:\n\n"
            "<code>/update\n"
            "scores: TOKEN SCORE, TOKEN SCORE\n"
            "claim: VOICE \"prediction\" DIRECTION new|repeated\n"
            "strategy: \"name\" VOICE CONDITION \"rules\"\n"
            "threshold: \"name\" VALUE VOICE\n"
            "consensus: TOKEN DIRECTION PCT%\n"
            "/end</code>"
        )
        await bot.send_message(chat_id, text, parse_mode="HTML")
        return True
    elif data == "x_addtoken":
        await bot.send_message(chat_id, "Send /addtoken TOKEN\nExample: /addtoken TAO")
        return True
    elif data == "x_system":
        try:
            from system_health import generate_health_dashboard
            await bot.send_message(chat_id, generate_health_dashboard())
        except Exception as e:
            await bot.send_message(chat_id, "Error: %s" % e)
        return True
    elif data == "noop":
        return True

    return False


# ---------------------------------------------------------------------------
# /scorehistory command
# ---------------------------------------------------------------------------
def format_score_history() -> str:
    rows = execute(
        """SELECT token, old_score, new_score, reason, changed_at
           FROM score_history ORDER BY changed_at DESC LIMIT 15""",
        fetch=True,
    )
    if not rows:
        return "📜 No score history yet."

    lines = ["📜 <b>SCORE HISTORY</b> (last 15)\n"]
    for r in rows:
        token, old, new, reason, ts = r
        date_str = ts.strftime("%b %d") if ts else "?"
        old_str = str(old) if old is not None else "—"
        new_str = str(new) if new is not None else "—"
        reason_str = (" (%s)" % reason[:40]) if reason else ""
        lines.append("%s: %s %s → %s%s" % (date_str, token, old_str, new_str, reason_str))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Today's intel summary
# ---------------------------------------------------------------------------
def format_today_intel() -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%b %d")

    row = execute_one(
        "SELECT COUNT(*) FROM youtube_videos WHERE processed_at >= CURRENT_DATE")
    count = row[0] if row else 0

    if count == 0:
        return "📺 No videos processed today yet."

    # Get consensus
    cons = execute_one(
        "SELECT bullish_pct, bearish_pct, consensus FROM consensus_daily WHERE date = CURRENT_DATE AND token = 'BTC'")

    lines = ["📺 <b>TODAY'S INTELLIGENCE</b> — %s\n" % date_str]
    lines.append("%d videos processed today" % count)

    if cons:
        bull, bear, direction = cons
        lines.append("\nOutlook: 🔴 %d%% bearish | 🟢 %d%% bullish" % (bear or 0, bull or 0))

    # Recent claims
    claims = execute(
        """SELECT voice, claim, direction FROM voice_claims
           WHERE first_seen = CURRENT_DATE ORDER BY id DESC LIMIT 5""",
        fetch=True,
    )
    if claims:
        lines.append("\n<b>New claims today:</b>")
        for voice, claim, direction in claims:
            lines.append("  %s: %s (%s)" % (voice, claim[:50], direction or "?"))

    lines.append("\n/digest for full dump")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /digest — compile 24h messages into .txt file
# ---------------------------------------------------------------------------
def generate_digest() -> tuple:
    """Generate digest file from last 24h of logged messages.
    Returns (filepath, count) or (None, 0)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = execute(
        """SELECT message_text, message_type, sent_at
           FROM telegram_messages
           WHERE sent_at >= %s ORDER BY sent_at ASC""",
        (cutoff,), fetch=True,
    )
    if not rows:
        return None, 0

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content = "FIERY EYES DIGEST — %s\n" % now_str
    content += "Last 24 hours: %d items\n" % len(rows)
    content += "=" * 60 + "\n\n"

    for i, (text, msg_type, sent_at) in enumerate(rows, 1):
        ts = sent_at.strftime("%Y-%m-%d %H:%M UTC") if sent_at else "?"
        content += "[%d/%d] %s — %s\n" % (i, len(rows), ts, msg_type or "general")
        content += (text or "") + "\n"
        content += "\n" + "=" * 60 + "\n\n"

    content += "END OF DIGEST — %d items\n" % len(rows)
    content += "Paste into Claude Opus for synthesis\n"

    filepath = "/tmp/fiery_eyes_digest_%s.txt" % datetime.now().strftime("%Y%m%d_%H%M")
    with open(filepath, "w") as f:
        f.write(content)

    return filepath, len(rows)


# ---------------------------------------------------------------------------
# Message logging — call this instead of raw send_telegram
# ---------------------------------------------------------------------------
def log_telegram_message(text: str, message_type: str = "general"):
    """Log an outgoing Telegram message for /digest."""
    try:
        execute(
            "INSERT INTO telegram_messages (message_text, message_type) VALUES (%s, %s)",
            (text[:4000], message_type),
        )
    except Exception as e:
        log.error("Message log failed: %s", e)


# ---------------------------------------------------------------------------
# /addtoken command
# ---------------------------------------------------------------------------
def addtoken(token: str) -> str:
    token = token.strip().upper()
    if not token:
        return ("➕ Usage: /addtoken TOKEN\nExample: /addtoken TAO\n\n"
                "Adds token to watchlist. Score assigned in next Opus synthesis.")

    existing = execute_one("SELECT score FROM watchlist_scores WHERE token = %s", (token,))
    if existing:
        return "⚠️ %s already on watchlist (score: %s)" % (token, existing[0])

    # Fetch basic data from CoinGecko
    price, mcap, ath_pct = 0, 0, 0
    cg_map = {
        "TAO": "bittensor", "FET": "fetch-ai", "NEAR": "near",
        "AKT": "akash-network", "AR": "arweave", "SUI": "sui",
        "RENDER": "render-token", "SOL": "solana", "BTC": "bitcoin",
        "HYPE": "hyperliquid", "JUP": "jupiter-exchange-solana",
    }
    cg_id = cg_map.get(token, token.lower())
    try:
        from config import COINGECKO_API_KEY
        headers = {"x-cg-demo-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/%s" % cg_id,
            params={"localization": "false", "tickers": "false",
                    "community_data": "false", "developer_data": "false"},
            headers=headers, timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            md = d.get("market_data", {})
            price = md.get("current_price", {}).get("usd", 0)
            mcap = md.get("market_cap", {}).get("usd", 0)
            ath_pct = md.get("ath_change_percentage", {}).get("usd", 0)
    except Exception:
        pass

    execute(
        "INSERT INTO watchlist_scores (token) VALUES (%s) ON CONFLICT DO NOTHING",
        (token,))
    execute(
        "INSERT INTO score_history (token, old_score, new_score, reason) VALUES (%s, NULL, NULL, %s)",
        (token, "Added via /addtoken"))

    mcap_str = "$%.1fB" % (mcap / 1e9) if mcap > 1e9 else "$%.1fM" % (mcap / 1e6) if mcap > 1e6 else "?"
    return (
        "✅ %s added to watchlist\n\n"
        "Price: $%.4f\nMCap: %s\nATH: %.0f%%\n"
        "Score: TBD — assigned in next Opus synthesis\n\n"
        "Use /deepdive %s for full analysis"
    ) % (token, price, mcap_str, ath_pct, token)


# ---------------------------------------------------------------------------
# Lesson navigation (Phase 3 stub)
# ---------------------------------------------------------------------------
def show_lesson_list(module: str):
    rows = execute(
        "SELECT lesson_number, title FROM lessons WHERE module = %s ORDER BY lesson_number",
        (module,), fetch=True)
    if not rows:
        return "📚 No lessons yet for %s" % module, None

    names = {"macro": "MACRO", "cycle": "CYCLE", "ta": "TA", "risk": "RISK"}
    msg = "📚 <b>%s 101</b>\n\n" % names.get(module, module.upper())
    buttons = []
    for i in range(0, len(rows), 2):
        row = []
        for num, title in rows[i:i + 2]:
            short = title[:22] + "…" if len(title) > 22 else title
            row.append(InlineKeyboardButton("%d. %s" % (num, short), callback_data="lesson_%s_%d" % (module, num)))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="menu_learn")])
    return msg, InlineKeyboardMarkup(buttons)


def show_lesson(module: str, num: int):
    row = execute_one(
        "SELECT title, content, enrichments FROM lessons WHERE module = %s AND lesson_number = %s",
        (module, num))
    if not row:
        return "Lesson not found", None

    title, content, enrichments_json = row
    msg = "📚 Lesson %d: %s\n" % (num, title)
    msg += "━" * 30 + "\n\n"
    msg += content or ""

    if enrichments_json:
        try:
            enrichments = json.loads(enrichments_json)
            if enrichments:
                msg += "\n\n📎 FROM YOUTUBE:\n"
                for e in enrichments[-3:]:
                    msg += "  • %s (%s): %s\n" % (e.get("voice", "?"), e.get("date", "?"), e.get("insight", "")[:100])
        except Exception:
            pass

    execute("UPDATE lessons SET times_accessed = times_accessed + 1 WHERE module = %s AND lesson_number = %s",
            (module, num))

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Prev", callback_data="lesson_%s_%d" % (module, max(1, num - 1))),
         InlineKeyboardButton("%d/10" % num, callback_data="noop"),
         InlineKeyboardButton("Next ▶️", callback_data="lesson_%s_%d" % (module, min(10, num + 1)))],
        [InlineKeyboardButton("◀️ Back", callback_data="l_%s" % module)],
    ])
    return msg, nav


# ---------------------------------------------------------------------------
# Discovery alerting
# ---------------------------------------------------------------------------
def check_discovery_alerts():
    """Check for non-watchlist tokens mentioned by 3+ voices in 72h."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    rows = execute(
        """SELECT target_token, COUNT(DISTINCT voice) as vc, array_agg(DISTINCT voice) as voices
           FROM voice_claims
           WHERE first_seen >= %s AND target_token IS NOT NULL
           AND UPPER(target_token) NOT IN (SELECT UPPER(token) FROM watchlist_scores)
           GROUP BY target_token HAVING COUNT(DISTINCT voice) >= 3
           ORDER BY COUNT(DISTINCT voice) DESC""",
        (cutoff.date(),), fetch=True)

    if not rows:
        return

    for token, vcount, voices in rows:
        # Check recent alert
        recent = execute_one(
            "SELECT id FROM discovery_alerts WHERE token = %s AND alerted_at >= NOW() - INTERVAL '72 hours'",
            (token,))
        if recent:
            continue

        voice_list = ", ".join(voices[:5]) if isinstance(voices, list) else str(voices)
        alert = (
            "🔍🔍🔍 <b>DISCOVERY ALERT</b> 🔍🔍🔍\n"
            "📅 %s\n\n"
            "<b>%s</b> mentioned by %d voices in 72h\n"
            "Sources: %s\n\n"
            "Use /addtoken %s to add to watchlist"
        ) % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
             token, vcount, voice_list, token)

        _send_tg(alert)
        log_telegram_message(alert, "discovery")
        execute(
            "INSERT INTO discovery_alerts (token, voice_count, voices) VALUES (%s, %s, %s)",
            (token, vcount, json.dumps(voices if isinstance(voices, list) else [str(voices)])))
        log.info("Discovery alert: %s (%d voices)", token, vcount)


def _send_tg(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": KEYBOARD_JSON},
            timeout=15)
    except Exception as e:
        log.error("Telegram: %s", e)
