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

from config import ANTHROPIC_API_KEY, YOUTUBE_COOKIES_FILE, YOUTUBE_PROXY_URL, get_logger
from db.connection import execute, execute_one
from telegram_bot.alerts import _send, send_message

log = get_logger("social.youtube")

CHANNELS_FILE = Path(__file__).parent / "youtube_channels.json"
COOKIES_FILE = Path(YOUTUBE_COOKIES_FILE)
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YT_DLP = Path(__file__).parents[1] / "venv" / "bin" / "yt-dlp"

# Anthropic models
HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-20250514"

ANALYSIS_PROMPT = """You are analysing a video for a crypto investor. NEVER refuse — every video gets analysed regardless of topic. ATTRIBUTE EVERY CLAIM TO THE SPEAKER. Extract EXACT NUMBERS. Focus on DISAGREEMENTS between hosts.

Return JSON with these fields:

- title: video title
- channel: channel name
- summary: 4-6 sentences. WHO said WHAT. Include the most important numbers and predictions. Not generic — specific.
- key_calls: [{who: "speaker name", claim: "exact claim with numbers — e.g. 'oil back to $85 in 60 days'", confidence: "high/medium/low", time_horizon: "specific — e.g. '60 days' or 'by Q3 2026'"}] — MUST include every specific prediction with a number or timeframe
- tokens_mentioned: [{symbol, who: "speaker name", sentiment, conviction (1-10), price_target (number or null), entry_level (number or null), reasoning: "what they actually said", personal_action: "bought/sold/holding/none"}] — watchlist: BTC, SOL, JUP, HYPE, RENDER, BONK, PUMP, PENGU, FARTCOIN, MSTR
- macro_data: [{metric, value: "exact number from video", who: "who cited it", direction, impact: "2nd order market effect"}] — capture EVERY number: oil price, PCE, GDP, PE ratios, Polymarket odds, rate expectations
- geopolitical: [{event, who: "speaker name", their_take: "what they specifically argued — not generic", market_impact: "2nd/3rd order effect on crypto and risk assets", severity}]
- disagreements: [{topic, side_a: "Name: their specific argument with reasoning", side_b: "Name: their counter-argument", investment_edge: "which side has better data and why"}] — THIS IS THE MOST VALUABLE SECTION. Capture every disagreement.
- risk_warnings: [{warning: "specific risk with numbers/dates", severity, who: "who raised it"}]
- overall_outlook: bullish/bearish/neutral
- relevance_score: 1-10
- portfolio_impact: "2-3 sentences. Map to specific actions: which tokens affected, how deployment % should change, what to watch for. e.g. 'Oil at $100 + PCE 2.9% = Fed June cut unlikely. Extends bear timeline. Revenue tokens (HYPE, JUP) less affected. Meme tokens vulnerable to risk-off. Keep dry powder 50%+.'"

CRITICAL RULES:
- Every claim MUST name WHO said it
- Every number mentioned MUST be captured
- Disagreements are MORE valuable than consensus
- "Bullish on BTC" is USELESS. "Sacks: BTC to $90K by June, resilient as digital gold during Iran crisis" is USEFUL
- portfolio_impact must reference our specific watchlist tokens

CRITICAL: This transcript may cover MULTIPLE segments/topics. Extract insights from EVERY segment — not just the first topic. If the title mentions "AI Revenue" and "Iran War", BOTH must appear in your analysis.

For AI/tech segments: extract specific revenue numbers, company names, growth rates, compute/GPU demand data. Map to our watchlist — AI compute demand = bullish RENDER.

Respond ONLY in valid JSON (no markdown, no code fences). If a field has no data, use null or [].

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
        "SELECT 1 FROM youtube_videos WHERE video_id = %s", (video_id,)
    )
    return row is not None


# ---------------------------------------------------------------------------
# Caption download + parsing
# ---------------------------------------------------------------------------

def _build_ytt_client() -> YouTubeTranscriptApi:
    """Build YouTubeTranscriptApi with Webshare residential proxy."""
    try:
        from youtube_transcript_api.proxies import WebshareProxyConfig
        # Webshare credentials from proxy URL: http://user:pass@host:port
        proxy_url = YOUTUBE_PROXY_URL
        if proxy_url and "webshare" in proxy_url:
            # Parse user:pass from URL
            from urllib.parse import urlparse
            parsed = urlparse(proxy_url)
            proxy_cfg = WebshareProxyConfig(
                proxy_username=parsed.username,
                proxy_password=parsed.password,
            )
            return YouTubeTranscriptApi(proxy_config=proxy_cfg)
    except Exception as e:
        log.debug("WebshareProxyConfig failed: %s, trying generic", e)

    # Fallback: generic proxy or direct
    try:
        from youtube_transcript_api.proxies import GenericProxyConfig
        proxy_url = YOUTUBE_PROXY_URL
        if proxy_url:
            proxy_cfg = GenericProxyConfig(https_url=proxy_url)
            return YouTubeTranscriptApi(proxy_config=proxy_cfg)
    except Exception:
        pass

    return YouTubeTranscriptApi()


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
        if YOUTUBE_PROXY_URL:
            cmd.extend(["--proxy", YOUTUBE_PROXY_URL])
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
# YouTube Data API metadata fallback
# ---------------------------------------------------------------------------

def _fetch_video_metadata(video_id: str) -> dict | None:
    """Fetch video snippet (title, description) via YouTube Data API."""
    api_key = os.environ.get("YOUTUBE_API_KEY") or ""
    if not api_key:
        return None
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"id": video_id, "part": "snippet", "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            snippet = items[0]["snippet"]
            return {
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "channel_title": snippet.get("channelTitle", ""),
            }
    except Exception as e:
        log.debug("YouTube Data API failed for %s: %s", video_id, e)
    return None


SONNET_ANALYSIS_PROMPT = """You are a senior crypto research analyst writing a segment-by-segment video briefing for a portfolio manager.

