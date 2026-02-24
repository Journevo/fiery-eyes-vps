"""Token Lifecycle Tracker — classifies tokens into 5 maturity stages.

Stages:
  1. BIRTH:          <48h old, <5K holders. Watch or Momentum only.
  2. VIRAL:          volume >5x baseline, holder growth accelerating.
  3. COMMUNITY:      7d retention >40%, buyer/seller >1.2 sustained 3d, median wallet increasing.
  4. ADOPTION:       fee revenue appearing, dev activity, 30d retention >50%. Auto-flag promotion.
  5. INFRASTRUCTURE: revenue >$1M/mo 3+ months, buyback/burn active, treasury >12mo runway.

DB columns on tokens table:
  lifecycle_stage     INT DEFAULT 1
  stage_entered_at    TIMESTAMP
  promotion_history   JSONB DEFAULT '[]'
"""

import json
from datetime import datetime, timezone, timedelta

from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json

log = get_logger("engines.lifecycle")

# ---------------------------------------------------------------------------
# DexScreener endpoint for live volume/price data
# ---------------------------------------------------------------------------
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------
STAGE_NAMES = {
    1: "BIRTH",
    2: "VIRAL",
    3: "COMMUNITY",
    4: "ADOPTION",
    5: "INFRASTRUCTURE",
}

# Stage 1 thresholds
BIRTH_MAX_AGE_HOURS = 48
BIRTH_MAX_HOLDERS = 5_000

# Stage 2 thresholds
VIRAL_VOLUME_MULTIPLIER = 5.0     # volume > 5x baseline
VIRAL_MIN_GROWTH_RATE = 0.05      # 5% holder growth rate (day over day)

# Stage 3 thresholds
COMMUNITY_RETENTION_7D = 0.40     # 7d retention > 40%
COMMUNITY_BUYER_SELLER_RATIO = 1.2
COMMUNITY_SUSTAINED_DAYS = 3

# Stage 4 thresholds
ADOPTION_RETENTION_30D = 0.50     # 30d retention > 50%

# Stage 5 thresholds
INFRA_REVENUE_MONTHLY = 1_000_000   # $1M/month
INFRA_REVENUE_MONTHS = 3            # sustained 3+ months
INFRA_TREASURY_RUNWAY_MONTHS = 12


# ---------------------------------------------------------------------------
# Snapshot fetcher (same schema as momentum engine)
# ---------------------------------------------------------------------------

def _get_snapshots(token_id: int, days: int = 180) -> list[dict]:
    """Fetch recent daily snapshots for a token."""
    try:
        rows = execute(
            """SELECT date, price, mcap, volume, liquidity_depth_10k,
                      holders_raw, holders_quality_adjusted,
                      retention_7d, retention_30d,
                      top10_pct, top50_pct, gini,
                      median_wallet_balance, fees, revenue,
                      stablecoin_inflow, dev_commits, dev_active,
                      social_velocity, smart_money_netflow
               FROM snapshots_daily
               WHERE token_id = %s AND date >= CURRENT_DATE - %s
               ORDER BY date ASC""",
            (token_id, days),
            fetch=True,
        )
        if not rows:
            return []

        keys = ["date", "price", "mcap", "volume", "liquidity_depth_10k",
                "holders_raw", "holders_quality_adjusted",
                "retention_7d", "retention_30d",
                "top10_pct", "top50_pct", "gini",
                "median_wallet_balance", "fees", "revenue",
                "stablecoin_inflow", "dev_commits", "dev_active",
                "social_velocity", "smart_money_netflow"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to fetch snapshots for token_id=%d: %s", token_id, e)
        return []


def _get_token_info(token_id: int) -> dict | None:
    """Fetch basic token info from the tokens table."""
    try:
        row = execute_one(
            """SELECT id, symbol, contract_address, category,
                      lifecycle_stage, stage_entered_at, promotion_history,
                      created_at
               FROM tokens WHERE id = %s""",
            (token_id,)
        )
        if not row:
            return None
        return {
            "id": row[0],
            "symbol": row[1],
            "contract_address": row[2],
            "category": row[3],
            "lifecycle_stage": row[4] or 1,
            "stage_entered_at": row[5],
            "promotion_history": row[6] or [],
            "created_at": row[7],
        }
    except Exception as e:
        log.error("Failed to fetch token info for id=%d: %s", token_id, e)
        return None


def _fetch_dexscreener_data(mint: str) -> dict | None:
    """Fetch live volume and price data from DexScreener."""
    try:
        data = get_json(DEXSCREENER_TOKEN_URL.format(mint=mint))
        pairs = data.get("pairs") or []
        if not pairs:
            return None

        # Use highest-volume pair
        pairs_sorted = sorted(
            pairs,
            key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0),
            reverse=True,
        )
        pair = pairs_sorted[0]

        volume = pair.get("volume", {})
        created_ms = pair.get("pairCreatedAt")
        age_hours = None
        if created_ms:
            created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600

        return {
            "volume_h24": float(volume.get("h24", 0) or 0),
            "volume_h6": float(volume.get("h6", 0) or 0),
            "age_hours": age_hours,
        }
    except Exception as e:
        log.debug("DexScreener fetch failed for %s: %s", mint, e)
        return None


