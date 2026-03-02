"""Telegram alert sender — Quality Gate results, engine scores, convergence alerts.

Only PASS and WATCH alerts are sent to Telegram. FAIL results are logged to DB only.
"""

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

log = get_logger("telegram")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------

def _fmt_usd(value: float) -> str:
    """Format USD value: $1.2B, $45.3M, $120K, $0.0042."""
    if value >= 1_000_000_000:
        return f"${value / 1e9:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1e6:.1f}M"
    if value >= 1_000:
        return f"${value / 1e3:.1f}K"
    if value >= 1:
        return f"${value:.2f}"
    if value > 0:
        # Small prices: show enough decimals
        return f"${value:.6f}".rstrip("0").rstrip(".")
    return "$0"


def _fmt_count(value: int | float) -> str:
    """Format count: 1.2M, 45.3K, 890."""
    value = int(value)
    if value >= 1_000_000:
        return f"{value / 1e6:.1f}M"
    if value >= 1_000:
        return f"{value / 1e3:.1f}K"
    return str(value)


def _fmt_pct(value: float) -> str:
    """Format percentage with sign: +12.3%, -5.1%."""
    return f"{value:+.1f}%"


def _token_links(mint: str) -> str:
    """Generate DexScreener + Birdeye links."""
    return (
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
        f' | <a href="https://birdeye.so/token/{mint}">Birdeye</a>'
    )


def _extract_token_info(gate_result: dict) -> dict:
    """Extract token name, symbol, price, mcap, volume, holders from gate result."""
    dex = gate_result.get("dex_data", {})
    checks = gate_result.get("checks", {})
    sybil = checks.get("sybil", {})
    age_vol = checks.get("age_volume", {})
    holders_check = checks.get("holders", {})

    return {
        "name": dex.get("token_name", "Unknown"),
        "symbol": dex.get("token_symbol", "???"),
        "price": dex.get("price_usd", 0),
        "mcap": dex.get("market_cap", 0),
        "volume_24h": dex.get("volume_h24", 0) or age_vol.get("volume_usd", 0),
        "volume_h1": dex.get("volume_h1", 0),
        "change_24h": dex.get("price_change_h24", 0),
        "change_h6": dex.get("price_change_h6", 0),
        "holders_raw": sybil.get("total_holders", 0),
        "holders_qa": sybil.get("quality_adjusted_holders", 0),
        "top10_pct": holders_check.get("top10_pct"),
    }


# ---------------------------------------------------------------------------
# Quality Gate alert — PASS and WATCH only (FAIL -> DB only)
# ---------------------------------------------------------------------------

