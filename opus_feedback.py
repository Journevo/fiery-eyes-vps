"""opus_feedback.py — Parse Opus feedback messages and persist to database.

Handles /update ... /end blocks from Telegram, parsing score changes,
voice claims, strategies, thresholds, and consensus data.
"""

import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("opus_feedback")


# ---------------------------------------------------------------------------
# Score update — reads current score from token_scores, logs change
# ---------------------------------------------------------------------------
def _process_scores(line: str) -> list[str]:
    """Parse 'scores: TOKEN SCORE, TOKEN SCORE, ...' and update."""
    raw = line.split(":", 1)[1].strip()
    pairs = [p.strip() for p in raw.split(",") if p.strip()]
    results = []

    for pair in pairs:
        parts = pair.split()
        if len(parts) != 2:
            results.append("❌ Bad score pair: %s" % pair)
            continue
        token = parts[0].upper()
        try:
            new_score = int(parts[1])
        except ValueError:
            results.append("❌ Bad score value: %s" % pair)
            continue

        # Get current score from token_scores table
        row = execute_one(
            "SELECT total_score FROM token_scores WHERE token = %s ORDER BY date DESC LIMIT 1",
            (token,),
        )
        old_score = int(round(row[0] * 6)) if row and row[0] else None

        # Log to score_history
        execute(
            "INSERT INTO score_history (token, old_score, new_score, reason) VALUES (%s, %s, %s, %s)",
            (token, old_score, new_score, "Opus feedback"),
        )
        if old_score is not None:
            delta = new_score - old_score
            arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            results.append("%s %d %s %d" % (token, old_score, arrow, new_score))
        else:
            results.append("%s → %d (no prior)" % (token, new_score))

    return results


# ---------------------------------------------------------------------------
# Voice claims
# ---------------------------------------------------------------------------
def _process_claim(line: str) -> str:
    """Parse 'claim: VOICE "text" DIRECTION new|repeated'."""
    m = re.match(
        r'claim:\s+(\S+)\s+"([^"]+)"\s+(bullish|bearish|neutral)\s+(new|repeated)',
        line, re.IGNORECASE,
    )
    if not m:
        return "❌ Could not parse: %s" % line

    voice = m.group(1).lower()
    claim_text = m.group(2)
    direction = m.group(3).lower()
    mode = m.group(4).lower()

    # Extract target token if mentioned (uppercase word that looks like a ticker)
    token_match = re.search(r'\b([A-Z]{2,6})\b', claim_text)
    target_token = token_match.group(1) if token_match else None

    if mode == "new":
        execute(
            """INSERT INTO voice_claims (voice, claim, target_token, direction)
               VALUES (%s, %s, %s, %s)""",
            (voice, claim_text, target_token, direction),
        )
        # Ensure voice_accuracy row exists
        execute(
            """INSERT INTO voice_accuracy (voice, total_claims)
               VALUES (%s, 1)
               ON CONFLICT (voice) DO UPDATE SET total_claims = voice_accuracy.total_claims + 1""",
            (voice,),
        )
        return "NEW: %s — \"%s\" (%s)" % (voice, claim_text[:40], direction)
    else:
        # Find existing claim and increment
        row = execute_one(
            """SELECT id FROM voice_claims
               WHERE voice = %s AND claim ILIKE %s AND status = 'PENDING'
               ORDER BY last_seen DESC LIMIT 1""",
            (voice, "%" + claim_text[:30] + "%"),
        )
        if row:
            execute(
                """UPDATE voice_claims
                   SET times_repeated = times_repeated + 1, last_seen = CURRENT_DATE
                   WHERE id = %s""",
                (row[0],),
            )
            return "REPEATED: %s — \"%s\"" % (voice, claim_text[:40])
        else:
            # No match found — insert as new
            execute(
                """INSERT INTO voice_claims (voice, claim, target_token, direction)
                   VALUES (%s, %s, %s, %s)""",
                (voice, claim_text, target_token, direction),
            )
            execute(
                """INSERT INTO voice_accuracy (voice, total_claims)
                   VALUES (%s, 1)
                   ON CONFLICT (voice) DO UPDATE SET total_claims = voice_accuracy.total_claims + 1""",
                (voice,),
            )
            return "NEW (no match for repeated): %s — \"%s\"" % (voice, claim_text[:40])


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
def _process_strategy(line: str) -> str:
    """Parse 'strategy: "name" VOICE CONDITION "rules"'."""
    m = re.match(
        r'strategy:\s+"([^"]+)"\s+(\S+)\s+(bear|bull|choppy|any)\s+"([^"]+)"',
        line, re.IGNORECASE,
    )
    if not m:
        return "❌ Could not parse: %s" % line

    name = m.group(1)
    voice = m.group(2).lower()
    condition = m.group(3).lower()
    rules = m.group(4)

    row = execute_one("SELECT id FROM strategies WHERE name = %s", (name,))
    if row:
        execute(
            "UPDATE strategies SET times_referenced = times_referenced + 1 WHERE id = %s",
            (row[0],),
        )
        return "UPDATED: \"%s\" (ref +1)" % name
    else:
        execute(
            """INSERT INTO strategies (name, source_voice, rules_text, market_condition)
               VALUES (%s, %s, %s, %s)""",
            (name, voice, rules, condition),
        )
        return "NEW: \"%s\" by %s (%s)" % (name, voice, condition)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