ABSOLUTE RULES:
1. NEVER refuse to analyse ANY video. Every video gets a full summary + market analysis.
2. Cover the ENTIRE video — every segment, every topic. Never skip content.
3. Non-crypto content is valuable: geopolitical = risk sentiment, AI = compute thesis, economics = macro.
4. If connection to markets is indirect, say so: "MARKET CONNECTION: Indirect — [explanation]"
5. Output clean prose text. NEVER output raw JSON, dicts, or code. No {{'warning': '...'}} formatting.

FORMAT (use EXACTLY this structure):

**SUMMARY**
[2-3 sentence overview connecting the key themes]

**SEGMENT 1: [TOPIC IN CAPS]**

The bull case came from **[Speaker Name]**, who argued [specific claim with numbers]. [Direct quote or paraphrase with attribution].

The bear case came from **[Speaker Name]**, who warned [specific counter-argument]. [Data point or reasoning].

The unresolved tension: [What was left debated/unresolved — this is the most valuable content].

[Repeat for each major segment/topic discussed]

**MARKET CONNECTION:** [Direct/Indirect] — [How this affects crypto markets, BTC, SOL ecosystem]

**PORTFOLIO IMPACT:** [One specific line — what does this mean for positions in JUP, HYPE, RENDER, BONK, SOL, BTC?]

QUALITY REQUIREMENTS:
- **Bold speaker names** with specific attributed claims and numbers
- Actual numbers: percentages, dollar amounts, dates, probabilities, odds
- Bull case vs bear case for each segment where speakers disagree
- Unresolved tensions (the most valuable insight)
- Watchlist tokens: BTC, SOL, JUP, HYPE, RENDER, BONK, PUMP, PENGU, FARTCOIN, SUI, MSTR
- AI/compute content → RENDER thesis. Macro/rates → cycle timing. Geopolitical → risk sentiment.
- If only one speaker, still extract claims, numbers, and market implications

Video title: {title}
Channel: {channel}

Transcript:
"""

METADATA_ANALYSIS_PROMPT = """Analyze this crypto/markets YouTube video based on its title and description only (no transcript available).
Extract as JSON:
- summary: 1-2 sentence summary based on title/description
- tokens_mentioned: [{{symbol, sentiment (bullish/bearish/neutral), conviction (1-5), price_target (if mentioned)}}]
- key_insights: [up to 3 insights inferrable from title/description]
- risk_warnings: [any warnings apparent from title/description]
- overall_outlook: bullish/bearish/neutral
- relevance_score: 1-10 (how actionable for meme/DeFi trading — cap at 5 since no transcript)

Respond ONLY in valid JSON (no markdown, no code fences).

