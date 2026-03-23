"""Huoyan Jinjing (火眼金睛) — 4-hourly intelligence pulse.

Runs at 02:00, 06:00, 10:00, 14:00, 18:00, 22:00 UTC.
Max 50 lines per pulse. If nothing new -> "X quiet" (confirms monitoring active).

Section order: Macro → Chain Scorecard → Holdings → Positions →
Intelligence (YouTube + X) → Meme Radar → Scanner

06:00 adds: Today's watchlist + catalysts
22:00 adds: Portfolio summary
"""

from datetime import date, datetime, timezone
from config import get_logger
from db.connection import execute, execute_one
from telegram_bot.severity import _send_to_channel, HUOYAN_CHAT_ID, flush_huoyan_batch

log = get_logger("telegram_bot.huoyan")


def generate_pulse(hour: int | None = None) -> str:
    """Generate and send the Huoyan pulse.

    Args:
        hour: Current UTC hour (auto-detected if None)
    """
    if hour is None:
        hour = datetime.now(timezone.utc).hour

    lines = [
        f"📡 <b>HUOYAN PULSE</b> — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        "",
    ]

    # 1. Macro Regime (enhanced)
    lines.extend(_macro_regime_section())

    # 2. Chain Scorecard
    lines.extend(_chain_scorecard_section())

    # 3. Holdings Health
    lines.extend(_holdings_health_section())

    # 4. Open positions health dashboard
    lines.extend(_positions_section())

    # 5. Intelligence: YouTube + X
    lines.extend(_youtube_section())
    lines.extend(_x_intelligence_section())

    # 6. Meme Radar (strong convergence only)
    lines.extend(_smart_money_radar_section())

    # Batched Tier 3 alerts
    batch = flush_huoyan_batch()
    if batch:
        lines.append("<b>📋 Alerts</b>")
        for item in batch[:5]:
            clean = item.replace("<b>", "").replace("</b>", "")
            lines.append(f"  • {clean[:80]}")
        if len(batch) > 5:
            lines.append(f"  ... +{len(batch) - 5} more")
        lines.append("")

    # 7. Scanner summary
    lines.extend(_scanner_section())

    # 06:00 special: watchlist + catalysts
    if hour == 6:
        lines.extend(_morning_watchlist())

    # 22:00 special: portfolio summary
    if hour == 22:
        lines.extend(_portfolio_summary())

    # Cap at 50 lines (increased for richer format)
    if len(lines) > 50:
        lines = lines[:49] + ["... (truncated)"]

    # If nothing interesting, confirm we're alive
    if len(lines) <= 3:
        lines.append("😴 Quiet period — monitoring active")

    report = "\n".join(lines)

    # Send to Huoyan channel
    chat_id = HUOYAN_CHAT_ID
    if chat_id:
        _send_to_channel(chat_id, report)
        log.info("Huoyan pulse sent (%d lines)", len(lines))

    return report


def _macro_regime_section() -> list[str]:
    """Enhanced macro regime: BTC, dominance, SOL/BTC ratio, funding, stablecoins."""
    lines = ["<b>📊 Macro Regime</b>"]
    try:
        from chain_metrics.macro import get_macro_summary
        macro = get_macro_summary()
        if macro:
            # Regime signal
            signal = macro.get("regime_signal", "NEUTRAL")
            signal_map = {"RISK_ON": "🟢 RISK-ON", "RISK_OFF": "🔴 RISK-OFF", "NEUTRAL": "⚪ NEUTRAL"}
            lines.append(f"  {signal_map.get(signal, '⚪ NEUTRAL')}")

            # BTC price + dominance
            btc = macro.get("btc_price", 0)
            dom = macro.get("btc_dominance", 0)
            dom_trend = macro.get("dom_trend", "flat")
            trend_arrow = {"rising": "↑", "falling": "↓"}.get(dom_trend, "→")
            if btc:
                lines.append(f"  BTC: ${btc:,.0f} | Dom: {dom:.1f}% {trend_arrow}")

            # SOL/BTC ratio
            ratio = macro.get("sol_btc_ratio", 0)
            ratio_trend = macro.get("sol_btc_trend", "flat")
            ratio_arrow = {"up": "↑", "down": "↓"}.get(ratio_trend, "→")
            if ratio:
                lines.append(f"  SOL/BTC: {ratio:.6f} {ratio_arrow}")

            # Funding
            funding = macro.get("funding_avg")
            if funding is not None:
                label = "neutral"
                if funding > 0.03:
                    label = "greedy"
                elif funding < -0.03:
                    label = "fearful"
                lines.append(f"  Funding: {funding:.4f} ({label})")

            # Fear & Greed
            fg = macro.get("fear_greed")
            fg_label = macro.get("fear_greed_label")
            if fg is not None:
                lines.append(f"  Sentiment: {fg} ({fg_label})")

            # Stablecoin total
            stable = macro.get("stablecoin_total", 0)
            if stable:
                lines.append(f"  Stablecoins: ${stable / 1e9:.1f}B")
        else:
            # Fallback to old regime data
            from regime.multiplier import get_current_regime
            regime = get_current_regime()
            if regime:
                mult = regime['regime_multiplier']
                lines.append(f"  Regime: {mult:.2f}")
                raw = regime.get('raw_data', {})
                if raw.get('btc_price'):
                    lines.append(f"  BTC: ${raw['btc_price']:,.0f}")
    except Exception:
        lines.append("  Macro data unavailable")

    lines.append("")
    return lines


