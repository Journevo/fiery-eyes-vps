"""notebook.py — Compile all daily intelligence into a paste-ready Opus prompt."""
import json
import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("notebook")

KEYBOARD = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True, "is_persistent": True,
}


def _gather_market():
    parts = []
    try:
        from watchlist import fetch_prices
        prices = fetch_prices() or {}
        btc = prices.get("BTC", {})
        sol = prices.get("SOL", {})
        parts.append("BTC: $%s | SOL: $%s" % (btc.get("price", "?"), sol.get("price", "?")))
    except Exception as e:
        parts.append("Prices: unavailable (%s)" % e)

    try:
        from market_structure import run_market_structure
        ms = run_market_structure() or {}
        fg = ms.get("fear_greed", {})
        parts.append("F&G: %s (%s)" % (fg.get("value", "?"), fg.get("label", "")))
        funding = ms.get("funding", {})
        if funding.get("current_pct") is not None:
            parts.append("BTC Funding: %s%%" % funding["current_pct"])
    except Exception:
        parts.append("Market structure: unavailable")

    try:
        from nimbus_sync import get_nimbus_data, get_regimes
        nimbus = get_nimbus_data() or {}
        regimes = get_regimes() or {}
        crypto = nimbus.get("crypto", {})
        rates = nimbus.get("rates", {})
        dxy = nimbus.get("dxy", {})
        cbbi_vals = crypto.get("cbbi", [])
        cbbi = cbbi_vals[-1] if isinstance(cbbi_vals, list) and cbbi_vals else "?"
        fg_vals = crypto.get("fear_greed", [])
        fg_val = fg_vals[-1] if isinstance(fg_vals, list) and fg_vals else "?"

        parts.append("CBBI: %s | Nimbus F&G: %s" % (cbbi, fg_val))
        fed = rates.get("fed", {})
        parts.append("Fed: %s%% (next: %s)" % (fed.get("rate", "?"), fed.get("next", "?")))
        dxy_vals = dxy.get("values", [])
        parts.append("DXY: %s" % (dxy_vals[-1] if dxy_vals else "?"))
        parts.append("US Liq: %s (%s) | Global: %s | M2: %s" % (
            regimes.get("us_regime", "?"), regimes.get("us_slope", "?"),
            regimes.get("global_regime", "?"), regimes.get("m2_regime", "?")))
        parts.append("Nimbus as_of: %s" % nimbus.get("meta", {}).get("as_of_date", "?"))
    except Exception as e:
        parts.append("Nimbus: unavailable (%s)" % e)

    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        bp = fetch_btc_price()
        if bp:
            c = calculate_cycle(bp)
            parts.append("BTC Cycle: %s%% complete, ~%sd to bottom" % (
                c.get("bear_progress_pct", "?"), c.get("days_remaining", "?")))
    except Exception:
        pass

    return "\n".join(parts)


def _gather_positions():
    try:
        from portfolio import ensure_tables, get_portfolio, format_portfolio_telegram
        ensure_tables()
        p = get_portfolio()
        return format_portfolio_telegram(p)
    except Exception as e:
        return "Positions: unavailable (%s)" % e


def _gather_youtube():
    rows = execute("""
        SELECT channel_name, title, processed_at, analysis_json
        FROM youtube_videos
        WHERE processed_at > NOW() - INTERVAL '24 hours'
        ORDER BY processed_at DESC
    """, fetch=True)
    if not rows:
        return "No YouTube videos in last 24h."

    parts = ["YOUTUBE INTELLIGENCE — %d videos\n" % len(rows)]
    for r in rows:
        ch, title, ts, aj = r[0], r[1], r[2], r[3]
        ts_str = ts.strftime("%H:%M") if ts else "?"

        text = ""
        if aj:
            if isinstance(aj, dict):
                text = aj.get("summary", json.dumps(aj)[:500])
            elif isinstance(aj, str):
                try:
                    d = json.loads(aj)
                    text = d.get("summary", aj[:500])
                except Exception:
                    text = aj[:500]

        parts.append("--- %s (%s) ---" % (ch, ts_str))
        parts.append('"%s"' % (title or "?"))
        if text:
            parts.append(text[:2000])
        parts.append("")

    return "\n".join(parts)