# ---------------------------------------------------------------------------
# Stage criteria checks
# ---------------------------------------------------------------------------

def _check_stage1_birth(token_info: dict, snapshots: list[dict], dex_data: dict | None) -> dict:
    """Stage 1 BIRTH: <48h old, <5K holders."""
    criteria_met = []
    criteria_missing = []

    # Age check
    age_hours = None
    if dex_data and dex_data.get("age_hours") is not None:
        age_hours = dex_data["age_hours"]
    elif token_info.get("created_at"):
        created = token_info["created_at"]
        if hasattr(created, "tzinfo") and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600

    if age_hours is not None and age_hours < BIRTH_MAX_AGE_HOURS:
        criteria_met.append(f"age={age_hours:.1f}h (<{BIRTH_MAX_AGE_HOURS}h)")
    else:
        criteria_missing.append(f"age={age_hours:.1f}h (>={BIRTH_MAX_AGE_HOURS}h)" if age_hours else "age unknown")

    # Holder count check
    holder_count = None
    if snapshots:
        holder_count = snapshots[-1].get("holders_raw") or snapshots[-1].get("holders_quality_adjusted")

    if holder_count is not None and holder_count < BIRTH_MAX_HOLDERS:
        criteria_met.append(f"holders={holder_count} (<{BIRTH_MAX_HOLDERS})")
    elif holder_count is not None:
        criteria_missing.append(f"holders={holder_count} (>={BIRTH_MAX_HOLDERS})")
    else:
        criteria_missing.append("holders unknown")

    return {"criteria_met": criteria_met, "criteria_missing": criteria_missing}


