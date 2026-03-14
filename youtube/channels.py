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
    # --- Core macro / infrastructure ---
    {"name": "InvestAnswers", "handle": "@InvestAnswers", "channel_id": "UClgJyzwGs-GyaNxUHcLZrkg"},
    {"name": "Benjamin Cowen", "handle": "@intocryptoverse", "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg"},
    {"name": "Coin Bureau", "handle": "@CoinBureau", "channel_id": "UCTULWwXF7dj19_ovr7rbgog"},
    {"name": "Raoul Pal", "handle": "@RaoulPalJourneyMan", "channel_id": "UCVFSzL3VuZKP3cN9IXdLOtw"},
    {"name": "Bankless", "handle": "@Bankless", "channel_id": "UCAl9Ld79qaZxp9JzEOwd3aA"},
    {"name": "The Breakdown", "handle": "@TheBreakdownNLW", "channel_id": "UCMKxYhVC2lJat7iB9Gec5kw"},
    {"name": "Lyn Alden", "handle": "@LynAlden", "channel_id": "UC26OTzxt9ixdrr3qdUJrYBQ"},
    # --- Crypto markets / analysis ---
    {"name": "DataDash", "handle": "@DataDash", "channel_id": "UCCatR7nWbYrkVXdxXb4cGXw"},
    {"name": "Altcoin Daily", "handle": "@AltcoinDaily", "channel_id": "UCbLhGKVY-bJPcawebgtNfbw"},
    {"name": "Anthony Pompliano", "handle": "@AnthonyPompliano", "channel_id": "UCevXpeL8cNyAnww-NqJ4m2w"},
    {"name": "Ivan on Tech", "handle": "@IvanonTech", "channel_id": "UCrYmtJBtLdtm2ov84ulV-yg"},
    {"name": "Crypto Banter", "handle": "@CryptoBanterGroup", "channel_id": "UCN9Nj4tjXbVTLYWN0EKly_Q"},
    {"name": "Lark Davis", "handle": "@TheCryptoLark", "channel_id": "UCl2oCaw8hdR_kbqyqd2klIA"},
    {"name": "Miles Deutscher", "handle": "@MilesDeutscher", "channel_id": "UCVVX-7tHff75fRAEEEnZiAQ"},
    {"name": "All-In Podcast", "handle": "@alaborofdave", "channel_id": "UCESLZhusAkFfsNsApnjF_Cg"},
    {"name": "Virtual Bacon", "handle": "@VirtualBacon", "channel_id": "UCcrBKMR8tZEXjjBJVvzNlYQ"},
    {"name": "Real Vision Finance", "handle": "@RealVisionFinance", "channel_id": "UCBH5VZE_Y4F3CMcPIzPEB5A"},
]

TIER_2 = [
    {"name": "Real Vision", "handle": "@RealVisionPresents", "channel_id": "UCBH5VZE_Y4F3CMcPIzPEB5A"},
    {"name": "Wolf of All Streets", "handle": "@ScottMelker", "channel_id": "UCxIU1RFIdDpvA8VOITswQ1A"},
    {"name": "CryptoCon", "handle": "@CryptoCon", "channel_id": "UCdoz_Hi--I26WH8sY2Qx3bg"},
    {"name": "Crypto Crew University", "handle": "@CryptoCrewUniversity", "channel_id": "UC7ndkZ4vViKiM7kVEgdrlZQ"},
    {"name": "Patrick Boyle", "handle": "@PBoyle", "channel_id": "UCASM0cgfkJxQ1ICmRilfHLw"},
]

TIER_3_EXCLUDED = [
    "Tom Bilyeu", "Lex Fridman", "Huberman", "Joe Rogan",
    "Brian Jung", "Johnny Harris", "Dr Berg",
    "DLM Christian Lifestyle", "belvoir_london",
    "Codie Sanchez", "Alex Hormozi", "Diary of a CEO", "Daily Stoic",
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
