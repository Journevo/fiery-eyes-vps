"""GMGN Smart Money Wallet Scraper.

Scrapes the GMGN.ai Solana smart money leaderboard weekly (Sunday 00:00 UTC).
Filters, scores, classifies wallets into tiers A/B/C, and stores in DB.

Primary source: GMGN API (requires residential proxy from datacenter IPs).
Fallback: curated seed list of known profitable wallets, validated via Helius.

Run: python main.py gmgn-scrape
Schedule: every Sunday 00:00 UTC via scanner/scheduler.py
"""

import json
import math
import time
from datetime import datetime, timezone, timedelta

from config import HELIUS_RPC_URL, GMGN_PROXY_URL, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json, post_json
from monitoring.degraded import record_api_call
from telegram_bot.severity import SYSTEM_CHAT_ID, _send_to_channel

log = get_logger("wallets.gmgn_scraper")

# GMGN API (Cloudflare-protected — needs residential proxy on datacenter VPS)
GMGN_LEADERBOARD_URL = "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d"

# Filter thresholds
MIN_WIN_RATE = 0.60       # 60%
MIN_TRADE_COUNT = 20
MAX_INACTIVE_DAYS = 30

# Scoring weights (total = 100)
WEIGHT_WIN_RATE = 30
WEIGHT_PNL = 25
WEIGHT_TRADE_COUNT = 20
WEIGHT_CONSISTENCY = 25

# Tier thresholds on gmgn_score (0-100)
TIER_A_MIN = 75
TIER_B_MIN = 50