def _check_stage2_viral(snapshots: list[dict], dex_data: dict | None) -> dict:
    """Stage 2 VIRAL: volume >5x baseline, holder growth accelerating."""
    criteria_met = []
    criteria_missing = []

    # Volume vs baseline
    volumes = [s.get("volume") for s in snapshots if s.get("volume") is not None]
    if volumes and len(volumes) >= 3:
        baseline = sum(volumes) / len(volumes)
        current_vol = volumes[-1]
        # Also incorporate DexScreener live data
        if dex_data:
            current_vol = max(current_vol, dex_data.get("volume_h24", 0))

        if baseline > 0:
            ratio = current_vol / baseline
            if ratio >= VIRAL_VOLUME_MULTIPLIER:
                criteria_met.append(f"volume={ratio:.1f}x baseline (>{VIRAL_VOLUME_MULTIPLIER}x)")
            else:
                criteria_missing.append(f"volume={ratio:.1f}x baseline (need >{VIRAL_VOLUME_MULTIPLIER}x)")
        else:
            if current_vol > 0:
                criteria_met.append("volume active (baseline=0)")
            else:
                criteria_missing.append("no volume data")
    else:
        criteria_missing.append("insufficient volume history")

    # Holder growth accelerating
    qa_holders = [s.get("holders_quality_adjusted") or s.get("holders_raw")
                  for s in snapshots if (s.get("holders_quality_adjusted") or s.get("holders_raw"))]
    if len(qa_holders) >= 3:
        # Recent growth rate
        recent_growth = (qa_holders[-1] - qa_holders[-2]) / max(qa_holders[-2], 1)
        prior_growth = (qa_holders[-2] - qa_holders[-3]) / max(qa_holders[-3], 1)

        if recent_growth > prior_growth and recent_growth > VIRAL_MIN_GROWTH_RATE:
            criteria_met.append(f"holder_growth accelerating ({recent_growth:.1%} > {prior_growth:.1%})")
        else:
            criteria_missing.append(
                f"holder_growth not accelerating (recent={recent_growth:.1%}, prior={prior_growth:.1%})")
    else:
        criteria_missing.append("insufficient holder history for growth analysis")

    return {"criteria_met": criteria_met, "criteria_missing": criteria_missing}


def _check_stage3_community(snapshots: list[dict]) -> dict:
    """Stage 3 COMMUNITY: 7d retention >40%, buyer/seller >1.2 sustained 3d, median wallet increasing."""
    criteria_met = []
    criteria_missing = []

    # 7d retention >40%
    if snapshots:
        ret_7d = snapshots[-1].get("retention_7d")
        if ret_7d is not None:
            if ret_7d >= COMMUNITY_RETENTION_7D:
                criteria_met.append(f"7d_retention={ret_7d:.0%} (>={COMMUNITY_RETENTION_7D:.0%})")
            else:
                criteria_missing.append(f"7d_retention={ret_7d:.0%} (need >={COMMUNITY_RETENTION_7D:.0%})")
        else:
            # Estimate from holder stability
            if len(snapshots) >= 7:
                holders_now = snapshots[-1].get("holders_quality_adjusted") or 0
                holders_7d = snapshots[-7].get("holders_quality_adjusted") or 0
                if holders_7d > 0:
                    est_retention = holders_now / holders_7d
                    if est_retention >= COMMUNITY_RETENTION_7D:
                        criteria_met.append(
                            f"7d_retention_est={est_retention:.0%} (>={COMMUNITY_RETENTION_7D:.0%})")
                    else:
                        criteria_missing.append(
                            f"7d_retention_est={est_retention:.0%} (need >={COMMUNITY_RETENTION_7D:.0%})")
                else:
                    criteria_missing.append("7d retention data unavailable")
            else:
                criteria_missing.append("insufficient history for 7d retention")
    else:
        criteria_missing.append("no snapshot data for retention")

    # Buyer/seller ratio >1.2 sustained 3 days
    # Proxy: use volume trend as buyer/seller proxy (increasing volume = more buyers)
    if len(snapshots) >= COMMUNITY_SUSTAINED_DAYS:
        recent_days = snapshots[-COMMUNITY_SUSTAINED_DAYS:]
        volumes = [s.get("volume") for s in recent_days if s.get("volume") is not None]
        holders_series = [s.get("holders_quality_adjusted") or s.get("holders_raw")
                          for s in recent_days if (s.get("holders_quality_adjusted") or s.get("holders_raw"))]

        # Check if holders growing consistently (proxy for buyer/seller >1.2)
        if len(holders_series) >= COMMUNITY_SUSTAINED_DAYS:
            all_growing = all(
                holders_series[i] > holders_series[i - 1] * (1 / COMMUNITY_BUYER_SELLER_RATIO)
                for i in range(1, len(holders_series))
            )
            if all_growing:
                criteria_met.append(
                    f"buyer_seller_proxy sustained {COMMUNITY_SUSTAINED_DAYS}d (holders growing)")
            else:
                criteria_missing.append(
                    f"buyer_seller_proxy not sustained {COMMUNITY_SUSTAINED_DAYS}d")
        else:
            criteria_missing.append("insufficient data for buyer/seller ratio")
    else:
        criteria_missing.append(f"need {COMMUNITY_SUSTAINED_DAYS}d of data for sustained metrics")

    # Median wallet increasing
    if len(snapshots) >= 3:
        median_vals = [s.get("median_wallet_balance") for s in snapshots[-3:]
                       if s.get("median_wallet_balance") is not None]
        if len(median_vals) >= 2:
            if median_vals[-1] > median_vals[0]:
                criteria_met.append(
                    f"median_wallet increasing (${median_vals[0]:,.0f} -> ${median_vals[-1]:,.0f})")
            else:
                criteria_missing.append(
                    f"median_wallet not increasing (${median_vals[0]:,.0f} -> ${median_vals[-1]:,.0f})")
        else:
            criteria_missing.append("median wallet data insufficient")
    else:
        criteria_missing.append("insufficient history for median wallet trend")

    return {"criteria_met": criteria_met, "criteria_missing": criteria_missing}


