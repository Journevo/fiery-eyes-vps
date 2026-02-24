"""YouTube Channel Monitor — RSS + transcript API + Claude AI analysis.

No YouTube API key required. Uses:
  - RSS feeds for new video detection
  - youtube-transcript-api for caption download (primary)
  - yt-dlp for auto-caption download (fallback)
  - Claude API (Haiku) for per-video analysis
  - Claude API (Sonnet) for daily digest synthesis

Runs every 2 hours via systemd timer.
Daily digest at 03:00 UTC.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import requests
from youtube_transcript_api import YouTubeTranscriptApi

from config import ANTHROPIC_API_KEY, get_logger
from db.connection import execute, execute_one
from telegram_bot.alerts import _send, send_message

log = get_logger("social.youtube")

CHANNELS_FILE = Path(__file__).parent / "youtube_channels.json"
COOKIES_FILE = Path(__file__).parents[1] / "cookies.txt"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YT_DLP = Path(__file__).parents[1] / "venv" / "bin" / "yt-dlp"

# Anthropic models
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-20250514"

ANALYSIS_PROMPT = """You are a crypto investment analyst. Analyse this video transcript:
1. THESIS: Main investment argument (2-3 sentences)
2. TOKEN CALLS: Every specific token mentioned with sentiment (bullish/bearish/neutral), reasoning, price targets if given
3. KEY INSIGHTS: Non-obvious alpha, data points, insider info
4. MACRO VIEW: Market conditions view
5. ACTION ITEMS: Specific actions recommended
6. RISK WARNINGS: Risks mentioned

Respond ONLY in valid JSON (no markdown, no code fences):
{"thesis": "...", "token_calls": [{"token": "BTC", "sentiment": "bullish", "reasoning": "...", "price_target": "$X"}], "key_insights": ["..."], "macro_view": "...", "action_items": ["..."], "risk_warnings": ["..."]}

Transcript:
"""

DIGEST_PROMPT = """You are a crypto investment strategist synthesising YouTube intelligence.

Given analyses from multiple crypto YouTube channels in the last 24 hours, produce a consensus digest.

For each section, be specific and cite which channels said what:

1. CONSENSUS: Overall sentiment count (bullish/neutral/bearish). Most discussed tokens with channel counts.
2. TOP_CONVICTION: Tokens with strongest multi-channel bullish consensus. Include best quote and channel name.
3. CONTRARIAN: Views that go against consensus. Channel name and their argument.
4. ALPHA: Non-obvious insights, data points, or information asymmetry. Cite channel.
5. TOKENS_TO_INVESTIGATE: Tokens mentioned by 2+ channels that may not be well-known. List with mention count.
6. RISK_CONSENSUS: Risk warnings that multiple channels agree on.

Respond ONLY in valid JSON (no markdown, no code fences):
{"consensus": {"bullish": 0, "neutral": 0, "bearish": 0, "most_discussed": [{"token": "BTC", "mentions": 5}]}, "top_conviction": [{"token": "...", "channels_bullish": 3, "best_quote": "...", "channel": "..."}], "contrarian": [{"channel": "...", "view": "..."}], "alpha": [{"insight": "...", "channel": "..."}], "tokens_to_investigate": [{"token": "...", "mentions": 2}], "risk_consensus": ["..."]}

