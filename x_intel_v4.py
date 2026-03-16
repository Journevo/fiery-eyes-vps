"""X Intelligence v4 — Actionable Intelligence System

Flow:
1. Grok polls 173 accounts (raw tweets in x_intelligence DB)
2. Every 4h, batch tweets + Grok research queries → Haiku
3. Haiku outputs TWO sections:
   - BRIEFING: prose for the operator to read
   - ACTIONS: structured JSON that the system executes
4. System auto-executes: conviction changes, risk flags,
   watchlist alerts, narrative updates

Cost: ~$0.01/batch × 6/day = $1.80/month
"""

import json
import re
import time
import requests
import anthropic
from datetime import datetime, timezone
from config import GROK_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("x_intel")

GROK_URL = "https://api.x.ai/v1/responses"
GROK_MODEL = "grok-4-1-fast"
HAIKU_MODEL = "claude-haiku-4-5-20251001"

WATCHLIST = {"BTC", "SOL", "SUI", "HYPE", "RENDER", "JUP", "BONK", "PUMP", "PENGU", "FARTCOIN", "DEEP"}

RESEARCH_QUERIES = [
    "Latest whale movements and large buys/sells for RENDER HYPE JUP SOL SUI BONK in the last 4 hours",
    "Any major news affecting Solana ecosystem tokens or Bitcoin in the last 4 hours",
]

_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# ---------------------------------------------------------------------------
# DB tables for actionable intelligence
# ---------------------------------------------------------------------------
def ensure_tables():
    execute("""
        CREATE TABLE IF NOT EXISTS x_briefings (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            raw_intel TEXT,
            summary TEXT,
            actions_json JSONB,
            word_count INTEGER
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS conviction_changes (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            token TEXT NOT NULL,
            old_score REAL,
            new_score REAL,
            reason TEXT,
            source TEXT DEFAULT 'x_intel'
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS narrative_tracker (
            id SERIAL PRIMARY KEY,
            narrative TEXT NOT NULL,
            status TEXT NOT NULL,
            sources TEXT,
            weeks_tracked INTEGER DEFAULT 1,
            last_updated TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(narrative)
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS risk_flags (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            token TEXT NOT NULL,
            risk_type TEXT,
            description TEXT,
            source TEXT,
            resolved BOOLEAN DEFAULT FALSE
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS watchlist_flags (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            token TEXT NOT NULL,
            reason TEXT,
            source_count INTEGER,
            sources TEXT
        )
    """)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def _get_recent_tweets(hours: int = 4) -> str:
    try:
        rows = execute("""
            SELECT source_handle, token_symbol, parsed_type, tweet_text, amount_usd,
                   signal_strength
            FROM x_intelligence
            WHERE detected_at > NOW() - INTERVAL '%s hours'
            ORDER BY detected_at DESC LIMIT 200
        """ % int(hours), fetch=True)

        if not rows:
            return ""

        lines = []
        seen = set()
        for source, symbol, ptype, text, amount, strength in rows:
            key = (text or "")[:100]
            if key in seen:
                continue
            seen.add(key)
            t = (text or "").strip()
            if not t or len(t) < 20:
                continue
            lines.append("%s: %s" % (source or "unknown", t[:300]))

        return "\n\n".join(lines[:150])
    except Exception as e:
        log.error("Failed to get recent tweets: %s", e)
        return ""