def _check_stage4_adoption(snapshots: list[dict]) -> dict:
    """Stage 4 ADOPTION: fee revenue appearing, dev activity, 30d retention >50%."""
    criteria_met = []
    criteria_missing = []

    # Fee revenue appearing
    if snapshots:
        recent_fees = [s.get("fees") for s in snapshots[-30:] if s.get("fees") is not None]
        recent_revenue = [s.get("revenue") for s in snapshots[-30:] if s.get("revenue") is not None]

        has_fees = recent_fees and any(f > 0 for f in recent_fees)
        has_revenue = recent_revenue and any(r > 0 for r in recent_revenue)

        if has_fees or has_revenue:
            total_fees = sum(f for f in recent_fees if f) if recent_fees else 0
            total_rev = sum(r for r in recent_revenue if r) if recent_revenue else 0
            criteria_met.append(f"fee_revenue active (fees=${total_fees:,.0f}, rev=${total_rev:,.0f} 30d)")
        else:
            criteria_missing.append("no fee revenue detected")
    else:
        criteria_missing.append("no snapshot data for revenue")

    # Dev activity present
    if snapshots:
        dev_data = [s for s in snapshots[-30:] if s.get("dev_commits") is not None or s.get("dev_active")]
        if dev_data:
            total_commits = sum(s.get("dev_commits", 0) or 0 for s in dev_data)
            any_active = any(s.get("dev_active") for s in dev_data)
            if total_commits > 0 or any_active:
                criteria_met.append(f"dev_activity present ({total_commits} commits 30d)")
            else:
                criteria_missing.append("dev data present but no activity")
        else:
            criteria_missing.append("no dev activity data")
    else:
        criteria_missing.append("no snapshot data for dev activity")

    # 30d retention >50%
    if snapshots:
        ret_30d = snapshots[-1].get("retention_30d")
        if ret_30d is not None:
            if ret_30d >= ADOPTION_RETENTION_30D:
                criteria_met.append(f"30d_retention={ret_30d:.0%} (>={ADOPTION_RETENTION_30D:.0%})")
            else:
                criteria_missing.append(f"30d_retention={ret_30d:.0%} (need >={ADOPTION_RETENTION_30D:.0%})")
        else:
            # Estimate from holder stability over 30d
            if len(snapshots) >= 30:
                holders_now = snapshots[-1].get("holders_quality_adjusted") or 0
                holders_30d = snapshots[-30].get("holders_quality_adjusted") or 0
                if holders_30d > 0:
                    est = holders_now / holders_30d
                    if est >= ADOPTION_RETENTION_30D:
                        criteria_met.append(f"30d_retention_est={est:.0%} (>={ADOPTION_RETENTION_30D:.0%})")
                    else:
                        criteria_missing.append(
                            f"30d_retention_est={est:.0%} (need >={ADOPTION_RETENTION_30D:.0%})")
                else:
                    criteria_missing.append("30d retention data unavailable")
            else:
                criteria_missing.append("insufficient history for 30d retention")
    else:
        criteria_missing.append("no snapshot data for retention")

    return {"criteria_met": criteria_met, "criteria_missing": criteria_missing}