# Curated smart money wallets — sourced from GMGN leaderboards, on-chain analysis,
# and community research. Used as fallback when GMGN API is blocked (Cloudflare).
# Stats are approximate, from GMGN/Cielo at time of curation.
SEED_WALLETS = [
    # --- Known KOLs (also in kol_wallets for tx monitoring) ---
    {"address": "AVAZvHLR2PcWpDf8BXY4rVxNHYRBytycHkcB5z5QNXYm",
     "name": "Ansem", "winrate": 0.65, "pnl_30d": 200000, "pnl_7d": 45000,
     "buy_30d": 40, "sell_30d": 35, "notes": "Narrative caller, post-WIF mixed"},
    {"address": "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm",
     "name": "Gake", "winrate": 0.42, "pnl_30d": 2480000, "pnl_7d": 400000,
     "buy_30d": 350, "sell_30d": 340, "notes": "$2.48M profit, 42% WR but volume king"},
    {"address": "CRVidEDtEUTYZisCxBZkpELzhQc9eauMLR3FWg74tReL",
     "name": "Frank (DeGods)", "winrate": 0.70, "pnl_30d": 350000, "pnl_7d": 80000,
     "buy_30d": 25, "sell_30d": 20, "notes": "DeGods founder, big conviction plays"},
    {"address": "9jyqFiLnruggwNn4EQwBNFXwpbLM9hrA4hV59ytyAVVz",
     "name": "Nach", "winrate": 0.62, "pnl_30d": 95000, "pnl_7d": 22000,
     "buy_30d": 60, "sell_30d": 55, "notes": "Consistent performer"},
    {"address": "5M8ACGKEXG1ojKDTMH3sMqhTihTgHYMSsZc6W8i7QW3Y",
     "name": "BONKGUY/Unipcs", "winrate": 0.68, "pnl_30d": 180000, "pnl_7d": 40000,
     "buy_30d": 70, "sell_30d": 65, "notes": "Memecoin specialist"},
    {"address": "rgPyefcNqJCsJj1wrWhdQqHVphVWFXLqU5wtiFStBEN",
     "name": "Truth Terminal", "winrate": 0.75, "pnl_30d": 500000, "pnl_7d": 120000,
     "buy_30d": 15, "sell_30d": 10, "notes": "AI agent wallet, conviction plays"},

    # --- Murad Mahmudov (3 Solana wallets, ZachXBT identified) ---
    {"address": "7QZGS7MQ4S6hRmE8iXoFTXgQ2hXVUCho2ZhgeWvLNPZT",
     "name": "Murad 1", "winrate": 0.75, "pnl_30d": 500000, "pnl_7d": 120000,
     "buy_30d": 15, "sell_30d": 15, "notes": "Murad main — SPX6900 61x, conviction holds"},
    {"address": "GyBkVYkHBPMapyQeueQ6d44YthwqYiX4ajgnGLqq9P7r",
     "name": "Murad 2", "winrate": 0.75, "pnl_30d": 300000, "pnl_7d": 70000,
     "buy_30d": 12, "sell_30d": 13, "notes": "Murad alt — bought MINI before public post"},
    {"address": "2xn57hPD2v6ighJFPXNPSoiGUXkW4KKo8Hb3NpXmHZvZ",
     "name": "Murad 3", "winrate": 0.70, "pnl_30d": 150000, "pnl_7d": 35000,
     "buy_30d": 10, "sell_30d": 10, "notes": "Murad alt — deBridge funded"},

    # --- Nansen Top 10 Memecoin Smart Money (verified, labeled) ---
    {"address": "4EtAJ1p8RjqccEVhEhaYnEgQ6kA4JHR8oYqyLFwARUj6",
     "name": "Trump Whale", "winrate": 0.68, "pnl_30d": 260000, "pnl_7d": 60000,
     "buy_30d": 120, "sell_30d": 115, "notes": "Nansen #1 — 97% avg ROI/trade"},
    {"address": "8zFZHuSRuDpuAR7J6FzwyF3vKNx4CVW3DFHJerQhc7Zd",
     "name": "traderpow", "winrate": 0.72, "pnl_30d": 14800000, "pnl_7d": 3000000,
     "buy_30d": 100, "sell_30d": 95, "notes": "Nansen #3 — $14.8M profits"},
    {"address": "8mZYBV8aPvPCo34CyCmt6fWkZRFviAUoBZr1Bn993gro",
     "name": "popchad.sol", "winrate": 0.65, "pnl_30d": 7240000, "pnl_7d": 1500000,
     "buy_30d": 50, "sell_30d": 45, "notes": "Nansen #4 — $7.24M realized"},
    {"address": "5CP6zv8a17mz91v6rMruVH6ziC5qAL8GFaJzwrX9Fvup",
     "name": "naseem", "winrate": 0.70, "pnl_30d": 8000000, "pnl_7d": 1800000,
     "buy_30d": 30, "sell_30d": 25, "notes": "Nansen #5 — $8M SHROOM, sniper"},
    {"address": "H2ikJvq8or5MyjvFowD7CDY6fG3Sc2yi4mxTnfovXy3K",
     "name": "shatter.sol", "winrate": 0.72, "pnl_30d": 35000000, "pnl_7d": 8000000,
     "buy_30d": 80, "sell_30d": 75, "notes": "Nansen #6 — $3M to $35M on TRUMP"},
    {"address": "2h7s3FpSvc6v2oHke6Uqg191B5fPCeFTmMGnh5oPWhX7",
     "name": "tonka.sol", "winrate": 0.68, "pnl_30d": 21800000, "pnl_7d": 5000000,
     "buy_30d": 60, "sell_30d": 55, "notes": "Nansen #7 — 196% ROI, short-term"},
    {"address": "HWdeCUjBvPP1HJ5oCJt7aNsvMWpWoDgiejUWvfFX6T7R",
     "name": "Multi Memecoin", "winrate": 0.65, "pnl_30d": 9650000, "pnl_7d": 2000000,
     "buy_30d": 85, "sell_30d": 80, "notes": "Nansen #8 — $9.65M realized"},
    {"address": "4DPxYoJ5DgjvXPUtZdT3CYUZ3EEbSPj4zMNEVFJTd1Ts",
     "name": "Sigil Fund", "winrate": 0.70, "pnl_30d": 6100000, "pnl_7d": 1400000,
     "buy_30d": 45, "sell_30d": 40, "notes": "Nansen #9 — $6.1M profits, 820 trades"},
    {"address": "Hwz4BDgtDRDBTScpEKDawshdKatZJh6z1SJYmRUxTxKE",
     "name": "Anon HP", "winrate": 0.62, "pnl_30d": 500000, "pnl_7d": 100000,
     "buy_30d": 15, "sell_30d": 12, "notes": "Nansen #10 — 127 trades, 30 tokens"},

    # --- ChainCatcher top performers (from 1,080 wallet study) ---
    {"address": "4Be9CvxqHW6BYiRAxW9Q3xu1ycTMWaL5z8NX4HR3ha7t",
     "name": "CC 50x Flipper", "winrate": 0.68, "pnl_30d": 500000, "pnl_7d": 100000,
     "buy_30d": 75, "sell_30d": 75, "notes": "ChainCatcher #10 — consistent 50x flips"},
    {"address": "FTg1gqW7vPm4kdU1LPM7JJnizbgPdRDy2PitKw6mY27j",
     "name": "CC Trader 7", "winrate": 0.63, "pnl_30d": 200000, "pnl_7d": 45000,
     "buy_30d": 50, "sell_30d": 45, "notes": "ChainCatcher #7 — mid-tier consistent"},
    {"address": "69ngexW9UkgRp5KFjLpaK9XNSCxUFmps6jYmqhK3q6m9",
     "name": "CC Trader 8", "winrate": 0.61, "pnl_30d": 180000, "pnl_7d": 40000,
     "buy_30d": 55, "sell_30d": 50, "notes": "ChainCatcher #8 — high volume"},

    # --- Other validated smart money ---
    {"address": "orcACRJYTFjTeo2pV8TfYRTpmqfoYgbVi9GeANXTCc8",
     "name": "Orca Whale", "winrate": 0.70, "pnl_30d": 190000, "pnl_7d": 42000,
     "buy_30d": 50, "sell_30d": 45, "notes": "DEX whale, narrative rotation"},
    {"address": "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",
     "name": "Meme Expert", "winrate": 0.63, "pnl_30d": 120000, "pnl_7d": 28000,
     "buy_30d": 130, "sell_30d": 120, "notes": "Memecoin specialist, high volume"},

    # NOTE: Cupsey & Orangie excluded — 900+ trades/day burns Helius credits
    # NOTE: cifwifhatday.sol excluded — wallet empty on-chain
    # NOTE: CC #1 (6FNbu3i...) excluded — 100% WR over 42 trades, likely bot
]


