"""Fiery Eyes v5.1 — Telegram Bot + Scheduler

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

Scheduled:
- Every 4h: watchlist prices, swap detection
- Daily 00:00 UTC: full report + recommendation logging
"""

import threading
import time
import schedule
from datetime import datetime, timezone
from telegram import Update, Bot, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

log = get_logger("v5_bot")

# Persistent reply keyboard — always visible at bottom of chat
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📊 Intel", "🐋 Signals", "💼 Portfolio", "📈 Market", "🔧 Tools"]],
    resize_keyboard=True,
    is_persistent=True,
)




# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Persistent menu handlers
# ---------------------------------------------------------------------------
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle persistent keyboard button taps."""
    text = update.message.text

    if text == "📊 Intel":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Synthesis", callback_data="cmd_synthesis"),
            InlineKeyboardButton("Cycle", callback_data="cmd_cycle"),
            InlineKeyboardButton("Liquidity", callback_data="cmd_liquidity"),
            InlineKeyboardButton("Report", callback_data="cmd_report"),
        ]])
        await update.message.reply_text("📊 <b>Intelligence</b>", parse_mode="HTML",
                                         reply_markup=keyboard)

    elif text == "🐋 Signals":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Whale", callback_data="cmd_sunflow"),
            InlineKeyboardButton("Smart Money", callback_data="cmd_signals"),
            InlineKeyboardButton("YouTube", callback_data="cmd_youtube"),
            InlineKeyboardButton("Supply", callback_data="cmd_supply"),
        ]])
        await update.message.reply_text("🐋 <b>Signals</b>", parse_mode="HTML",
                                         reply_markup=keyboard)

    elif text == "💼 Portfolio":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Positions", callback_data="cmd_portfolio"),
            InlineKeyboardButton("PnL", callback_data="cmd_pnl"),
            InlineKeyboardButton("Bought", callback_data="tool_bought"),
            InlineKeyboardButton("Sold", callback_data="tool_sold"),
        ]])
        await update.message.reply_text("💼 <b>Portfolio</b>", parse_mode="HTML",
                                         reply_markup=keyboard)

    elif text == "📈 Market":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Watchlist", callback_data="cmd_watchlist"),
            InlineKeyboardButton("DeFi", callback_data="cmd_defi"),
            InlineKeyboardButton("Market", callback_data="cmd_market"),
            InlineKeyboardButton("Chains", callback_data="cmd_chains"),
        ]])
        await update.message.reply_text("📈 <b>Market</b>", parse_mode="HTML",
                                         reply_markup=keyboard)

    elif text == "🔧 Tools":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Analyse URL", callback_data="tool_analyse"),
            InlineKeyboardButton("Deepdive", callback_data="tool_deepdive"),
            InlineKeyboardButton("Ledger", callback_data="cmd_ledger"),
            InlineKeyboardButton("Help", callback_data="tool_help"),
        ]])
        await update.message.reply_text("🔧 <b>Tools</b>", parse_mode="HTML",
                                         reply_markup=keyboard)

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
        # Create a fake Update with the callback's message for reply
        class FakeUpdate:
            def __init__(self, message):
                self.message = message
        class FakeMessage:
            def __init__(self, chat_id, bot):
                self.chat_id = chat_id
                self._bot = bot
            async def reply_text(self, text, **kwargs):
                kwargs["reply_markup"] = MAIN_KEYBOARD
                await self._bot.send_message(self.chat_id, text, **kwargs)

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

    elif data == "tool_help":
        # Trigger the help command
        class FakeUpdate2:
            def __init__(self, msg):
                self.message = msg
        class FakeMsg2:
            def __init__(self, cid, bot):
                self.chat_id = cid
                self._bot = bot
            async def reply_text(self, text, **kwargs):
                kwargs["reply_markup"] = MAIN_KEYBOARD
                await self._bot.send_message(self.chat_id, text, **kwargs)
        await cmd_help(FakeUpdate2(FakeMsg2(query.message.chat_id, context.bot)), context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the persistent keyboard menu."""
    await update.message.reply_text(
        "🔥 <b>FIERY EYES v5.1</b>\nTap a button below:",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send daily intelligence report."""
    await update.message.reply_text("⏳ Generating report...", reply_markup=MAIN_KEYBOARD)
    try:
        from daily_report import generate_report
        report = generate_report(send_to_telegram=False)
        # Split if needed
        if len(report) > 4000:
            parts = report.split("\n━━━")
            current = parts[0]
            for part in parts[1:]:
                candidate = current + "\n━━━" + part
                if len(candidate) > 4000:
                    await update.message.reply_text(current, parse_mode="HTML",
                                                     disable_web_page_preview=True)
                    current = "━━━" + part
                else:
                    current = candidate
            if current:
                await update.message.reply_text(current, parse_mode="HTML",
                                                 disable_web_page_preview=True)
        else:
            await update.message.reply_text(report, parse_mode="HTML",
                                             disable_web_page_preview=True)
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
                                             parse_mode="HTML")
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
                                             parse_mode="HTML")
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
                                             parse_mode="HTML")
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
            parse_mode="HTML")
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

        # Extract video ID
        import re
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if not match:
            await update.message.reply_text("Invalid YouTube URL", reply_markup=MAIN_KEYBOARD)
            return
        video_id = match.group(1)

        await update.message.reply_text(f"Downloading transcript + running Sonnet analysis...\nThis takes 30-60s for long videos.", reply_markup=MAIN_KEYBOARD)

        from social.youtube_free import _download_captions, _analyse_transcript, SONNET_ANALYSIS_PROMPT
        import requests as req

        # Get transcript
        transcript = _download_captions(url, video_id)
        if not transcript or len(transcript) < 100:
            await update.message.reply_text("Could not get transcript for this video. Check if it has captions.", reply_markup=MAIN_KEYBOARD)
            return

        # Get video title
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

        # Analyse — force Sonnet + full transcript
        result = _analyse_transcript(transcript, title, channel_name="All-In Podcast")  # Use priority channel name to trigger Sonnet

        if result and result.get("_essay_format"):
            text = result["summary"]
            header = f"\U0001f4fa <b>VIDEO ANALYSIS</b>\n<i>{title}</i>\n\n"
            full = header + text

            # Split at paragraphs
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
            # JSON format fallback
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
        from outputs import generate_weekly_review, send_telegram, _split_message
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
            parse_mode="HTML")
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
            # Split if needed
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
            parse_mode="HTML")
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
    """Show recent X smart money signals."""
    try:
        from social.grok_poller import get_recent_x_signals
        signals = get_recent_x_signals(hours=12, min_strength="medium")
        if not signals:
            await update.message.reply_text("No medium/strong X signals in last 12h", reply_markup=MAIN_KEYBOARD)
            return
        lines = ["<b>X SMART MONEY (12h)</b>", ""]
        for s in signals[:10]:
            sym = s.get("token_symbol") or "?"
            amt = ""
            if s.get("amount_usd"):
                amt = " $" + "{:,.0f}".format(s["amount_usd"])
            lines.append(s["source_handle"] + " " + s["parsed_type"] + " $" + sym + amt + " [" + s["signal_strength"] + "]")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=MAIN_KEYBOARD)
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


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show v5 commands and ensure persistent keyboard."""
    msg = (
        "🔥 <b>FIERY EYES v5.1</b>\n\n"
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
        "/deepdive CA \u2014 full token analysis\n"
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

    def job_4h():
        """Every 4 hours: update watchlist, check for swaps, send pulse."""
        log.info("Running 4h job: watchlist + swap detection + pulse")
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
        except Exception as e:
            log.error("4h swap detection failed: %s", e)

    def job_grok_high():
        """Every 30 min: poll Tier 1 specialized + HIGH generic accounts."""
        log.info("Running Grok HIGH tier poll")
        try:
            from social.grok_poller import run_smart_money_poll_high
            result = run_smart_money_poll_high()
            log.info("Grok HIGH: %d new signals", result.get("total_signals", 0))
        except Exception as e:
            log.error("Grok HIGH poll failed: %s", e)
        # Check convergence after polling
        try:
            from convergence import run_convergence_check
            run_convergence_check(hours=12, send_to_telegram=True)
        except Exception as e:
            log.error("Convergence check failed: %s", e)

    def job_grok_medium():
        """Every 2 hours: poll MEDIUM generic accounts."""
        log.info("Running Grok MEDIUM tier poll")
        try:
            from social.grok_poller import run_smart_money_poll_medium
            result = run_smart_money_poll_medium()
            log.info("Grok MEDIUM: %d new signals", result.get("total_signals", 0))
        except Exception as e:
            log.error("Grok MEDIUM poll failed: %s", e)

    def job_youtube():
        """Every 2 hours: scan YouTube channels for new videos."""
        log.info("Running YouTube scan")
        try:
            from social.youtube_free import run_youtube_scan
            run_youtube_scan()
        except Exception as e:
            log.error("YouTube scan failed: %s", e)

    def job_morning_brief():
        """06:00 UTC (6am UK): Full morning brief — overnight synthesis, what to watch."""
        log.info("Running MORNING BRIEF")
        try:
            from daily_report import generate_report
            generate_report(send_to_telegram=True, report_type="morning")
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
            from synthesis import run_synthesis
            run_synthesis(send_to_telegram=True)
        except Exception as e:
            log.error("Morning synthesis failed: %s", e)
        try:
            from rec_ledger import run_log_daily
            run_log_daily()
        except Exception as e:
            log.error("Morning rec logging failed: %s", e)

    def job_evening_review():
        """20:00 UTC (8pm UK): Evening review — day recap, changes, actions."""
        log.info("Running EVENING REVIEW")
        try:
            from daily_report import generate_report
            generate_report(send_to_telegram=True, report_type="evening")
        except Exception as e:
            log.error("Evening review report failed: %s", e)

    # Schedule jobs
    schedule.every(30).minutes.do(job_grok_high)
    schedule.every(2).hours.do(job_grok_medium)
    schedule.every(2).hours.do(job_youtube)
    schedule.every(4).hours.do(job_4h)
    schedule.every().day.at("06:00").do(job_x_intel)
    schedule.every().day.at("10:00").do(job_x_intel)
    schedule.every().day.at("14:00").do(job_x_intel)
    schedule.every().day.at("18:00").do(job_x_intel)
    schedule.every().day.at("22:00").do(job_x_intel)
    def job_x_intel():
        """Every 4h (06,10,14,18,22): consolidated X intelligence batch."""
        log.info("Running X INTEL batch")
        try:
            from db.connection import execute
            # Get X signals from last 4 hours for watchlist tokens
            watchlist = {"JUP","HYPE","RENDER","BONK","SOL","BTC","PUMP","PENGU","FARTCOIN"}
            rows = execute("""
                SELECT token_symbol, source_handle, parsed_type, amount_usd,
                       signal_strength, signal_category
                FROM x_intelligence
                WHERE detected_at > NOW() - INTERVAL '4 hours'
                  AND signal_strength IN ('medium', 'strong')
                  AND token_symbol IS NOT NULL
                ORDER BY detected_at DESC
            """, fetch=True)

            if not rows:
                log.info("X INTEL: nothing notable in last 4h, skipping")
                return

            wl_signals = []
            macro_signals = []
            large_signals = []
            for token, source, ptype, amount, strength, category in rows:
                tok = (token or "").upper()
                entry = f"{source}: {ptype} ${tok}"
                if amount and amount > 0 and amount < 1e11:
                    entry += f" (${amount:,.0f})"
                entry += f" [{strength}]"

                if tok in watchlist:
                    wl_signals.append(entry)
                elif category in ("macro", "geopolitical", "regulation"):
                    macro_signals.append(entry)
                elif amount and amount >= 5_000_000:
                    large_signals.append(entry)

            if not wl_signals and not macro_signals and not large_signals:
                log.info("X INTEL: no watchlist/macro/large signals, skipping")
                return

            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            lines = [f"\U0001f4e1 <b>X INTEL \u2014 {now}</b>", ""]

            if wl_signals:
                lines.append("<b>Watchlist:</b>")
                for s in wl_signals[:8]:
                    lines.append(f"  {s}")

            if macro_signals:
                lines.append("\n<b>Macro/Geo:</b>")
                for s in macro_signals[:5]:
                    lines.append(f"  {s}")

            if large_signals:
                lines.append("\n<b>Notable (>$5M):</b>")
                for s in large_signals[:3]:
                    lines.append(f"  {s}")

            msg = "\n".join(lines)

            import requests as req
            from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
            req.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg,
                      "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
            log.info("X INTEL batch sent: %d watchlist, %d macro, %d large",
                     len(wl_signals), len(macro_signals), len(large_signals))
        except Exception as e:
            log.error("X INTEL batch failed: %s", e)

    def job_weekly():
        """Weekly: cross-chain + full review."""
        log.info("Running weekly review")
        try:
            from outputs import send_weekly
            send_weekly()
        except Exception as e:
            log.error("Weekly review failed: %s", e)

    schedule.every().sunday.at("08:00").do(job_weekly)
    schedule.every().day.at("06:00").do(job_morning_brief)
    schedule.every().day.at("20:00").do(job_evening_review)

    # Run watchlist + first Grok poll on startup
    job_4h()
    job_grok_high()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    """Start the v5 bot and scheduler."""
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set")
        return

    log.info("Starting Fiery Eyes v5.1 bot...")

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
    app.add_handler(CommandHandler("deepdive", cmd_deepdive))
    app.add_handler(CommandHandler("dd", cmd_deepdive))
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
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'^(📊 Intel|🐋 Signals|💼 Portfolio|📈 Market|🔧 Tools)$'),
        handle_menu_text))
    app.add_handler(CommandHandler("start", cmd_help))

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