def _check_stage5_infrastructure(snapshots: list[dict]) -> dict:
    """Stage 5 INFRASTRUCTURE: revenue >$1M/mo for 3+ months, buyback/burn active,
    treasury >12 months runway."""
    criteria_met = []
    criteria_missing = []

    # Revenue >$1M/month for 3+ months
    if len(snapshots) >= 90:
        monthly_revenues = []
        # Split into 30-day chunks
        for start in range(0, min(len(snapshots), 180), 30):
            chunk = snapshots[start:start + 30]
            rev = sum(s.get("revenue", 0) or 0 for s in chunk)
            monthly_revenues.append(rev)

        qualifying_months = sum(1 for r in monthly_revenues if r >= INFRA_REVENUE_MONTHLY)
        if qualifying_months >= INFRA_REVENUE_MONTHS:
            criteria_met.append(
                f"revenue >=${INFRA_REVENUE_MONTHLY / 1e6:.0f}M/mo for {qualifying_months} months "
                f"(need {INFRA_REVENUE_MONTHS})")
        else:
            criteria_missing.append(
                f"revenue >=${INFRA_REVENUE_MONTHLY / 1e6:.0f}M/mo only {qualifying_months}/{INFRA_REVENUE_MONTHS} months")
    else:
        criteria_missing.append("insufficient history for monthly revenue analysis (need 90+ days)")

    # Buyback/burn active
    # Detect by checking if total supply or circulating supply is decreasing
    if len(snapshots) >= 30:
        mcaps = [s.get("mcap") for s in snapshots if s.get("mcap") is not None]
        volumes = [s.get("volume") for s in snapshots if s.get("volume") is not None]
        # Proxy: sustained high volume + stable/growing price suggests buyback activity
        if len(mcaps) >= 30 and len(volumes) >= 30:
            mcap_trend = mcaps[-1] - mcaps[-30] if len(mcaps) >= 30 else 0
            avg_volume = sum(volumes[-30:]) / 30 if volumes else 0
            if avg_volume > 100_000 and mcap_trend >= 0:
                criteria_met.append(f"buyback_burn_proxy (avg_vol=${avg_volume:,.0f}/d, mcap stable/growing)")
            else:
                criteria_missing.append("no buyback/burn signals detected")
        else:
            criteria_missing.append("insufficient data for buyback/burn analysis")
    else:
        criteria_missing.append("insufficient history for buyback/burn analysis")

    # Treasury >12 months runway
    if snapshots:
        recent_revenue = [s.get("revenue", 0) or 0 for s in snapshots[-30:]]
        monthly_rev = sum(recent_revenue)
        mcap = snapshots[-1].get("mcap") or 0

        if monthly_rev > 0 and mcap > 0:
            annual_rev = monthly_rev * 12
            runway_ratio = annual_rev / mcap
            # High revenue/mcap ratio suggests long runway
            estimated_runway = runway_ratio * 12  # rough months estimate
            if estimated_runway >= INFRA_TREASURY_RUNWAY_MONTHS:
                criteria_met.append(f"treasury_runway_est ~{estimated_runway:.0f} months (>={INFRA_TREASURY_RUNWAY_MONTHS})")
            else:
                criteria_missing.append(
                    f"treasury_runway_est ~{estimated_runway:.0f} months (need >={INFRA_TREASURY_RUNWAY_MONTHS})")
        else:
            criteria_missing.append("insufficient revenue/mcap data for runway estimate")
    else:
        criteria_missing.append("no snapshot data for treasury analysis")

    return {"criteria_met": criteria_met, "criteria_missing": criteria_missing}