def _process_threshold(line: str) -> str:
    """Parse 'threshold: "name" VALUE VOICE'."""
    m = re.match(
        r'threshold:\s+"([^"]+)"\s+(\S+)\s+(\S+)',
        line, re.IGNORECASE,
    )
    if not m:
        return "❌ Could not parse: %s" % line

    name = m.group(1)
    value = m.group(2)
    voice = m.group(3).lower()

    row = execute_one("SELECT id FROM thresholds WHERE LOWER(name) = LOWER(%s)", (name,))
    if row:
        execute(
            "UPDATE thresholds SET trigger_value = %s, times_cited = times_cited + 1, last_checked = NOW() WHERE id = %s",
            (value, row[0]),
        )
        return "UPDATED: \"%s\" = %s" % (name, value)
    else:
        execute(
            "INSERT INTO thresholds (name, trigger_value, source_voice) VALUES (%s, %s, %s)",
            (name, value, voice),
        )
        return "NEW: \"%s\" trigger=%s (by %s)" % (name, value, voice)


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------
def _process_consensus(line: str) -> list[str]:
    """Parse 'consensus: TOKEN DIRECTION PCT%, TOKEN DIRECTION PCT%, ...'."""
    raw = line.split(":", 1)[1].strip()
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    results = []

    for entry in entries:
        m = re.match(r'(\S+)\s+(bullish|bearish|neutral)\s+(\d+)%', entry, re.IGNORECASE)
        if not m:
            results.append("❌ Bad consensus: %s" % entry)
            continue

        token = m.group(1).upper()
        direction = m.group(2).lower()
        pct = int(m.group(3))

        bullish = pct if direction == "bullish" else 0
        bearish = pct if direction == "bearish" else 0
        neutral = pct if direction == "neutral" else 0
        # Fill remainder
        remainder = 100 - pct
        if direction == "bullish":
            bearish = remainder
        elif direction == "bearish":
            bullish = remainder
        else:
            bullish = remainder // 2
            bearish = remainder - bullish

        execute(
            """INSERT INTO consensus_daily (date, token, bullish_pct, bearish_pct, neutral_pct, consensus)
               VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
               ON CONFLICT (date, token) DO UPDATE SET
                 bullish_pct = EXCLUDED.bullish_pct, bearish_pct = EXCLUDED.bearish_pct,
                 neutral_pct = EXCLUDED.neutral_pct, consensus = EXCLUDED.consensus""",
            (token, bullish, bearish, neutral, direction),
        )
        results.append("%s: %s %d%%" % (token, direction, pct))

    return results


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------
def parse_update(text: str) -> str:
    """Parse a full /update ... /end block and return confirmation message."""
    lines = text.strip().split("\n")

    score_results = []
    claim_results = []
    strategy_results = []
    threshold_results = []
    consensus_results = []
    errors = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("/update") or line.startswith("/end"):
            continue

        low = line.lower()
        try:
            if low.startswith("scores:"):
                score_results.extend(_process_scores(line))
            elif low.startswith("claim:"):
                claim_results.append(_process_claim(line))
            elif low.startswith("strategy:"):
                strategy_results.append(_process_strategy(line))
            elif low.startswith("threshold:"):
                threshold_results.append(_process_threshold(line))
            elif low.startswith("consensus:"):
                consensus_results.extend(_process_consensus(line))
            else:
                errors.append("❌ Unknown line: %s" % line[:60])
        except Exception as e:
            errors.append("❌ Error on \"%s\": %s" % (line[:40], e))
            log.error("Parse error on '%s': %s", line, e, exc_info=True)

    # Build confirmation
    parts = ["✅ <b>OPUS UPDATE PROCESSED</b>"]

    if score_results:
        parts.append("📊 Scores: %s" % ", ".join(score_results))
    if claim_results:
        new_count = sum(1 for c in claim_results if c.startswith("NEW"))
        rep_count = sum(1 for c in claim_results if c.startswith("REPEATED"))
        parts.append("🔮 Claims: %d new, %d repeated" % (new_count, rep_count))
        for c in claim_results:
            parts.append("   %s" % c)
    if strategy_results:
        parts.append("📚 Strategies: %s" % ", ".join(strategy_results))
    if threshold_results:
        parts.append("⚠️ Thresholds: %s" % ", ".join(threshold_results))
    if consensus_results:
        parts.append("📈 Consensus: %s" % ", ".join(consensus_results))
    if errors:
        parts.append("")
        parts.extend(errors)

    if not any([score_results, claim_results, strategy_results, threshold_results, consensus_results]):
        parts.append("⚠️ No valid lines parsed")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Query functions (for /consensus, /voices, /claims, etc.)