def _grok_search(query: str) -> str:
    if not GROK_API_KEY:
        return ""
    try:
        resp = requests.post(
            GROK_URL,
            headers={"Authorization": "Bearer " + GROK_API_KEY, "Content-Type": "application/json"},
            json={
                "model": GROK_MODEL,
                "instructions": "You are a crypto research assistant. Return detailed, specific information with numbers and source attribution.",
                "input": [{"role": "user", "content": query}],
                "tools": [{"type": "x_search"}],
                "temperature": 0,
            },
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        from monitoring.degraded import record_api_call
        record_api_call("grok", True)

        content = ""
        for item in data.get("output", []):
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        content += block.get("text", "")
        return content.strip()
    except Exception as e:
        log.error("Grok research query failed: %s", e)
        from monitoring.degraded import record_api_call
        record_api_call("grok", False)
        return ""


# ---------------------------------------------------------------------------
# Haiku analysis — briefing + structured actions
# ---------------------------------------------------------------------------
def _haiku_analyse(tweet_dump: str, grok_research: str) -> tuple[str | None, dict | None]:
    """Run Haiku analysis. Returns (briefing_text, actions_dict)."""
    if not ANTHROPIC_API_KEY:
        return None, None

    # Get current conviction scores for context
    sf_context = ""
    try:
        rows = execute("""
            SELECT token, conviction_score, net_flow_usd, timeframes_present
            FROM sunflow_conviction WHERE is_watchlist = TRUE
            ORDER BY conviction_score DESC
        """, fetch=True)
        if rows:
            sf_lines = ["%s: conviction %s, %s/4 TF" % (r[0], r[1], r[3]) for r in rows]
            sf_context = "\nCurrent SunFlow conviction: " + " | ".join(sf_lines)
    except Exception:
        pass

    # Get current narratives
    narr_context = ""
    try:
        rows = execute("SELECT narrative, status, weeks_tracked FROM narrative_tracker ORDER BY last_updated DESC LIMIT 5", fetch=True)
        if rows:
            narr_lines = ["%s: %s (%dw)" % (r[0], r[1], r[2]) for r in rows]
            narr_context = "\nActive narratives: " + " | ".join(narr_lines)
    except Exception:
        pass

    intel_parts = []
    if tweet_dump:
        intel_parts.append("=== ACCOUNT TWEETS (last 4h) ===\n" + tweet_dump)
    if grok_research:
        intel_parts.append("=== RESEARCH QUERIES ===\n" + grok_research)
    combined = "\n\n".join(intel_parts)
    if not combined.strip():
        return None, None

    # Build prompt with string concatenation to avoid format/% issues with JSON braces
    prompt_lines = [
        "You are a crypto research analyst producing an actionable intelligence briefing.",
        "",
        "WATCHLIST: BTC, SOL, SUI, HYPE, RENDER, JUP, BONK, PUMP, PENGU, FARTCOIN, DEEP",
        "POSITIONS: JUP, HYPE, RENDER, BONK (held). 50%+ dry powder in USDC.",
    ]
    if sf_context:
        prompt_lines.append(sf_context.strip())
    if narr_context:
        prompt_lines.append(narr_context.strip())
    prompt_lines.extend([
        "",
        "Read ALL the intelligence below. Output TWO sections separated by ===ACTIONS===",
        "",
        "SECTION 1 - BRIEFING (prose, under 400 words):",
        "For each watchlist token with new information: 2-3 sentences citing sources and numbers. Flag BULLISH/BEARISH/NEUTRAL.",
        "Then: MACRO (1-2 sentences), SMART MONEY (whale moves), NON-WATCHLIST (tokens getting 3+ source attention).",
        "Skip tokens with no new info. Be specific. Cite sources.",
        "",
        "SECTION 2 - ACTIONS (valid JSON after ===ACTIONS===):",
        'Return a JSON object with these arrays (empty arrays if nothing to report):',
        '',
        '{"conviction_changes": [{"token": "RENDER", "delta": 0.5, "reason": "3 sources confirm whale accumulation"}],',
        ' "risk_flags": [{"token": "PUMP", "risk": "team wallet moved tokens", "source": "lookonchain", "severity": "high"}],',
        ' "watchlist_flags": [{"token": "TAO", "reason": "AI compute thesis", "sources": ["aixbt", "nansen"]}],',
        ' "narrative_updates": [{"narrative": "AI COMPUTE", "status": "STRENGTHENING", "detail": "RENDER revenue growth"}],',
        ' "status": "3 changes detected"}',
        '',
        "Rules for actions:",
        "- conviction_changes delta: +0.5 mild bullish, +1.0 strong (3+ sources), -0.5 mild bearish, -2.0 risk event",
        "- risk_flags severity: low, medium, high, critical",
        "- watchlist_flags: only if 3+ DIFFERENT sources mention with substantive analysis",
        "- narrative_updates status: FORMING, EMERGING, STRENGTHENING, MAINSTREAM, WEAKENING, DYING",
        "- If nothing actionable, return empty arrays and status quiet period",
        "- MCap mentions are NOT buy amounts",
        "",
        "INTELLIGENCE:",
    ])
    prompt = "\n".join(prompt_lines) + "\n" + combined[:12000]

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        full_text = response.content[0].text
        cost = (response.usage.input_tokens * 1 / 1e6) + (response.usage.output_tokens * 5 / 1e6)
        log.info("Haiku analysis: %d in / %d out, $%.4f",
                 response.usage.input_tokens, response.usage.output_tokens, cost)

        # Split into briefing and actions
        if "===ACTIONS===" in full_text:
            parts = full_text.split("===ACTIONS===", 1)
            briefing = parts[0].strip()
            actions_raw = parts[1].strip()
        else:
            briefing = full_text.strip()
            actions_raw = ""

        # Parse actions JSON
        actions = None
        if actions_raw:
            actions_raw = re.sub(r'^```json?\s*', '', actions_raw, flags=re.MULTILINE)
            actions_raw = re.sub(r'\s*```$', '', actions_raw, flags=re.MULTILINE)
            try:
                actions = json.loads(actions_raw)
            except json.JSONDecodeError:
                match = re.search(r'\{[\s\S]*\}', actions_raw)
                if match:
                    try:
                        actions = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        log.warning("Could not parse actions JSON")

        return briefing, actions

    except Exception as e:
        log.error("Haiku analysis failed: %s", e)
        return None, None


def _execute_actions(actions: dict) -> list[str]:
    """Execute structured actions from Haiku. Returns list of action summaries."""
    if not actions:
        return []

    summaries = []

    # 1. Conviction changes
    for change in actions.get("conviction_changes", []):
        token = change.get("token", "").upper()
        delta = change.get("delta", 0)
        reason = change.get("reason", "")
        if not token or delta == 0:
            continue

        # Get current conviction
        try:
            row = execute(
                "SELECT conviction_score FROM sunflow_conviction WHERE token = %s",
                (token,), fetch=True)
            old_score = row[0][0] if row else 5.0
            new_score = max(0, min(16, old_score + delta))

            # Update conviction
            execute("""
                UPDATE sunflow_conviction SET conviction_score = %s WHERE token = %s
            """, (new_score, token))

            # Log the change
            execute("""
                INSERT INTO conviction_changes (token, old_score, new_score, reason)
                VALUES (%s, %s, %s, %s)
            """, (token, old_score, new_score, reason))

            direction = "+" if delta > 0 else ""
            summary = "%s conviction %.1f \u2192 %.1f (%s%s: %s)" % (
                token, old_score, new_score, direction, delta, reason[:80])
            summaries.append(summary)
            log.info("Conviction change: %s", summary)

        except Exception as e:
            log.error("Conviction update failed for %s: %s", token, e)

    # 2. Risk flags
    for flag in actions.get("risk_flags", []):
        token = flag.get("token", "").upper()
        risk = flag.get("risk", "")
        source = flag.get("source", "")
        severity = flag.get("severity", "medium")
        if not token or not risk:
            continue

        try:
            execute("""
                INSERT INTO risk_flags (token, risk_type, description, source)
                VALUES (%s, %s, %s, %s)
            """, (token, severity, risk, source))

            emoji = {"critical": "\U0001f534", "high": "\u26a0\ufe0f", "medium": "\U0001f7e1"}.get(severity, "\u26aa")
            summary = "%s RISK %s: %s (source: %s)" % (emoji, token, risk[:80], source)
            summaries.append(summary)
            log.warning("Risk flag: %s", summary)

            # Auto-reduce conviction on high/critical risk
            if severity in ("high", "critical"):
                penalty = -2.0 if severity == "critical" else -1.0
                try:
                    row = execute(
                        "SELECT conviction_score FROM sunflow_conviction WHERE token = %s",
                        (token,), fetch=True)
                    if row:
                        old = row[0][0]
                        new = max(0, old + penalty)
                        execute("UPDATE sunflow_conviction SET conviction_score = %s WHERE token = %s",
                                (new, token))
                        execute("""INSERT INTO conviction_changes (token, old_score, new_score, reason)
                                   VALUES (%s, %s, %s, %s)""",
                                (token, old, new, "Auto-reduced: " + risk[:60]))
                        summaries.append("%s conviction %.1f \u2192 %.1f (auto-risk)" % (token, old, new))
                except Exception:
                    pass

        except Exception as e:
            log.error("Risk flag failed for %s: %s", token, e)

    # 3. Watchlist flags
    for flag in actions.get("watchlist_flags", []):
        token = flag.get("token", "").upper()
        reason = flag.get("reason", "")
        sources = flag.get("sources", [])
        if not token or not reason:
            continue
        try:
            execute("""
                INSERT INTO watchlist_flags (token, reason, source_count, sources)
                VALUES (%s, %s, %s, %s)
            """, (token, reason, len(sources), ", ".join(sources)))
            summary = "\U0001f50d %s flagged for /deepdive \u2014 %s" % (token, reason[:80])
            summaries.append(summary)
            log.info("Watchlist flag: %s", summary)
        except Exception as e:
            log.error("Watchlist flag failed for %s: %s", token, e)

    # 4. Narrative updates
    for narr in actions.get("narrative_updates", []):
        narrative = narr.get("narrative", "")
        status = narr.get("status", "")
        detail = narr.get("detail", "")
        if not narrative or not status:
            continue
        try:
            execute("""
                INSERT INTO narrative_tracker (narrative, status, sources, weeks_tracked)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (narrative) DO UPDATE SET
                    status = EXCLUDED.status,
                    sources = EXCLUDED.sources,
                    weeks_tracked = narrative_tracker.weeks_tracked + 1,
                    last_updated = NOW()
            """, (narrative, status, detail))
            summary = "\U0001f4ca %s: %s" % (narrative, status)
            summaries.append(summary)
            log.info("Narrative update: %s — %s", narrative, status)
        except Exception as e:
            log.error("Narrative update failed: %s", e)

    return summaries


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def _send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    max_len = 4000
    chunks = [text] if len(text) <= max_len else []
    if not chunks:
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
            requests.post(
                "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True, "reply_markup": _KEYBOARD_JSON},
                timeout=15)
        except Exception as e:
            log.error("Telegram send error: %s", e)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_x_intel_batch(send_to_telegram: bool = True) -> dict:
    """Full X Intel pipeline: tweets + research → Haiku → actions → Telegram."""
    log.info("=== X Intel Briefing starting ===")
    ensure_tables()

    # 1. Collect any existing tweets from DB (historical, may be stale)
    tweet_dump = _get_recent_tweets(hours=12)
    log.info("Tweet dump: %d chars from DB", len(tweet_dump))

    # 2. YouTube intelligence from DB
    yt_intel = ""
    try:
        from youtube_intel import get_recent_youtube_intel
        yt = get_recent_youtube_intel(hours=12)
        if yt.get("analyses"):
            yt_parts = []
            for a in yt["analyses"][:10]:
                aj = a.get("analysis_json") or {}
                summary = aj.get("summary", "")[:200] if isinstance(aj, dict) else str(aj)[:200]
                yt_parts.append("%s: %s" % (a.get("channel_name", "?"), summary))
            yt_intel = "=== YOUTUBE (12h) ===\n" + "\n".join(yt_parts)
            log.info("YouTube intel: %d analyses", len(yt["analyses"]))
    except Exception as e:
        log.error("YouTube intel failed: %s", e)

    # 3. SunFlow data is included in Haiku prompt via sf_context

    if not tweet_dump and not yt_intel:
        log.warning("No intelligence — skipping")
        return {"status": "empty"}

    grok_research = yt_intel  # Feed YouTube findings as "research"

    # 3. Haiku analysis (briefing + actions)
    log.info("Running Haiku analysis...")
    briefing, actions = _haiku_analyse(tweet_dump, grok_research)

    if not briefing:
        log.error("Haiku failed")
        return {"status": "haiku_failed"}

    # 4. Execute actions
    action_summaries = _execute_actions(actions)
    action_status = actions.get("status", "no actions") if actions else "no actions"

    # 5. Format message
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    parts = ["\U0001f4e1 <b>INTEL BRIEFING \u2014 %s</b>\n\n%s" % (now, briefing)]

    if action_summaries:
        parts.append("\n\n\u2501\u2501\u2501 <b>SYSTEM ACTIONS</b> \u2501\u2501\u2501")
        for s in action_summaries:
            parts.append(s)
    else:
        parts.append("\n<i>%s</i>" % action_status)

    message = "\n".join(parts)

    # 6. Store
    try:
        execute("""
            INSERT INTO x_briefings (raw_intel, summary, actions_json, word_count)
            VALUES (%s, %s, %s, %s)
        """, (
            (tweet_dump[:5000] + "\n---\n" + grok_research[:5000]),
            briefing,
            json.dumps(actions) if actions else None,
            len(briefing.split()),
        ))
    except Exception as e:
        log.error("Store briefing failed: %s", e)

    # 7. Send
    if send_to_telegram:
        _send_telegram(message)
        log.info("X Intel sent (%d words, %d actions)", len(briefing.split()), len(action_summaries))

    return {
        "status": "ok",
        "tweet_chars": len(tweet_dump),
        "research_chars": len(grok_research),
        "summary_words": len(briefing.split()),
        "actions_executed": len(action_summaries),
        "action_status": action_status,
    }


def get_latest_briefing() -> str | None:
    try:
        row = execute("SELECT summary FROM x_briefings ORDER BY created_at DESC LIMIT 1", fetch=True)
        return row[0][0] if row else None
    except Exception:
        return None


def get_recent_actions(hours: int = 24) -> list[dict]:
    """Get recent conviction changes and risk flags."""
    results = []
    try:
        rows = execute("""
            SELECT token, old_score, new_score, reason, created_at
            FROM conviction_changes WHERE created_at > NOW() - INTERVAL '%s hours'
            ORDER BY created_at DESC
        """ % int(hours), fetch=True)
        for r in (rows or []):
            results.append({"type": "conviction", "token": r[0],
                            "old": r[1], "new": r[2], "reason": r[3], "at": str(r[4])})
    except Exception:
        pass
    try:
        rows = execute("""
            SELECT token, risk_type, description, source, created_at
            FROM risk_flags WHERE created_at > NOW() - INTERVAL '%s hours'
            ORDER BY created_at DESC
        """ % int(hours), fetch=True)
        for r in (rows or []):
            results.append({"type": "risk", "token": r[0],
                            "severity": r[1], "desc": r[2], "source": r[3], "at": str(r[4])})
    except Exception:
        pass
    return results


if __name__ == "__main__":
    result = run_x_intel_batch(send_to_telegram=False)
    print("Result:", json.dumps(result, indent=2))
    briefing = get_latest_briefing()
    if briefing:
        print("\n" + briefing)