# ---------------------------------------------------------------------------
# Main stage detection
# ---------------------------------------------------------------------------

def detect_stage(token_id: int, mint: str) -> dict:
    """Detect the current lifecycle stage of a token.

    Evaluates criteria for all stages top-down (5 -> 1) and returns the highest
    stage where ALL criteria are met.

    Args:
        token_id: database token ID
        mint: Solana token contract address

    Returns:
        {
            "stage": int (1-5),
            "stage_name": str,
            "criteria_met": list[str],
            "criteria_missing": list[str],
            "promotion_ready": bool,
            "current_db_stage": int,
        }
    """
    token_info = _get_token_info(token_id)
    if not token_info:
        return {
            "stage": 1,
            "stage_name": "BIRTH",
            "criteria_met": [],
            "criteria_missing": ["token not found in DB"],
            "promotion_ready": False,
            "current_db_stage": 1,
        }

    snapshots = _get_snapshots(token_id, days=180)
    dex_data = _fetch_dexscreener_data(mint)
    current_db_stage = token_info.get("lifecycle_stage", 1)

    # Evaluate all stages (highest first)
    stage_checks = {
        5: _check_stage5_infrastructure(snapshots),
        4: _check_stage4_adoption(snapshots),
        3: _check_stage3_community(snapshots),
        2: _check_stage2_viral(snapshots, dex_data),
        1: _check_stage1_birth(token_info, snapshots, dex_data),
    }

    # Find the highest stage where all criteria are met (no missing criteria)
    detected_stage = 1
    detected_criteria_met = []
    detected_criteria_missing = []

    for stage_num in [5, 4, 3, 2, 1]:
        check_result = stage_checks[stage_num]
        if check_result["criteria_met"] and not check_result["criteria_missing"]:
            detected_stage = stage_num
            detected_criteria_met = check_result["criteria_met"]
            detected_criteria_missing = []
            break

    # If no stage fully qualifies, find the highest partially met stage
    if detected_stage == 1 and stage_checks[1].get("criteria_missing"):
        # Even stage 1 isn't fully met, still report stage 1 with missing criteria
        detected_criteria_met = stage_checks[1]["criteria_met"]
        detected_criteria_missing = stage_checks[1]["criteria_missing"]

    # Check for promotion readiness: detected stage > current DB stage
    promotion_ready = detected_stage > current_db_stage

    # Also collect what's needed for the NEXT stage above detected
    next_stage = detected_stage + 1
    if next_stage <= 5 and next_stage in stage_checks:
        next_check = stage_checks[next_stage]
        # Append next-stage missing as context
        if next_check["criteria_missing"]:
            detected_criteria_missing = next_check["criteria_missing"]

    result = {
        "stage": detected_stage,
        "stage_name": STAGE_NAMES.get(detected_stage, "UNKNOWN"),
        "criteria_met": detected_criteria_met,
        "criteria_missing": detected_criteria_missing,
        "promotion_ready": promotion_ready,
        "current_db_stage": current_db_stage,
    }

    log.info("Lifecycle stage for token_id=%d (%s): Stage %d %s (db=%d, promotion_ready=%s)",
             token_id, mint, detected_stage, STAGE_NAMES.get(detected_stage),
             current_db_stage, promotion_ready)

    return result


# ---------------------------------------------------------------------------
# Promotion management
# ---------------------------------------------------------------------------