# ---------------------------------------------------------------------------
def query_consensus(token: str) -> str:
    """Last 7 days of consensus for a token, with trend."""
    token = token.upper()
    rows = execute(
        """SELECT date, bullish_pct, bearish_pct, neutral_pct, consensus
           FROM consensus_daily
           WHERE token = %s AND date >= CURRENT_DATE - 7
           ORDER BY date ASC""",
        (token,), fetch=True,
    )
    if not rows:
        return "No consensus data for %s" % token

    lines = ["📈 <b>CONSENSUS: %s</b> (7 days)\n" % token]
    for r in rows:
        date_str = r[0].strftime("%a %d")
        bull, bear, neut = r[1] or 0, r[2] or 0, r[3] or 0
        consensus = r[4] or "?"
        bar = "🟢" * (bull // 20) + "🔴" * (bear // 20) + "⚪" * (neut // 20)
        lines.append("%s: %s %d%% %s" % (date_str, bar, max(bull, bear, neut), consensus))

    # Trend
    if len(rows) >= 2:
        first_dom = max(rows[0][1] or 0, rows[0][2] or 0)
        last_dom = max(rows[-1][1] or 0, rows[-1][2] or 0)
        first_dir = rows[0][4] or ""
        last_dir = rows[-1][4] or ""
        if last_dir == first_dir:
            if last_dom > first_dom + 5:
                trend = "⬆️ STRENGTHENING"
            elif last_dom < first_dom - 5:
                trend = "⬇️ WEAKENING"
            else:
                trend = "➡️ STABLE"
        else:
            trend = "🔄 SHIFTED: %s → %s" % (first_dir, last_dir)
        lines.append("\nTrend: %s" % trend)

    return "\n".join(lines)


def query_voices() -> str:
    """Top 10 voices by accuracy."""
    rows = execute(
        """SELECT voice, total_claims, confirmed_claims, invalidated_claims,
                  accuracy_pct, conviction_weight
           FROM voice_accuracy
           ORDER BY accuracy_pct DESC, total_claims DESC
           LIMIT 10""",
        fetch=True,
    )
    if not rows:
        return "No voice accuracy data yet."

    lines = ["🎙️ <b>VOICE ACCURACY</b> (top 10)\n"]
    for i, r in enumerate(rows, 1):
        voice, total, confirmed, invalidated, acc, weight = r
        lines.append(
            "%d. <b>%s</b>: %.0f%% (%d/%d) w=%.1f"
            % (i, voice, acc or 0, confirmed or 0, total or 0, weight or 1)
        )
    return "\n".join(lines)


def query_voice(name: str) -> str:
    """All claims for a specific voice, grouped by status."""
    name = name.lower()
    rows = execute(
        """SELECT claim, direction, target_token, status, times_repeated, first_seen, last_seen
           FROM voice_claims WHERE voice = %s
           ORDER BY status, last_seen DESC""",
        (name,), fetch=True,
    )
    if not rows:
        return "No claims found for voice: %s" % name

    lines = ["🎙️ <b>%s</b> — %d claims\n" % (name.upper(), len(rows))]
    current_status = None
    for r in rows:
        claim, direction, token, status, repeats, first, last = r
        if status != current_status:
            current_status = status
            icon = {"PENDING": "⏳", "CONFIRMED": "✅", "INVALIDATED": "❌"}.get(status, "?")
            lines.append("\n%s <b>%s</b>" % (icon, status))
        rep_str = " (x%d)" % repeats if repeats > 1 else ""
        lines.append(
            '  • "%s" %s%s %s' % (claim[:50], direction or "", rep_str, token or "")
        )
    return "\n".join(lines)


def query_claims(token: str) -> str:
    """All claims about a specific token."""
    token = token.upper()
    rows = execute(
        """SELECT voice, claim, direction, status, times_repeated, last_seen
           FROM voice_claims WHERE UPPER(target_token) = %s
           ORDER BY last_seen DESC""",
        (token,), fetch=True,
    )
    if not rows:
        return "No claims about %s" % token

    lines = ["🔮 <b>CLAIMS: %s</b>\n" % token]
    for r in rows:
        voice, claim, direction, status, repeats, last = r
        icon = {"PENDING": "⏳", "CONFIRMED": "✅", "INVALIDATED": "❌"}.get(status, "?")
        rep = " (x%d)" % repeats if repeats > 1 else ""
        lines.append('%s %s: "%s" %s%s' % (icon, voice, claim[:50], direction or "", rep))
    return "\n".join(lines)


def query_thesis() -> str:
    """All PENDING claims ordered by conviction (times_repeated)."""
    rows = execute(
        """SELECT voice, claim, direction, target_token, times_repeated, first_seen
           FROM voice_claims WHERE status = 'PENDING'
           ORDER BY times_repeated DESC, first_seen DESC
           LIMIT 20""",
        fetch=True,
    )
    if not rows:
        return "No pending theses."

    lines = ["📋 <b>ACTIVE THESES</b> (by conviction)\n"]
    for r in rows:
        voice, claim, direction, token, repeats, first = r
        token_str = " [%s]" % token if token else ""
        lines.append(
            '⏳ x%d %s: "%s" %s%s (since %s)'
            % (repeats, voice, claim[:45], direction or "", token_str, first)
        )
    return "\n".join(lines)


def query_learn(keyword: str = None) -> str:
    """Query strategies, optionally filtered by keyword."""
    if keyword:
        rows = execute(
            """SELECT name, source_voice, market_condition, rules_text,
                      times_referenced, validated, date_learned
               FROM strategies
               WHERE name ILIKE %s OR strategy_type ILIKE %s OR rules_text ILIKE %s
               ORDER BY times_referenced DESC""",
            ("%" + keyword + "%", "%" + keyword + "%", "%" + keyword + "%"),
            fetch=True,
        )
    else:
        rows = execute(
            """SELECT name, source_voice, market_condition, rules_text,
                      times_referenced, validated, date_learned
               FROM strategies ORDER BY market_condition, times_referenced DESC""",
            fetch=True,
        )

    if not rows:
        return "No strategies found%s." % (" for '%s'" % keyword if keyword else "")

    lines = ["📚 <b>STRATEGIES</b>%s\n" % (" matching '%s'" % keyword if keyword else "")]
    for r in rows:
        name, voice, cond, rules, refs, validated, date = r
        v_icon = "✅" if validated else "⏳"
        lines.append(
            '%s <b>"%s"</b> by %s [%s] x%d'
            % (v_icon, name, voice, cond or "any", refs or 1)
        )
        if rules:
            lines.append("   %s" % rules[:100])
    return "\n".join(lines)


def query_thresholds() -> str:
    """Show all thresholds with current vs trigger values."""
    rows = execute(
        """SELECT name, trigger_value, current_value, source_voice, times_cited, last_checked
           FROM thresholds ORDER BY times_cited DESC""",
        fetch=True,
    )
    if not rows:
        return "No thresholds tracked."

    lines = ["⚠️ <b>THRESHOLDS</b>\n"]
    for r in rows:
        name, trigger, current, voice, cited, checked = r
        current_str = current if current else "?"
        lines.append(
            '"%s": trigger=%s current=%s (by %s, x%d)'
            % (name, trigger, current_str, voice or "?", cited or 1)
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Weekly thesis validation
# ---------------------------------------------------------------------------
def run_thesis_validation(send_to_telegram: bool = False) -> str:
    """Check all PENDING claims for auto-validation/invalidation."""
    rows = execute(
        """SELECT id, voice, claim, target_token, direction, first_seen
           FROM voice_claims WHERE status = 'PENDING'""",
        fetch=True,
    )
    if not rows:
        return "No pending claims to validate."

    # Fetch current prices
    prices = _fetch_prices()
    confirmed = 0
    invalidated = 0
    checked = 0

    for r in rows:
        cid, voice, claim, token, direction, first_seen = r
        checked += 1

        # Check if claim is older than 90 days — auto-invalidate
        if first_seen and (datetime.now(timezone.utc).date() - first_seen).days > 90:
            _mark_claim(cid, voice, "INVALIDATED")
            invalidated += 1
            continue

        # Try to extract price target from claim text
        price_match = re.search(r'\$?([\d,]+(?:\.\d+)?)\s*[kK]?', claim)
        if not price_match or not token or token not in prices:
            continue

        target_str = price_match.group(1).replace(",", "")
        try:
            target = float(target_str)
            if "k" in claim.lower() or "K" in claim:
                target *= 1000
        except ValueError:
            continue

        current = prices.get(token)
        if not current:
            continue

        # Check if price has reached target
        if direction == "bullish" and current >= target:
            _mark_claim(cid, voice, "CONFIRMED")
            confirmed += 1
        elif direction == "bearish" and current <= target:
            _mark_claim(cid, voice, "CONFIRMED")
            confirmed += 1

    result = "🔍 Thesis validation: %d checked, %d confirmed, %d invalidated" % (
        checked, confirmed, invalidated)

    if send_to_telegram:
        _send_telegram(result)

    return result


def _mark_claim(claim_id: int, voice: str, new_status: str):
    """Update claim status and voice accuracy."""
    execute(
        "UPDATE voice_claims SET status = %s, confirmed_date = CURRENT_DATE WHERE id = %s",
        (new_status, claim_id),
    )
    if new_status == "CONFIRMED":
        execute(
            """UPDATE voice_accuracy
               SET confirmed_claims = confirmed_claims + 1,
                   accuracy_pct = CASE WHEN total_claims > 0
                     THEN ((confirmed_claims + 1)::decimal / total_claims) * 100
                     ELSE 0 END
               WHERE voice = %s""",
            (voice,),
        )
    elif new_status == "INVALIDATED":
        execute(
            """UPDATE voice_accuracy
               SET invalidated_claims = invalidated_claims + 1,
                   accuracy_pct = CASE WHEN total_claims > 0
                     THEN (confirmed_claims::decimal / total_claims) * 100
                     ELSE 0 END
               WHERE voice = %s""",
            (voice,),
        )


def _fetch_prices() -> dict:
    """Fetch current prices for common tokens from CoinGecko."""
    import requests
    from config import COINGECKO_API_KEY
    cg_map = {
        "BTC": "bitcoin", "SOL": "solana", "ETH": "ethereum",
        "HYPE": "hyperliquid", "JUP": "jupiter-exchange-solana",
        "RENDER": "render-token", "BONK": "bonk", "PUMP": "pump-fun",
        "PENGU": "pudgy-penguins", "FARTCOIN": "fartcoin",
    }
    ids_str = ",".join(cg_map.values())
    try:
        headers = {"x-cg-demo-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ids_str, "vs_currencies": "usd"},
            headers=headers, timeout=10,
        )
        data = resp.json()
        return {sym: data.get(cg_id, {}).get("usd") for sym, cg_id in cg_map.items()}
    except Exception as e:
        log.error("Price fetch failed: %s", e)
        return {}


def _send_telegram(text: str):
    import requests
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": {"keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"],
                                                 ["💼 Portfolio", "⚙️ System"]],
                                    "resize_keyboard": True, "is_persistent": True}},
            timeout=15,
        )
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# Notebook sections
# ---------------------------------------------------------------------------
def notebook_consensus_shift() -> str:
    """Consensus shift section for notebook."""
    rows = execute(
        """SELECT token,
                  (SELECT consensus FROM consensus_daily c2 WHERE c2.token = c1.token ORDER BY date DESC LIMIT 1) as latest_consensus,
                  (SELECT GREATEST(bullish_pct, bearish_pct, neutral_pct) FROM consensus_daily c2 WHERE c2.token = c1.token ORDER BY date DESC LIMIT 1) as latest_pct,
                  (SELECT GREATEST(bullish_pct, bearish_pct, neutral_pct) FROM consensus_daily c2 WHERE c2.token = c1.token AND c2.date <= CURRENT_DATE - 7 ORDER BY date DESC LIMIT 1) as week_ago_pct
           FROM consensus_daily c1
           WHERE date >= CURRENT_DATE - 7
           GROUP BY token""",
        fetch=True,
    )
    if not rows:
        return "CONSENSUS SHIFT: No data yet"

    lines = ["CONSENSUS SHIFT (7 days):"]
    for r in rows:
        token, consensus, now_pct, week_pct = r
        now_pct = now_pct or 0
        week_pct = week_pct or 0
        if week_pct > 0:
            if now_pct > week_pct + 5:
                trend = "STRENGTHENING"
            elif now_pct < week_pct - 5:
                trend = "WEAKENING"
            else:
                trend = "STABLE"
            lines.append("  %s: %d%% %s (was %d%% last week) — %s" % (
                token, now_pct, consensus or "?", week_pct, trend))
        else:
            lines.append("  %s: %d%% %s — NEW" % (token, now_pct, consensus or "?"))
    return "\n".join(lines)


