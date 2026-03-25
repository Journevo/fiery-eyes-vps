"""Fiery Eyes v5.2 — Telegram Bot + Scheduler

Handles v5 commands and runs scheduled tasks:
- /report   — generate daily intelligence report on demand
- /cycle    — BTC cycle position
- /watchlist— token prices and zones
- /liquidity— FRED liquidity data
- /ledger   — recent recommendations
- /portfolio— current positions
- /pnl      — unrealised PnL
- /bought TOKEN AMOUNT PRICE — log purchase
- /sold TOKEN AMOUNT PRICE — log sale

Schedule (all UTC):
  05:45    Nimbus sync from Jingubang
  06:00    Morning Brief (full report + synthesis)
  06,10,14,18,22:00  X Intel batch
  20:00    Evening Review
  Sunday 08:00  Weekly review
  Every 2h:  Grok MEDIUM poll (data collection)
  Every 4h:  Watchlist + swap detection + market structure

YouTube: on-demand only (Sonnet analysis via /analyse)
H-Fire alerts: immediate (convergence, large swaps)
"""

import threading
import time
import schedule
import requests as req
from datetime import datetime, timezone
from telegram import Update, Bot, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

log = get_logger("v5_bot")

# Persistent reply keyboard — always visible at bottom of chat
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    resize_keyboard=True,
    is_persistent=True,
)

# Keyboard as JSON for raw API calls (scheduled sends)
MAIN_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