def _gather_scorecard():
    try:
        from research.research_manager import get_scorecard
        return get_scorecard()
    except Exception as e:
        return "Scorecard: unavailable (%s)" % e


def _gather_sunflow():
    try:
        rows = execute("""
            SELECT token, conviction_score, timeframes_present, net_flow_usd
            FROM sunflow_conviction ORDER BY conviction_score DESC
        """, fetch=True)
        if not rows:
            return "SunFlow: no data"
        lines = ["SUNFLOW CONVICTION:"]
        for r in rows:
            flow = "$%s" % "{:,.0f}".format(r[3]) if r[3] else "?"
            lines.append("  %s: conviction %s, %s/4 TF, net %s" % (r[0], r[1], r[2], flow))
        return "\n".join(lines)
    except Exception as e:
        return "SunFlow: unavailable (%s)" % e


def _gather_opus_feedback():
    try:
        from opus_feedback import notebook_consensus_shift, notebook_voice_accuracy, notebook_new_claims
        parts = []
        parts.append(notebook_consensus_shift())
        parts.append("")
        parts.append(notebook_voice_accuracy())
        parts.append("")
        parts.append(notebook_new_claims())
        return "\n".join(parts)
    except Exception as e:
        return "Opus feedback: unavailable (%s)" % e


OPUS_PROMPT = """You are my portfolio intelligence analyst for Fiery Eyes.
Below is today's complete intelligence dump.

TASKS:
1. SYNTHESIS: What did today's voices AGREE on? Where did they DISAGREE?
   What are the unresolved tensions?

2. WATCHLIST SCORES: Based on today's intel, should any scores change?
   Current: BTC:90 | SOL:82 | HYPE:79 | JUP:78 | RENDER:72 | SUI:71 | BONK:64 | PUMP:55
   Format: [TOKEN] [OLD] -> [NEW] [REASON]
   Only change if today's intel justifies it.

3. POSITION CALL: Based on all layers (business cycle, liquidity, BTC cycle,
   market structure, macro) — should I be IN or OUT? Conviction 1-10.

4. DEEP DIVE FLAGS: Any token where today's intel materially changes the thesis?
   Quote the specific video and claim.

5. DISCOVERY: Any non-watchlist token mentioned 3+ times that deserves investigation?

6. ACTIONS: 0-3 specific things to do before tomorrow. "No action" is valid."""


def generate_notebook():
    """Compile all intelligence into one paste-ready block."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A %d %B %Y")

    sections = [
        "=" * 50,
        "FIERY EYES NOTEBOOK — %s" % date_str,
        "=" * 50,
        "",
        "PROMPT FOR CLAUDE OPUS:",
        OPUS_PROMPT,
        "",
        "=" * 50,
        "MARKET STATE",
        "=" * 50,
        _gather_market(),
        "",
        "=" * 50,
        "POSITIONS",
        "=" * 50,
        _gather_positions(),
        "",
        "=" * 50,
        "DEEP DIVE SCORECARD",
        "=" * 50,
        _gather_scorecard(),
        "",
        "=" * 50,
        "SUNFLOW WHALE DATA",
        "=" * 50,
        _gather_sunflow(),
        "",
        "=" * 50,
        "OPUS FEEDBACK LOOP",
        "=" * 50,
        _gather_opus_feedback(),
        "",
        "=" * 50,
        _gather_youtube(),
        "=" * 50,
        "",
        "END — PASTE EVERYTHING ABOVE INTO CLAUDE OPUS",
        "=" * 50,
    ]

    return "\n".join(sections)


def send_notebook(text=None):
    """Generate and send notebook to Telegram."""
    if text is None:
        text = generate_notebook()

    max_len = 4000
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    for i, chunk in enumerate(chunks):
        label = "(%d/%d) " % (i + 1, len(chunks)) if len(chunks) > 1 else ""
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": label + chunk,
                  "disable_web_page_preview": True, "reply_markup": KEYBOARD},
            timeout=15)

    log.info("Notebook sent: %d chunks" % len(chunks))
    return len(chunks)


if __name__ == "__main__":
    text = generate_notebook()
    print(text[:3000])
    print("\n... (%d total chars)" % len(text))
