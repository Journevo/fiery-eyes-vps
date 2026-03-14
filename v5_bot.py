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
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

log = get_logger("v5_bot")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send daily intelligence report."""
    await update.message.reply_text("⏳ Generating report...")
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
        await update.message.reply_text(f"⚠️ Report failed: {e}")


async def cmd_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show BTC cycle position."""
    try:
        from btc_cycle import run_cycle_tracker, format_cycle_telegram
        cycle = run_cycle_tracker()
        if cycle:
            await update.message.reply_text(format_cycle_telegram(cycle),
                                             parse_mode="HTML")
        else:
            await update.message.reply_text("⚠️ BTC price unavailable")
    except Exception as e:
        log.error("/cycle error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show watchlist prices and zones."""
    try:
        from watchlist import run_watchlist, format_watchlist_telegram
        prices = run_watchlist()
        if prices:
            await update.message.reply_text(format_watchlist_telegram(prices),
                                             parse_mode="HTML")
        else:
            await update.message.reply_text("⚠️ No prices available")
    except Exception as e:
        log.error("/watchlist error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_liquidity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show FRED liquidity data."""
    try:
        from liquidity import run_liquidity_tracker, format_liquidity_telegram
        data = run_liquidity_tracker()
        if data:
            await update.message.reply_text(format_liquidity_telegram(data),
                                             parse_mode="HTML")
        else:
            await update.message.reply_text("⚠️ Liquidity data unavailable")
    except Exception as e:
        log.error("/liquidity error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent recommendations."""
    try:
        from rec_ledger import ensure_tables, format_recent_recs_telegram
        ensure_tables()
        msg = format_recent_recs_telegram()
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.error("/ledger error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show portfolio positions."""
    try:
        from portfolio import ensure_tables, get_portfolio, format_portfolio_telegram
        ensure_tables()
        portfolio = get_portfolio()
        msg = format_portfolio_telegram(portfolio)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.error("/portfolio error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show unrealised PnL."""
    try:
        from portfolio import ensure_tables, get_portfolio, format_pnl_telegram
        ensure_tables()
        portfolio = get_portfolio()
        msg = format_pnl_telegram(portfolio)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.error("/pnl error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_bought(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a purchase: /bought TOKEN AMOUNT PRICE"""
    try:
        args = context.args
        if not args or len(args) < 3:
            await update.message.reply_text("Usage: /bought TOKEN AMOUNT PRICE\nExample: /bought JUP 1000 0.166")
            return
        token = args[0].upper()
        amount = float(args[1])
        price = float(args[2])
        from portfolio import ensure_tables, log_buy, _fmt_price
        ensure_tables()
        log_buy(token, amount, price)
        await update.message.reply_text(f"✅ Bought {amount:,.0f} {token} at {_fmt_price(price)}")
    except Exception as e:
        log.error("/bought error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_sold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log a sale: /sold TOKEN AMOUNT PRICE"""
    try:
        args = context.args
        if not args or len(args) < 3:
            await update.message.reply_text("Usage: /sold TOKEN AMOUNT PRICE\nExample: /sold JUP 500 0.25")
            return
        token = args[0].upper()
        amount = float(args[1])
        price = float(args[2])
        from portfolio import ensure_tables, log_sell, _fmt_price
        ensure_tables()
        log_sell(token, amount, price)
        await update.message.reply_text(f"✅ Sold {amount:,.0f} {token} at {_fmt_price(price)}")
    except Exception as e:
        log.error("/sold error: %s", e)
        await update.message.reply_text(f"⚠️ Error: {e}")


async def cmd_deepdive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deep dive a token: /deepdive <contract_address>"""
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /deepdive <contract_address>\nExample: /deepdive JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN")
            return
        address = args[0]
        if len(address) < 20:
            await update.message.reply_text("Invalid address — need full Solana contract address")
            return
        await update.message.reply_text(f"Diving into <code>{address[:20]}...</code> (10-15s)", parse_mode="HTML")
        from deepdive import run_deepdive, format_deepdive_telegram
        result = run_deepdive(address)
        msg = format_deepdive_telegram(result)
        if len(msg) > 4000:
            await update.message.reply_text(msg[:4000], parse_mode="HTML")
            if len(msg) > 4000:
                await update.message.reply_text(msg[4000:], parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.error("/deepdive error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show 4h pulse — lightweight summary."""
    try:
        from outputs import generate_pulse
        await update.message.reply_text(generate_pulse(), parse_mode="HTML")
    except Exception as e:
        log.error("/pulse error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekly review — performance + accuracy."""
    await update.message.reply_text("Generating weekly review...")
    try:
        from outputs import generate_weekly_review, send_telegram, _split_message
        msg = generate_weekly_review()
        if len(msg) > 4000:
            for chunk in _split_message(msg):
                await update.message.reply_text(chunk, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.error("/weekly error: %s", e)
        await update.message.reply_text("Error: " + str(e))


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
        await update.message.reply_text("Error: " + str(e))


async def cmd_synthesis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run synthesis engine on demand."""
    await update.message.reply_text("Running synthesis engine (15-20s)...")
    try:
        from synthesis import run_synthesis, format_synthesis_telegram
        result = run_synthesis()
        if "output" in result:
            msg = format_synthesis_telegram(result["output"])
            # Split if needed
            if len(msg) > 4000:
                await update.message.reply_text(msg[:4000], parse_mode="HTML")
                if len(msg) > 4000:
                    await update.message.reply_text(msg[4000:], parse_mode="HTML")
            else:
                await update.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text("Synthesis failed: " + result.get("error", "unknown"))
    except Exception as e:
        log.error("/synthesis error: %s", e)
        await update.message.reply_text("Error: " + str(e))


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
        await update.message.reply_text("Error: " + str(e))


async def cmd_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show YouTube intelligence summary."""
    try:
        from youtube_intel import run_youtube_intel, format_youtube_telegram
        intel = run_youtube_intel()
        await update.message.reply_text(format_youtube_telegram(intel), parse_mode="HTML")
    except Exception as e:
        log.error("/youtube error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market structure (OI, funding, L/S, F&G)."""
    try:
        from market_structure import run_market_structure, format_market_structure_telegram
        data = run_market_structure()
        if data:
            await update.message.reply_text(format_market_structure_telegram(data), parse_mode="HTML")
        else:
            await update.message.reply_text("Market structure data unavailable")
    except Exception as e:
        log.error("/market error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_defi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show DeFiLlama market data."""
    await update.message.reply_text("Fetching DeFi data...")
    try:
        from defi_llama import run_defi_tracker, format_defi_telegram
        data = run_defi_tracker()
        if data:
            await update.message.reply_text(format_defi_telegram(data), parse_mode="HTML")
        else:
            await update.message.reply_text("DeFi data unavailable")
    except Exception as e:
        log.error("/defi error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent X smart money signals."""
    try:
        from social.grok_poller import get_recent_x_signals
        signals = get_recent_x_signals(hours=12, min_strength="medium")
        if not signals:
            await update.message.reply_text("No medium/strong X signals in last 12h")
            return
        lines = ["<b>X SMART MONEY (12h)</b>", ""]
        for s in signals[:10]:
            sym = s.get("token_symbol") or "?"
            amt = ""
            if s.get("amount_usd"):
                amt = " $" + "{:,.0f}".format(s["amount_usd"])
            lines.append(s["source_handle"] + " " + s["parsed_type"] + " $" + sym + amt + " [" + s["signal_strength"] + "]")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        log.error("/signals error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_convergence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show convergence signals (X + on-chain agreement)."""
    try:
        from convergence import detect_convergence, format_convergence_telegram
        results = detect_convergence(hours=24)
        if results:
            msg = format_convergence_telegram(results)
            await update.message.reply_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text("No convergence signals in last 24h")
    except Exception as e:
        log.error("/convergence error: %s", e)
        await update.message.reply_text("Error: " + str(e))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show v5 commands."""
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
        "/deepdive CA \u2014 full token analysis\n"
        "\n"
        "<b>Tracking:</b>\n"
        "/ledger — recent recommendations\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


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

    def job_daily():
        """Daily at 00:00 UTC: full report + log recommendations."""
        log.info("Running daily job: report + recommendations")
        try:
            from daily_report import generate_report
            generate_report(send_to_telegram=True)
        except Exception as e:
            log.error("Daily report failed: %s", e)
        try:
            from defi_llama import run_defi_tracker
            run_defi_tracker()
        except Exception as e:
            log.error("Daily DeFi data failed: %s", e)
        try:
            from synthesis import run_synthesis
            run_synthesis(send_to_telegram=True)
        except Exception as e:
            log.error("Daily synthesis failed: %s", e)
        try:
            from rec_ledger import run_log_daily
            run_log_daily()
        except Exception as e:
            log.error("Daily rec logging failed: %s", e)

    # Schedule jobs
    schedule.every(30).minutes.do(job_grok_high)
    schedule.every(2).hours.do(job_grok_medium)
    schedule.every(2).hours.do(job_youtube)
    schedule.every(4).hours.do(job_4h)
    def job_weekly():
        """Weekly: cross-chain + full review."""
        log.info("Running weekly review")
        try:
            from outputs import send_weekly
            send_weekly()
        except Exception as e:
            log.error("Weekly review failed: %s", e)

    schedule.every().sunday.at("08:00").do(job_weekly)
    schedule.every().day.at("00:00").do(job_daily)

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
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    log.info("Bot started. Polling for commands...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