def send_telegram_with_keyboard(text: str, parse_mode: str = "HTML"):
    """Send message via raw API with persistent keyboard attached."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    max_len = 4000
    if len(text) <= max_len:
        chunks = [text]
    else:
        chunks = []
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
            resp = req.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                    "reply_markup": MAIN_KEYBOARD_JSON,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                log.info("Telegram sent (%d chars, msg %s)",
                         len(chunk), resp.json().get("result", {}).get("message_id", "?"))
            elif resp.status_code == 400 and parse_mode == "HTML":
                # HTML parse error — retry without formatting
                log.warning("Telegram HTML parse failed, retrying as plain text")
                resp2 = req.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": chunk,
                        "disable_web_page_preview": True,
                        "reply_markup": MAIN_KEYBOARD_JSON,
                    },
                    timeout=15,
                )
                if resp2.status_code == 200:
                    log.info("Telegram sent as plain text (%d chars)", len(chunk))
                else:
                    log.error("Telegram send failed even as plain: %s", resp2.text[:200])
            else:
                log.error("Telegram send failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Persistent menu handlers
# ---------------------------------------------------------------------------
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle V6 persistent keyboard button taps."""
    text = update.message.text

    if text == "📊 Intel":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("YouTube", callback_data="v6_yt_dash"),
             InlineKeyboardButton("Macro", callback_data="v6_macro"),
             InlineKeyboardButton("Liquidity", callback_data="cmd_liquidity"),
             InlineKeyboardButton("Chain", callback_data="cmd_defi")],
        ])
        await update.message.reply_text("📊 <b>Intelligence</b>", parse_mode="HTML", reply_markup=keyboard)

    elif text == "🐋 Signals":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Market", callback_data="cmd_market"),
             InlineKeyboardButton("Whale", callback_data="cmd_sunflow"),
             InlineKeyboardButton("Discovery", callback_data="v6_discovery"),
             InlineKeyboardButton("Unlocks", callback_data="cmd_supply")],
        ])
        await update.message.reply_text("🐋 <b>Signals</b>", parse_mode="HTML", reply_markup=keyboard)

    elif text == "🔥 Fiery Eyes":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Morning", callback_data="cmd_report"),
             InlineKeyboardButton("Evening", callback_data="v6_evening"),
             InlineKeyboardButton("Notebook", callback_data="v6_notebook"),
             InlineKeyboardButton("Cycle", callback_data="v6_cycle")],
        ])
        await update.message.reply_text("🔥 <b>Fiery Eyes</b>", parse_mode="HTML", reply_markup=keyboard)

    elif text == "💼 Portfolio":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Positions", callback_data="cmd_portfolio"),
             InlineKeyboardButton("Deep Dive", callback_data="cmd_deepdive"),
             InlineKeyboardButton("Trades", callback_data="cmd_ledger"),
             InlineKeyboardButton("Risk", callback_data="cmd_pnl")],
        ])
        await update.message.reply_text("💼 <b>Portfolio</b>", parse_mode="HTML", reply_markup=keyboard)

    elif text == "⚙️ System":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Health", callback_data="v6_health"),
             InlineKeyboardButton("YT Health", callback_data="v6_yt_health"),
             InlineKeyboardButton("Costs", callback_data="v6_costs"),
             InlineKeyboardButton("Help", callback_data="tool_help")],
        ])
        await update.message.reply_text("⚙️ <b>System</b>", parse_mode="HTML", reply_markup=keyboard)

    else:
        log.info("Unrecognized menu text: %s", repr(text))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Map callback data to command functions
    cmd_map = {
        "cmd_report": cmd_report,
        "cmd_cycle": cmd_cycle,
        "cmd_watchlist": cmd_watchlist,
        "cmd_liquidity": cmd_liquidity,
        "cmd_defi": cmd_defi,
        "cmd_market": cmd_market,
        "cmd_chains": cmd_chains,
        "cmd_scores": cmd_scores,
        "cmd_deepdive": cmd_deepdive_research,
        "cmd_synthesis": cmd_synthesis,
        "cmd_sunflow": cmd_sunflow,
        "cmd_signals": cmd_signals,
        "cmd_youtube": cmd_youtube,
        "cmd_supply": cmd_supply,
        "cmd_convergence": cmd_convergence,
        "cmd_portfolio": cmd_portfolio,
        "cmd_pnl": cmd_pnl,
        "cmd_ledger": cmd_ledger,
        "cmd_exits": cmd_exits,
        "cmd_yields": cmd_yields,
        "cmd_pulse": cmd_pulse,
        "cmd_weekly": cmd_weekly,
    }

    if data in cmd_map:
        class FakeMessage:
            def __init__(self, chat_id, bot):
                self.chat_id = chat_id
                self._bot = bot
            async def reply_text(self, text, **kwargs):
                kwargs["reply_markup"] = MAIN_KEYBOARD
                await self._bot.send_message(self.chat_id, text, **kwargs)

        class FakeUpdate:
            def __init__(self, message):
                self.message = message

        fake_msg = FakeMessage(query.message.chat_id, context.bot)
        fake_update = FakeUpdate(fake_msg)
        await cmd_map[data](fake_update, context)

    elif data == "tool_analyse":
        await context.bot.send_message(
            query.message.chat_id,
            "Send: /analyse <YouTube URL>\nExample: /analyse https://youtube.com/watch?v=abc123",
            reply_markup=MAIN_KEYBOARD)

    elif data == "tool_deepdive":
        await context.bot.send_message(
            query.message.chat_id,
            "Send: /deepdive <contract address>\nExample: /deepdive JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
            reply_markup=MAIN_KEYBOARD)

    elif data == "tool_bought":
        await context.bot.send_message(
            query.message.chat_id,
            "Send: /bought TOKEN AMOUNT PRICE\nExample: /bought JUP 1000 0.166",
            reply_markup=MAIN_KEYBOARD)

    elif data == "tool_sold":
        await context.bot.send_message(
            query.message.chat_id,
            "Send: /sold TOKEN AMOUNT PRICE\nExample: /sold JUP 500 0.25",
            reply_markup=MAIN_KEYBOARD)

    elif data == "v6_yt_dash":
        try:
            from youtube_dashboard import generate_youtube_dashboard
            text = generate_youtube_dashboard()
            await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_macro":
        try:
            from nimbus_sync import get_nimbus_data
            from nimbus_engine import compute_nimbus
            from macro_display import generate_macro_signal
            nd = get_nimbus_data()
            if nd:
                state = compute_nimbus(nd)
                text = generate_macro_signal(nd, state)
                # Add sub-buttons for detail tables
                detail_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("PMI", callback_data="macro_pmi"),
                     InlineKeyboardButton("CPI", callback_data="macro_cpi"),
                     InlineKeyboardButton("GDP", callback_data="macro_gdp"),
                     InlineKeyboardButton("Key Dates", callback_data="macro_dates")],
                ])
                await context.bot.send_message(query.message.chat_id, text, reply_markup=detail_kb)
            else:
                await context.bot.send_message(query.message.chat_id, "Nimbus data not synced yet. Run nimbus_sync.", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Macro error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data.startswith("macro_"):
        table = data[6:]  # pmi, cpi, gdp, dates
        try:
            from nimbus_sync import get_nimbus_data
            from macro_display import generate_pmi_table, generate_cpi_table, generate_gdp_table, generate_key_dates
            nd = get_nimbus_data()
            if nd:
                if table == "pmi":
                    text = generate_pmi_table(nd)
                elif table == "cpi":
                    text = generate_cpi_table(nd)
                elif table == "gdp":
                    text = generate_gdp_table(nd)
                elif table == "dates":
                    text = generate_key_dates(nd)
                else:
                    text = "Unknown table: " + table
                await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)
            else:
                await context.bot.send_message(query.message.chat_id, "Nimbus data not available", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_cycle":
        try:
            from cycle_screen import generate_cycle_screen
            text = generate_cycle_screen()
            await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_notebook":
        await context.bot.send_message(query.message.chat_id, "Generating notebook (30-60s)...", reply_markup=MAIN_KEYBOARD)
        try:
            from notebook import generate_notebook, send_notebook
            send_notebook()
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_evening":
        try:
            from db.connection import execute_one
            row = execute_one("SELECT raw_output FROM synthesis ORDER BY created_at DESC LIMIT 1")
            if row and row[0]:
                await context.bot.send_message(query.message.chat_id, row[0][:4000], reply_markup=MAIN_KEYBOARD)
            else:
                await context.bot.send_message(query.message.chat_id, "No evening review available yet.", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_health":
        try:
            from system_health import generate_health_dashboard
            text = generate_health_dashboard()
            await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_yt_health":
        try:
            from youtube_health import generate_health_report
            text = generate_health_report()
            await context.bot.send_message(query.message.chat_id, text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Error: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data == "v6_costs":
        text = "API COSTS (March 2026)\n\nClaude API: ~$46/mo (YouTube Sonnet/Haiku + Intel Briefing)\nHetzner VPS: $8/mo\nGrok: $0 (disabled)\nTotal: ~$54/mo"
        await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)

    elif data == "v6_discovery":
        try:
            from db.connection import execute as db_exec
            rows = db_exec("""
                SELECT token_symbol, COUNT(*) as cnt, COUNT(DISTINCT channel_name) as channels
                FROM (
                    SELECT unnest(
                        CASE WHEN tokens_mentioned IS NOT NULL
                        THEN string_to_array(
                            regexp_replace(tokens_mentioned::text, '[^a-zA-Z,]', '', 'g'), ',')
                        ELSE ARRAY[]::text[] END
                    ) as token_symbol, channel_name
                    FROM youtube_videos WHERE processed_at > NOW() - INTERVAL '48 hours'
                ) sub
                WHERE length(token_symbol) BETWEEN 2 AND 10
                GROUP BY token_symbol HAVING COUNT(DISTINCT channel_name) >= 2
                ORDER BY COUNT(DISTINCT channel_name) DESC, COUNT(*) DESC LIMIT 10
            """, fetch=True)
            if rows:
                lines = ["DISCOVERY (48h YouTube mentions)\n"]
                for r in rows:
                    sym = (r[0] or "").upper()
                    if not sym:
                        continue
                    lines.append("%s: %d mentions from %d channels" % (sym, r[1], r[2]))
                text = "\n".join(lines)
            else:
                text = "No multi-channel token mentions in 48h"
            await context.bot.send_message(query.message.chat_id, text, reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(query.message.chat_id, "Discovery: " + str(e), reply_markup=MAIN_KEYBOARD)

    elif data.startswith("ddf_"):
        # Full document request
        token = data[4:]
        try:
            import asyncio as _aio
            from research.research_manager import get_full_document_chunks
            chunks = get_full_document_chunks(token)
            for chunk in chunks:
                await context.bot.send_message(
                    query.message.chat_id, chunk,
                    reply_markup=MAIN_KEYBOARD)
                await _aio.sleep(0.5)
        except Exception as e:
            await context.bot.send_message(
                query.message.chat_id, "Error: " + str(e),
                reply_markup=MAIN_KEYBOARD)

    elif data.startswith("dd_"):
        token = data[3:]
        try:
            from research.research_manager import get_summary_card, get_scorecard
            if token == "ALL":
                text = get_scorecard()
            else:
                text = get_summary_card(token)
                text += "\n\nTap \"Full\" above or /deepdive %s full for complete doc" % token
            await context.bot.send_message(
                query.message.chat_id, text,
                reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await context.bot.send_message(
                query.message.chat_id, "Error: " + str(e),
                reply_markup=MAIN_KEYBOARD)

    elif data == "tool_help":
        class FakeMessage2:
            def __init__(self, cid, bot):
                self.chat_id = cid
                self._bot = bot
            async def reply_text(self, text, **kwargs):
                kwargs["reply_markup"] = MAIN_KEYBOARD
                await self._bot.send_message(self.chat_id, text, **kwargs)

        class FakeUpdate2:
            def __init__(self, msg):
                self.message = msg

        await cmd_help(FakeUpdate2(FakeMessage2(query.message.chat_id, context.bot)), context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the persistent keyboard menu."""
    await update.message.reply_text(
        "🔥 <b>FIERY EYES v6.0</b>\nTap a button below:",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# Command handlers — ALL include reply_markup=MAIN_KEYBOARD
# ---------------------------------------------------------------------------
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send daily intelligence report."""
    await update.message.reply_text("⏳ Generating report...", reply_markup=MAIN_KEYBOARD)
    try:
        from daily_report import generate_report
        report = generate_report(send_to_telegram=False)
        if len(report) > 4000:
            parts = report.split("\n━━━")
            current = parts[0]
            for part in parts[1:]:
                candidate = current + "\n━━━" + part
                if len(candidate) > 4000:
                    await update.message.reply_text(current, parse_mode="HTML",
                                                     disable_web_page_preview=True,
                                                     reply_markup=MAIN_KEYBOARD)
                    current = "━━━" + part
                else:
                    current = candidate
            if current:
                await update.message.reply_text(current, parse_mode="HTML",
                                                 disable_web_page_preview=True,
                                                 reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text(report, parse_mode="HTML",
                                             disable_web_page_preview=True,
                                             reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/report error: %s", e)
        await update.message.reply_text(f"⚠️ Report failed: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show BTC cycle position."""
    try:
        from btc_cycle import run_cycle_tracker, format_cycle_telegram
        cycle = run_cycle_tracker()
        if cycle:
            await update.message.reply_text(format_cycle_telegram(cycle),
                                             parse_mode="HTML",
                                             reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("⚠️ BTC price unavailable", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/cycle error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show watchlist prices and zones."""
    try:
        from watchlist import run_watchlist, format_watchlist_telegram
        prices = run_watchlist()
        if prices:
            await update.message.reply_text(format_watchlist_telegram(prices),
                                             parse_mode="HTML",
                                             reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("⚠️ No prices available", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/watchlist error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show FRED liquidity data."""
    try:
        from liquidity import run_liquidity_tracker, format_liquidity_telegram
        data = run_liquidity_tracker()
        if data:
            await update.message.reply_text(format_liquidity_telegram(data),
                                             parse_mode="HTML",
                                             reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("⚠️ Liquidity data unavailable", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/liquidity error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent recommendations."""
    try:
        from rec_ledger import ensure_tables, format_recent_recs_telegram
        ensure_tables()
        msg = format_recent_recs_telegram()
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/ledger error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show portfolio positions."""
    try:
        from portfolio import ensure_tables, get_portfolio, format_portfolio_telegram
        ensure_tables()
        portfolio = get_portfolio()
        msg = format_portfolio_telegram(portfolio)
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/portfolio error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show unrealised PnL."""
    try:
        from portfolio import ensure_tables, get_portfolio, format_pnl_telegram
        ensure_tables()
        portfolio = get_portfolio()
        msg = format_pnl_telegram(portfolio)
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/pnl error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_bought(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a purchase: /bought TOKEN AMOUNT PRICE"""
    try:
        args = context.args
        if not args or len(args) < 3:
            await update.message.reply_text("Usage: /bought TOKEN AMOUNT PRICE\nExample: /bought JUP 1000 0.166", reply_markup=MAIN_KEYBOARD)
            return
        token = args[0].upper()
        amount = float(args[1])
        price = float(args[2])
        from portfolio import ensure_tables, log_buy, _fmt_price
        ensure_tables()
        log_buy(token, amount, price)
        await update.message.reply_text(f"✅ Bought {amount:,.0f} {token} at {_fmt_price(price)}", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/bought error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_sold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a sale: /sold TOKEN AMOUNT PRICE"""
    try:
        args = context.args
        if not args or len(args) < 3:
            await update.message.reply_text("Usage: /sold TOKEN AMOUNT PRICE\nExample: /sold JUP 500 0.25", reply_markup=MAIN_KEYBOARD)
            return
        token = args[0].upper()
        amount = float(args[1])
        price = float(args[2])
        from portfolio import ensure_tables, log_sell, _fmt_price
        ensure_tables()
        log_sell(token, amount, price)
        await update.message.reply_text(f"✅ Sold {amount:,.0f} {token} at {_fmt_price(price)}", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/sold error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_sunflow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show SunFlow whale conviction scores."""
    try:
        from sunflow_telegram import get_conviction_summary
        await update.message.reply_text(get_conviction_summary(), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/sunflow error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_exits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check exit alert status for all positions."""
    try:
        from exit_alerts import run_exit_check, format_exit_status_telegram
        result = run_exit_check()
        await update.message.reply_text(
            format_exit_status_telegram(result["positions"], result["alerts"], result["circuit_breaker"]),
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/exits error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_yields(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show USDC yield opportunities."""
    try:
        from dry_powder import run_yield_monitor, format_yields_telegram
        yields = run_yield_monitor()
        await update.message.reply_text(format_yields_telegram(yields), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/yields error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show auto-updated token scores."""
    try:
        from token_scores import run_score_update, format_scores_telegram
        scores = run_score_update()
        await update.message.reply_text(format_scores_telegram(scores), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/scores error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyse any YouTube video: /analyse <URL>"""
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /analyse <YouTube URL>\nExample: /analyse https://youtube.com/watch?v=abc123", reply_markup=MAIN_KEYBOARD)
            return
        url = args[0]

        import re
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if not match:
            await update.message.reply_text("Invalid YouTube URL", reply_markup=MAIN_KEYBOARD)
            return
        video_id = match.group(1)

        await update.message.reply_text("Downloading transcript + running Sonnet analysis...\nThis takes 30-60s for long videos.", reply_markup=MAIN_KEYBOARD)

        from social.youtube_free import _download_captions, _analyse_transcript

        transcript = _download_captions(url, video_id)
        if not transcript or len(transcript) < 100:
            await update.message.reply_text("Could not get transcript for this video. Check if it has captions.", reply_markup=MAIN_KEYBOARD)
            return

        title = "Unknown"
        try:
            from config import YOUTUBE_API_KEY
            if YOUTUBE_API_KEY:
                r = req.get(f"https://www.googleapis.com/youtube/v3/videos?id={video_id}&key={YOUTUBE_API_KEY}&part=snippet", timeout=10)
                if r.ok:
                    items = r.json().get("items", [])
                    if items:
                        title = items[0].get("snippet", {}).get("title", "Unknown")
        except Exception:
            pass

        await update.message.reply_text(f"Transcript: {len(transcript):,} chars\nTitle: {title}\nAnalysing with Sonnet...", reply_markup=MAIN_KEYBOARD)

        result = _analyse_transcript(transcript, title, channel_name="All-In Podcast")

        if result and result.get("_essay_format"):
            text = result["summary"]
            header = f"\U0001f4fa <b>VIDEO ANALYSIS</b>\n\U0001f3ac \"{title}\"\n\n"
            full = header + text

            max_len = 4000
            if len(full) <= max_len:
                chunks = [full]
            else:
                chunks = []
                current = header
                for para in text.split("\n\n"):
                    if current and len(current) + len(para) + 2 > max_len:
                        chunks.append(current)
                        current = ""
                    current = current + "\n\n" + para if current else para
                if current:
                    chunks.append(current)

            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True, reply_markup=MAIN_KEYBOARD)
        elif result:
            import json
            text = json.dumps(result, indent=2, default=str)[:4000]
            await update.message.reply_text(f"<pre>{text}</pre>", parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("Analysis failed. The video may be too short or in a non-English language.", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/analyse error: %s", e)
        await update.message.reply_text(f"Error: {e}", reply_markup=MAIN_KEYBOARD)


async def cmd_deepdive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deep dive a token: /deepdive <contract_address>"""
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /deepdive <contract_address>\nExample: /deepdive JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", reply_markup=MAIN_KEYBOARD)
            return
        address = args[0]
        if len(address) < 20:
            await update.message.reply_text("Invalid address — need full Solana contract address", reply_markup=MAIN_KEYBOARD)
            return
        await update.message.reply_text(f"Diving into <code>{address[:20]}...</code> (10-15s)", parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        from deepdive import run_deepdive, format_deepdive_telegram
        result = run_deepdive(address)
        msg = format_deepdive_telegram(result)
        if len(msg) > 4000:
            await update.message.reply_text(msg[:4000], parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
            if len(msg) > 4000:
                await update.message.reply_text(msg[4000:], parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/deepdive error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show 4h pulse — lightweight summary."""
    try:
        from outputs import generate_pulse
        await update.message.reply_text(generate_pulse(), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/pulse error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekly review — performance + accuracy."""
    await update.message.reply_text("Generating weekly review...", reply_markup=MAIN_KEYBOARD)
    try:
        from outputs import generate_weekly_review, _split_message
        msg = generate_weekly_review()
        if len(msg) > 4000:
            for chunk in _split_message(msg):
                await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/weekly error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_chains(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show cross-chain scorecard."""
    try:
        from cross_chain import run_cross_chain, format_cross_chain_telegram
        result = run_cross_chain()
        await update.message.reply_text(
            format_cross_chain_telegram(result["data"], result["alerts"]),
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/chains error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_synthesis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run synthesis engine on demand."""
    await update.message.reply_text("Running synthesis engine (15-20s)...", reply_markup=MAIN_KEYBOARD)
    try:
        from synthesis import run_synthesis, format_synthesis_telegram
        result = run_synthesis()
        if "output" in result:
            msg = format_synthesis_telegram(result["output"])
            if len(msg) > 4000:
                await update.message.reply_text(msg[:4000], parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
                if len(msg) > 4000:
                    await update.message.reply_text(msg[4000:], parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
            else:
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("Synthesis failed: " + result.get("error", "unknown"), reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/synthesis error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_supply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show supply flow monitor."""
    try:
        from supply_flow import run_supply_flow, format_supply_telegram
        data = run_supply_flow()
        await update.message.reply_text(
            format_supply_telegram(data["hype"], data["pump_cliff"], data["penalties"]),
            parse_mode="HTML",
            reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/supply error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show YouTube intelligence summary."""
    try:
        from youtube_intel import run_youtube_intel, format_youtube_telegram
        intel = run_youtube_intel()
        await update.message.reply_text(format_youtube_telegram(intel), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/youtube error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market structure (OI, funding, L/S, F&G)."""
    try:
        from market_structure import run_market_structure, format_market_structure_telegram
        data = run_market_structure()
        if data:
            await update.message.reply_text(format_market_structure_telegram(data), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("Market structure data unavailable", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/market error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_defi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show DeFiLlama market data."""
    await update.message.reply_text("Fetching DeFi data...", reply_markup=MAIN_KEYBOARD)
    try:
        from defi_llama import run_defi_tracker, format_defi_telegram
        data = run_defi_tracker()
        if data:
            await update.message.reply_text(format_defi_telegram(data), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("DeFi data unavailable", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/defi error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show latest X Intel briefing."""
    try:
        from x_intel_v4 import get_latest_briefing
        briefing = get_latest_briefing()
        if briefing:
            await update.message.reply_text(
                "\U0001f4e1 <b>LATEST X INTEL</b>\n\n" + briefing,
                parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("No X Intel briefing yet. Next one at the next 4h mark.", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/signals error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_convergence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show convergence signals (X + on-chain agreement)."""
    try:
        from convergence import detect_convergence, format_convergence_telegram
        results = detect_convergence(hours=24)
        if results:
            msg = format_convergence_telegram(results)
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
        else:
            await update.message.reply_text("No convergence signals in last 24h", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/convergence error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_deepdive_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The Headband research library: /deepdive [TOKEN] [full]"""
    import asyncio as _asyncio
    try:
        from research.research_manager import get_summary_card, get_full_document_chunks, get_scorecard
        args = context.args if context.args else []

        if not args:
            await update.message.reply_text(
                "\u2501\u2501\u2501 THE HEADBAND \u2501\u2501\u2501\n\n"
                "Usage:\n"
                "/deepdive all \u2014 Scorecard\n"
                "/deepdive BTC \u2014 Summary card\n"
                "/deepdive BTC full \u2014 Full document\n",
                reply_markup=MAIN_KEYBOARD)
            return

        token = args[0].upper()

        if token == "ALL":
            card = get_scorecard()
            await update.message.reply_text(card, reply_markup=MAIN_KEYBOARD)
            return

        if len(args) > 1 and args[1].lower() == "full":
            chunks = get_full_document_chunks(token)
            for chunk in chunks:
                await update.message.reply_text(chunk, reply_markup=MAIN_KEYBOARD)
                await _asyncio.sleep(0.5)
            return

        card = get_summary_card(token)
        await update.message.reply_text(card, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/deepdive error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force retry all failed YouTube transcript downloads."""
    await update.message.reply_text("\U0001f504 Retrying failed videos (48h)...", reply_markup=MAIN_KEYBOARD)
    try:
        from social.youtube_free import retry_failed_videos
        results = retry_failed_videos(max_age_hours=48)
        if not results:
            await update.message.reply_text("No failed videos to retry.", reply_markup=MAIN_KEYBOARD)
            return
        lines = ["\U0001f504 <b>RETRY RESULTS</b>\n"]
        recovered = 0
        for r in results:
            if r["status"] == "recovered":
                recovered += 1
                lines.append("\u2705 %s \u2014 \"%s\" \u2014 %s chars recovered" % (
                    r["channel"], r["title"][:35], "{:,}".format(r["chars"])))
            else:
                lines.append("\u274c %s \u2014 still no captions" % r["channel"])
        lines.append("\nRecovered: %d/%d" % (recovered, len(results)))
        if recovered > 0:
            lines.append("Now analysing recovered videos...")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        log.error("/retry error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_notebook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send the daily notebook for Opus."""
    await update.message.reply_text("Generating notebook...", reply_markup=MAIN_KEYBOARD)
    try:
        from notebook import send_notebook
        send_notebook()
    except Exception as e:
        log.error("/notebook error: %s", e)
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system health dashboard."""
    try:
        from system_health import generate_health_dashboard
        await update.message.reply_text(generate_health_dashboard(), reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        await update.message.reply_text("Error: " + str(e), reply_markup=MAIN_KEYBOARD)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show v5 commands and ensure persistent keyboard."""
    msg = (
        "🔥 <b>FIERY EYES v6.0</b>\n\n"
        "<b>Reports:</b>\n"
        "/report — daily intelligence report\n"
        "/cycle — BTC cycle position\n"
        "/watchlist — token prices & zones\n"
        "/liquidity — FRED liquidity data\n\n"
        "<b>Portfolio:</b>\n"
        "/bought TOKEN AMT PRICE\n"
        "/sold TOKEN AMT PRICE\n"
        "/portfolio — positions vs targets\n"
        "/pnl — unrealised PnL\n\n"
        "<b>Research:</b>\n"
        "/exits \u2014 stop loss / take profit status\n"
        "/yields \u2014 USDC yield opportunities\n"
        "/scores \u2014 auto-updated token scores\n"
        "/analyse URL \u2014 analyse any YouTube video\n"
        "/deepdive TOKEN \u2014 research summary\n"
        "/deepdive all \u2014 full scorecard\n"
        "\n"
        "<b>Tracking:</b>\n"
        "/ledger — recent recommendations\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------
def _run_scheduled():
    """Background thread for scheduled tasks."""
    log.info("Scheduler thread started")

    def job_convergence_check():
        """Every 30min: check for convergence signals (free — DB only)."""
        try:
            from convergence import run_convergence_check
            run_convergence_check(hours=12, send_to_telegram=True)
        except Exception as e:
            log.error("Convergence check failed: %s", e)

    def job_4h():
        """Every 4 hours: update watchlist, check for swaps, market structure."""
        log.info("Running 4h job: watchlist + swap detection + market structure")
        try:
            from watchlist import run_watchlist
            run_watchlist(send_to_telegram=False)
        except Exception as e:
            log.error("4h watchlist failed: %s", e)
        try:
            from large_swaps import run_swap_detection
            run_swap_detection(send_to_telegram=True)
        except Exception as e:
            log.error("4h swap detection failed: %s", e)
        try:
            from market_structure import run_market_structure
            run_market_structure()
        except Exception as e:
            log.error("4h market structure failed: %s", e)

    def job_x_intel():
        """Every 4h (06,10,14,18,22): X Intel research briefing via Grok + Haiku."""
        log.info("Running INTEL BRIEFING (YouTube+SunFlow+Haiku)")
        try:
            from x_intel_v4 import run_x_intel_batch
            result = run_x_intel_batch(send_to_telegram=True)
            log.info("INTEL: %s (%d raw chars, %d summary words)",
                     result.get("status"), result.get("raw_chars", 0),
                     result.get("summary_words", 0))
        except Exception as e:
            log.error("X INTEL briefing failed: %s", e)

    def job_youtube_health():
        """Every 4h: YouTube health check report to Telegram."""
        try:
            from youtube_health import send_health_report
            send_health_report()
        except Exception as e:
            log.error("YouTube health check failed: %s", e)

    def job_youtube_watchdog():
        """Every 4h (independent of scan): check if YouTube pipeline is alive."""
        try:
            from youtube_watchdog import run_watchdog
            status = run_watchdog(send_alert=True)
            log.info("YouTube watchdog: %s", status.get("heartbeat", "?"))
        except Exception as e:
            log.error("YouTube watchdog failed: %s", e)

    def job_youtube_scan():
        """Every 2h: scan all channels for new videos, analyse, store in DB.
        Runs in a sub-thread with 30min timeout to prevent scheduler hangs."""
        log.info("Running YouTube auto-scan")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_youtube_scan_worker)
            try:
                future.result(timeout=1800)
            except concurrent.futures.TimeoutError:
                log.error("YouTube scan TIMED OUT after 30 minutes")
            except Exception as e:
                log.error("YouTube scan failed: %s", e)

    def _youtube_scan_worker():
        from social.youtube_free import run_youtube_scan
        result = run_youtube_scan(send_alerts=True)
        if result:
            log.info("YouTube scan: %d new videos processed", result.get("new_videos", 0))
        return result

    def job_nimbus_sync():
        """05:45 UTC: Sync Nimbus data from Jingubang (after autopull at 05:30)."""
        try:
            from nimbus_sync import run_sync
            result = run_sync()
            log.info("Nimbus sync: %s (as_of: %s)", result["status"], result.get("as_of_date"))
        except Exception as e:
            log.error("Nimbus sync failed: %s", e)

    def job_morning_brief():
        """06:00 UTC: Full morning brief — report + synthesis."""
        log.info("Running MORNING BRIEF")
        try:
            from daily_report import generate_report
            report = generate_report(send_to_telegram=False, report_type="morning")
            send_telegram_with_keyboard(report)
        except Exception as e:
            log.error("Morning brief report failed: %s", e)
        try:
            from defi_llama import run_defi_tracker
            run_defi_tracker()
        except Exception as e:
            log.error("Morning DeFi data failed: %s", e)
        try:
            from token_scores import run_score_update
            run_score_update()
        except Exception as e:
            log.error("Morning scores failed: %s", e)
        try:
            from synthesis import run_synthesis, format_synthesis_telegram
            result = run_synthesis()
            if result.get("output"):
                send_telegram_with_keyboard(format_synthesis_telegram(result["output"]))
        except Exception as e:
            log.error("Morning synthesis failed: %s", e)
        try:
            from rec_ledger import run_log_daily
            run_log_daily()
        except Exception as e:
            log.error("Morning rec logging failed: %s", e)

    def job_evening_review():
        """20:00 UTC: Evening review + notebook auto-send."""
        log.info("Running EVENING REVIEW")
        try:
            from daily_report import generate_report
            report = generate_report(send_to_telegram=False, report_type="evening")
            send_telegram_with_keyboard(report)
        except Exception as e:
            log.error("Evening review report failed: %s", e)
        # Auto-send notebook for Opus paste
        log.info("Auto-sending notebook")
        try:
            from notebook import send_notebook
            send_notebook()
        except Exception as e:
            log.error("Notebook auto-send failed: %s", e)

    def job_weekly():
        """Weekly: cross-chain + full review."""
        log.info("Running weekly review")
        try:
            from outputs import generate_weekly_review
            msg = generate_weekly_review()
            send_telegram_with_keyboard(msg)
        except Exception as e:
            log.error("Weekly review failed: %s", e)

    # ━━━ SCHEDULE ━━━
    # Data collection
    # Grok polling removed — $0.55/call x_search surcharge
    schedule.every(30).minutes.do(job_convergence_check)
    schedule.every(4).hours.do(job_4h)

    # Nimbus sync (before morning brief)
    schedule.every().day.at("05:45").do(job_nimbus_sync)

    # Morning Brief: 06:00 UTC (full report + synthesis)
    schedule.every().day.at("06:00").do(job_morning_brief)

    # X Intel: every 4h batched
    schedule.every().day.at("06:00").do(job_x_intel)
    schedule.every().day.at("10:00").do(job_x_intel)
    schedule.every().day.at("14:00").do(job_x_intel)
    schedule.every().day.at("18:00").do(job_x_intel)
    schedule.every().day.at("22:00").do(job_x_intel)

    # YouTube watchdog (independent of scan, fixed 4h marks)
    schedule.every().day.at("07:00").do(job_youtube_watchdog)
    schedule.every().day.at("11:00").do(job_youtube_watchdog)
    schedule.every().day.at("15:00").do(job_youtube_watchdog)
    schedule.every().day.at("19:00").do(job_youtube_watchdog)

    # YouTube health check (same times as X Intel)
    schedule.every().day.at("06:00").do(job_youtube_health)
    schedule.every().day.at("14:00").do(job_youtube_health)
    schedule.every().day.at("22:00").do(job_youtube_health)

    # Evening Review: 20:00 UTC
    schedule.every().day.at("20:00").do(job_evening_review)

    # Weekly: Sunday 08:00 UTC
    schedule.every().sunday.at("08:00").do(job_weekly)

    # YouTube: every 2h scan, no individual Telegram sends
    schedule.every(2).hours.do(job_youtube_scan)
    # H-Fire alerts: immediate via convergence check in job_grok_high

    # Run initial data collection on startup
    job_4h()


    # Grace period: if we started within 30min of a scheduled slot, run it
    now_utc = datetime.now(timezone.utc)
    hour_min = now_utc.hour * 60 + now_utc.minute
    # Morning brief at 06:00 = 360min
    if 360 <= hour_min <= 390:
        log.info("Startup within morning brief window — running brief")
        job_nimbus_sync()
        job_morning_brief()
        job_x_intel()
    # Evening review at 20:00 = 1200min
    elif 1200 <= hour_min <= 1230:
        log.info("Startup within evening review window — running review")
        job_evening_review()

    log.info("Schedule configured:")
    log.info("  05:45  Nimbus sync")
    log.info("  06:00  Morning Brief + X Intel")
    log.info("  10:00  X Intel")
    log.info("  14:00  X Intel")
    log.info("  18:00  X Intel")
    log.info("  20:00  Evening Review")
    log.info("  22:00  X Intel")
    log.info("  Sun 08:00  Weekly")
    log.info("  Every 2h   YouTube scan")
    log.info("  Every 30m  Convergence check")
    log.info("  Every 4h   Watchlist + swaps")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            log.error("Scheduler tick error: %s", e)
        time.sleep(60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    """Start the v5 bot and scheduler."""
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set")
        return

    log.info("Starting Fiery Eyes v6.0 bot...")

    # Start SunFlow Telegram listener in background thread
    def _run_sunflow():
        try:
            from sunflow_telegram import run_listener
            log.info("Starting SunFlow Telegram listener...")
            run_listener()
        except Exception as e:
            log.error("SunFlow listener failed: %s", e)

    sunflow_thread = threading.Thread(target=_run_sunflow, daemon=True)
    sunflow_thread.start()

    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=_run_scheduled, daemon=True)
    scheduler_thread.start()

    # Build and start Telegram bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("cycle", cmd_cycle))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("liquidity", cmd_liquidity))
    app.add_handler(CommandHandler("ledger", cmd_ledger))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("bought", cmd_bought))
    app.add_handler(CommandHandler("sold", cmd_sold))
    app.add_handler(CommandHandler("sunflow", cmd_sunflow))
    app.add_handler(CommandHandler("exits", cmd_exits))
    app.add_handler(CommandHandler("yields", cmd_yields))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("analyse", cmd_analyse))
    app.add_handler(CommandHandler("analyze", cmd_analyse))
    app.add_handler(CommandHandler("deepdive", cmd_deepdive_research))
    app.add_handler(CommandHandler("dd", cmd_deepdive_research))
    app.add_handler(CommandHandler("pulse", cmd_pulse))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("chains", cmd_chains))
    app.add_handler(CommandHandler("synthesis", cmd_synthesis))
    app.add_handler(CommandHandler("supply", cmd_supply))
    app.add_handler(CommandHandler("youtube", cmd_youtube))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("defi", cmd_defi))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("convergence", cmd_convergence))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("notebook", cmd_notebook))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("start", cmd_help))

    # Callback and menu handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'^(📊 Intel|🐋 Signals|🔥 Fiery Eyes|💼 Portfolio|⚙️ System)$'),
        handle_menu_text))

    async def post_init(application):
        from telegram import BotCommand
        try:
            await application.bot.set_my_commands([
                BotCommand("menu", "Open main menu"),
                BotCommand("report", "Full daily report"),
                BotCommand("cycle", "BTC cycle position"),
                BotCommand("watchlist", "Token prices and zones"),
                BotCommand("portfolio", "My positions"),
                BotCommand("analyse", "Analyse any YouTube URL"),
            ])
            log.info("Bot commands registered with Telegram")
        except Exception as e:
            log.error("Failed to register bot commands: %s", e)

    app.post_init = post_init
    log.info("Bot started. Polling for commands...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