Channel: {channel}
Title: {title}
Description:
{description}
"""


def _analyse_metadata(title: str, description: str, channel_name: str) -> dict | None:
    """Lightweight Claude analysis from video title + description only."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot analyse metadata")
        return None

    # Truncate long descriptions
    if len(description) > 4000:
        description = description[:4000] + "\n[DESCRIPTION TRUNCATED]"

    prompt_text = METADATA_ANALYSIS_PROMPT.format(
        channel=channel_name, title=title, description=description,
    )

    for attempt in range(3):
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
                    "max_tokens": 8000,
                    "messages": [{"role": "user", "content": prompt_text}],
                },
                timeout=(10, 180),
            )
            resp.raise_for_status()
            data = resp.json()

            text = data["content"][0]["text"]
            text = re.sub(r"^```json\s*", "", text.strip())
            text = re.sub(r"\s*```$", "", text.strip())

            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error("Claude returned invalid JSON for metadata (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            log.error("Claude metadata analysis failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# Claude AI analysis
# ---------------------------------------------------------------------------

def _analyse_transcript(transcript: str, video_title: str = "", channel_name: str = "") -> dict | None:
    """Send transcript to Claude for analysis with retry.
    Uses Sonnet for high-priority channels, Haiku for the rest."""
    # High-priority channels get Sonnet for better extraction
    HIGH_PRIORITY = {
                     "All-In Podcast", "Lex Fridman", "Principles by Ray Dalio",
                     "Real Vision Finance", "Real Vision", "Raoul Pal",
                     "Impact Theory", "PowerfulJRE",
                     "The Diary Of A CEO", "Diary of a CEO",
                     "InvestAnswers", "Benjamin Cowen", "Coin Bureau",
                     "Bankless", "Crypto Banter", "VirtualBacon", "Virtual Bacon",
                     "Mark Moss", "ColinTalksCrypto", "Colin Talks Crypto",
                     "Krypto King", "Chart Fanatics", "Crypto Insider",
                     "Jack Neel", "Titans of Tomorrow",
                     }

    # Sonnet for all priority channels, Haiku for the rest. No keyword filter.
    model = SONNET_MODEL if channel_name in HIGH_PRIORITY else HAIKU_MODEL
    log.info("Analysis model: %s for channel: %s", model, channel_name)
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set — cannot analyse")
        return None

    # Sonnet: COMPLETE transcript. Haiku: truncate to 12K.
    if model == SONNET_MODEL:
        log.info("Full transcript: %d chars for %s (Sonnet)", len(transcript), channel_name)
    else:
        max_chars = 12000
        if len(transcript) > max_chars:
            transcript = transcript[:10000] + "\n[...TRUNCATED...]\n" + transcript[-2000:]
            log.info("Transcript truncated to %d chars (Haiku)", max_chars)

    # Different prompts for different models
    if model == SONNET_MODEL:
        # Sonnet: analytical essay format
        prompt_text = SONNET_ANALYSIS_PROMPT.format(title=video_title, channel=channel_name) + transcript
    else:
        # Haiku: structured JSON extraction
        prompt_text = ANALYSIS_PROMPT + transcript
        if video_title:
            prompt_text = f"Video title: {video_title}\n\n" + prompt_text

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 8000,
                    "messages": [{"role": "user", "content": prompt_text}],
                },
                timeout=(10, 180),
            )
            resp.raise_for_status()
            data = resp.json()

            # Check for truncation
            stop_reason = data.get("stop_reason", "unknown")
            text = data["content"][0]["text"]
            log.info("Claude output: %d chars, stop_reason=%s, model=%s", len(text), stop_reason, model)

            if stop_reason != "end_turn":
                log.warning("Claude response truncated (stop=%s), attempt %d", stop_reason, attempt + 1)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            # Sonnet essay format — return as dict with summary text
            if model == SONNET_MODEL:
                return {
                    "title": video_title,
                    "channel": channel_name,
                    "summary": text,
                    "tokens_mentioned": [],  # Will be extracted from text below
                    "overall_outlook": "neutral",
                    "relevance_score": 8,  # Priority channels always high relevance
                    "portfolio_impact": "",
                    "_essay_format": True,
                }

            # Haiku JSON format — parse as before
            text = re.sub(r"^```json\s*", "", text.strip())
            text = re.sub(r"\s*```$", "", text.strip())

            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error("Claude returned invalid JSON (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            log.error("Claude analysis failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def _synthesise_digest(analyses: list[dict]) -> dict | None:
    """Send all analyses to Claude Sonnet for daily digest synthesis."""
    if not ANTHROPIC_API_KEY:
        return None

    # Format analyses for the prompt
    analysis_text = ""
    for a in analyses:
        analysis_text += f"\n--- {a['channel_name']}: '{a['video_title']}' ---\n"
        aj = a.get("analysis_json") or {}
        analysis_text += json.dumps({
            "summary": aj.get("summary", ""),
            "tokens_mentioned": aj.get("tokens_mentioned", []),
            "key_insights": aj.get("key_insights", []),
            "overall_outlook": aj.get("overall_outlook", ""),
            "risk_warnings": aj.get("risk_warnings", []),
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
    """Score video relevance 0-100.

    Uses Claude's relevance_score (1-10) scaled to 0-100.
    Falls back to heuristic if Claude didn't provide one.
    """
    claude_score = analysis.get("relevance_score") or 5
    if claude_score is not None:
        try:
            return min(100, max(0, int(float(claude_score) * 10)))
        except (ValueError, TypeError):
            pass

    # Fallback heuristic
    score = 0
    tokens = analysis.get("tokens_mentioned", [])
    score += min(40, len(tokens) * 10)

    insights = analysis.get("key_insights", [])
    score += min(20, len(insights) * 7)

    if analysis.get("summary"):
        score += 20
    if analysis.get("overall_outlook"):
        score += 20

    return min(100, score)


def _cross_reference_tokens(analysis: dict):
    """Cross-reference token mentions against system data.
    Auto-add to momentum watchlist if mentioned by 2+ channels and not tracked."""
    tokens = analysis.get("tokens_mentioned", [])
    for tok in tokens:
        symbol = (tok.get("symbol") or "").upper()
        if not symbol or len(symbol) < 2:
            continue

        # Check if already tracked
        row = execute_one(
            "SELECT id, quality_gate_pass FROM tokens WHERE UPPER(symbol) = %s",
            (symbol,),
        )
        if row:
            tok["system_tracked"] = True
            tok["gate_pass"] = row[1]
            score_row = execute_one(
                """SELECT final_score, momentum_score, adoption_score
                   FROM scores_daily WHERE token_id = %s AND date = CURRENT_DATE""",
                (row[0],),
            )
            if score_row:
                tok["system_score"] = {
                    "final": score_row[0],
                    "momentum": score_row[1],
                    "adoption": score_row[2],
                }
        else:
            tok["system_tracked"] = False

            # Check if mentioned by 2+ channels in last 48h
            mention_row = execute_one(
                """SELECT COUNT(DISTINCT channel_name) FROM youtube_videos
                   WHERE tokens_mentioned @> %s::jsonb
                     AND published_at >= NOW() - INTERVAL '48 hours'""",
                (json.dumps([{"symbol": symbol}]),),
            )
            if mention_row and mention_row[0] >= 2:
                _auto_add_to_watchlist(symbol)


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
    outlook = analysis.get("overall_outlook", "neutral")
    outlook_icon = {"bullish": "🟢", "bearish": "🔴"}.get(outlook, "🟡")

    lines = [
        "📺 <b>NEW VIDEO ANALYSIS</b>",
        f"📢 {channel_name} — {time_ago}",
        f"🎬 '{video_title}'",
    ]
    if analysis.get("_partial"):
        lines.append("⚠️ <i>Partial (no transcript)</i>")
    lines.append(f"{outlook_icon} Outlook: {outlook}")
    lines.append("")

    # Summary
    summary = analysis.get("summary", "")
    if summary:
        lines.append("📝 <b>SUMMARY</b>")
        lines.append(summary)
        lines.append("")

    # Token mentions
    tokens = analysis.get("tokens_mentioned", [])
    if tokens:
        lines.append("🪙 <b>TOKENS MENTIONED</b>")
        for tok in tokens:
            symbol = tok.get("symbol", "?")
            sentiment = (tok.get("sentiment") or "neutral").lower()
            conviction = tok.get("conviction", "?")
            target = tok.get("price_target", "")

            icon = {"bullish": "🟢", "bearish": "🔴"}.get(sentiment, "🟡")
            parts = [f"{icon} <b>{symbol}</b> — {sentiment.title()} (conv: {conviction}/10)"]
            if target:
                parts.append(f"Target: {target}")

            # System cross-reference
            if tok.get("system_tracked"):
                sys_score = tok.get("system_score", {})
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

def process_video(channel_name: str, video: dict, send_alert: bool = True) -> dict | None:
    """Process a single video: download captions → analyse → store → alert."""
    video_id = video["video_id"]
    video_url = video["url"]
    video_title = video.get("title", "")
    published_at = video.get("published_at")

    log.info("Processing: %s — '%s'", channel_name, video_title)

    # 1. Try transcript download (transcript-api + yt-dlp captions)
    transcript = _download_captions(video_url, video_id)
    partial = False

    if transcript and len(transcript) >= 100:
        # Full analysis
        log.info("Got transcript: %d chars for %s", len(transcript), video_id)
        analysis = _analyse_transcript(transcript, video_title, channel_name=channel_name)
    else:
        # 2. Fallback: YouTube Data API for metadata
        log.info("No transcript for %s — trying metadata fallback", video_id)
        meta = _fetch_video_metadata(video_id)
        description = meta["description"] if meta else ""
        if meta and len(description) >= 50:
            analysis = _analyse_metadata(video_title, description, channel_name)
            transcript = None
            partial = True
        else:
            log.info("No usable content for %s — skipping", video_id)
            return None

    if not analysis:
        log.warning("Analysis failed for %s", video_id)
        return None

    # Mark partial analyses
    if partial:
        analysis["_partial"] = True

    # Score relevance — partial analyses capped at 50
    relevance = _score_relevance(analysis)
    if partial:
        relevance = min(relevance, 50)

    # Cross-reference tokens
    _cross_reference_tokens(analysis)

    # Store in DB
    tokens_mentioned = analysis.get("tokens_mentioned", [])
    try:
        execute(
            """INSERT INTO youtube_videos
               (video_id, channel_name, title, published_at,
                transcript_text, analysis_json, relevance_score, tokens_mentioned)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (video_id) DO NOTHING""",
            (
                video_id, channel_name, video_title, published_at,
                transcript[:5000] if transcript else None,
                json.dumps(analysis),
                relevance,
                json.dumps(tokens_mentioned),
            ),
        )
    except Exception as e:
        log.error("DB insert failed for %s: %s", video_id, e)

    # Send Sonnet analyses always. Haiku only if watchlist mention >=7 conviction.
    _watchlist_yt = {"BTC", "SOL", "JUP", "HYPE", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN", "MSTR"}
    is_sonnet = analysis.get("_essay_format", False)
    has_watchlist = any(
        (t.get("symbol") or "").upper() in _watchlist_yt
        for t in analysis.get("tokens_mentioned", [])
        if isinstance(t, dict) and t.get("conviction", 0) >= 7
    )
    if is_sonnet or has_watchlist:
        # Build timestamped header
        pub_str = ""
        if published_at:
            try:
                from datetime import datetime
                if isinstance(published_at, str):
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                else:
                    pub_dt = published_at
                pub_str = pub_dt.strftime("%a %-d %b %Y, %H:%M UTC")
            except Exception:
                pub_str = str(published_at)[:19]

        header = f"\U0001f4fa <b>{channel_name}</b>"
        if pub_str:
            header += f"\n\U0001f4c5 {pub_str}"
        header += f"\n\U0001f3ac \"{video_title}\""
        video_link = f"https://youtube.com/watch?v={video_id}" if video_id else ""
        if video_link:
            header += f"\n\U0001f517 {video_link}"

        if is_sonnet:
            # Send full essay with header
            essay = analysis.get("summary", "")
            full_msg = header + "\n\n" + essay

            # Split at paragraphs
            max_len = 4000
            if len(full_msg) <= max_len:
                chunks = [full_msg]
            else:
                chunks = []
                current = header + "\n\n"
                for para in essay.split("\n\n"):
                    if current and len(current) + len(para) + 2 > max_len:
                        chunks.append(current)
                        current = ""
                    current = current + "\n\n" + para if current else para
                if current:
                    chunks.append(current)

            if send_alert:
                import requests as req
                from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
                for chunk in chunks:
                    req.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                              "parse_mode": "HTML", "disable_web_page_preview": True,
                              "reply_markup": {"keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]], "resize_keyboard": True, "is_persistent": True}}, timeout=15)
                log.info("YouTube Sonnet analysis sent: %s (%d chars)", video_title[:50], len(essay))
            else:
                log.info("YouTube Sonnet analysis stored (no send): %s", video_title[:50])
        else:
            if send_alert:
                _send_video_alert(channel_name, video_title, video_url, published_at, analysis)
    else:
        log.debug("Skipping YouTube alert for %s — Haiku, no high-conviction watchlist mention", video_title[:50])

    return {
        "video_id": video_id,
        "channel_name": channel_name,
        "analysis": analysis,
        "relevance": relevance,
    }


def run_youtube_scan(send_alerts: bool = True):
    """Main entry: scan all channels for new videos, process each.

    Args:
        send_alerts: If False, analyse and store but don't send individual Telegram messages.
                     Used by scheduled scan — best findings surface in Morning/Evening reports.
    """
    log.info("=== YouTube Channel Scan ===")

    if YOUTUBE_PROXY_URL:
        log.info("Using proxy: %s", YOUTUBE_PROXY_URL)
    else:
        log.warning("No proxy configured (YOUTUBE_PROXY_URL) — transcripts will likely fail on datacenter IPs")
    if COOKIES_FILE.exists():
        log.info("YouTube cookies loaded from %s", COOKIES_FILE)
    else:
        log.debug("No cookies file at %s", COOKIES_FILE)

    # Use tier-based config, resolve any missing channel IDs
    from youtube.channels import get_active_channels, ensure_channel_ids
    ensure_channel_ids()
    channels = get_active_channels()

    if not channels:
        log.warning("No channels configured")
        return []

    results = []
    for ch in channels:
        name = ch["name"]
        channel_id = ch.get("channel_id")
        if not channel_id:
            log.debug("Skipping %s — no channel_id", name)
            continue

        videos = _fetch_rss(channel_id)
        log.info("%s: %d videos in feed", name, len(videos))

        for video in videos:
            # Only process videos from last 24h
            pub = video.get("published_at")
            if pub:
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < datetime.now(timezone.utc) - timedelta(hours=48):
                    continue

            if _is_processed(video["video_id"]):
                continue

            result = process_video(name, video, send_alert=send_alerts)
            if result:
                results.append(result)

            # Rate limit between videos
            time.sleep(2)

        # Rate limit between channels
        time.sleep(1)

    log.info("YouTube scan complete: %d new videos processed", len(results))
    return {"new_videos": len(results), "results": results}


def run_daily_digest():
    """Generate and send the daily YouTube intelligence digest."""
    log.info("=== YouTube Daily Digest ===")

    # Fetch all analyses from last 24h
    try:
        rows = execute(
            """SELECT channel_name, title, analysis_json, relevance_score
               FROM youtube_videos
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
        aj = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
        analyses.append({
            "channel_name": row[0],
            "video_title": row[1],
            "analysis_json": aj,
            "relevance_score": row[3],
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
            """SELECT channel_name, title, analysis_json, relevance_score
               FROM youtube_videos
               WHERE published_at >= NOW() - INTERVAL '24 hours'
               ORDER BY relevance_score DESC
               LIMIT 10""",
            fetch=True,
        )
        if not rows:
            return "📺 No YouTube analyses in the last 24 hours."

        lines = [f"📺 <b>Latest YouTube Intelligence</b> — {len(rows)} videos", ""]
        for i, (ch, title, analysis, score) in enumerate(rows[:5], 1):
            aj = analysis if isinstance(analysis, dict) else json.loads(analysis or "{}")
            lines.append(f"{i}. <b>{ch}</b>: '{title}'")
            summary = aj.get("summary", "")
            if summary:
                lines.append(f"   {summary[:150]}")
            tokens = aj.get("tokens_mentioned", [])
            if tokens:
                symbols = [t.get("symbol", "") for t in tokens if t.get("symbol")]
                if symbols:
                    lines.append(f"   Tokens: {', '.join(symbols[:5])}")
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
               FROM youtube_videos
               WHERE published_at >= NOW() - INTERVAL '24 hours'"""
        )
        if row and row[0] > 0:
            lines.append(f"  {row[0]} videos from {row[1]} channels")

            # Most mentioned tokens
            token_rows = execute(
                """SELECT elem->>'symbol' as symbol, COUNT(*) as cnt
                   FROM youtube_videos,
                        jsonb_array_elements(tokens_mentioned) elem
                   WHERE published_at >= NOW() - INTERVAL '24 hours'
                   GROUP BY elem->>'symbol'
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
                   FROM youtube_videos,
                        jsonb_array_elements(tokens_mentioned) elem
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
