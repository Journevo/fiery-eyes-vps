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
    {"name": "Benjamin Cowen", "handle": "@intothecryptoverse", "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg"},
    {"name": "Coin Bureau", "handle": "@CoinBureau", "channel_id": "UCqK_GSMbpiV8spgD3ZGloSw"},
    {"name": "Raoul Pal", "handle": "@raoulpaltjm", "channel_id": "UCVFSzL3VuZKP3cN9IXdLOtw"},
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
    {"name": "Miles Deutscher", "handle": "@thecrypto_edge", "channel_id": "UCa0QcJ73irUZGK726QTH2hg"},
    {"name": "All-In Podcast", "handle": "@allinpodcast", "channel_id": "UChJM-mF-4w_61Z6eCyl0eKQ"},
    {"name": "Virtual Bacon", "handle": "@VirtualBacon", "channel_id": "UCcrEA_xd9Ldf1C8DIJYdyyA"},
    {"name": "Diary of a CEO", "handle": "@TheDiaryOfACEO", "channel_id": "UCGq-a57w-aPwyi3pW7XLiHw"},
    {"name": "Lex Fridman", "handle": "@lexfridman", "channel_id": "UCSHZKyawb77ixDdsGog4iWA"},
    {"name": "PBD Podcast", "handle": "@PBDPodcast", "channel_id": "UCGX7nGXpz-CmO_Arg-cgJ7A"},
    {"name": "Principles by Ray Dalio", "handle": "@principlesbyraydalio", "channel_id": "UCqvaXJ1K3HheTPNjH-KpwXQ"},
    {"name": "Mark Moss", "handle": "@1MarkMoss", "channel_id": "UC9ZM3N0ybRtp44-WLqsW3iQ"},
    {"name": "Colin Talks Crypto", "handle": "@ColinTalksCrypto", "channel_id": "UCnqJ2HjWhm7MbhgFHLUENfQ"},
    {"name": "Real Vision Finance", "handle": "@RealVisionFinance", "channel_id": "UCGXWKlq1Oxr3ddEtmKhAkPg"},
    {"name": "Tyrelle Anderson-Brown", "handle": "@TyrelleAB", "channel_id": "UC6DkKXrHSEf5zTNGhEZdOJA"},
]

TIER_2 = [
    {"name": "Real Vision", "handle": "@RealVision", "channel_id": "UCwSVtQvURxiyn1CQeyoExZg"},
    {"name": "Wolf of All Streets", "handle": "@ScottMelker", "channel_id": "UCxIU1RFIdDpvA8VOITswQ1A"},
    {"name": "CryptoCon", "handle": "@CryptoCon", "channel_id": "UC6BR8Wcp8oKMRmTogFl9tmg"},
    {"name": "Crypto Crew University", "handle": "@CryptoCrewUniversity", "channel_id": "UC7ndkZ4vViKiM7kVEgdrlZQ"},
    {"name": "Paul Barron Network", "handle": "@PaulBarronNetwork", "channel_id": "UC4VPa7EOvObpyCRI4YKRQRw"},
    {"name": "CryptosRUs", "handle": "@CryptosRUs", "channel_id": "UCI7M65p3A-D3P4v5qW8POxQ"},
    {"name": "Gareth Soloway", "handle": "@GarethSoloway", "channel_id": "UCwTu6kD2igaLMpxswtcdxlg"},
    {"name": "The Bitcoin Layer", "handle": "@TheBitcoinLayer", "channel_id": "UCDo6-SUypaXlTmH6AyrYBZA"},
    {"name": "PlanB", "handle": "@100trillionUSD", "channel_id": "UCwrevyDwc6SFEobyZbBWPFg"},
    {"name": "Natalie Brunell", "handle": "@NatalieBrunell", "channel_id": "UCru3nlhzHrbgK21x0MdB_eg"},
    {"name": "Digital Asset News", "handle": "@DigitalAssetNews", "channel_id": "UCJgHxpqfhWEEjYH9cLXqhIQ"},
    {"name": "CTO Larsson", "handle": "@CTOLarsson", "channel_id": "UCFU-BE5HRJoudqIz1VDKlhQ"},
    {"name": "Kitco NEWS", "handle": "@KitcoNEWS", "channel_id": "UC9ijza42jVR3T6b8bColgvg"},
    {"name": "Rekt Capital", "handle": "@RektCapital", "channel_id": "UCffNwA5OkxWEmruYFrWJsoQ"},
    {"name": "Bob Loukas", "handle": "@BobLoukas", "channel_id": "UC0zGwzu0zzCImC1BwPuWyXQ"},
    {"name": "Bitcoin Magazine", "handle": "@BitcoinMagazine", "channel_id": "UCtOV5M-T3GcsJAq8QKaf0lg"},
    {"name": "Heresy Financial", "handle": "@HeresyFinancial", "channel_id": "UC4fg8o6oUkkZDLaC6eAZKwQ"},
    {"name": "Crypto Insider", "handle": "@CryptoInsider", "channel_id": "UCgEVPPnJoW_AmnKhb_D0-mw"},
    {"name": "British HODL", "handle": "@BritishHODL", "channel_id": "UCl9lcSQ1maUOJIqbkEoVO3A"},
    {"name": "Patrick Boyle", "handle": "@PBoyle", "channel_id": "UCASM0cgfkJxQ1ICmRilfHLw"},
]

TIER_3_EXCLUDED = [
    "Tom Bilyeu", "Huberman", "Joe Rogan",
    "Brian Jung", "Johnny Harris", "Dr Berg",
    "DLM Christian Lifestyle", "belvoir_london",
    "Codie Sanchez", "Alex Hormozi", "Daily Stoic",
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