def _chain_scorecard_section() -> list[str]:
    """Chain adoption scorecard: Solana vs ETH/Base/Sui/Arb."""
    lines = ["<b>🔗 Chain Scorecard</b>"]
    try:
        from chain_metrics.adoption import get_chain_scorecard
        sc = get_chain_scorecard()
        chains = sc.get("chains", {})
        sol = chains.get("Solana", {})
        if sol:
            tvl = sol.get("tvl", 0)
            tvl_pct = sol.get("tvl_7d_pct", 0)
            dex = sol.get("dex_volume", 0)
            dex_pct = sol.get("dex_volume_7d_pct", 0)
            stable = sol.get("stablecoin_mcap", 0)
            stable_pct = sol.get("stablecoin_mcap_7d_pct", 0)
            fees = sol.get("fees", 0)
            fees_pct = sol.get("fees_7d_pct", 0)
            addrs = sol.get("active_addresses", 0)
            addrs_pct = sol.get("active_addresses_7d_pct", 0)
            lines.append(
                f"  SOL: TVL ${tvl / 1e9:.1f}B ({tvl_pct:+.1f}%) | "
                f"DEX ${dex / 1e9:.1f}B ({dex_pct:+.1f}%)"
            )
            extras = []
            if fees:
                extras.append(f"Fees ${fees / 1e6:.1f}M ({fees_pct:+.1f}%)")
            if addrs:
                extras.append(f"Addrs {addrs / 1e6:.1f}M ({addrs_pct:+.1f}%)")
            if stable:
                extras.append(f"Stables ${stable / 1e9:.1f}B")
            if extras:
                lines.append(f"  {' | '.join(extras)}")
            # Compare vs ETH
            eth = chains.get("Ethereum", {})
            if eth:
                eth_tvl_pct = eth.get("tvl_7d_pct", 0)
                if tvl_pct > eth_tvl_pct + 2:
                    lines.append("  vs ETH: gaining share")
                elif tvl_pct < eth_tvl_pct - 2:
                    lines.append("  vs ETH: losing share")
                else:
                    lines.append("  vs ETH: steady")
            trend = sc.get("solana_trend", "unknown")
            trend_icon = {"accelerating": "🚀", "gaining": "📈", "steady": "➡️",
                          "losing": "📉", "decelerating": "⬇️"}.get(trend, "❓")
            lines.append(f"  Trend: {trend_icon} {trend}")
        else:
            lines.append("  No chain data yet — run chain-metrics")
    except Exception:
        lines.append("  Chain data unavailable")

    lines.append("")
    return lines


def _fmt_price(p: float) -> str:
    """Format price based on magnitude."""
    if p == 0:
        return "$0"
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    if p >= 0.0001:
        return f"${p:.6f}"
    return f"${p:.8f}"


