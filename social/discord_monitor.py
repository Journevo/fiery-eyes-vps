"""Discord Monitor — STUB with DexScreener Discord link check."""

from config import get_logger
from quality_gate.helpers import get_json

log = get_logger("social.discord")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


def check_discord_presence(mint: str) -> dict:
    """Check if token has a Discord server via DexScreener.

    Returns:
        {"has_discord": bool, "discord_url": str|None, "score": int}
    """
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return {"has_discord": False, "discord_url": None, "score": 0}

        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        info = best.get("info", {}) or {}
        socials = info.get("socials", [])

        for s in socials:
            if s.get("type") == "discord":
                return {
                    "has_discord": True,
                    "discord_url": s.get("url"),
                    "score": 60,
                }

        return {"has_discord": False, "discord_url": None, "score": 0}
    except Exception as e:
        log.error("Discord presence check failed for %s: %s", mint, e)
        return {"has_discord": False, "discord_url": None, "score": 0}


def get_member_count(invite_url: str) -> dict:
    """Get Discord server member count. STUB."""
    log.warning("STUB: Discord member count not implemented for %s", invite_url)
    return {"total_members": 0, "online_members": 0, "source": "stub"}


def get_activity_score(invite_url: str) -> dict:
    """Get Discord activity score. STUB."""
    log.warning("STUB: Discord activity score not implemented for %s", invite_url)
    return {"activity_score": 0, "messages_per_day": 0, "source": "stub"}
