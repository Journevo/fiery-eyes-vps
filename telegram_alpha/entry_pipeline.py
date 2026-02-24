"""Entry Pipeline — handles all entry types with appropriate sizing.

Door 1A: KK Call        -> 15% immediate, scale on confirmation
Door 1B: Tier 1 KOL     -> 30/30/40 tiered
Door 1C: KOLScan Conv   -> 30/30/40 after manual confirm
Door 2:  Organic        -> needs 2+ signals, conviction multiplier

Shadow mode: logs trades only, no real execution.
"""

import os
from datetime import datetime, timezone
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("telegram_alpha.entry_pipeline")

SHADOW_MODE = os.getenv('SHADOW_MODE', 'true').lower() == 'true'

# Phase sizing table
PHASE_SIZING = {
    'kk_call': {
        'phase1': 15, 'phase2': 25, 'phase3': 40,  # total 80%
        'requires_confirmation': True,
    },
    'kk_call_flags': {
        'phase1': 0, 'phase2': 0, 'phase3': 0,  # alert only
        'requires_confirmation': True,
    },
    'kol_wallet': {
        'phase1': 30, 'phase2': 30, 'phase3': 40,  # total 100%
        'requires_confirmation': False,
    },
    'kolscan_convergence': {
        'phase1': 30, 'phase2': 30, 'phase3': 40,  # total 100%
        'requires_confirmation': True,  # manual confirm
    },
    'organic_2': {
        'phase1': 25, 'phase2': 25, 'phase3': 0,  # total 50%
        'requires_confirmation': True,
    },
    'organic_3': {
        'phase1': 30, 'phase2': 30, 'phase3': 40,  # total 100%
        'requires_confirmation': True,
    },
}


def execute_entry(token_address: str, entry_type: str,
                  token_data: dict | None = None) -> dict:
    """Execute an entry trade (or shadow log it).

    Args:
        token_address: Solana token mint address
        entry_type: 'kk_call', 'kol_wallet', 'kolscan_convergence', 'organic'
        token_data: Optional dict with symbol, price, mcap, etc.

    Returns: {trade_id, entry_type, phase, size_pct, price, status}
    """
    token_data = token_data or {}
    symbol = token_data.get('symbol')

    # Determine actual entry type (for organic, check signal count)
    actual_type = entry_type
    if entry_type == 'organic':
        signal_count = check_organic_conviction(token_address)
        if signal_count < 2:
            log.info("Organic entry for %s rejected — only %d signals (need 2+)",
                     symbol or token_address[:12], signal_count)
            return {'trade_id': None, 'entry_type': entry_type, 'phase': 0,
                    'size_pct': 0, 'status': 'rejected', 'reason': 'insufficient_signals'}
        actual_type = 'organic_3' if signal_count >= 3 else 'organic_2'

    sizing = PHASE_SIZING.get(actual_type, PHASE_SIZING['kk_call'])
    phase1_pct = sizing['phase1']

    if phase1_pct == 0:
        log.info("Entry type %s has 0%% phase 1 — alert only", actual_type)
        return {'trade_id': None, 'entry_type': entry_type, 'phase': 0,
                'size_pct': 0, 'status': 'alert_only'}

    # Get current price and health score
    price = token_data.get('price', 0)
    mcap = token_data.get('mcap', 0)
    health_score = None
    confidence = None

    if not price:
        try:
            from quality_gate.helpers import get_json
            data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            pairs = data.get("pairs", [])
            if pairs:
                pair = pairs[0]
                price = float(pair.get("priceUsd", 0) or 0)
                mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
                if not symbol:
                    symbol = pair.get("baseToken", {}).get("symbol")
        except Exception:
            pass

    try:
        from health_score.engine import score_token
        hs = score_token(token_address, symbol)
        health_score = hs.get('scaled_score')
        confidence = hs.get('confidence_pct')
    except Exception:
        pass

    # Build entry reason
    kol_name = token_data.get('kol_name', '')
    entry_reason = _build_entry_reason(entry_type, kol_name, token_data)

    if SHADOW_MODE:
        # Shadow trade — log only
        from shadow.tracker import open_shadow_trade
        trade_id = open_shadow_trade(
            token_address=token_address,
            entry_source=entry_type,
            entry_reason=entry_reason,
            entry_price=price,
            entry_mcap=mcap,
            health_score=health_score,
            confidence=confidence,
            position_size_pct=phase1_pct,
        )
        log.info("SHADOW ENTRY: %s %s phase1=%d%% price=%.8f",
                 symbol or token_address[:12], entry_type, phase1_pct, price)
    else:
        # TODO: Real execution via Jupiter swap
        trade_id = None
        log.warning("LIVE EXECUTION not implemented — use shadow mode")

    # Update token record
    try:
        execute(
            """UPDATE tokens SET
                 entry_source = %s,
                 kol_trigger_wallet = %s,
                 updated_at = NOW()
               WHERE contract_address = %s""",
            (entry_type, token_data.get('kol_wallet'), token_address),
        )
    except Exception:
        pass

    # Send entry alert
    try:
        from telegram_bot.severity import route_alert
        mode = "SHADOW" if SHADOW_MODE else "LIVE"
        msg = (f"⚡ <b>ENTRY [{mode}]</b>\n"
               f"🪙 ${symbol or token_address[:12]}\n"
               f"📊 Type: {entry_type}\n"
               f"📐 Phase 1: {phase1_pct}%\n"
               f"💰 Price: ${price:.8f}" if price < 1 else
               f"⚡ <b>ENTRY [{mode}]</b>\n"
               f"🪙 ${symbol or token_address[:12]}\n"
               f"📊 Type: {entry_type}\n"
               f"📐 Phase 1: {phase1_pct}%\n"
               f"💰 Price: ${price:.4f}")
        if health_score is not None:
            msg += f"\n🏥 Health: {health_score:.0f}/100"
        route_alert(2, msg)
    except Exception:
        pass

    return {
        'trade_id': trade_id,
        'entry_type': entry_type,
        'phase': 1,
        'size_pct': phase1_pct,
        'price': price,
        'status': 'shadow' if SHADOW_MODE else 'live',
    }