def _fetch_watchlist_extra() -> dict:
    """Fetch HYPE, RENDER, BONK from CoinGecko; PUMP/PENGU/FARTCOIN/USELESS from DexScreener."""
    result = {}

    # CoinGecko: BONK, RENDER, HYPE (24h + 7d)
    try:
        from config import COINGECKO_API_KEY
        from quality_gate.helpers import get_json as _gj
        h = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        coins = _gj(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": "bonk,render-token,hyperliquid",
                "sparkline": "false",
                "price_change_percentage": "7d",
            },
            headers=h,
        )
        CG_MAP = {"bonk": "BONK", "render-token": "RENDER", "hyperliquid": "HYPE"}
        for c in (coins or []):
            sym = CG_MAP.get(c.get("id", ""))
            if sym:
                result[sym] = {
                    "price": c.get("current_price") or 0,
                    "change_24h": c.get("price_change_percentage_24h") or 0,
                    "change_7d": c.get("price_change_percentage_7d_in_currency") or 0,
                }
    except Exception as e:
        log.debug("Watchlist CG fetch: %s", e)

    # DexScreener: PUMP, PENGU, FARTCOIN, USELESS — lookup addresses from DB
    DEX_WANT = {"PUMP", "PENGU", "FARTCOIN", "USELESS"}
    try:
        rows = execute(
            """SELECT upper(symbol), contract_address FROM tokens
               WHERE upper(symbol) = ANY(%s) AND contract_address IS NOT NULL
               LIMIT 10""",
            (list(DEX_WANT),),
            fetch=True,
        )
        if rows:
            from quality_gate.helpers import get_json as _gj
            addr_to_sym: dict = {}
            for sym, addr in rows:
                if sym not in addr_to_sym.values():
                    addr_to_sym[addr] = sym
            addr_list = ",".join(addr_to_sym.keys())
            data = _gj(f"https://api.dexscreener.com/latest/dex/tokens/{addr_list}")
            for pair in ((data or {}).get("pairs") or []):
                base_addr = (pair.get("baseToken") or {}).get("address", "")
                sym = addr_to_sym.get(base_addr)
                if sym and sym not in result:
                    pc = pair.get("priceChange") or {}
                    result[sym] = {
                        "price": float(pair.get("priceUsd") or 0),
                        "change_24h": float(pc.get("h24") or 0),
                    }
    except Exception as e:
        log.debug("Watchlist DexScreener fetch: %s", e)

    return result