def notebook_voice_accuracy() -> str:
    """Voice accuracy section for notebook."""
    rows = execute(
        """SELECT voice, accuracy_pct, confirmed_claims, total_claims
           FROM voice_accuracy
           WHERE total_claims > 0
           ORDER BY accuracy_pct DESC
           LIMIT 5""",
        fetch=True,
    )
    if not rows:
        return "VOICE ACCURACY: No data yet"

    lines = ["VOICE ACCURACY (top 5):"]
    for i, r in enumerate(rows, 1):
        voice, acc, confirmed, total = r
        lines.append("  %d. %s: %.0f%% (%d/%d claims)" % (i, voice, acc or 0, confirmed or 0, total or 0))
    return "\n".join(lines)


def notebook_new_claims() -> str:
    """New claims this week for notebook."""
    rows = execute(
        """SELECT voice, claim, direction, times_repeated
           FROM voice_claims
           WHERE first_seen >= CURRENT_DATE - 7 AND status = 'PENDING'
           ORDER BY first_seen DESC""",
        fetch=True,
    )
    if not rows:
        return "NEW CLAIMS THIS WEEK: None"

    lines = ["NEW CLAIMS THIS WEEK:"]
    for r in rows:
        voice, claim, direction, repeats = r
        tag = "(NEW)" if repeats <= 1 else "(x%d)" % repeats
        lines.append('  - %s: %s %s %s' % (voice, claim[:60], direction or "", tag))
    return "\n".join(lines)