def _fetch_gmgn_leaderboard(limit: int = 200) -> list[dict]:
    """Try to fetch GMGN smart money leaderboard.

    Returns empty list if Cloudflare blocks (expected on datacenter IPs).
    """
    url = (f"{GMGN_LEADERBOARD_URL}"
           f"?orderby=pnl_7d&direction=desc&limit={limit}")
    try:
        kwargs = {"headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://gmgn.ai/",
        }}
        if GMGN_PROXY_URL:
            kwargs["proxies"] = {"https": GMGN_PROXY_URL, "http": GMGN_PROXY_URL}
        data = get_json(url, **kwargs)
        record_api_call("gmgn", True)

        if not data or data.get("code") != 0:
            return []

        wallets = data.get("data", {}).get("rank", [])
        log.info("GMGN API success: %d wallets fetched", len(wallets))
        return wallets

    except Exception as e:
        log.info("GMGN API unavailable (expected on datacenter IP): %s",
                 str(e)[:80])
        record_api_call("gmgn", False)
        return []


def _validate_wallet_onchain(address: str) -> dict | None:
    """Validate a wallet exists on-chain and has activity via Helius.

    Returns basic wallet stats or None if invalid/inactive.
    """
    if not HELIUS_RPC_URL:
        return {"valid": True, "sol_balance": 0, "token_count": 0}

    try:
        # Get SOL balance
        bal_resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0", "id": 1,
            "method": "getBalance",
            "params": [address],
        })
        lamports = bal_resp.get("result", {}).get("value", 0) or 0
        sol_balance = lamports / 1e9

        # Get token account count
        tok_resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        })
        token_accounts = tok_resp.get("result", {}).get("value", [])

        # Must have some SOL or tokens to be valid
        if sol_balance < 0.01 and len(token_accounts) == 0:
            return None

        return {
            "valid": True,
            "sol_balance": sol_balance,
            "token_count": len(token_accounts),
        }

    except Exception as e:
        log.debug("On-chain validation failed for %s: %s", address[:12], e)
        return None