def send_gate_result(gate_result: dict):
    """Format and send Quality Gate results to Telegram.

    Only sends PASS and WATCH alerts. FAIL results are silently skipped
    (already logged to DB by the gate).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return False

    gate_status = gate_result.get("gate_status", "rejected")
    mint = gate_result["mint"]

    # FAIL -> do not send to Telegram
    if gate_status == "rejected":
        log.debug("Skipping Telegram for rejected token %s", mint[:16])
        return False

    info = _extract_token_info(gate_result)
    checks = gate_result.get("checks", {})

    if gate_status == "passed":
        lines = _format_pass_alert(mint, info, checks)
    elif gate_status == "watching":
        velocity = gate_result.get("velocity_signals", [])
        failures = gate_result.get("failures", [])
        lines = _format_watch_alert(mint, info, checks, velocity, failures)
    else:
        return False

    return _send("\n".join(lines))


def _format_pass_alert(mint: str, info: dict, checks: dict) -> list[str]:
    """Format PASS alert with full details."""
    lines = [
        "✅ <b>QUALITY GATE PASS</b>",
        f"🪙 {info['name']} (<b>${info['symbol']}</b>) — {_fmt_usd(info['price'])} ({_fmt_pct(info['change_24h'])} 24h)",
        f"MCap: {_fmt_usd(info['mcap'])} | Vol: {_fmt_usd(info['volume_24h'])} | Holders: {_fmt_count(info['holders_raw'])}",
    ]

    if info["holders_qa"]:
        lines[-1] = lines[-1].rstrip() + f" (QA: {_fmt_count(info['holders_qa'])})"

    lines.append("")
    lines.append("<b>Checks:</b>")

    check_labels = {
        "contract_safety": "Contract",
        "liquidity": "Liquidity",
        "holders": "Holders",
        "sybil": "Sybil",
        "unlocks": "Unlocks",
        "wash_trading": "Wash",
        "age_volume": "Age/Vol",
    }

    for key, label in check_labels.items():
        c = checks.get(key, {})
        icon = "✅" if c.get("pass") else "❌"
        detail = _format_check_detail(key, c)
        lines.append(f"  {icon} {label}: {detail}")

    lines.append("")
    lines.append(f"📋 CA: <code>{mint}</code>")
    lines.append(f"🔗 {_token_links(mint)}")

    return lines


def _format_watch_alert(mint: str, info: dict, checks: dict,
                        velocity: list[str], failures: list[str]) -> list[str]:
    """Format WATCH alert with velocity signals."""
    lines = [
        "👀 <b>WATCHING</b>",
        f"🪙 {info['name']} (<b>${info['symbol']}</b>) — {_fmt_usd(info['price'])} ({_fmt_pct(info['change_h6'])} 4h)",
        f"MCap: {_fmt_usd(info['mcap'])} | Vol: {_fmt_usd(info['volume_24h'])}",
    ]

    # Velocity signals
    signal_map = {
        "volume_acceleration": "📈 Volume accelerating",
        "holder_growth_velocity": "👥 Holder growth surging",
        "trending_dexscreener": "🔥 Trending on DexScreener",
        "price_action": f"🚀 Price action ({_fmt_pct(info['change_h6'])} 6h)",
    }
    signal_strs = [signal_map.get(s, s) for s in velocity]
    lines.append(f"Signals: {' | '.join(signal_strs)}")

    if failures:
        lines.append(f"Failing: {', '.join(failures)}")

    lines.append("")
    lines.append(f"📋 CA: <code>{mint}</code>")
    lines.append(f"🔗 {_token_links(mint)}")

    return lines


# ---------------------------------------------------------------------------
# Engine-scored alert (PASS with scores)
# ---------------------------------------------------------------------------

def send_scored_alert(gate_result: dict, score_result: dict):
    """Send alert with Quality Gate PASS + engine scores."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    gate_status = gate_result.get("gate_status", "rejected")
    if gate_status == "rejected":
        return False

    mint = gate_result["mint"]
    info = _extract_token_info(gate_result)
    convergence = score_result.get("convergence", {})
    is_converging = convergence.get("is_converging", False)

    if is_converging:
        header = "🔥 <b>CONVERGENCE ALERT</b> 🔥"
    else:
        header = "✅ <b>QUALITY GATE PASS</b>"

    lines = [
        header,
        f"🪙 {info['name']} (<b>${info['symbol']}</b>) — {_fmt_usd(info['price'])} ({_fmt_pct(info['change_24h'])} 24h)",
        f"MCap: {_fmt_usd(info['mcap'])} | Vol: {_fmt_usd(info['volume_24h'])} | Holders: {_fmt_count(info['holders_raw'])}",
    ]

    if info["holders_qa"]:
        lines[-1] = lines[-1].rstrip() + f" (QA: {_fmt_count(info['holders_qa'])})"

    lines.append("")

    # Engine scores
    lines.append("<b>Scores:</b>")
    engine_results = score_result.get("engine_results", {})
    for engine_name, result in engine_results.items():
        score_key = f"{engine_name}_score"
        score_val = result.get(score_key, 0)
        bar = _score_bar(score_val)
        lines.append(f"  {_engine_icon(engine_name)} {engine_name.title()}: {score_val:.0f}/100 {bar}")

    lines.append(f"<b>Composite:</b> {score_result['composite_score']:.0f}/100 | <b>Confidence:</b> {score_result['confidence']:.0f}%")

    # Convergence detail
    if is_converging:
        engines = convergence.get("converging_engines", [])
        strength = convergence.get("convergence_strength", 0)
        lines.append(f"🔥 Converging: {', '.join(e.title() for e in engines)} (avg {strength:.0f})")

    # Virality
    virality = score_result.get("virality")
    if virality:
        lines.append(f"Virality: {virality['adjusted_virality']:.0f} (integrity {virality['integrity']:.0f})")

    # Exit triggers
    triggers = score_result.get("all_exit_triggers", [])
    if triggers:
        lines.append(f"⚠️ Triggers: {', '.join(triggers)}")

    lines.append("")
    lines.append(f"📋 CA: <code>{mint}</code>")
    lines.append(f"🔗 {_token_links(mint)}")

    return _send("\n".join(lines))


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

_CATEGORY_ICON = {"meme": "🔥", "adoption": "📈", "infrastructure": "🏗"}

_ENGINE_SCORE_KEYS = {
    "momentum": "momentum_score",
    "adoption": "adoption_score",
    "infrastructure": "infra_score",
}

_ENGINE_SHORT = {"momentum": "Mom", "adoption": "Adopt", "infrastructure": "Infra"}


