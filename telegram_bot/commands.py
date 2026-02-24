"""Telegram Bot Commands — interactive command handler.

Commands:
  /scan <address>    — scan a token through Quality Gate
  /dd <address>      — generate Due Diligence card
  /regime            — current regime multiplier and components
  /top               — top 5 tokens by final score
  /status            — system health summary
  /portfolio         — current position summary
  /watch <track> <symbol> <contract> [name]  — add token to watchlist
  /unwatch <track> <symbol>                  — remove from watchlist
  /watchlist [track]                         — show watchlist
  /promote <address>                         — promote token lifecycle stage
  /lifecycle <address>                       — show token lifecycle stage
  /stages                                    — lifecycle stage summary
  /unlocks [symbol]                          — upcoming token unlocks
  /buybacks [symbol]                         — buyback/burn data
"""

import threading
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from telegram_bot.alerts import _send, send_message

log = get_logger("telegram.commands")


def _handle_scan(args: str):
    """Handle /scan <address> command."""
    mint = args.strip()
    if not mint or len(mint) < 32:
        _send("Usage: /scan <token_address>\nExample: /scan So111...112")
        return

    _send(f"🔍 Scanning <code>{mint}</code>...")

    try:
        from quality_gate.gate import run_gate
        from telegram_bot.alerts import send_gate_result
        result = run_gate(mint, category="meme")
        send_gate_result(result)
    except Exception as e:
        log.error("/scan error: %s", e)
        _send(f"⚠️ Scan failed: {e}")


def _handle_dd(args: str):
    """Handle /dd <address> command."""
    mint = args.strip()
    if not mint or len(mint) < 32:
        _send("Usage: /dd <token_address>\nExample: /dd So111...112")
        return

    _send(f"📋 Generating DD card for <code>{mint}</code>...")

    try:
        from reports.dd_card import generate_dd_card
        generate_dd_card(mint)
    except Exception as e:
        log.error("/dd error: %s", e)
        _send(f"⚠️ DD card generation failed: {e}")


def _handle_regime():
    """Handle /regime command."""
    try:
        from regime.multiplier import get_current_regime
        regime = get_current_regime()

        if not regime:
            _send("⚠️ No regime data available. Run regime calculation first.")
            return

        mult = regime["regime_multiplier"]
        comp = regime["components"]
        guidance = regime["allocation_guidance"]

        status_icons = {
            "full_allocation": "🟢 Full Allocation",
            "half_allocation": "🟡 Half Allocation",
            "tier_1_2_only": "🟠 Tier 1-2 Only",
            "cash_mode": "🔴 Cash Mode",
        }

        lines = [
            "📊 <b>REGIME STATUS</b>",
            "",
            f"Multiplier: <b>{mult:.3f}</b>",
            f"Guidance: {status_icons.get(guidance, guidance)}",
            "",
            "<b>Components:</b>",
            f"  BTC Trend: {comp.get('btc_trend', 'N/A')}",
            f"  Stablecoin Supply: {comp.get('stablecoin_supply', 'N/A')}",
            f"  Liquidity Proxy: {comp.get('liquidity_proxy', 'N/A')}",
            f"  Risk Appetite (F&G): {comp.get('risk_appetite', 'N/A')}",
        ]

        if comp.get("oi_leverage") is not None:
            lines.append(f"  OI Leverage: {comp['oi_leverage']}")

        # Add raw data if available
        raw = regime.get("raw_data", {})
        if raw.get("btc_price"):
            lines.extend([
                "",
                "<b>Market Data:</b>",
                f"  BTC: ${raw['btc_price']:,.0f}",
            ])
            if raw.get("fear_greed_value"):
                lines.append(f"  Fear & Greed: {raw['fear_greed_value']} ({raw.get('fear_greed_classification', '?')})")

        _send("\n".join(lines))
    except Exception as e:
        log.error("/regime error: %s", e)
        _send(f"⚠️ Regime lookup failed: {e}")


_CAT_ICON = {"meme": "🔥", "adoption": "📈", "infrastructure": "🏗"}


