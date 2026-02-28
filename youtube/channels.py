"""YouTube channel tier configuration.

Tier 1: Core crypto/markets channels — always monitored.
Tier 2: Broader finance/macro — monitored but lower priority.
Tier 3: Excluded — too general or irrelevant.
"""

import json
import re
from pathlib import Path

import requests

from config import get_logger

log = get_logger("youtube.channels")

CACHE_FILE = Path(__file__).parent / "channel_cache.json"

TIER_1 = [
    {"name": "InvestAnswers", "handle": "@investaborad", "channel_id": "UCH6KS5IiLfTyunVHPCDYT8Q"},
    {"name": "Coin Bureau", "handle": "@CoinBureau", "channel_id": "UCTULWwXF7dj19_ovr7rbgog"},
    {"name": "Benjamin Cowen", "handle": "@intocryptoverse", "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg"},
    {"name": "Coin Bureau Clips", "handle": "@CoinBureauClips"},
    {"name": "DataDash", "handle": "@DataDash", "channel_id": "UCCatR7nWbYrkVXdxXb4cGXw"},
    {"name": "Lark Davis", "handle": "@TheCryptoLark", "channel_id": "UCl2oCaw8hdR_kbqyqd2klIA"},
    {"name": "Altcoin Daily", "handle": "@AltcoinDaily", "channel_id": "UCbLhGKVY-bJPcuxxvN2WzKA"},
    {"name": "Anthony Pompliano", "handle": "@AnthonyPompliano", "channel_id": "UCnzCGCGlq0eJ_NkhGNOt2OQ"},
    {"name": "Raoul Pal", "handle": "@RaoulPal", "channel_id": "UCR8PGPFMso2bftEt3JaIMCQ"},
    {"name": "Miles Deutscher", "handle": "@MilesDeutscher", "channel_id": "UCYkE4VjBB3RMvUxuDhDLxTw"},
    {"name": "SolanaFloor", "handle": "@SolanaFloor"},
    {"name": "HovWaves", "handle": "@HovWaves"},
    {"name": "SavageCharts", "handle": "@SavageCharts"},
    {"name": "Ivan on Tech", "handle": "@IvanonTech", "channel_id": "UCrYmtJBtLdtm2ov84ulV-yg"},
    {"name": "Crypto Banter", "handle": "@CryptoBanterGroup", "channel_id": "UCN9Nj4tjXbVTLYWN0EKly_Q"},
]

TIER_2 = [
    {"name": "Real Vision", "handle": "@RealVisionPresents", "channel_id": "UCBH5VZE_Y4F3CMcPIzPEB5A"},
    {"name": "Codie Sanchez", "handle": "@CodieSanchez"},
    {"name": "Alex Hormozi", "handle": "@AlexHormozi"},
    {"name": "Diary of a CEO", "handle": "@TheDiaryOfACEO"},
    {"name": "Daily Stoic", "handle": "@DailyStoic"},
    {"name": "Patrick Boyle", "handle": "@PBoyle", "channel_id": "UCASM3CGMneOMHBaEbqdBH0g"},
]

TIER_3_EXCLUDED = [
    "Tom Bilyeu", "Lex Fridman", "Huberman", "Joe Rogan",
    "Brian Jung", "Johnny Harris", "Dr Berg",
    "DLM Christian Lifestyle", "belvoir_london",
]


def get_active_channels() -> list[dict]:
    """Return combined Tier 1 + Tier 2 channels with tier label."""
    channels = []
    for ch in TIER_1:
        entry = dict(ch)
        entry["tier"] = 1
        channels.append(entry)
    for ch in TIER_2:
        entry = dict(ch)
        entry["tier"] = 2
        channels.append(entry)
    return channels


def resolve_handle(handle: str) -> str | None:
    """Scrape YouTube channel page for externalId given a handle.

    Reuses the pattern from telegram_bot/commands.py:_handle_addchannel.
    """
    if not handle.startswith("@"):
        handle = "@" + handle

    url = f"https://www.youtube.com/{handle}"
    try:
        page = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        ).text
        match = re.search(r'"externalId":"(UC[^"]+)"', page)
        if match:
            return match.group(1)
        log.warning("Could not find externalId for %s", handle)
        return None
    except Exception as e:
        log.error("Failed to resolve handle %s: %s", handle, e)
        return None


def _load_cache() -> dict:
    """Load cached handle → channel_id mappings."""
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict):
    """Save handle → channel_id cache."""
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def ensure_channel_ids():
    """Resolve any channels missing channel_id, using cache then scraping."""
    cache = _load_cache()
    updated = False

    for tier_list in (TIER_1, TIER_2):
        for ch in tier_list:
            if ch.get("channel_id"):
                continue

            handle = ch.get("handle", "")
            # Check cache first
            if handle in cache:
                ch["channel_id"] = cache[handle]
                log.debug("Cache hit for %s: %s", handle, cache[handle])
                continue

            # Resolve via scraping
            channel_id = resolve_handle(handle)
            if channel_id:
                ch["channel_id"] = channel_id
                cache[handle] = channel_id
                updated = True
                log.info("Resolved %s → %s", handle, channel_id)
            else:
                log.warning("Could not resolve channel ID for %s (%s)", ch["name"], handle)

    if updated:
        _save_cache(cache)