def send_daily_summary(scored_tokens: list[dict]):
    """Send daily summary: top 5 scored tokens across all engines."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not scored_tokens:
        return send_message("📊 <b>Daily Summary</b>\nNo scored tokens today.")

    # Enrich with DB name/symbol/mcap if missing
    _enrich_from_db(scored_tokens)

    lines = [
        "📊 <b>Daily Score Summary</b>",
        f"Tracked: {len(scored_tokens)} tokens",
        "",
        "<b>Top 5:</b>",
    ]

    for i, token in enumerate(scored_tokens[:5], 1):
        symbol = token.get("symbol", "???")
        tok_name = token.get("name", "")
        category = token.get("category", "meme")
        final = token.get("final_score", 0)
        mcap = token.get("mcap", 0)
        icon = _CATEGORY_ICON.get(category, "🔥")

        display = f"{tok_name} (<b>${symbol}</b>)" if tok_name else f"<b>${symbol}</b>"

        # Engine breakdown — show highest engine
        engine_results = token.get("engine_results", {})
        engine_parts = []
        for eng_name, result in engine_results.items():
            key = _ENGINE_SCORE_KEYS.get(eng_name, f"{eng_name}_score")
            val = result.get(key, 0)
            short = _ENGINE_SHORT.get(eng_name, eng_name[:3].title())
            engine_parts.append(f"{short}: {val:.0f}")

        mcap_str = f" | MCap: {_fmt_usd(mcap)}" if mcap else ""
        engine_str = " | ".join(engine_parts)

        lines.append(f"{i}. {icon} {display} — <b>{final:.0f}</b>/100{mcap_str}")
        if engine_str:
            lines.append(f"   {engine_str}")

    # Convergence alerts
    converging = [t for t in scored_tokens if t.get("convergence", {}).get("is_converging")]
    if converging:
        lines.append("")
        lines.append(f"🔥 <b>{len(converging)} convergence alert(s)</b>")
        for t in converging:
            engines = t["convergence"]["converging_engines"]
            label = t.get("name") or t.get("symbol", "?")
            lines.append(f"  • {label}: {', '.join(e.title() for e in engines)}")

    # Exit trigger warnings
    triggered = [t for t in scored_tokens if t.get("all_exit_triggers")]
    if triggered:
        lines.append("")
        lines.append(f"⚠️ <b>{len(triggered)} token(s) with exit triggers</b>")
        for t in triggered:
            triggers = t["all_exit_triggers"]
            label = t.get("name") or t.get("symbol", "?")
            lines.append(f"  • {label}: {', '.join(triggers)}")

    return _send("\n".join(lines))


def _enrich_from_db(scored_tokens: list[dict]):
    """Fill in name, symbol, mcap from DB for tokens missing them."""
    try:
        from db.connection import execute
        rows = execute(
            """SELECT t.id, t.symbol, t.name, t.category,
                      snap.mcap
               FROM tokens t
               LEFT JOIN snapshots_daily snap
                 ON snap.token_id = t.id AND snap.date = CURRENT_DATE
               WHERE t.quality_gate_pass = TRUE""",
            fetch=True,
        )
        db_map = {}
        for tid, sym, name, cat, mcap in rows:
            db_map[tid] = {"symbol": sym, "name": name or "", "category": cat or "meme", "mcap": float(mcap or 0)}
    except Exception:
        return

    for token in scored_tokens:
        tid = token.get("token_id")
        if tid and tid in db_map:
            info = db_map[tid]
            if not token.get("name"):
                token["name"] = info["name"]
            if not token.get("symbol") or len(token.get("symbol", "")) > 10:
                token["symbol"] = info["symbol"]
            if not token.get("category"):
                token["category"] = info["category"]
            if not token.get("mcap"):
                token["mcap"] = info["mcap"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_check_detail(check_name: str, data: dict) -> str:
    """One-line summary for each check."""
    if not data.get("pass") and data.get("reason"):
        return data["reason"]

    match check_name:
        case "contract_safety":
            lp = data.get("lp_status", "?")
            return f"LP {lp}"
        case "liquidity":
            s = data.get("slippage_pct")
            return f"{s}% slippage" if s is not None else "OK"
        case "holders":
            pct = data.get("top10_pct")
            return f"Top10 = {pct}%" if pct is not None else "OK"
        case "sybil":
            score = data.get("sybil_score", "?")
            quality = data.get("avg_quality")
            qa = data.get("quality_adjusted_holders")
            parts = [f"Score {score}"]
            if quality:
                parts.append(f"Quality {quality:.0f}")
            if qa:
                parts.append(f"QA {_fmt_count(qa)}")
            return ", ".join(parts)
        case "unlocks":
            if data.get("skipped"):
                return "Skipped"
            r = data.get("unlock_to_volume_ratio")
            return f"Ratio {r}x" if r is not None else "OK"
        case "wash_trading":
            return f"Score {data.get('wash_score', '?')}"
        case "age_volume":
            age = data.get("age_hours")
            vol = data.get("volume_usd")
            parts = []
            if age is not None:
                parts.append(f"{age:.0f}h old")
            if vol is not None:
                parts.append(f"{_fmt_usd(vol)} vol")
            return ", ".join(parts) if parts else "OK"
        case _:
            return "OK"


def _score_bar(score: float) -> str:
    """Visual score bar using Unicode blocks."""
    filled = int(score / 10)
    return "▓" * filled + "░" * (10 - filled)


def _engine_icon(name: str) -> str:
    """Icon per engine type."""
    icons = {
        "momentum": "📈",
        "adoption": "👥",
        "infrastructure": "🏗",
    }
    return icons.get(name, "📊")


# ---------------------------------------------------------------------------
# Low-level senders
# ---------------------------------------------------------------------------

def _send(text: str) -> bool:
    """Send HTML-formatted message to Telegram."""
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent")
        return True
    except Exception as e:
        log.error("Failed to send Telegram alert: %s", e)
        return False


def send_message(text: str) -> bool:
    """Send a plain text message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured")
        return False
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Telegram send failed: %s", e)
        return False