def _fmt_usd(value: float) -> str:
    """Human-readable USD: $1.2B, $45.3M, $120K."""
    if not value:
        return "$0"
    if value >= 1_000_000_000:
        return f"${value / 1e9:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1e6:.1f}M"
    if value >= 1_000:
        return f"${value / 1e3:.1f}K"
    if value >= 1:
        return f"${value:.2f}"
    if value > 0:
        return f"${value:.6f}".rstrip("0").rstrip(".")
    return "$0"


def _handle_top():
    """Handle /top command — rich display with names, mcap, links."""
    try:
        from db.connection import execute
        rows = execute(
            """SELECT t.symbol, t.name, t.contract_address, t.category,
                      s.composite_score, s.confidence_score, s.final_score,
                      s.momentum_score, s.adoption_score, s.infra_score,
                      snap.price, snap.mcap, snap.volume
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               LEFT JOIN snapshots_daily snap
                 ON snap.token_id = t.id AND snap.date = CURRENT_DATE
               WHERE s.date = CURRENT_DATE AND t.quality_gate_pass = TRUE
               ORDER BY COALESCE(s.final_score, s.composite_score) DESC NULLS LAST
               LIMIT 5""",
            fetch=True,
        )

        if not rows:
            _send("📊 No scored tokens today. Run scoring first.")
            return

        # For infra tokens without snapshots, fetch CoinGecko data
        _cg_cache = {}
        for row in rows:
            mint = row[2]
            cat = row[3]
            snap_mcap = row[11]
            if cat == "infrastructure" and not snap_mcap and mint:
                try:
                    from quality_gate.helpers import get_json
                    from config import COINGECKO_API_KEY
                    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
                    data = get_json(f"https://api.coingecko.com/api/v3/coins/{mint}", headers=headers)
                    m = data.get("market_data", {})
                    _cg_cache[mint] = {
                        "price": float(m.get("current_price", {}).get("usd", 0) or 0),
                        "mcap": float(m.get("market_cap", {}).get("usd", 0) or 0),
                        "volume": float(m.get("total_volume", {}).get("usd", 0) or 0),
                        "change_7d": m.get("price_change_percentage_7d"),
                    }
                except Exception:
                    pass

        lines = ["🏆 <b>Top 5 by Final Score</b>", ""]

        for i, row in enumerate(rows, 1):
            (sym, name, mint, cat, comp, conf, final,
             mom, adopt, infra, price, mcap, volume) = row

            # Use CoinGecko data for infra tokens missing snapshots
            cg = _cg_cache.get(mint, {})
            price = price or cg.get("price")
            mcap = mcap or cg.get("mcap")
            volume = volume or cg.get("volume")
            change_7d = cg.get("change_7d")

            score_val = final or comp or 0
            icon = _CAT_ICON.get(cat or "meme", "🔥")
            display_name = name or sym or "?"
            display_sym = sym or "?"

            # Header line
            lines.append(f"{i}. {icon} <b>{display_name}</b> (${display_sym}) — <b>{score_val:.0f}</b>/100")

            # Market data line
            market_parts = []
            if mcap:
                market_parts.append(f"MCap: {_fmt_usd(float(mcap))}")
            if volume:
                market_parts.append(f"Vol: {_fmt_usd(float(volume))}")
            if price:
                market_parts.append(f"Price: {_fmt_usd(float(price))}")
            if change_7d is not None:
                market_parts.append(f"{change_7d:+.1f}% 7d")
            if market_parts:
                lines.append(f"   {' | '.join(market_parts)}")

            # Engine scores line
            engine_parts = []
            if mom is not None:
                engine_parts.append(f"Mom {mom:.0f}")
            if adopt is not None:
                engine_parts.append(f"Adopt {adopt:.0f}")
            if infra is not None:
                engine_parts.append(f"Infra {infra:.0f}")
            engine_str = " | ".join(engine_parts) if engine_parts else "Scoring..."
            conf_val = conf or 0
            lines.append(f"   Engine: {engine_str} | Conf: {conf_val:.0f}%")

            # Links — only for Solana tokens (long addresses)
            if mint and len(mint) > 30:
                lines.append(
                    f'   🔗 <a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
                    f' | <a href="https://birdeye.so/token/{mint}">Birdeye</a>'
                )

            lines.append("")  # blank line between tokens

        _send("\n".join(lines))
    except Exception as e:
        log.error("/top error: %s", e)
        _send(f"⚠️ Top tokens lookup failed: {e}")