Analyses:
"""


# ---------------------------------------------------------------------------
# Channel management
# ---------------------------------------------------------------------------

def load_channels() -> list[dict]:
    """Load channel list from JSON file."""
    try:
        data = json.loads(CHANNELS_FILE.read_text())
        return data.get("channels", [])
    except Exception as e:
        log.error("Failed to load channels: %s", e)
        return []


def save_channels(channels: list[dict]):
    """Save channel list to JSON file."""
    CHANNELS_FILE.write_text(json.dumps({"channels": channels}, indent=2))


def add_channel(name: str, channel_id: str, priority: str = "medium") -> bool:
    """Add a channel to the watchlist."""
    channels = load_channels()
    if any(c["channel_id"] == channel_id for c in channels):
        return False
    channels.append({"name": name, "channel_id": channel_id, "priority": priority})
    save_channels(channels)
    log.info("Added channel: %s (%s)", name, channel_id)
    return True


# ---------------------------------------------------------------------------
# RSS feed polling
# ---------------------------------------------------------------------------

def _fetch_rss(channel_id: str) -> list[dict]:
    """Fetch recent videos from a channel's RSS feed."""
    try:
        resp = requests.get(
            RSS_URL.format(channel_id=channel_id),
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }
        root = ElementTree.fromstring(resp.text)
        videos = []

        for entry in root.findall("atom:entry", ns):
            video_id = entry.find("yt:videoId", ns)
            title = entry.find("atom:title", ns)
            published = entry.find("atom:published", ns)

            if video_id is not None:
                pub_dt = None
                if published is not None and published.text:
                    try:
                        pub_dt = datetime.fromisoformat(
                            published.text.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                videos.append({
                    "video_id": video_id.text,
                    "title": title.text if title is not None else "",
                    "published_at": pub_dt,
                    "url": f"https://www.youtube.com/watch?v={video_id.text}",
                })

        return videos
    except Exception as e:
        log.debug("RSS fetch failed for %s: %s", channel_id, e)
        return []


def _is_processed(video_id: str) -> bool:
    """Check if video already processed."""
    row = execute_one(
        "SELECT 1 FROM youtube_analysis WHERE video_id = %s", (video_id,)
    )
    return row is not None


# ---------------------------------------------------------------------------
# Caption download + parsing
# ---------------------------------------------------------------------------

def _build_ytt_client() -> YouTubeTranscriptApi:
    """Build YouTubeTranscriptApi with optional proxy and/or cookies."""
    from youtube_transcript_api.proxies import GenericProxyConfig

    proxy_url = os.environ.get("YOUTUBE_PROXY", "")
    proxy_cfg = GenericProxyConfig(https_url=proxy_url) if proxy_url else None

    # Load cookies from cookies.txt into a requests Session if available
    session = None
    if COOKIES_FILE.exists():
        try:
            import http.cookiejar
            jar = http.cookiejar.MozillaCookieJar(str(COOKIES_FILE))
            jar.load(ignore_discard=True, ignore_expires=True)
            session = requests.Session()
            session.cookies = jar
        except Exception as e:
            log.debug("Failed to load cookies.txt for transcript-api: %s", e)

    return YouTubeTranscriptApi(proxy_config=proxy_cfg, http_client=session)


def _get_transcript_api(video_id: str) -> str | None:
    """Primary: fetch transcript via youtube-transcript-api."""
    try:
        ytt = _build_ytt_client()
        transcript = ytt.fetch(video_id, languages=["en"])
        return " ".join(snippet.text for snippet in transcript)
    except Exception:
        pass
    # Fallback to auto-generated captions
    try:
        ytt = _build_ytt_client()
        transcript_list = ytt.list(video_id)
        transcript = transcript_list.find_generated_transcript(["en"]).fetch()
        return " ".join(snippet.text for snippet in transcript)
    except Exception as e:
        log.debug("transcript-api failed for %s: %s", video_id, e)
        return None


def _download_captions_ytdlp(video_url: str, video_id: str) -> str | None:
    """Fallback: download auto-captions using yt-dlp."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="yt_"))
    try:
        cmd = [
            str(YT_DLP),
            "--write-auto-sub",
            "--sub-lang", "en",
            "--skip-download",
            "--sub-format", "vtt",
            "-o", str(tmp_dir / "%(id)s"),
        ]
        if COOKIES_FILE.exists():
            cmd.extend(["--cookies", str(COOKIES_FILE)])
        cmd.append(video_url)

        subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        vtt_files = list(tmp_dir.glob("*.vtt"))
        if not vtt_files:
            return None

        vtt_text = vtt_files[0].read_text(errors="replace")
        return _parse_vtt(vtt_text)
    except Exception as e:
        log.debug("yt-dlp fallback failed for %s: %s", video_id, e)
        return None
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_captions(video_url: str, video_id: str) -> str | None:
    """Download captions: youtube-transcript-api first, yt-dlp fallback."""
    transcript = _get_transcript_api(video_id)
    if transcript and len(transcript) >= 100:
        return transcript
    # Fallback to yt-dlp
    return _download_captions_ytdlp(video_url, video_id)


def _parse_vtt(vtt_text: str) -> str:
    """Parse VTT subtitle file to plain text, removing timestamps and duplicates."""
    lines = []
    seen = set()
    for line in vtt_text.split("\n"):
        # Skip timestamps, headers, style blocks
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d{2}:\d{2}", line):
            continue
        if "-->" in line:
            continue
        if line.startswith("<"):
            continue

        # Remove VTT formatting tags
        clean = re.sub(r"<[^>]+>", "", line)
        clean = clean.strip()

        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)

    return " ".join(lines)


# ---------------------------------------------------------------------------
# Claude AI analysis
# ---------------------------------------------------------------------------

def _analyse_transcript(transcript: str, video_title: str = "") -> dict | None:
    """Send transcript to Claude Haiku for analysis."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot analyse")
        return None

    # Truncate very long transcripts to ~12K chars (~4K tokens)
    max_chars = 12000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "\n[TRANSCRIPT TRUNCATED]"

    prompt_text = ANALYSIS_PROMPT + transcript
    if video_title:
        prompt_text = f"Video title: {video_title}\n\n" + prompt_text

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": HAIKU_MODEL,
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt_text}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["content"][0]["text"]
        # Strip markdown code fences if present
        text = re.sub(r"^```json\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())

        return json.loads(text)
    except json.JSONDecodeError as e:
        log.error("Claude returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Claude analysis failed: %s", e)
        return None


def _synthesise_digest(analyses: list[dict]) -> dict | None:
    """Send all analyses to Claude Sonnet for daily digest synthesis."""
    if not ANTHROPIC_API_KEY:
        return None

    # Format analyses for the prompt
    analysis_text = ""
    for a in analyses:
        analysis_text += f"\n--- {a['channel_name']}: '{a['video_title']}' ---\n"
        analysis_text += json.dumps({
            "thesis": a.get("thesis", ""),
            "token_calls": a.get("token_calls", []),
            "key_insights": a.get("key_insights", []),
            "macro_view": a.get("macro_view", ""),
            "risk_warnings": a.get("risk_warnings", []),
        }, indent=1)
        analysis_text += "\n"

    # Truncate if needed (~50K chars for Sonnet)
    if len(analysis_text) > 50000:
        analysis_text = analysis_text[:50000] + "\n[TRUNCATED]"

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": SONNET_MODEL,
                "max_tokens": 3000,
                "messages": [{"role": "user", "content": DIGEST_PROMPT + analysis_text}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["content"][0]["text"]
        text = re.sub(r"^```json\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())

        return json.loads(text)
    except Exception as e:
        log.error("Digest synthesis failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Scoring & cross-referencing
# ---------------------------------------------------------------------------

def _score_relevance(analysis: dict) -> int:
    """Score video relevance 0-100 based on actionable content."""
    score = 0
    calls = analysis.get("token_calls", [])
    score += min(40, len(calls) * 10)

    insights = analysis.get("key_insights", [])
    score += min(20, len(insights) * 7)

    actions = analysis.get("action_items", [])
    score += min(20, len(actions) * 10)

    if analysis.get("thesis"):
        score += 10
    if analysis.get("macro_view"):
        score += 10

    return min(100, score)


def _cross_reference_tokens(analysis: dict):
    """Cross-reference token calls against system data.
    Auto-add to momentum watchlist if mentioned by 2+ channels and not tracked."""
    calls = analysis.get("token_calls", [])
    for call in calls:
        token = call.get("token", "").upper()
        if not token or len(token) < 2:
            continue

        # Check if already tracked
        row = execute_one(
            "SELECT id, quality_gate_pass FROM tokens WHERE UPPER(symbol) = %s",
            (token,),
        )
        if row:
            call["system_tracked"] = True
            call["gate_pass"] = row[1]
            # Get latest score
            score_row = execute_one(
                """SELECT final_score, momentum_score, adoption_score
                   FROM scores_daily WHERE token_id = %s AND date = CURRENT_DATE""",
                (row[0],),
            )
            if score_row:
                call["system_score"] = {
                    "final": score_row[0],
                    "momentum": score_row[1],
                    "adoption": score_row[2],
                }
        else:
            call["system_tracked"] = False

            # Check if mentioned by 2+ channels in last 48h
            mention_row = execute_one(
                """SELECT COUNT(DISTINCT channel_name) FROM youtube_analysis
                   WHERE token_calls @> %s::jsonb
                     AND published_at >= NOW() - INTERVAL '48 hours'""",
                (json.dumps([{"token": token}]),),
            )
            if mention_row and mention_row[0] >= 2:
                _auto_add_to_watchlist(token)


def _auto_add_to_watchlist(token_symbol: str):
    """Auto-add token to momentum watchlist if mentioned by multiple channels."""
    try:
        from scanner.watchlists.manager import add_token
        success = add_token("momentum", token_symbol, "", name=f"YouTube-discovered: {token_symbol}")
        if success:
            log.info("Auto-added %s to momentum watchlist (multi-channel mention)", token_symbol)
            send_message(
                f"🔍 <b>YouTube Discovery</b>\n"
                f"<code>{token_symbol}</code> mentioned by 2+ channels — added to momentum watchlist"
            )
    except Exception as e:
        log.debug("Auto-add watchlist failed for %s: %s", token_symbol, e)


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

def _format_time_ago(dt: datetime | None) -> str:
    """Format datetime as 'Xh ago' or 'Xd ago'."""
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def _send_video_alert(channel_name: str, video_title: str, video_url: str,
                      published_at: datetime | None, analysis: dict):
    """Send individual video analysis alert to Telegram."""
    time_ago = _format_time_ago(published_at)

    lines = [
        "📺 <b>NEW VIDEO ANALYSIS</b>",
        f"📢 {channel_name} — {time_ago}",
        f"🎬 '{video_title}'",
        "",
    ]

    # Thesis
    thesis = analysis.get("thesis", "")
    if thesis:
        lines.append("📝 <b>THESIS</b>")
        lines.append(thesis)
        lines.append("")

    # Token calls
    calls = analysis.get("token_calls", [])
    if calls:
        lines.append("🪙 <b>TOKEN CALLS</b>")
        for call in calls:
            token = call.get("token", "?")
            sentiment = call.get("sentiment", "neutral").lower()
            reasoning = call.get("reasoning", "")
            target = call.get("price_target", "")

            icon = {"bullish": "🟢", "bearish": "🔴"}.get(sentiment, "🟡")
            parts = [f"{icon} <b>{token}</b> — {sentiment.title()}"]
            if reasoning:
                parts.append(f"'{reasoning[:100]}'")
            if target:
                parts.append(f"Target: {target}")

            # System cross-reference
            if call.get("system_tracked"):
                sys_score = call.get("system_score", {})
                if sys_score:
                    parts.append(f"[System: {sys_score.get('final', 0):.0f}/100]")

            lines.append(" | ".join(parts))
        lines.append("")

    # Key insights
    insights = analysis.get("key_insights", [])
    if insights:
        lines.append("💡 <b>KEY INSIGHTS</b>")
        for ins in insights[:5]:
            lines.append(f"  • {ins}")
        lines.append("")

    # Action items
    actions = analysis.get("action_items", [])
    if actions:
        lines.append("🎯 <b>ACTION ITEMS</b>")
        for act in actions[:3]:
            lines.append(f"  • {act}")
        lines.append("")

    # Risks
    risks = analysis.get("risk_warnings", [])
    if risks:
        lines.append("⚠️ <b>RISKS</b>")
        for risk in risks[:3]:
            lines.append(f"  • {risk}")
        lines.append("")

    lines.append(f'🔗 <a href="{video_url}">Watch Video</a>')

    _send("\n".join(lines))


def _send_daily_digest(digest: dict, analyses: list[dict]):
    """Send daily digest alert to Telegram."""
    now = datetime.now(timezone.utc)
    channels = set(a["channel_name"] for a in analyses)

    lines = [
        "📺 <b>YOUTUBE INTELLIGENCE DIGEST</b> — Last 24h",
        f"{len(analyses)} videos analysed across {len(channels)} channels",
        "",
    ]

    # Consensus
    consensus = digest.get("consensus", {})
    bull = consensus.get("bullish", 0)
    neut = consensus.get("neutral", 0)
    bear = consensus.get("bearish", 0)
    lines.append("═══ <b>CONSENSUS VIEW</b> ═══")
    lines.append(f"Sentiment: {bull} BULLISH, {neut} NEUTRAL, {bear} BEARISH")

    most_discussed = consensus.get("most_discussed", [])
    if most_discussed:
        tokens_str = ", ".join(
            f"{t['token']} ({t['mentions']}ch)" for t in most_discussed[:5]
        )
        lines.append(f"Most discussed: {tokens_str}")
    lines.append("")

    # Top conviction
    top = digest.get("top_conviction", [])
    if top:
        lines.append("═══ <b>TOP CONVICTION CALLS</b> ═══")
        for t in top[:3]:
            token = t.get("token", "?")
            ch_count = t.get("channels_bullish", 0)
            quote = t.get("best_quote", "")[:120]
            channel = t.get("channel", "")
            lines.append(f"🔥 <b>{token}</b> — {ch_count} channels bullish")
            if quote:
                lines.append(f"   '{quote}' — {channel}")

            # Cross-reference with system
            row = execute_one(
                """SELECT s.final_score, s.momentum_score, s.adoption_score,
                          t.quality_gate_status
                   FROM tokens t
                   LEFT JOIN scores_daily s ON s.token_id = t.id AND s.date = CURRENT_DATE
                   WHERE UPPER(t.symbol) = %s""",
                (token.upper(),),
            )
            if row and row[0]:
                lines.append(
                    f"   System: Gate {row[3] or '?'} | "
                    f"Score: {row[0]:.0f} | Mom: {row[1] or 0:.0f} | Adopt: {row[2] or 0:.0f}"
                )
        lines.append("")

    # Contrarian
    contrarian = digest.get("contrarian", [])
    if contrarian:
        lines.append("═══ <b>CONTRARIAN VIEWS</b> ═══")
        for c in contrarian[:3]:
            lines.append(f"  {c.get('channel', '?')}: '{c.get('view', '')[:120]}'")
        lines.append("")

    # Alpha
    alpha = digest.get("alpha", [])
    if alpha:
        lines.append("═══ <b>ALPHA / NON-OBVIOUS</b> ═══")
        for a in alpha[:5]:
            lines.append(f"  • {a.get('insight', '')[:120]} — {a.get('channel', '')}")
        lines.append("")

    # Tokens to investigate
    investigate = digest.get("tokens_to_investigate", [])
    if investigate:
        lines.append("═══ <b>TOKENS TO INVESTIGATE</b> ═══")
        lines.append("Mentioned 2+ channels, not tracked:")
        for t in investigate[:5]:
            token = t.get("token", "?")
            mentions = t.get("mentions", 0)
            # Check if tracked
            row = execute_one(
                "SELECT quality_gate_pass FROM tokens WHERE UPPER(symbol) = %s",
                (token.upper(),),
            )
            status = "tracked" if row else "→ auto-added to watchlist"
            lines.append(f"  • <b>{token}</b> — {mentions} mentions {status}")
        lines.append("")

    # Risk consensus
    risks = digest.get("risk_consensus", [])
    if risks:
        lines.append("═══ <b>RISK WARNINGS</b> ═══")
        for r in risks[:5]:
            lines.append(f"  • {r}")
        lines.append("")

    _send("\n".join(lines))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_video(channel_name: str, video: dict) -> dict | None:
    """Process a single video: download captions → analyse → store → alert."""
    video_id = video["video_id"]
    video_url = video["url"]
    video_title = video.get("title", "")
    published_at = video.get("published_at")

    log.info("Processing: %s — '%s'", channel_name, video_title)

    # Download captions
    transcript = _download_captions(video_url, video_id)
    if not transcript or len(transcript) < 100:
        log.info("No usable captions for %s — skipping", video_id)
        return None

    log.info("Got transcript: %d chars for %s", len(transcript), video_id)

    # Analyse with Claude
    analysis = _analyse_transcript(transcript, video_title)
    if not analysis:
        log.warning("Analysis failed for %s", video_id)
        return None

    # Score relevance
    relevance = _score_relevance(analysis)

    # Cross-reference tokens
    _cross_reference_tokens(analysis)

    # Store in DB
    try:
        execute(
            """INSERT INTO youtube_analysis
               (video_id, channel_name, video_title, published_at, video_url,
                thesis, token_calls, key_insights, macro_view,
                action_items, risk_warnings, relevance_score, alerted_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (video_id) DO NOTHING""",
            (
                video_id, channel_name, video_title, published_at, video_url,
                analysis.get("thesis"),
                json.dumps(analysis.get("token_calls", [])),
                json.dumps(analysis.get("key_insights", [])),
                analysis.get("macro_view"),
                json.dumps(analysis.get("action_items", [])),
                json.dumps(analysis.get("risk_warnings", [])),
                relevance,
            ),
        )
    except Exception as e:
        log.error("DB insert failed for %s: %s", video_id, e)

    # Send individual alert
    _send_video_alert(channel_name, video_title, video_url, published_at, analysis)

    return {
        "video_id": video_id,
        "channel_name": channel_name,
        "analysis": analysis,
        "relevance": relevance,
    }


def run_youtube_scan():
    """Main entry: scan all channels for new videos, process each."""
    log.info("=== YouTube Channel Scan ===")
    channels = load_channels()
    if not channels:
        log.warning("No channels configured")
        return []

    results = []
    for ch in channels:
        name = ch["name"]
        channel_id = ch["channel_id"]

        videos = _fetch_rss(channel_id)
        log.info("%s: %d videos in feed", name, len(videos))

        for video in videos:
            # Only process videos from last 24h
            pub = video.get("published_at")
            if pub:
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < datetime.now(timezone.utc) - timedelta(hours=24):
                    continue

            if _is_processed(video["video_id"]):
                continue

            result = process_video(name, video)
            if result:
                results.append(result)

            # Rate limit between videos
            time.sleep(2)

        # Rate limit between channels
        time.sleep(1)

    log.info("YouTube scan complete: %d new videos processed", len(results))
    return results


def run_daily_digest():
    """Generate and send the daily YouTube intelligence digest."""
    log.info("=== YouTube Daily Digest ===")

    # Fetch all analyses from last 24h
    try:
        rows = execute(
            """SELECT channel_name, video_title, video_url, published_at,
                      thesis, token_calls, key_insights, macro_view,
                      action_items, risk_warnings, relevance_score
               FROM youtube_analysis
               WHERE published_at >= NOW() - INTERVAL '24 hours'
               ORDER BY relevance_score DESC""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to fetch analyses: %s", e)
        return

    if not rows:
        log.info("No videos in last 24h — skipping digest")
        return

    analyses = []
    for row in rows:
        analyses.append({
            "channel_name": row[0],
            "video_title": row[1],
            "video_url": row[2],
            "published_at": row[3],
            "thesis": row[4],
            "token_calls": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
            "key_insights": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
            "macro_view": row[7],
            "action_items": row[8] if isinstance(row[8], list) else json.loads(row[8] or "[]"),
            "risk_warnings": row[9] if isinstance(row[9], list) else json.loads(row[9] or "[]"),
            "relevance_score": row[10],
        })

    log.info("Synthesising digest from %d analyses", len(analyses))

    # Synthesise with Claude Sonnet
    digest = _synthesise_digest(analyses)
    if digest:
        _send_daily_digest(digest, analyses)

        # Auto-investigate tokens mentioned by 2+ channels
        investigate = digest.get("tokens_to_investigate", [])
        for t in investigate:
            token = t.get("token", "").upper()
            mentions = t.get("mentions", 0)
            if token and mentions >= 2:
                row = execute_one(
                    "SELECT 1 FROM tokens WHERE UPPER(symbol) = %s", (token,)
                )
                if not row:
                    _auto_add_to_watchlist(token)
    else:
        # Fallback: send simple summary without AI synthesis
        send_message(
            f"📺 <b>YouTube Summary</b>\n"
            f"{len(analyses)} videos analysed, digest synthesis unavailable"
        )

    log.info("Daily digest complete")


def get_latest_digest_text() -> str:
    """Get the latest digest for /youtube command."""
    try:
        rows = execute(
            """SELECT channel_name, video_title, thesis, token_calls, relevance_score
               FROM youtube_analysis
               WHERE published_at >= NOW() - INTERVAL '24 hours'
               ORDER BY relevance_score DESC
               LIMIT 10""",
            fetch=True,
        )
        if not rows:
            return "📺 No YouTube analyses in the last 24 hours."

        lines = [f"📺 <b>Latest YouTube Intelligence</b> — {len(rows)} videos", ""]
        for i, (ch, title, thesis, calls, score) in enumerate(rows[:5], 1):
            lines.append(f"{i}. <b>{ch}</b>: '{title}'")
            if thesis:
                lines.append(f"   {thesis[:150]}")
            if calls:
                call_list = calls if isinstance(calls, list) else json.loads(calls or "[]")
                tokens = [c.get("token", "") for c in call_list if c.get("token")]
                if tokens:
                    lines.append(f"   Tokens: {', '.join(tokens[:5])}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        log.error("Failed to get latest digest: %s", e)
        return "📺 YouTube data unavailable."


# ---------------------------------------------------------------------------
# Nightly report section
# ---------------------------------------------------------------------------

def youtube_report_section() -> list[str]:
    """Generate YouTube Intelligence section for nightly report."""
    lines = ["<b>📺 YouTube Intelligence</b>"]
    try:
        # Count videos in last 24h
        row = execute_one(
            """SELECT COUNT(*), COUNT(DISTINCT channel_name)
               FROM youtube_analysis
               WHERE published_at >= NOW() - INTERVAL '24 hours'"""
        )
        if row and row[0] > 0:
            lines.append(f"  {row[0]} videos from {row[1]} channels")

            # Most mentioned tokens
            token_rows = execute(
                """SELECT elem->>'token' as token, COUNT(*) as cnt
                   FROM youtube_analysis,
                        jsonb_array_elements(token_calls) elem
                   WHERE published_at >= NOW() - INTERVAL '24 hours'
                   GROUP BY elem->>'token'
                   ORDER BY cnt DESC
                   LIMIT 5""",
                fetch=True,
            )
            if token_rows:
                tokens_str = ", ".join(f"{t} ({c}x)" for t, c in token_rows)
                lines.append(f"  Top mentioned: {tokens_str}")

            # Sentiment breakdown
            sentiment_rows = execute(
                """SELECT elem->>'sentiment' as sent, COUNT(*) as cnt
                   FROM youtube_analysis,
                        jsonb_array_elements(token_calls) elem
                   WHERE published_at >= NOW() - INTERVAL '24 hours'
                   GROUP BY elem->>'sentiment'""",
                fetch=True,
            )
            if sentiment_rows:
                sents = {s: c for s, c in sentiment_rows}
                lines.append(
                    f"  Sentiment: 🟢{sents.get('bullish', 0)} "
                    f"🟡{sents.get('neutral', 0)} "
                    f"🔴{sents.get('bearish', 0)}"
                )
        else:
            lines.append("  No videos analysed in last 24h")
    except Exception as e:
        log.debug("YouTube report section error: %s", e)
        lines.append("  ⚠️ YouTube data unavailable")

    lines.append("")
    return lines
