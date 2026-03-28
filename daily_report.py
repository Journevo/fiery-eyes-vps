"""Daily Intelligence Report — Task 5 of Fiery Eyes v5.1

ONE daily report combining BTC cycle, liquidity, watchlist, and large swaps.
Sent at 00:00 UTC + on-demand via /report command.
"""

import requests
from datetime import datetime, date, timezone, timedelta
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("daily_report")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# Token allocation targets (of deployed capital)
ALLOCATION_TARGETS = {
    "JUP": 20, "HYPE": 20, "RENDER": 17, "BONK": 15,
    "PUMP": 10, "PENGU": 8, "FARTCOIN": 5,
}

# Estimated beta to BTC (for downside calculation)
BTC_BETA = {
    "JUP": 1.5, "HYPE": 1.2, "RENDER": 1.5, "BONK": 1.8,
    "PUMP": 2.0, "PENGU": 1.8, "FARTCOIN": 2.5,
    "SOL": 1.3, "MSTR": 1.0, "COIN": 0.8,
}

# BTC $50K scenario drawdown from current ~$70K
BTC_50K_DRAWDOWN = (70000 - 50000) / 70000  # ~28.6%


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    elif price >= 0.001:
        return f"${price:.4f}"
    else:
        return f"${price:.2e}"


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def generate_report(send_to_telegram: bool = False, report_type: str = "morning") -> str:
    """Generate the complete daily intelligence report."""
    from btc_cycle import fetch_btc_price, calculate_cycle, SCENARIOS
    from watchlist import fetch_prices, calc_mstr_mnav, CORE_TOKENS, ISA_TOKENS, classify_zone
    from liquidity import run_liquidity_tracker
    from large_swaps import run_swap_detection

    today = datetime.now(timezone.utc)
    date_str = today.strftime("%b %-d %Y")

    # Fetch BTC price early for TLDR + Nimbus
    btc_price = fetch_btc_price()
    cycle = calculate_cycle(btc_price, today.date()) if btc_price else None

    sections = []
    sections.append(f"🌙 <b>FIERY EYES — {date_str}</b>")


    # TLDR
    try:
        if btc_price and cycle:
            fg_val = None
            try:
                from market_structure import fetch_fear_greed
                fg_val = fetch_fear_greed().get("value")
            except Exception:
                pass
            bear = cycle["bear_progress_pct"]
            t = f"Bear {bear:.0f}% done"
            if fg_val and fg_val <= 25:
                t = f"Extreme fear ({fg_val}) + {t.lower()}"
            t += ". Accumulate slowly, keep 50%+ dry powder." if bear < 60 else ". Approaching bottom."
            sections.append(f"<i>{t}</i>\n")
    except Exception:
        pass


    # ━━━ NIMBUS STALENESS ━━━
    try:
        from nimbus_check import check_nimbus_staleness, format_nimbus_warning
        nimbus = check_nimbus_staleness(btc_price)
        nimbus_warn = format_nimbus_warning(nimbus)
        if nimbus_warn:
            sections.append(nimbus_warn)
    except Exception as e:
        log.error("Nimbus check failed: %s", e)

    # ━━━ SYNTHESIS (auto) ━━━
    try:
        # Quick synthesis — 3-4 sentences connecting the dots
        from synthesis import collect_all_layers, build_synthesis_prompt, call_synthesis
        layers = collect_all_layers()
        prompt = build_synthesis_prompt(layers)
        # Add instruction for brief output
        # Use full synthesis prompt — this is the main report narrative
        result = call_synthesis(prompt)
        if result.get("output"):
            synth_text = result["output"].strip()
            # Keep only first ~600 chars for report brevity
            if len(synth_text) > 600:
                synth_text = synth_text[:600].rsplit(".", 1)[0] + "."
            sections.append(f"\n━━━ <b>SYNTHESIS</b> ━━━\n{synth_text}")
    except Exception as e:
        log.error("Report synthesis failed: %s", e)

    # ━━━ SUPPORTING DATA ━━━
    sections.append("\n━━━ <b>SUPPORTING DATA</b> ━━━")

    # ━━━ BTC CYCLE ━━━
    if btc_price and cycle:
        bar = _progress_bar(cycle["bear_progress_pct"])
        scenarios_str = " | ".join(
            f"-{s['drawdown_pct']}% = ${s['target_price']:,}" for s in SCENARIOS
        )
        peak_date = datetime.strptime(cycle["peak_date"], "%Y-%m-%d").strftime("%b %-d")
        sections.append(
            f"━━━ <b>BTC CYCLE</b> ━━━\n"
            f"Peak: ${cycle['peak_price']:,} ({peak_date}) | Now: ${btc_price:,.0f} (-{cycle['drawdown_pct']}%)\n"
            f"Bear: {bar} {cycle['bear_progress_pct']:.0f}% (~{cycle['days_remaining']}d to est. bottom)\n"
            f"Scenarios: {scenarios_str}"
        )
    else:
        sections.append("━━━ <b>BTC CYCLE</b> ━━━\n⚠️ BTC price unavailable")

    # ━━━ MARKET STRUCTURE ━━━
    try:
        from market_structure import run_market_structure
        mkt = run_market_structure()
        if mkt:
            oi = mkt.get("oi", {})
            funding = mkt.get("funding", {})
            ls = mkt.get("long_short", {})
            fg = mkt.get("fear_greed", {})

            oi_str = f"${oi['oi_usd']/1e9:.1f}B" if oi.get("oi_usd") else "N/A"
            rate_str = f"{funding['current_pct']:+.4f}%" if funding.get("current_pct") is not None else "N/A"
            streak = funding.get("streak_days", 0)
            streak_dir = funding.get("streak_direction", "")
            streak_str = f" ({streak:.0f}d {streak_dir})" if streak >= 2 else ""
            fg_str = f"{fg['value']} ({fg.get('label', '')})" if fg.get("value") is not None else "N/A"

            # Context annotations
            fg_context = ""
            fg_v = fg.get("value", 50)
            if fg_v <= 20:
                fg_context = " \u2014 bottom 5% historically, contrarian BULLISH \u2705"
            elif fg_v <= 30:
                fg_context = " \u2014 fear zone, accumulation territory \u2705"
            elif fg_v >= 80:
                fg_context = " \u2014 extreme greed, take profit territory \U0001f534"

            fund_context = ""
            streak_d = funding.get("streak_days", 0)
            if funding.get("streak_direction") == "negative" and streak_d >= 10:
                fund_context = " \u2014 local bottom signal \U0001f525"
            elif funding.get("streak_direction") == "negative" and streak_d >= 5:
                fund_context = " \u2014 shorts dominant \u2705"

            sections.append(
                f"\n━━━ <b>MARKET STRUCTURE</b> ━━━\n"
                f"OI: {oi_str} | Fund: {rate_str}{streak_str}{fund_context}\n"
                f"F&G: {fg_str}{fg_context}"
            )

            for insight in mkt.get("insights", []):
                sections.append(insight)
    except Exception as e:
        log.error("Market structure section failed: %s", e)

    # ━━━ LIQUIDITY ━━━
    liq = run_liquidity_tracker()
    if liq and liq.get("us_net_liq"):
        us_dir = "↗" if liq.get("fred_slope", 0) > 0.05 else ("↘" if liq.get("fred_slope", 0) < -0.05 else "→")
        us_str = f"${liq['us_net_liq']:.2f}T" if liq["us_net_liq"] else "N/A"
        m2_str = f"${liq['global_m2']:.0f}T" if liq.get("global_m2") else "N/A"
        dxy_str = f"{liq['dxy']:.0f}" if liq.get("dxy") else "N/A"
        regime = liq.get("fred_regime", "UNKNOWN")
        slope = liq.get("fred_slope", 0)
        m2_status = liq.get("m2_lag_status", "UNKNOWN")
        m2_days = liq.get("m2_lag_days", 0)
        alignment = liq.get("alignment", "UNKNOWN")

        # Forward-looking 3-timeframe format
        regime_emoji = "\u2705" if regime == "EXPANDING" else ("\U0001f534" if regime == "CONTRACTING" else "\u23f3")
        m2_context = ""
        if m2_status == "EXPIRED":
            m2_context = " (QT handbrake \u23f3)"
        elif m2_status == "WINDOW":
            m2_context = " (BTC response expected \U0001f525)"

        sections.append(
            f"\n━━━ <b>LIQUIDITY</b> ━━━\n"
            f"<b>SHORT:</b> US {us_str} ({us_dir} {regime} {slope:+.1f}%) {regime_emoji}\n"
            f"<b>MED:</b> Global ${liq.get('global_net_liq', 0):.2f}T | M2 {m2_str} | lag {m2_days}d {m2_status}{m2_context}\n"
            f"<b>LONG:</b> DXY {dxy_str} | {alignment}"
        )


    # ━━━ MACRO CHANGES ━━━
    try:
        from macro.dashboard_formatter import format_macro_changes
        macro_section = format_macro_changes()
        if macro_section:
            sections.append("\n" + macro_section)
    except Exception as e:
        log.error("Macro changes section failed: %s", e)
    # ━━━ WUKONG REGIME ━━━
    try:
        from nimbus_sync import format_regime_for_report, get_nimbus_section
        regime_section = format_regime_for_report()
        if regime_section:
            sections.append("\n━━━ " + regime_section)

        # Add key macro context from Nimbus
        nimbus_pmi = get_nimbus_section("pmi")
        nimbus_rates = get_nimbus_section("rates")
        nimbus_geo = get_nimbus_section("geopolitics")
        macro_parts = []
        if nimbus_pmi:
            gw = nimbus_pmi.get("global_weighted", [])
            if gw:
                macro_parts.append(f"Global PMI {gw[-1]}")
        if nimbus_rates:
            fed = nimbus_rates.get("fed", {})
            macro_parts.append(f"Fed {fed.get('rate', '?')}% (next: {fed.get('next', '?')})")
        if nimbus_geo:
            macro_parts.append(f"Iran: {nimbus_geo.get('iran_war', '?')}")
        if macro_parts:
            sections.append("  Macro: " + " | ".join(macro_parts))
    except Exception as e:
        log.error("Wukong regime section failed: %s", e)

    # ━━━ WATCHLIST ━━━
    prices = fetch_prices()
    if prices:
        watchlist_lines = []
        header = f"{'Token':<8s} {'Price':>9s}  {'%ATH':>5s}  {'Zone':<10s}  {'Down':>5s}"
        watchlist_lines.append(header)

        # BTC row
        if "BTC" in prices and btc_price:
            d = prices["BTC"]
            down_pct = round(BTC_50K_DRAWDOWN * 100)
            watchlist_lines.append(
                f"{'BTC':<8s} {_fmt_price(btc_price):>9s}  {d['pct_from_ath']:+.0f}%  {'Bear ' + str(round(cycle['bear_progress_pct'])) + '%':<10s}  -{down_pct}%"
            )

        # SOL row
        if "SOL" in prices:
            d = prices["SOL"]
            down = round(BTC_50K_DRAWDOWN * BTC_BETA.get("SOL", 1.3) * 100)
            watchlist_lines.append(
                f"{'SOL':<8s} {_fmt_price(d['price']):>9s}  {d['pct_from_ath']:+.0f}%  {d['zone']:<10s}  -{down}%"
            )

        # Core tokens
        for symbol in CORE_TOKENS:
            if symbol not in prices:
                continue
            d = prices[symbol]
            beta = BTC_BETA.get(symbol, 1.5)
            down = round(BTC_50K_DRAWDOWN * beta * 100)
            watchlist_lines.append(
                f"{symbol:<8s} {_fmt_price(d['price']):>9s}  {d['pct_from_ath']:+.0f}%  {d['zone']:<10s}  -{down}%"
            )

        sections.append("\n━━━ <b>WATCHLIST</b> ━━━\n<pre>" + "\n".join(watchlist_lines) + "</pre>")

        # ━━━ ISA PROXIES ━━━
        isa_parts = []
        for symbol in ISA_TOKENS:
            if symbol in prices:
                d = prices[symbol]
                part = f"{symbol}: {_fmt_price(d['price'])}"
                if symbol == "MSTR" and "BTC" in prices:
                    mnav = calc_mstr_mnav(d["price"], prices["BTC"]["price"], d["mcap"])
                    if mnav:
                        warn = " ⚠️ near book" if mnav < 1.2 else ""
                        part += f" (mNAV {mnav}{warn})"
                isa_parts.append(part)

        if isa_parts:
            sections.append("\n━━━ <b>ISA PROXIES</b> ━━━\n" + " | ".join(isa_parts))

    # ━━━ LARGE SWAPS (24h) ━━━
    recent_swaps = execute("""
        SELECT token, direction, amount_usd, pct_of_mcap, pool, alert_type
        FROM large_swaps
        WHERE timestamp > NOW() - INTERVAL '24 hours'
        ORDER BY amount_usd DESC
        LIMIT 5
    """, fetch=True)

    if recent_swaps:
        swap_lines = []
        for row in recent_swaps:
            token, direction, amount, pct_mcap, pool, alert_type = row
            amount_str = f"${amount/1e6:.1f}M" if amount >= 1e6 else f"${amount/1e3:.0f}K"
            swap_lines.append(f"{token}: {amount_str} {direction.lower()} on {pool} ({pct_mcap:.2f}% MCap) — {alert_type}")
        sections.append("\n━━━ <b>LARGE SWAPS (24h)</b> ━━━\n" + "\n".join(swap_lines))

    # ━━━ DeFi MARKET ━━━
    try:
        from defi_llama import collect_defi_data
        defi = collect_defi_data()
        if defi and defi.get("total_tvl"):
            def _fusd(v):
                if v >= 1e12: return f"${v/1e12:.1f}T"
                if v >= 1e9: return f"${v/1e9:.1f}B"
                if v >= 1e6: return f"${v/1e6:.1f}M"
                return f"${v:,.0f}"

            sol_share = defi.get("sol_dex_share", 0)
            sol_rank = defi.get("sol_tvl_rank", 0)

            defi_lines = [f"\n━━━ <b>DeFi MARKET</b> ━━━"]
            defi_lines.append(f"TVL: {_fusd(defi['total_tvl'])} | SOL {_fusd(defi['sol_tvl'])} (#{sol_rank})")
            defi_lines.append(f"DEX Vol: {_fusd(defi['total_dex_vol_24h'])} | SOL share: {sol_share}%")

            revs = defi.get("revenues", {})
            rev_parts = []
            for sym in ["HYPE", "JUP", "PUMP"]:
                r = revs.get(sym, {})
                if r.get("rev_24h"):
                    rev_parts.append(f"{sym} {_fusd(r['rev_24h'])}/d")
            if rev_parts:
                defi_lines.append("Revenue: " + " | ".join(rev_parts))

            stables = defi.get("total_stablecoins", 0)
            if stables:
                defi_lines.append(f"Stablecoins: {_fusd(stables)}")

            sections.append("\n".join(defi_lines))
    except Exception as e:
        log.error("DeFi section failed: %s", e)

    # ━━━ SUPPLY FLOW ━━━
    try:
        from supply_flow import calc_hype_supply_flow, calc_pump_cliff, format_supply_for_report
        hype_flow = calc_hype_supply_flow()
        pump_cliff = calc_pump_cliff()
        sections.append("\n━━━ <b>SUPPLY FLOW</b> ━━━\n" + format_supply_for_report(hype_flow, pump_cliff))
    except Exception as e:
        log.error("Supply flow section failed: %s", e)

    # ━━━ SUNFLOW WHALE ━━━
    try:
        from sunflow_telegram import format_for_report
        sf_report = format_for_report()
        if sf_report:
            sections.append("\n━━━ <b>SUNFLOW WHALE</b> ━━━\n" + sf_report)
    except Exception as e:
        log.error("SunFlow section failed: %s", e)

    # ━━━ YOUTUBE ━━━
    try:
        from youtube_intel import get_recent_youtube_intel, format_youtube_for_report
        yt_intel = get_recent_youtube_intel(hours=48)
        yt_section = format_youtube_for_report(yt_intel)
        if yt_section:
            sections.append("\n━━━ <b>YOUTUBE</b> ━━━\n" + yt_section)
            # Add convergence note
            for c in yt_intel.get("convergence", []):
                if c["symbol"] in ("JUP", "HYPE", "RENDER", "BONK"):
                    sections.append(f"  🔀 ${c['symbol']}: {c['count']} channels bullish")
    except Exception as e:
        log.error("YouTube section failed: %s", e)

    # ━━━ DRY POWDER ━━━
    try:
        from dry_powder import fetch_usdc_yields, format_for_report
        yields = fetch_usdc_yields()
        yield_line = format_for_report(yields)
        if yield_line:
            sections.append("\n━━━ <b>DRY POWDER</b> ━━━\n" + yield_line)
    except Exception as e:
        log.error("Dry powder section failed: %s", e)

    # ━━━ SMART MONEY (24h) ━━━
    try:
        watchlist_tokens = {"JUP", "HYPE", "RENDER", "BONK", "SOL", "PUMP", "PENGU", "FARTCOIN"}
        # Get X signals per watchlist token
        sm_rows = execute("""
            SELECT token_symbol,
                   COUNT(*) as signal_count,
                   COUNT(DISTINCT source_handle) as source_count,
                   array_agg(DISTINCT source_handle) as sources,
                   MAX(signal_strength) as max_strength,
                   array_agg(DISTINCT parsed_type) as types
            FROM x_intelligence
            WHERE detected_at > NOW() - INTERVAL '24 hours'
              AND token_symbol IS NOT NULL
              AND signal_strength IN ('medium', 'strong')
            GROUP BY token_symbol
            ORDER BY COUNT(DISTINCT source_handle) DESC, COUNT(*) DESC
        """, fetch=True)

        sm_lines = []
        for token, count, sources, source_list, strength, types in (sm_rows or []):
            if not token:
                continue
            tok = token.upper()
            is_wl = tok in watchlist_tokens
            if not is_wl:
                continue  # Only watchlist tokens in daily report

            # Determine emoji verdict
            if sources >= 3:
                emoji = "\U0001f7e2"  # green
                verdict = "STRONG"
            elif sources >= 2:
                emoji = "\U0001f7e1"  # yellow
                verdict = "WATCHING"
            elif strength == "strong":
                emoji = "\U0001f7e1"
                verdict = "WATCHING"
            else:
                emoji = "\u26aa"  # white
                verdict = "QUIET"

            # Format sources and signal types
            src_str = ", ".join((s or "").lstrip("@")[:15] for s in (source_list or [])[:3])
            type_str = ", ".join((t or "") for t in (types or [])[:2])

            sm_lines.append(f"  {emoji} {tok}: {sources} src ({src_str}) \u2014 {verdict}")

        if sm_lines:
            sections.append("\n━━━ <b>SMART MONEY (24h)</b> ━━━\n" + "\n".join(sm_lines[:8]))
    except Exception as e:
        log.error("Smart money section failed: %s", e)

    # ━━━ DEEP DIVE ALERTS ━━━
    try:
        from research.research_manager import get_price_alerts
        if prices:
            dd_alerts = get_price_alerts(prices)
            if dd_alerts:
                sections.append("\n\u2501\u2501\u2501 <b>DEEP DIVE ALERTS</b> \u2501\u2501\u2501\n" + "\n".join(dd_alerts))
    except Exception as e:
        log.error("Deep dive alerts failed: %s", e)

    # ━━━ REGIME ━━━
    bear_pct = cycle["bear_progress_pct"] if btc_price else 0
    if bear_pct < 60:
        deploy = "40-50% max"
        dry = "50-60%"
    else:
        deploy = "60-70%"
        dry = "30-40%"

    cycle_says = "accumulate slowly, save for $50-60K BTC zone" if bear_pct < 60 else "nearing bottom, increase exposure"
    # Use Wukong regime if available (matches TradingView exactly)
    try:
        from nimbus_sync import get_regimes
        wk = get_regimes()
        us_r = wk.get("us_regime", "UNKNOWN") if wk else "UNKNOWN"
        macro_says = f"US liq {us_r.lower()}" if us_r != "UNKNOWN" else "uncertain direction"
    except Exception:
        macro_says = "early expansion building" if liq and liq.get("fred_regime") == "EXPANDING" else "uncertain direction"

    sections.append(
        f"\n━━━ <b>REGIME</b> ━━━\n"
        f"Deploy: {deploy} | Dry powder: {dry}\n"
        f"Cycle says: {cycle_says}\n"
        f"Macro says: {macro_says}"
    )

    # ━━━ RECOMMENDATION ━━━
    recs = []
    if prices:
      try:
        # Get SunFlow conviction data
        sf_conviction = {}
        try:
            sf_rows = execute("""
                SELECT token, conviction_score, net_flow_usd, timeframes_present
                FROM sunflow_conviction WHERE is_watchlist = TRUE
                ORDER BY conviction_score DESC
            """, fetch=True)
            for row in (sf_rows or []):
                sf_conviction[row[0]] = {"score": row[1], "net": row[2], "tfs": row[3]}
        except Exception:
            pass

        # Get smart money source count (24h)
        sm_sources = {}
        try:
            sm_rows = execute("""
                SELECT token_symbol, COUNT(DISTINCT source_handle) as src
                FROM x_intelligence
                WHERE detected_at > NOW() - INTERVAL '24 hours'
                  AND signal_strength IN ('medium', 'strong')
                  AND token_symbol IS NOT NULL
                GROUP BY token_symbol
            """, fetch=True)
            for row in (sm_rows or []):
                sm_sources[row[0].upper()] = row[1]
        except Exception:
            pass

        # Score each token: SunFlow conviction + smart money + depth
        all_tokens = list(set(CORE_TOKENS) | set(["PUMP", "PENGU", "FARTCOIN"]))
        scored = []
        for sym in all_tokens:
            if sym not in prices:
                continue
            d = prices[sym]
            sf = sf_conviction.get(sym, {})
            sm = sm_sources.get(sym, 0)
            # Composite score: SunFlow conviction (40%) + smart money sources (30%) + depth (30%)
            sf_score = sf.get("score", 0) / 16 * 10  # Normalize to 0-10
            sm_score = min(sm * 3, 10)  # 0-3 sources = 0-9
            depth_score = min(abs(d["pct_from_ath"]) / 10, 10)  # -90% = 9
            composite = sf_score * 0.4 + sm_score * 0.3 + depth_score * 0.3
            scored.append((sym, composite, sf, sm, d))

        scored.sort(key=lambda x: x[1], reverse=True)
        log.info("Recommendation scoring: %s", [(s[0], round(s[1],1)) for s in scored])

        for sym, comp, sf, sm, d in scored[:4]:
            sf_str = f"SunFlow {sf.get('tfs', 0)}/4 TF" if sf.get("tfs") else "no SunFlow"
            sm_str = f"{sm} X sources" if sm else "no X signals"
            zone = d.get("zone", "")

            if comp >= 5:
                action = "FOCUS"
            elif comp >= 3 or "Mid" in zone:
                action = "WATCH" if "Mid" not in zone else "PATIENCE"
            else:
                action = "WATCH"

            if "Mid" in zone and comp < 5:
                action = "PATIENCE"

            recs.append(f"{action}: {sym} ({d['pct_from_ath']:+.0f}% ATH | {sf_str} | {sm_str})")
      except Exception as e:
        log.error("Recommendation scoring failed: %s", e)

    if bear_pct < 50:
        recs.append("AVOID: adding new positions until bear >50%")

    if recs:
        sections.append("\n━━━ <b>RECOMMENDATION</b> ━━━\n" + "\n".join(recs))

    report = "\n".join(sections)

    log.info("Daily report generated (%d chars)", len(report))

    if send_to_telegram:
        send_telegram(report)

    return report


def send_telegram(text: str):
    """Send report to Telegram, splitting at sections then paragraphs."""
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
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                "parse_mode": "HTML", "disable_web_page_preview": True,
                    "reply_markup": _KEYBOARD_JSON,
            }, timeout=15)
            if resp.status_code == 200:
                log.info("Telegram chunk sent (%d chars)", len(chunk))
            else:
                log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
        except Exception as e:
            log.error("Telegram error: %s", e)

if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    rtype = "evening" if "--evening" in sys.argv else "morning"
    report = generate_report(send_to_telegram=send_tg, report_type=rtype)
    print(report)
