"""Seed the kol_wallets table with known wallets from research.

Run: python -m kol_tracking.seed_wallets
"""

from config import get_logger
from db.connection import execute

log = get_logger("kol_tracking.seed")

SEED_WALLETS = [
    # Tier 1 — Conviction trackers (auto-execute on big buys)
    {
        "name": "Ansem",
        "wallet_address": "AVAZvHLR2PcWpDf8BXY4rVxNHYRBytycHkcB5z5QNXYm",
        "tier": 1,
        "style": "narrative_caller",
        "conviction_filter_min_usd": 5000,
        "notes": "Filter >$5K only, post-WIF mixed",
    },
    {
        "name": "Gake",
        "wallet_address": "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm",
        "tier": 1,
        "style": "conviction",
        "conviction_filter_min_usd": 500,
        "trades_per_day": 23,
        "notes": "$2.48M profit, ~42% win rate, 23 trades/day",
    },
    {
        "name": "Frank (DeGods)",
        "wallet_address": "CRVidEDtEUTYZisCxBZkpELzhQc9eauMLR3FWg74tReL",
        "tier": 1,
        "style": "conviction",
        "conviction_filter_min_usd": 1000,
        "notes": "DeGods founder, big conviction plays",
    },

    # Tier 2 — Monitor only (confirmation signal)
    {
        "name": "Cupsey",
        "wallet_address": "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",
        "tier": 2,
        "style": "high_frequency",
        "conviction_filter_min_usd": 500,
        "trades_per_day": 910,
        "notes": "FARMS copy-traders. Only flag conviction buys >$500 held >10min",
    },
    {
        "name": "Orangie",
        "wallet_address": "96sErVjEN7LNJ6Uvj63bdRWZxNuBngj56fnT9biHLKBf",
        "tier": 2,
        "style": "high_frequency",
        "conviction_filter_min_usd": 500,
        "notes": "Similar to Cupsey, recently changed wallets",
    },
    {
        "name": "Nach",
        "wallet_address": "9jyqFiLnruggwNn4EQwBNFXwpbLM9hrA4hV59ytyAVVz",
        "tier": 2,
        "style": "conviction",
        "conviction_filter_min_usd": 300,
        "notes": "Consistent performer",
    },
]


def seed_kol_wallets():
    """Insert seed wallets into kol_wallets table (skip duplicates)."""
    inserted = 0
    skipped = 0

    for wallet in SEED_WALLETS:
        try:
            execute(
                """INSERT INTO kol_wallets
                   (name, wallet_address, tier, style, conviction_filter_min_usd,
                    trades_per_day, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (wallet_address) DO UPDATE SET
                     name = EXCLUDED.name,
                     tier = EXCLUDED.tier,
                     style = EXCLUDED.style,
                     conviction_filter_min_usd = EXCLUDED.conviction_filter_min_usd,
                     notes = EXCLUDED.notes,
                     updated_at = NOW()""",
                (wallet["name"], wallet["wallet_address"], wallet["tier"],
                 wallet.get("style"), wallet.get("conviction_filter_min_usd", 500),
                 wallet.get("trades_per_day"), wallet.get("notes")),
            )
            inserted += 1
            log.info("Seeded KOL wallet: %s (Tier %d)", wallet["name"], wallet["tier"])
        except Exception as e:
            log.error("Failed to seed wallet %s: %s", wallet["name"], e)
            skipped += 1

    print(f"KOL wallet seed complete: {inserted} inserted/updated, {skipped} errors")
    return inserted


if __name__ == "__main__":
    seed_kol_wallets()