def _holdings_health_section() -> list[str]:
    """Holdings health: SOL/JUP + full V5 watchlist prices."""
    lines = ["<b>💰 Holdings Health</b>"]
    added = 0

    # SOL + JUP from stored holdings (24h + 7d)
    try:
        from chain_metrics.holdings import get_holdings_summary
        holdings = get_holdings_summary()
        for token, fmt in (("SOL", "${:.2f}"), ("JUP", "${:.4f}")):
            h = holdings.get(token)
            if not h:
                continue
            price = h.get("price", 0)
            c24 = h.get("change_24h", 0)
            c7d = h.get("change_7d", 0)
            arrow = "📈" if c24 > 0 else "📉" if c24 < 0 else "➡️"
            p_str = fmt.format(price)
            lines.append(f"  {token}: {p_str} {arrow} 24h:{c24:+.1f}% 7d:{c7d:+.1f}%")
            added += 1
    except Exception:
        pass

    # Watchlist extras: HYPE, RENDER, BONK, PUMP, PENGU, FARTCOIN, USELESS
    extra = _fetch_watchlist_extra()
    for sym in ("HYPE", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN", "USELESS"):
        d = extra.get(sym)
        if not d or not d.get("price"):
            continue
        price = d["price"]
        c24 = d.get("change_24h", 0)
        c7d = d.get("change_7d")
        arrow = "📈" if c24 > 0 else "📉" if c24 < 0 else "➡️"
        ch_str = f"24h:{c24:+.1f}%"
        if c7d is not None:
            ch_str += f" 7d:{c7d:+.1f}%"
        lines.append(f"  {sym}: {_fmt_price(price)} {arrow} {ch_str}")
        added += 1

    if added == 0:
        lines.append("  No holdings data yet")

    lines.append("")
    return lines


def _positions_section() -> list[str]:
    """Health dashboard for open positions — hidden if empty."""
    try:
        rows = execute(
            """SELECT st.token_symbol, st.current_pnl_pct,
                      hs.scaled_score, hs.confidence_pct, hs.recommended_action
               FROM shadow_trades st
               LEFT JOIN LATERAL (
                   SELECT scaled_score, confidence_pct, recommended_action
                   FROM health_scores
                   WHERE token_address = st.token_address
                   ORDER BY scored_at DESC LIMIT 1
               ) hs ON true
               WHERE st.status = 'open'
               ORDER BY st.entry_time DESC LIMIT 5""",
            fetch=True,
        )
        if not rows:
            return []
        lines = ["<b>🏥 Positions</b>"]
        for sym, pnl, health, conf, action in rows:
            pnl_str = f"{float(pnl or 0):+.1f}%" if pnl else "?"
            health_str = f"{float(health or 0):.0f}" if health else "?"
            conf_str = f"{float(conf or 0):.0f}%" if conf else "?"
            emoji = "🟢" if float(health or 0) >= 65 else "🟡" if float(health or 0) >= 50 else "🔴"
            lines.append(
                f"  {emoji} ${sym or '?'}: {pnl_str} | H:{health_str} "
                f"({conf_str}) | {action or '?'}"
            )
        lines.append("")
        return lines
    except Exception as e:
        log.debug("Positions section error: %s", e)
        return []


def _kol_activity_section() -> list[str]:
    """Recent KOL wallet activity."""
    lines = ["<b>👤 KOL Activity</b>"]
    try:
        rows = execute(
            """SELECT kw.name, kt.token_symbol, kt.action, kt.amount_usd,
                      kt.is_conviction_buy
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.detected_at > NOW() - INTERVAL '4 hours'
                 AND kt.amount_usd >= 500
               ORDER BY kt.amount_usd DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            for name, sym, action, usd, conviction in rows:
                icon = "🟢" if action == "buy" else "🔴"
                conv = " ⭐" if conviction else ""
                lines.append(
                    f"  {icon} {name}: {action} ${sym or '?'} "
                    f"(${float(usd or 0):,.0f}){conv}"
                )
        else:
            lines.append("  No KOL activity in 4h")
    except Exception as e:
        log.debug("KOL activity section: %s", e)
        lines.append("  KOL data unavailable")

    lines.append("")
    return lines


def _youtube_section() -> list[str]:
    """YouTube intel from last 4 hours — hidden if nothing relevant."""
    try:
        rows = execute(
            """SELECT channel_name, title, analysis_json, relevance_score
               FROM youtube_videos
               WHERE published_at > NOW() - INTERVAL '4 hours'
                 AND relevance_score > 5
               ORDER BY relevance_score DESC
               LIMIT 3""",
            fetch=True,
        )
        if not rows:
            return []
        lines = ["<b>📺 YouTube Intel</b>"]
        for channel, title, analysis, score in rows:
            aj = analysis if isinstance(analysis, dict) else {}
            outlook = aj.get("overall_outlook", "neutral")
            icon = {"bullish": "🟢", "bearish": "🔴"}.get(outlook, "🟡")
            tokens = aj.get("tokens_mentioned", [])
            tok_str = ", ".join(t.get("symbol", "") for t in tokens[:3]) if tokens else ""
            line = f"  {icon} {channel}: \"{(title or '')[:40]}\" — {outlook}"
            if tok_str:
                line += f", mentioned {tok_str}"
            lines.append(line)
        lines.append("")
        return lines
    except Exception as e:
        log.debug("YouTube section error: %s", e)
        return []


def _x_intelligence_section() -> list[str]:
    """X smart money signals from last 4 hours — hidden if none."""
    try:
        from social.grok_poller import get_recent_x_signals
        signals = get_recent_x_signals(hours=4, min_strength="medium")
        if not signals:
            return []

        lines = ["<b>📡 X Intelligence</b>"]
        # Group by category
        groups = {}
        for sig in signals:
            cat = sig.get("signal_category", "info")
            groups.setdefault(cat, []).append(sig)

        cat_config = [
            ("risk",      "⚠️ Risk Alerts"),
            ("macro",     "📊 Macro"),
            ("ecosystem", "🔗 SOL Ecosystem"),
            ("infra",     "🏗 Infrastructure"),
            ("meme",      "🎰 Meme Activity"),
        ]

        shown = 0
        for cat_key, cat_label in cat_config:
            sigs = groups.get(cat_key, [])
            if not sigs:
                continue
            lines.append(f"  <b>{cat_label}</b>")
            for sig in sigs[:2]:
                handle = sig.get("source_handle", "?")
                symbol = sig.get("token_symbol")
                strength = sig.get("signal_strength", "?")
                amount = sig.get("amount_usd")
                icon = "🔴" if strength == "strong" else "🟡"
                sym_str = f" ${symbol}" if symbol else ""
                amount_str = f" (${amount:,.0f})" if amount else ""
                ptype = sig.get("parsed_type", "info")
                lines.append(f"    {icon} {handle}: {ptype}{sym_str}{amount_str}")
                shown += 1
            if shown >= 6:
                break

        info_count = len(groups.get("info", []))
        if info_count and shown == 0:
            lines.append(f"  {info_count} general signals (no macro/risk themes)")

        lines.append("")
        return lines
    except Exception as e:
        log.debug("X intelligence section error: %s", e)
        return []


def _smart_money_radar_section() -> list[str]:
    """Smart money convergence radar — hidden if quiet."""
    try:
        from wallets.convergence_detector import get_radar_summary
        radar = get_radar_summary()
        strong_signals = [
            c for c in radar.get("convergences", [])
            if c["convergence_level"] in ("EMERGING", "STRONG CONVERGENCE")
        ]
        if not strong_signals:
            return []
        lines = ["<b>🎯 Smart Money Radar</b>"]
        for conv in strong_signals[:3]:
            symbol = f"${conv['token_symbol']}" if conv.get("token_symbol") else conv["token_address"][:12]
            level = conv["convergence_level"]
            icon = {"STRONG CONVERGENCE": "🔴", "EMERGING": "🟡"}.get(level, "🟡")
            lines.append(
                f"  {icon} {symbol}: {conv['wallet_count']} wallets, "
                f"score {conv['weighted_score']:.1f} [{level}]"
            )
        lines.append("")
        return lines
    except Exception as e:
        log.debug("Smart money radar section: %s", e)
        return []


def _scanner_section() -> list[str]:
    """Scanner summary — hidden if no activity."""
    try:
        row = execute_one(
            """SELECT
                 COUNT(*) FILTER (WHERE type = 'gate_pass') as passes,
                 COUNT(*) FILTER (WHERE type LIKE 'gate_%') as total
               FROM alerts
               WHERE timestamp > NOW() - INTERVAL '4 hours'""",
        )
        if not row or (row[1] == 0):
            return []
        passes, total = row
        lines = ["<b>🔍 Scanner</b>"]
        lines.append(f"  Last 4h: {total} scanned, {passes} passed")
        lines.append("")
        return lines
    except Exception:
        return []


def _morning_watchlist() -> list[str]:
    """06:00 special: today's watchlist."""
    lines = ["<b>📋 Today's Watchlist</b>"]
    try:
        rows = execute(
            """SELECT symbol, contract_address, last_health_score, token_tier
               FROM tokens
               WHERE health_state IS NOT NULL
                 AND last_health_score > 50
               ORDER BY last_health_score DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            for sym, addr, score, tier in rows:
                lines.append(f"  📍 ${sym}: Health {float(score or 0):.0f} ({tier or '?'})")
        else:
            lines.append("  No tokens on watchlist")
    except Exception:
        lines.append("  Watchlist unavailable")

    lines.append("")
    return lines


def _portfolio_summary() -> list[str]:
    """22:00 special: portfolio/shadow summary."""
    lines = ["<b>💼 Portfolio Summary</b>"]
    try:
        from shadow.tracker import get_shadow_summary
        summary = get_shadow_summary()
        lines.append(f"  Total trades: {summary.get('total', 0)}")
        lines.append(f"  Open: {summary.get('open', 0)} | Closed: {summary.get('closed', 0)}")
        lines.append(f"  Win rate: {summary.get('win_rate', 0):.0f}%")
        lines.append(f"  Total PnL: {summary.get('total_pnl', 0):+.1f}%")
    except Exception:
        lines.append("  Portfolio data unavailable")

    lines.append("")
    return lines