def execute_phase2(trade_id: int):
    """Execute phase 2 after confirmation window."""
    try:
        row = execute_one(
            """SELECT token_address, token_symbol, entry_source, position_size_pct
               FROM shadow_trades WHERE id = %s""",
            (trade_id,),
        )
        if not row:
            log.error("Trade %d not found for phase 2", trade_id)
            return

        token_address, symbol, entry_source, current_pct = row
        sizing = PHASE_SIZING.get(entry_source, PHASE_SIZING['kk_call'])
        phase2_pct = sizing['phase2']

        if phase2_pct == 0:
            return

        new_total = float(current_pct or 0) + phase2_pct

        execute(
            """UPDATE shadow_trades SET
                 position_size_pct = %s,
                 phases_entered = 2,
                 confirmation_received = TRUE,
                 notes = COALESCE(notes, '') || ' | Phase 2 at ' || NOW()::text
               WHERE id = %s""",
            (new_total, trade_id),
        )

        log.info("PHASE 2: %s +%d%% (total %d%%)",
                 symbol or token_address[:12], phase2_pct, new_total)

        # Schedule phase 3 at +1h if applicable
        if sizing['phase3'] > 0:
            import threading

            def _phase3():
                import time
                time.sleep(3600)
                execute_phase3(trade_id)

            threading.Thread(target=_phase3, daemon=True).start()

    except Exception as e:
        log.error("Phase 2 execution failed: %s", e)


def execute_phase3(trade_id: int):
    """Execute phase 3 at +1h if still holding above entry."""
    try:
        row = execute_one(
            """SELECT token_address, token_symbol, entry_source,
                      entry_price, position_size_pct
               FROM shadow_trades WHERE id = %s AND status = 'open'""",
            (trade_id,),
        )
        if not row:
            return

        token_address, symbol, entry_source, entry_price, current_pct = row
        sizing = PHASE_SIZING.get(entry_source, PHASE_SIZING['kk_call'])
        phase3_pct = sizing['phase3']

        if phase3_pct == 0:
            return

        # Check if still above entry
        try:
            from quality_gate.helpers import get_json
            data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            pairs = data.get("pairs", [])
            if pairs:
                current_price = float(pairs[0].get("priceUsd", 0) or 0)
                if entry_price and current_price < float(entry_price):
                    log.info("Phase 3 skipped for %s — below entry price",
                             symbol or token_address[:12])
                    return
        except Exception:
            pass

        new_total = float(current_pct or 0) + phase3_pct
        execute(
            """UPDATE shadow_trades SET
                 position_size_pct = %s,
                 phases_entered = 3,
                 notes = COALESCE(notes, '') || ' | Phase 3 at ' || NOW()::text
               WHERE id = %s""",
            (new_total, trade_id),
        )
        log.info("PHASE 3: %s +%d%% (total %d%%)",
                 symbol or token_address[:12], phase3_pct, new_total)

    except Exception as e:
        log.error("Phase 3 execution failed: %s", e)


def check_organic_conviction(token_address: str) -> int:
    """Count independent conviction signals for organic entry.

    Signals:
    - KOL wallet buy = 1
    - X mentions rising = 1
    - DexScreener trending = 1
    - KK mentioned = 1

    Returns signal count. Need 2+ for organic entry.
    """
    signals = 0

    # Signal 1: KOL wallet buy
    try:
        row = execute_one(
            """SELECT COUNT(DISTINCT kol_wallet_id) FROM kol_transactions
               WHERE token_address = %s AND action = 'buy'
                 AND is_conviction_buy = TRUE
                 AND detected_at > NOW() - INTERVAL '24 hours'""",
            (token_address,),
        )
        if row and row[0] and row[0] > 0:
            signals += 1
    except Exception:
        pass

    # Signal 2: DexScreener trending (use volume as proxy)
    try:
        from quality_gate.helpers import get_json
        data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs", [])
        if pairs:
            boosts = pairs[0].get("boosts", {})
            if boosts and boosts.get("active", 0) > 0:
                signals += 1
    except Exception:
        pass

    # Signal 3: KK mentioned
    try:
        row = execute_one(
            """SELECT COUNT(*) FROM telegram_calls
               WHERE token_address = %s
                 AND detected_at > NOW() - INTERVAL '24 hours'""",
            (token_address,),
        )
        if row and row[0] and row[0] > 0:
            signals += 1
    except Exception:
        pass

    # Signal 4: Social pulse high
    try:
        from health_score.social_signal import score_social
        social_score, _, details = score_social(token_address)
        if details.get('high_conviction'):
            signals += 1
    except Exception:
        pass

    return signals


def _build_entry_reason(entry_type: str, kol_name: str, token_data: dict) -> str:
    """Build a human-readable entry reason."""
    if entry_type == 'kk_call':
        return "Krypto King call — clean safety"
    elif entry_type == 'kol_wallet':
        return f"Tier 1 KOL {kol_name} conviction buy" + (
            f" (${token_data.get('amount_usd', 0):,.0f})" if token_data.get('amount_usd') else "")
    elif entry_type == 'kolscan_convergence':
        return "KOLScan convergence — 3+ wallets"
    elif entry_type == 'organic':
        return "Organic discovery — multi-signal confirmation"
    return entry_type
