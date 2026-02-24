"""Discover trending Solana tokens from DexScreener (no API key needed).
   Includes Pump.fun graduation monitor."""

import requests
from config import get_logger
from db.connection import execute

log = get_logger("discover")

DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"

REQUEST_TIMEOUT = 15


def _fetch_solana_mints(url: str) -> set[str]:
    """Fetch token list from a DexScreener endpoint, return Solana mint addresses."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        mints = set()
        for item in data:
            if item.get("chainId") == "solana":
                addr = item.get("tokenAddress", "")
                if addr:
                    mints.add(addr)

        log.info("Fetched %d Solana tokens from %s", len(mints), url)
        return mints

    except Exception as e:
        log.error("Failed to fetch from %s: %s", url, e)
        return set()


def _fetch_pumpfun_graduates() -> set[str]:
    """Discover tokens that recently graduated from Pump.fun.
    Uses DexScreener search for Raydium pairs with Pump.fun origin indicators:
    - Searches for recently created pairs on Raydium (Pump.fun tokens migrate there)
    - Filters for young tokens with growing liquidity."""
    mints = set()
    try:
        # Search DexScreener for recent Raydium pairs (Pump.fun graduates land here)
        resp = requests.get(
            DEXSCREENER_SEARCH_URL,
            params={"q": "pump"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []

        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            # Filter: recently created (< 48h), reasonable volume
            created_at = pair.get("pairCreatedAt")
            volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
            liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)

            if created_at and volume_24h >= 10_000 and liquidity >= 5_000:
                addr = pair.get("baseToken", {}).get("address", "")
                if addr:
                    mints.add(addr)

        log.info("Found %d potential Pump.fun graduates via DexScreener", len(mints))
    except Exception as e:
        log.error("Pump.fun graduation search failed: %s", e)

    return mints


def _get_already_scanned() -> set[str]:
    """Return set of contract addresses already in the tokens table."""
    try:
        rows = execute(
            "SELECT contract_address FROM tokens",
            fetch=True,
        )
        return {row[0] for row in rows} if rows else set()
    except Exception as e:
        log.error("Failed to query existing tokens: %s", e)
        return set()


def discover_new_tokens() -> list[str]:
    """
    Fetch trending Solana tokens from DexScreener, deduplicate against DB.

    Returns list of mint addresses that have NOT been scanned yet.
    """
    # Fetch from all sources
    boosts = _fetch_solana_mints(DEXSCREENER_BOOSTS_URL)
    profiles = _fetch_solana_mints(DEXSCREENER_PROFILES_URL)
    pumpfun = _fetch_pumpfun_graduates()

    # Combine and deduplicate
    all_mints = boosts | profiles | pumpfun
    log.info("Total unique Solana tokens: %d (boosts=%d, profiles=%d, pumpfun=%d)",
             len(all_mints), len(boosts), len(profiles), len(pumpfun))

    if not all_mints:
        return []

    # Filter out already-scanned tokens
    already_scanned = _get_already_scanned()
    new_mints = sorted(all_mints - already_scanned)
    log.info("New tokens to scan: %d (already scanned: %d)",
             len(new_mints), len(already_scanned & all_mints))

    return new_mints