def check_promotion_candidates() -> list[dict]:
    """Find all tokens ready for stage promotion.

    Returns list of dicts with token info and promotion reasons.
    """
    candidates = []

    try:
        rows = execute(
            """SELECT id, contract_address, symbol, lifecycle_stage, stage_entered_at
               FROM tokens
               WHERE quality_gate_pass = TRUE
                 AND lifecycle_stage < 5
               ORDER BY lifecycle_stage ASC, updated_at DESC""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to fetch tokens for promotion check: %s", e)
        return candidates

    if not rows:
        log.info("No tokens eligible for promotion check")
        return candidates

    log.info("Checking %d tokens for lifecycle promotion", len(rows))

    for row in rows:
        token_id, mint, symbol, current_stage, stage_entered = row[0], row[1], row[2], row[3] or 1, row[4]

        try:
            detection = detect_stage(token_id, mint)

            if detection["promotion_ready"]:
                candidate = {
                    "token_id": token_id,
                    "mint": mint,
                    "symbol": symbol or mint[:8],
                    "current_stage": current_stage,
                    "current_stage_name": STAGE_NAMES.get(current_stage, "UNKNOWN"),
                    "proposed_stage": detection["stage"],
                    "proposed_stage_name": detection["stage_name"],
                    "criteria_met": detection["criteria_met"],
                    "stage_entered_at": stage_entered.isoformat() if stage_entered else None,
                }
                candidates.append(candidate)
                log.info("Promotion candidate: %s (%s) Stage %d -> %d",
                         symbol or mint[:8], mint, current_stage, detection["stage"])

        except Exception as e:
            log.error("Promotion check failed for token_id=%d: %s", token_id, e)

    log.info("Found %d promotion candidates", len(candidates))
    return candidates


def promote_token(token_id: int, new_stage: int, reason: str) -> bool:
    """Promote a token to a new lifecycle stage.

    Updates DB, records promotion history, and sends Telegram alert.

    Args:
        token_id: database token ID
        new_stage: target stage number (1-5)
        reason: human-readable promotion reason

    Returns:
        True if promotion succeeded.
    """
    if new_stage not in STAGE_NAMES:
        log.error("Invalid stage %d for token_id=%d", new_stage, token_id)
        return False

    try:
        # Fetch current state
        token_info = _get_token_info(token_id)
        if not token_info:
            log.error("Token not found for promotion: token_id=%d", token_id)
            return False

        old_stage = token_info.get("lifecycle_stage", 1)
        mint = token_info["contract_address"]
        symbol = token_info.get("symbol") or mint[:8]

        if new_stage <= old_stage:
            log.warning("Promotion skipped: token_id=%d already at stage %d, requested %d",
                        token_id, old_stage, new_stage)
            return False

        # Build promotion history entry
        promotion_entry = {
            "from_stage": old_stage,
            "from_stage_name": STAGE_NAMES.get(old_stage, "UNKNOWN"),
            "to_stage": new_stage,
            "to_stage_name": STAGE_NAMES.get(new_stage, "UNKNOWN"),
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Update token record
        execute(
            """UPDATE tokens
               SET lifecycle_stage = %s,
                   stage_entered_at = NOW(),
                   promotion_history = COALESCE(promotion_history, '[]'::jsonb) || %s::jsonb,
                   updated_at = NOW()
               WHERE id = %s""",
            (new_stage, json.dumps([promotion_entry]), token_id)
        )

        log.info("Promoted token_id=%d (%s) from Stage %d %s -> Stage %d %s: %s",
                 token_id, symbol, old_stage, STAGE_NAMES.get(old_stage),
                 new_stage, STAGE_NAMES.get(new_stage), reason)

        # Record alert
        execute(
            """INSERT INTO alerts (token_id, type, severity, feature_vector_json)
               VALUES (%s, %s, %s, %s)""",
            (token_id, "lifecycle_promotion", "info",
             json.dumps(promotion_entry, default=str))
        )

        # Send Telegram alert
        _send_promotion_alert(token_id, symbol, mint, old_stage, new_stage, reason)

        return True

    except Exception as e:
        log.error("Failed to promote token_id=%d to stage %d: %s", token_id, new_stage, e)
        return False


def _send_promotion_alert(token_id: int, symbol: str, mint: str,
                          old_stage: int, new_stage: int, reason: str):
    """Send Telegram alert for lifecycle promotion."""
    try:
        from telegram_bot.alerts import send_message

        old_name = STAGE_NAMES.get(old_stage, "UNKNOWN")
        new_name = STAGE_NAMES.get(new_stage, "UNKNOWN")

        # Special flag for Stage 4 promotion
        promotion_flag = ""
        if new_stage >= 4:
            promotion_flag = "\n\U0001f393 <b>PROMOTION CANDIDATE</b>"

        message = (
            f"\U0001f4c8 <b>LIFECYCLE PROMOTION</b>\n"
            f"Token: <code>{symbol}</code> (<code>{mint[:12]}...</code>)\n"
            f"Stage {old_stage} {old_name} -> Stage {new_stage} {new_name}\n"
            f"Reason: {reason}"
            f"{promotion_flag}"
        )

        send_message(message)
    except Exception as e:
        log.warning("Failed to send lifecycle promotion alert: %s", e)


# ---------------------------------------------------------------------------
# Lifecycle summary
# ---------------------------------------------------------------------------

def get_lifecycle_summary() -> dict:
    """Get summary of all tokens by lifecycle stage.

    Returns:
        {
            "counts_per_stage": {1: int, 2: int, 3: int, 4: int, 5: int},
            "total_tracked": int,
            "recent_transitions": [
                {
                    "token_id": int,
                    "symbol": str,
                    "from_stage": int,
                    "to_stage": int,
                    "timestamp": str,
                }
            ],
            "stage_breakdown": {
                1: {"name": "BIRTH", "count": int, "tokens": [str]},
                ...
            }
        }
    """
    result = {
        "counts_per_stage": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
        "total_tracked": 0,
        "recent_transitions": [],
        "stage_breakdown": {},
    }

    try:
        # Counts per stage
        rows = execute(
            """SELECT COALESCE(lifecycle_stage, 1) AS stage, COUNT(*) AS cnt
               FROM tokens
               WHERE quality_gate_pass = TRUE
               GROUP BY COALESCE(lifecycle_stage, 1)
               ORDER BY stage""",
            fetch=True,
        )
        for row in rows:
            stage, count = row[0], row[1]
            result["counts_per_stage"][stage] = count
            result["total_tracked"] += count

        # Stage breakdown with token symbols
        for stage_num in range(1, 6):
            token_rows = execute(
                """SELECT symbol, contract_address
                   FROM tokens
                   WHERE quality_gate_pass = TRUE
                     AND COALESCE(lifecycle_stage, 1) = %s
                   ORDER BY updated_at DESC
                   LIMIT 20""",
                (stage_num,),
                fetch=True,
            )
            tokens_list = [r[0] or r[1][:8] for r in (token_rows or [])]
            result["stage_breakdown"][stage_num] = {
                "name": STAGE_NAMES.get(stage_num, "UNKNOWN"),
                "count": result["counts_per_stage"].get(stage_num, 0),
                "tokens": tokens_list,
            }

        # Recent transitions (from alerts table)
        transition_rows = execute(
            """SELECT a.token_id, t.symbol, a.feature_vector_json, a.created_at
               FROM alerts a
               JOIN tokens t ON t.id = a.token_id
               WHERE a.type = 'lifecycle_promotion'
               ORDER BY a.created_at DESC
               LIMIT 10""",
            fetch=True,
        )
        for row in (transition_rows or []):
            token_id, symbol, feature_json, created_at = row[0], row[1], row[2], row[3]
            try:
                details = json.loads(feature_json) if isinstance(feature_json, str) else feature_json
            except (json.JSONDecodeError, TypeError):
                details = {}

            result["recent_transitions"].append({
                "token_id": token_id,
                "symbol": symbol or "???",
                "from_stage": details.get("from_stage"),
                "to_stage": details.get("to_stage"),
                "reason": details.get("reason", ""),
                "timestamp": created_at.isoformat() if created_at else None,
            })

    except Exception as e:
        log.error("Failed to build lifecycle summary: %s", e)

    log.info("Lifecycle summary: %d tokens tracked, stages=%s",
             result["total_tracked"], result["counts_per_stage"])

    return result