def _convert_seed_to_gmgn_format(seed: dict) -> dict:
    """Convert a seed wallet entry to GMGN-like format for scoring."""
    return {
        "wallet_address": seed["address"],
        "address": seed["address"],
        "twitter_name": seed.get("name"),
        "winrate": seed.get("winrate", 0),
        "pnl_30d": seed.get("pnl_30d", 0),
        "realized_profit_30d": seed.get("pnl_30d", 0),
        "pnl_7d": seed.get("pnl_7d", 0),
        "realized_profit_7d": seed.get("pnl_7d", 0),
        "buy_30d": seed.get("buy_30d", 0),
        "sell_30d": seed.get("sell_30d", 0),
        "last_active_timestamp": int(time.time()),  # assume active
        "_source": "seed",
        "_notes": seed.get("notes", ""),
    }


def _filter_wallet(w: dict) -> bool:
    """Apply quality filters to a wallet entry."""
    win_rate = float(w.get("winrate", 0) or 0)
    if win_rate < MIN_WIN_RATE:
        return False

    buy_count = int(w.get("buy_30d", 0) or 0)
    sell_count = int(w.get("sell_30d", 0) or 0)
    if buy_count + sell_count < MIN_TRADE_COUNT:
        return False

    last_active = w.get("last_active_timestamp")
    if last_active:
        last_dt = datetime.fromtimestamp(last_active, tz=timezone.utc)
        if last_dt < datetime.now(timezone.utc) - timedelta(days=MAX_INACTIVE_DAYS):
            return False

    return True


def _score_wallet(w: dict) -> float:
    """Score a wallet on 0-100 scale."""
    score = 0.0

    # Win rate score (30pts)
    win_rate = float(w.get("winrate", 0) or 0)
    score += min(WEIGHT_WIN_RATE, max(0, (win_rate - 0.60) / 0.30 * WEIGHT_WIN_RATE))

    # PnL score (25pts) — log scale
    pnl = float(w.get("pnl_7d", 0) or w.get("realized_profit_7d", 0) or 0)
    if pnl > 0:
        pnl_normalized = min(1.0, math.log10(max(1, pnl)) / math.log10(500_000))
        score += pnl_normalized * WEIGHT_PNL

    # Trade count score (20pts)
    trade_count = int(w.get("buy_30d", 0) or 0) + int(w.get("sell_30d", 0) or 0)
    score += min(WEIGHT_TRADE_COUNT, max(0, (trade_count - 20) / 180 * WEIGHT_TRADE_COUNT))

    # Consistency score (25pts)
    pnl_30d = float(w.get("pnl_30d", 0) or w.get("realized_profit_30d", 0) or 0)
    pnl_7d = float(w.get("pnl_7d", 0) or w.get("realized_profit_7d", 0) or 0)

    if pnl_30d > 0 and pnl_7d > 0:
        ratio = pnl_7d / pnl_30d
        consistency = 0.3 if ratio > 0.8 else 0.6 if ratio > 0.5 else 1.0
    elif pnl_30d > 0:
        consistency = 0.7
    else:
        consistency = 0.4

    if win_rate > 0.75:
        consistency = min(1.0, consistency + 0.2)

    score += consistency * WEIGHT_CONSISTENCY
    return round(min(100, max(0, score)), 1)