def _handle_status():
    """Handle /status command."""
    try:
        from db.connection import is_healthy, execute_one

        lines = ["🔧 <b>SYSTEM STATUS</b>", ""]

        # Database
        db_ok = is_healthy()
        lines.append(f"Database: {'🟢 Connected' if db_ok else '🔴 Disconnected'}")

        # Degraded mode
        try:
            from monitoring.degraded import is_degraded, get_health_summary
            degraded = is_degraded()
            lines.append(f"Mode: {'🔴 DEGRADED' if degraded else '🟢 Normal'}")

            summary = get_health_summary()
            sources = summary.get("sources", {})
            if sources:
                lines.append("")
                lines.append("<b>API Status:</b>")
                for name, info in sources.items():
                    status_icon = {"healthy": "🟢", "degraded": "🟡", "down": "🔴"}.get(
                        info["status"], "⚪")
                    lines.append(f"  {status_icon} {name}: {info['status']} ({info['failure_rate']:.0f}% fail)")
        except Exception:
            lines.append("Mode: 🟢 Normal")

        # Stats
        row = execute_one("SELECT COUNT(*) FROM tokens WHERE quality_gate_pass = TRUE")
        tracked = row[0] if row else 0
        lines.append(f"\nTracked tokens: {tracked}")

        # Watching count
        try:
            row = execute_one("SELECT COUNT(*) FROM tokens WHERE quality_gate_status = 'watching'")
            watching = row[0] if row else 0
            if watching:
                lines.append(f"Watching: {watching}")
        except Exception:
            pass

        row = execute_one(
            "SELECT COUNT(*) FROM alerts WHERE timestamp >= CURRENT_DATE"
        )
        alerts_today = row[0] if row else 0
        lines.append(f"Alerts today: {alerts_today}")

        row = execute_one(
            "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )
        positions = row[0] if row else 0
        lines.append(f"Open positions: {positions}")

        _send("\n".join(lines))
    except Exception as e:
        log.error("/status error: %s", e)
        _send(f"⚠️ Status check failed: {e}")


def _handle_portfolio():
    """Handle /portfolio command."""
    try:
        from risk.portfolio import get_portfolio_summary

        summary = get_portfolio_summary()
        tiers = summary["tiers"]

        lines = [
            "💼 <b>PORTFOLIO SUMMARY</b>",
            "",
            f"Total allocated: {summary['total_allocated_pct']:.1f}%",
            f"Cash: {summary['cash_pct']:.1f}%",
            f"Open positions: {summary['open_positions']}",
            "",
            "<b>By Tier:</b>",
        ]

        for tier_num in sorted(tiers.keys()):
            t = tiers[tier_num]
            lines.append(
                f"  T{tier_num} {t['name']}: {t['allocated_pct']:.1f}% / {t['target_pct']:.0f}% "
                f"({t['position_count']}/{t['max_positions']} pos)"
            )
            if t["tokens"]:
                lines.append(f"     {', '.join(t['tokens'][:5])}")

        _send("\n".join(lines))
    except Exception as e:
        log.error("/portfolio error: %s", e)
        _send(f"⚠️ Portfolio lookup failed: {e}")


# ---------------------------------------------------------------------------
# New commands: watchlists, lifecycle, unlocks
# ---------------------------------------------------------------------------

def _handle_watch(args: str):
    """Handle /watch <track> <symbol> <contract> [name] command."""
    parts = args.strip().split()
    if len(parts) < 3:
        _send("Usage: /watch <track> <symbol> <contract> [name]\n"
              "Tracks: adoption, infrastructure, momentum\n"
              "Example: /watch adoption JUP JUPyiwr... Jupiter")
        return

    track = parts[0]
    symbol = parts[1]
    contract = parts[2]
    name = " ".join(parts[3:]) if len(parts) > 3 else None

    try:
        from scanner.watchlists.manager import add_token
        success = add_token(track, symbol, contract, name=name)
        if success:
            _send(f"✅ Added <code>{symbol}</code> to {track} watchlist")
        else:
            _send(f"⚠️ <code>{symbol}</code> already on {track} watchlist")
    except Exception as e:
        log.error("/watch error: %s", e)
        _send(f"⚠️ Watch failed: {e}")


def _handle_unwatch(args: str):
    """Handle /unwatch <track> <symbol> command."""
    parts = args.strip().split()
    if len(parts) < 2:
        _send("Usage: /unwatch <track> <symbol>\nExample: /unwatch adoption JUP")
        return

    track = parts[0]
    symbol = parts[1]

    try:
        from scanner.watchlists.manager import remove_token
        success = remove_token(track, symbol)
        if success:
            _send(f"✅ Removed <code>{symbol}</code> from {track} watchlist")
        else:
            _send(f"⚠️ <code>{symbol}</code> not found on {track} watchlist")
    except Exception as e:
        log.error("/unwatch error: %s", e)
        _send(f"⚠️ Unwatch failed: {e}")


def _handle_watchlist(args: str):
    """Handle /watchlist [track] command."""
    track = args.strip() or None

    try:
        from scanner.watchlists.manager import handle_watchlist_command
        response = handle_watchlist_command(track)
        _send(response)
    except Exception as e:
        log.error("/watchlist error: %s", e)
        _send(f"⚠️ Watchlist lookup failed: {e}")


def _handle_promote(args: str):
    """Handle /promote <address> command — promote token lifecycle stage."""
    mint = args.strip()
    if not mint or len(mint) < 32:
        _send("Usage: /promote <token_address>")
        return

    try:
        from db.connection import execute_one
        from engines.lifecycle import detect_stage, promote_token

        row = execute_one(
            "SELECT id, symbol FROM tokens WHERE contract_address = %s", (mint,))
        if not row:
            _send(f"⚠️ Token not found: <code>{mint}</code>")
            return

        token_id, symbol = row
        stage = detect_stage(token_id, mint)
        if stage.get("promotion_ready"):
            new_stage = stage["stage"] + 1
            promote_token(token_id, new_stage, f"Manual promotion via /promote")
            _send(f"🎓 <code>{symbol}</code> promoted to Stage {new_stage}!")
        else:
            _send(f"⚠️ <code>{symbol}</code> not ready for promotion.\n"
                  f"Current: Stage {stage['stage']} ({stage['stage_name']})\n"
                  f"Missing: {', '.join(stage.get('criteria_missing', []))}")
    except Exception as e:
        log.error("/promote error: %s", e)
        _send(f"⚠️ Promotion failed: {e}")


def _handle_lifecycle(args: str):
    """Handle /lifecycle <address> command."""
    mint = args.strip()
    if not mint or len(mint) < 32:
        _send("Usage: /lifecycle <token_address>")
        return

    try:
        from db.connection import execute_one
        from engines.lifecycle import detect_stage

        row = execute_one(
            "SELECT id, symbol FROM tokens WHERE contract_address = %s", (mint,))
        if not row:
            _send(f"⚠️ Token not found: <code>{mint}</code>")
            return

        token_id, symbol = row
        stage = detect_stage(token_id, mint)
        stage_names = {1: "Birth", 2: "Viral", 3: "Community", 4: "Adoption", 5: "Infrastructure"}

        lines = [
            f"🔄 <b>LIFECYCLE: {symbol}</b>",
            "",
            f"Stage: <b>{stage['stage']} — {stage['stage_name']}</b>",
            "",
        ]

        if stage.get("criteria_met"):
            lines.append("<b>Criteria Met:</b>")
            for c in stage["criteria_met"]:
                lines.append(f"  ✅ {c}")

        if stage.get("criteria_missing"):
            lines.append("<b>Still Needed:</b>")
            for c in stage["criteria_missing"]:
                lines.append(f"  ⬜ {c}")

        if stage.get("promotion_ready"):
            lines.append("")
            lines.append("🎓 <b>READY FOR PROMOTION</b>")
            lines.append("Use /promote to upgrade")

        _send("\n".join(lines))
    except Exception as e:
        log.error("/lifecycle error: %s", e)
        _send(f"⚠️ Lifecycle lookup failed: {e}")


def _handle_stages():
    """Handle /stages command — lifecycle stage summary."""
    try:
        from engines.lifecycle import get_lifecycle_summary
        summary = get_lifecycle_summary()
        stage_names = {1: "Birth", 2: "Viral", 3: "Community", 4: "Adoption", 5: "Infrastructure"}

        lines = [
            "🔄 <b>LIFECYCLE STAGES</b>",
            "",
        ]

        stage_counts = summary.get("stage_counts", {})
        for s in range(1, 6):
            count = stage_counts.get(s, 0)
            lines.append(f"  Stage {s} ({stage_names[s]}): {count} tokens")

        recent = summary.get("recent_transitions", [])
        if recent:
            lines.append("")
            lines.append("<b>Recent Transitions:</b>")
            for t in recent[:5]:
                lines.append(
                    f"  ↗️ <code>{t.get('symbol', '?')}</code>: "
                    f"S{t.get('from_stage', '?')} → S{t.get('to_stage', '?')}"
                )

        _send("\n".join(lines))
    except Exception as e:
        log.error("/stages error: %s", e)
        _send(f"⚠️ Stages lookup failed: {e}")


def _handle_unlocks(args: str):
    """Handle /unlocks [symbol] command."""
    symbol = args.strip() or None

    try:
        from market_intel.unlocks import get_upcoming_unlocks, calculate_unlock_risk

        if symbol:
            unlocks = get_upcoming_unlocks(symbol)
            risk = calculate_unlock_risk(symbol, 0)

            lines = [f"🔓 <b>UNLOCKS: {symbol.upper()}</b>", ""]

            if unlocks:
                for u in unlocks[:5]:
                    risk_icon = "🔴" if u.get("type") == "cliff" else "🟡"
                    lines.append(
                        f"  {risk_icon} {u.get('date', '?')}: "
                        f"{u.get('pct_of_supply', 0):.1f}% ({u.get('type', 'linear')})"
                    )
            else:
                lines.append("  No upcoming unlocks found")

            if risk:
                risk_level = risk.get("risk_level", "N/A")
                ratio = risk.get("unlock_to_volume_ratio", 0)
                level_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(risk_level, "⚪")
                lines.append(f"\n  Risk: {level_icon} {risk_level} (ratio: {ratio:.1f}x)")

            _send("\n".join(lines))
        else:
            from market_intel.unlocks import get_7day_cliff_warnings
            warnings = get_7day_cliff_warnings()

            lines = ["🔓 <b>UPCOMING UNLOCKS (7d)</b>", ""]
            if warnings:
                for w in warnings:
                    lines.append(
                        f"  🔴 <code>{w.get('symbol', '?')}</code>: "
                        f"cliff in {w.get('days_until', '?')}d "
                        f"({w.get('pct_of_supply', 0):.1f}% supply)"
                    )
            else:
                lines.append("  ✅ No cliff unlocks in next 7 days")

            _send("\n".join(lines))
    except Exception as e:
        log.error("/unlocks error: %s", e)
        _send(f"⚠️ Unlock data unavailable: {e}")


def _handle_youtube():
    """Handle /youtube command — show latest YouTube intelligence."""
    try:
        from social.youtube_free import get_latest_digest_text
        text = get_latest_digest_text()
        _send(text)
    except Exception as e:
        log.error("/youtube error: %s", e)
        _send(f"⚠️ YouTube data unavailable: {e}")


def _handle_addchannel(args: str):
    """Handle /addchannel <url_or_id> [name] command."""
    parts = args.strip().split(maxsplit=1)
    if not parts:
        _send("Usage: /addchannel <channel_url_or_id> [name]\n"
              "Example: /addchannel https://www.youtube.com/@CoinBureau Coin Bureau")
        return

    url_or_id = parts[0]
    name = parts[1] if len(parts) > 1 else ""

    try:
        import re
        # Extract channel ID if it's a URL
        channel_id = url_or_id
        if "youtube.com" in url_or_id:
            # Try to extract from URL
            match = re.search(r"channel/(UC[A-Za-z0-9_-]+)", url_or_id)
            if match:
                channel_id = match.group(1)
            else:
                # It's a handle URL — try to resolve
                import requests as req
                page = req.get(url_or_id,
                              headers={"User-Agent": "Mozilla/5.0"},
                              timeout=10).text
                match = re.search(r'"externalId":"(UC[^"]+)"', page)
                if match:
                    channel_id = match.group(1)
                else:
                    _send("⚠️ Could not extract channel ID from URL. Try using the channel ID directly (starts with UC).")
                    return

        if not channel_id.startswith("UC"):
            _send("⚠️ Invalid channel ID. Must start with 'UC'.")
            return

        if not name:
            name = channel_id[:12]

        from social.youtube_free import add_channel
        success = add_channel(name, channel_id)
        if success:
            _send(f"✅ Added <b>{name}</b> ({channel_id}) to YouTube watchlist")
        else:
            _send(f"⚠️ Channel already on watchlist")
    except Exception as e:
        log.error("/addchannel error: %s", e)
        _send(f"⚠️ Failed to add channel: {e}")


def _handle_channels():
    """Handle /channels command — list all YouTube channels."""
    try:
        from social.youtube_free import load_channels
        channels = load_channels()
        if not channels:
            _send("📺 No YouTube channels configured.")
            return

        lines = [f"📺 <b>YouTube Channels</b> ({len(channels)})", ""]
        priority_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        for ch in channels:
            icon = priority_icons.get(ch.get("priority", "medium"), "⚪")
            lines.append(f"  {icon} {ch['name']}")

        _send("\n".join(lines))
    except Exception as e:
        log.error("/channels error: %s", e)
        _send(f"⚠️ Channel list unavailable: {e}")


def _handle_buybacks(args: str):
    """Handle /buybacks [symbol] command."""
    symbol = args.strip() or None

    try:
        from market_intel.unlocks import get_buyback_burn_data

        if symbol:
            bb = get_buyback_burn_data(symbol)
            lines = [f"💰 <b>BUYBACKS: {symbol.upper()}</b>", ""]
            if bb:
                lines.append(f"  Buyback 30d: ${bb.get('buyback_30d_usd', 0):,.0f}")
                lines.append(f"  Burn 30d: {bb.get('burn_30d_tokens', 0):,.0f} tokens")
                lines.append(f"  Net emission: {bb.get('net_emission', 0):,.0f}")
            else:
                lines.append("  No buyback data available")
            _send("\n".join(lines))
        else:
            _send("Usage: /buybacks <symbol>\nExample: /buybacks SOL")
    except Exception as e:
        log.error("/buybacks error: %s", e)
        _send(f"⚠️ Buyback data unavailable: {e}")


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

COMMANDS = {
    "/scan": _handle_scan,
    "/dd": _handle_dd,
    "/regime": lambda _: _handle_regime(),
    "/top": lambda _: _handle_top(),
    "/status": lambda _: _handle_status(),
    "/portfolio": lambda _: _handle_portfolio(),
    "/watch": _handle_watch,
    "/unwatch": _handle_unwatch,
    "/watchlist": _handle_watchlist,
    "/promote": _handle_promote,
    "/lifecycle": _handle_lifecycle,
    "/stages": lambda _: _handle_stages(),
    "/unlocks": _handle_unlocks,
    "/buybacks": _handle_buybacks,
    "/youtube": lambda _: _handle_youtube(),
    "/addchannel": _handle_addchannel,
    "/channels": lambda _: _handle_channels(),
}


def handle_command(text: str) -> bool:
    """Parse and handle a Telegram command. Returns True if handled."""
    if not text or not text.startswith("/"):
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    handler = COMMANDS.get(cmd)
    if handler:
        # Run in thread to not block
        thread = threading.Thread(target=handler, args=(args,), daemon=True)
        thread.start()
        return True

    return False


def start_bot_polling():
    """Start polling for Telegram bot commands.
    Uses getUpdates long-polling (no webhook needed)."""
    import requests

    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram bot token not configured — commands disabled")
        return

    log.info("Starting Telegram bot command polling...")
    offset = 0

    while True:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            resp.raise_for_status()
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = str(message.get("chat", {}).get("id", ""))

                # Only respond to configured chat
                if chat_id == TELEGRAM_CHAT_ID and text.startswith("/"):
                    log.info("Received command: %s", text)
                    handle_command(text)

        except requests.exceptions.Timeout:
            continue  # normal for long-polling
        except Exception as e:
            log.error("Bot polling error: %s", e)
            import time
            time.sleep(5)