def _classify_tier(score: float) -> str:
    """Classify wallet into tier A/B/C based on score."""
    if score >= TIER_A_MIN:
        return "A"
    elif score >= TIER_B_MIN:
        return "B"
    return "C"


def _check_insider(w: dict) -> bool:
    """Basic insider detection heuristics."""
    win_rate = float(w.get("winrate", 0) or 0)
    trade_count = int(w.get("buy_30d", 0) or 0) + int(w.get("sell_30d", 0) or 0)

    if win_rate > 0.92 and trade_count > 100:
        return True

    pnl_30d = float(w.get("pnl_30d", 0) or w.get("realized_profit_30d", 0) or 0)
    if pnl_30d > 1_000_000 and win_rate > 0.85:
        return True

    return False


def run_gmgn_scrape() -> dict:
    """Main entry: scrape GMGN leaderboard (or use seed list), filter, score, store."""
    log.info("=== GMGN Weekly Wallet Scrape ===")
    start_time = time.time()

    # 1. Try GMGN API first
    raw_wallets = _fetch_gmgn_leaderboard(limit=200)
    source = "gmgn_api"

    # 2. Fallback to curated seed list if GMGN blocked
    if not raw_wallets:
        log.info("Using curated seed list (%d wallets)", len(SEED_WALLETS))
        raw_wallets = [_convert_seed_to_gmgn_format(s) for s in SEED_WALLETS]
        source = "seed_list"

    # 3. Filter
    filtered = [w for w in raw_wallets if _filter_wallet(w)]
    log.info("Filter pass: %d / %d wallets", len(filtered), len(raw_wallets))

    # 4. Validate on-chain (only for seed list — GMGN wallets are pre-validated)
    if source == "seed_list":
        validated = []
        for w in filtered:
            addr = w.get("wallet_address") or w.get("address", "")
            onchain = _validate_wallet_onchain(addr)
            if onchain:
                w["_onchain"] = onchain
                validated.append(w)
                log.info("Validated %s: %.2f SOL, %d tokens",
                         addr[:12], onchain["sol_balance"], onchain["token_count"])
            else:
                log.info("Skipping %s — not active on-chain", addr[:12])
            time.sleep(0.3)  # rate limit Helius
        filtered = validated
        log.info("On-chain validation: %d / %d passed", len(filtered), len(raw_wallets))

    # 5. Score and classify
    results = []
    tier_counts = {"A": 0, "B": 0, "C": 0}
    insider_count = 0

    for w in filtered:
        address = w.get("wallet_address") or w.get("address", "")
        if not address:
            continue

        score = _score_wallet(w)
        tier = _classify_tier(score)
        is_insider = _check_insider(w)

        if is_insider:
            insider_count += 1

        tier_counts[tier] += 1
        results.append({
            "address": address,
            "display_name": w.get("twitter_name") or w.get("ens") or address[:8] + "...",
            "tier": tier,
            "win_rate": float(w.get("winrate", 0) or 0),
            "pnl_usd": float(w.get("pnl_30d", 0) or w.get("realized_profit_30d", 0) or 0),
            "pnl_pct": float(w.get("pnl_pct_30d", 0) or 0),
            "trade_count": int(w.get("buy_30d", 0) or 0) + int(w.get("sell_30d", 0) or 0),
            "avg_hold_minutes": float(w.get("avg_hold_time", 0) or 0),
            "last_active": w.get("last_active_timestamp"),
            "gmgn_score": score,
            "is_insider": is_insider,
            "raw_data": w,
        })

    # 6. Store in DB
    new_wallets = 0
    updated_wallets = 0
    current_addresses = set()

    for r in results:
        current_addresses.add(r["address"])
        last_active_ts = None
        if r["last_active"]:
            try:
                last_active_ts = datetime.fromtimestamp(r["last_active"], tz=timezone.utc)
            except (ValueError, OSError):
                pass

        try:
            existing = execute_one(
                "SELECT id FROM gmgn_wallets WHERE wallet_address = %s",
                (r["address"],),
            )

            execute(
                """INSERT INTO gmgn_wallets
                   (wallet_address, display_name, tier, win_rate, pnl_usd, pnl_pct,
                    trade_count, avg_hold_minutes, last_active, gmgn_score,
                    is_insider, is_active, raw_data, last_updated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (wallet_address) DO UPDATE SET
                     display_name = EXCLUDED.display_name,
                     tier = EXCLUDED.tier,
                     win_rate = EXCLUDED.win_rate,
                     pnl_usd = EXCLUDED.pnl_usd,
                     pnl_pct = EXCLUDED.pnl_pct,
                     trade_count = EXCLUDED.trade_count,
                     avg_hold_minutes = EXCLUDED.avg_hold_minutes,
                     last_active = EXCLUDED.last_active,
                     gmgn_score = EXCLUDED.gmgn_score,
                     is_insider = EXCLUDED.is_insider,
                     is_active = TRUE,
                     raw_data = EXCLUDED.raw_data,
                     last_updated = NOW()""",
                (r["address"], r["display_name"], r["tier"], r["win_rate"],
                 r["pnl_usd"], r["pnl_pct"], r["trade_count"],
                 r["avg_hold_minutes"], last_active_ts, r["gmgn_score"],
                 r["is_insider"], not r["is_insider"],
                 json.dumps(r["raw_data"], default=str)),
            )

            if existing:
                updated_wallets += 1
            else:
                new_wallets += 1

        except Exception as e:
            log.error("Failed to store wallet %s: %s", r["address"][:12], e)

    # 7. Deactivate wallets no longer in leaderboard (only for API source)
    removed = 0
    if source == "gmgn_api":
        try:
            rows = execute(
                "SELECT wallet_address FROM gmgn_wallets WHERE is_active = TRUE",
                fetch=True,
            )
            if rows:
                for (addr,) in rows:
                    if addr not in current_addresses:
                        execute(
                            "UPDATE gmgn_wallets SET is_active = FALSE, last_updated = NOW() "
                            "WHERE wallet_address = %s",
                            (addr,),
                        )
                        removed += 1
        except Exception as e:
            log.error("Failed to deactivate old wallets: %s", e)

    elapsed = time.time() - start_time

    summary = {
        "source": source,
        "total_found": len(raw_wallets),
        "passed_filter": len(filtered),
        "insiders_flagged": insider_count,
        "tier_a": tier_counts["A"],
        "tier_b": tier_counts["B"],
        "tier_c": tier_counts["C"],
        "new_wallets": new_wallets,
        "updated_wallets": updated_wallets,
        "removed": removed,
        "elapsed_sec": round(elapsed, 1),
    }

    # 8. Log and report
    _log_scrape(
        len(raw_wallets), len(filtered),
        tier_counts["A"], tier_counts["B"], tier_counts["C"],
        new_wallets, removed, None,
    )
    _send_report(summary)

    log.info("GMGN scrape complete [%s] in %.1fs: %d found, %d passed, "
             "A=%d B=%d C=%d, %d new, %d removed",
             source, elapsed, len(raw_wallets), len(filtered),
             tier_counts["A"], tier_counts["B"], tier_counts["C"],
             new_wallets, removed)

    return summary


def _log_scrape(total: int, passed: int, tier_a: int, tier_b: int, tier_c: int,
                new: int, removed: int, error: str | None):
    """Log scrape run to gmgn_scrape_log."""
    try:
        execute(
            """INSERT INTO gmgn_scrape_log
               (total_found, passed_filter, tier_a, tier_b, tier_c,
                new_wallets, removed_wallets, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (total, passed, tier_a, tier_b, tier_c, new, removed, error),
        )
    except Exception as e:
        log.error("Failed to log scrape: %s", e)


def _send_report(summary: dict):
    """Send weekly scrape report to System Telegram channel."""
    source_label = "GMGN API" if summary["source"] == "gmgn_api" else "Seed List"
    report = (
        f"📊 <b>GMGN Weekly Wallet Scrape</b>\n"
        f"Source: {source_label}\n"
        f"\n"
        f"Found: {summary['total_found']} wallets\n"
        f"Passed filters: {summary['passed_filter']}\n"
        f"Insiders flagged: {summary['insiders_flagged']}\n"
        f"\n"
        f"<b>Tier Distribution:</b>\n"
        f"  🟢 Tier A: {summary['tier_a']}\n"
        f"  🟡 Tier B: {summary['tier_b']}\n"
        f"  ⚪ Tier C: {summary['tier_c']}\n"
        f"\n"
        f"Changes: +{summary['new_wallets']} new, "
        f"{summary['updated_wallets']} updated, "
        f"-{summary['removed']} removed\n"
        f"Time: {summary['elapsed_sec']}s"
    )
    try:
        _send_to_channel(SYSTEM_CHAT_ID, report)
    except Exception as e:
        log.error("Failed to send GMGN report: %s", e)


def get_gmgn_wallets(tier: str | None = None, active_only: bool = True) -> list[dict]:
    """Get GMGN wallets from DB, optionally filtered by tier."""
    conditions = []
    params = []

    if active_only:
        conditions.append("is_active = TRUE")
    if tier:
        conditions.append("tier = %s")
        params.append(tier)

    where = " AND ".join(conditions) if conditions else "TRUE"

    try:
        rows = execute(
            f"""SELECT wallet_address, display_name, tier, win_rate, pnl_usd,
                       trade_count, gmgn_score, is_insider, last_active
                FROM gmgn_wallets
                WHERE {where}
                ORDER BY gmgn_score DESC""",
            params if params else None,
            fetch=True,
        )
        return [
            {
                "wallet_address": r[0],
                "display_name": r[1],
                "tier": r[2],
                "win_rate": float(r[3] or 0),
                "pnl_usd": float(r[4] or 0),
                "trade_count": int(r[5] or 0),
                "gmgn_score": float(r[6] or 0),
                "is_insider": r[7],
                "last_active": r[8],
            }
            for r in rows
        ] if rows else []
    except Exception as e:
        log.error("Failed to fetch GMGN wallets: %s", e)
        return []


def get_gmgn_summary() -> dict:
    """Get summary stats for GMGN wallet fleet."""
    try:
        row = execute_one(
            """SELECT
                 COUNT(*) FILTER (WHERE is_active AND NOT is_insider),
                 COUNT(*) FILTER (WHERE tier = 'A' AND is_active),
                 COUNT(*) FILTER (WHERE tier = 'B' AND is_active),
                 COUNT(*) FILTER (WHERE tier = 'C' AND is_active),
                 AVG(gmgn_score) FILTER (WHERE is_active),
                 AVG(win_rate) FILTER (WHERE is_active)
               FROM gmgn_wallets""",
        )
        if row:
            return {
                "total_active": row[0] or 0,
                "tier_a": row[1] or 0,
                "tier_b": row[2] or 0,
                "tier_c": row[3] or 0,
                "avg_score": float(row[4] or 0),
                "avg_win_rate": float(row[5] or 0),
            }
    except Exception as e:
        log.error("Failed to get GMGN summary: %s", e)

    return {"total_active": 0, "tier_a": 0, "tier_b": 0, "tier_c": 0,
            "avg_score": 0, "avg_win_rate": 0}


if __name__ == "__main__":
    result = run_gmgn_scrape()
    for k, v in result.items():
        print(f"  {k}: {v}")
