"""4-Tier Alert Severity System.

Tier 1: ACT NOW (sound on) — max 2/day target
   - KOL Tier 1 wallet buy (auto-executing)
   - KOL exit >50%
   - Position CRITICAL health (<25, confidence >70%)
   - System failure

Tier 2: CHECK WHEN FREE (notification, no sound) — max 5/day
   - New token passed gate + health >65
   - Position state change (HEALTHY->COOLING)
   - KOLScan convergence
   - KK call parsed
   - Regime change

Tier 3: BATCHED IN HUOYAN (no standalone alert)
   - Scanner counts
   - Social velocity changes
   - Holder shifts
   - YouTube mentions
   - Unlock approaching 7+ days

Tier 4: LOGGED ONLY
   - Failed gate tokens
   - API latency
   - Stale data flags
"""

import os
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

log = get_logger("telegram_bot.severity")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Channel IDs with fallback
HFIRE_CHAT_ID = os.getenv('TELEGRAM_HFIRE_CHAT_ID', TELEGRAM_CHAT_ID)
HUOYAN_CHAT_ID = os.getenv('TELEGRAM_HUOYAN_CHAT_ID', TELEGRAM_CHAT_ID)
SYSTEM_CHAT_ID = os.getenv('TELEGRAM_SYSTEM_CHAT_ID', TELEGRAM_CHAT_ID)

# Tier 3 batch buffer
_huoyan_batch: list[str] = []


def classify_alert(alert_type: str, **kwargs) -> int:
    """Classify an alert into tier 1-4.

    Args:
        alert_type: Type of alert event
        **kwargs: Additional context (health_score, confidence, tier, etc.)

    Returns: tier (1-4)
    """
    # Tier 1: ACT NOW
    if alert_type in ('kol_tier1_buy', 'kol_exit_major'):
        return 1
    if alert_type == 'health_critical':
        score = kwargs.get('health_score', 100)
        confidence = kwargs.get('confidence', 0)
        if score < 25 and confidence > 70:
            return 1
    if alert_type == 'system_failure':
        return 1

    # Tier 2: CHECK WHEN FREE
    if alert_type in ('gate_pass_scored', 'state_change', 'kolscan_convergence',
                       'kk_call', 'regime_change', 'entry_executed',
                       'kol_tier2_buy', 'health_warning',
                       'smart_money_strong_accumulation',
                       'smart_money_strong_whale_flow',
                       'smart_money_multi_kol'):
        return 2
    if alert_type == 'gate_pass' and kwargs.get('health_score', 0) > 65:
        return 2

    # Tier 3: BATCHED
    if alert_type in ('scanner_count', 'social_velocity', 'holder_shift',
                       'youtube_mention', 'unlock_approaching', 'price_milestone',
                       'kol_tier2_activity', 'smart_money_medium_signal'):
        return 3

    # Tier 4: LOGGED ONLY
    return 4


def route_alert(tier: int, message: str) -> str:
    """Route an alert to the correct channel based on tier.

    Tier 1-2: H-Fire channel
    Tier 3:   Batched for Huoyan pulse
    Tier 4:   Database only (logged)

    Returns: channel sent to, or 'logged'/'batched'
    """
    if tier <= 2:
        # Send to H-Fire channel
        chat_id = HFIRE_CHAT_ID or TELEGRAM_CHAT_ID
        disable_notification = (tier == 2)  # Tier 2 = silent notification
        success = _send_to_channel(chat_id, message, disable_notification)
        if success:
            log.info("Tier %d alert sent to H-Fire", tier)
            return 'hfire'
        else:
            log.error("Failed to send tier %d alert", tier)
            return 'failed'

    elif tier == 3:
        # Batch for Huoyan
        _huoyan_batch.append(message)
        log.debug("Tier 3 alert batched for Huoyan (%d in batch)", len(_huoyan_batch))
        return 'batched'

    else:
        # Tier 4: log only
        log.info("Tier 4 (logged): %s", message[:100])
        return 'logged'


def flush_huoyan_batch() -> list[str]:
    """Get and clear the batched Tier 3 alerts for Huoyan pulse."""
    global _huoyan_batch
    batch = _huoyan_batch.copy()
    _huoyan_batch = []
    return batch


def format_health_alert(score_data: dict) -> str:
    """Format a health score for Telegram alert.

    Includes confidence indicator:
    - High confidence: "Health: 72/100 (94% conf)"
    - Low confidence: "Health: ~72*/100 (58% conf -- social missing) -- AUTO DISABLED"
    """
    score = score_data.get('scaled_score', 0)
    confidence = score_data.get('confidence_pct', 0)
    symbol = score_data.get('token_symbol', '???')
    action = score_data.get('recommended_action', 'unknown')

    # Score emoji
    if score >= 80:
        emoji = "🟢"
    elif score >= 65:
        emoji = "🟡"
    elif score >= 50:
        emoji = "🟠"
    elif score >= 35:
        emoji = "🔴"
    else:
        emoji = "💀"

    # Build confidence note
    missing_signals = []
    for signal in ['volume', 'price', 'kol', 'social', 'holders']:
        state = score_data.get(f'{signal}_data_state', 'missing')
        if state == 'missing':
            missing_signals.append(signal)
        elif state == 'stale':
            missing_signals.append(f"{signal} stale")

    if confidence >= 80:
        conf_str = f"({confidence:.0f}% conf)"
        score_str = f"{score:.0f}"
    elif confidence >= 60:
        conf_str = f"({confidence:.0f}% conf"
        if missing_signals:
            conf_str += f" — {', '.join(missing_signals[:2])} missing"
        conf_str += ")"
        score_str = f"{score:.0f}"
    else:
        conf_str = f"({confidence:.0f}% conf"
        if missing_signals:
            conf_str += f" — {', '.join(missing_signals[:2])} missing"
        conf_str += ") — AUTO DISABLED"
        score_str = f"~{score:.0f}*"

    auto_str = "auto" if score_data.get('auto_action_enabled') else "manual"

    return (f"{emoji} ${symbol} Health: {score_str}/100 {conf_str}\n"
            f"   Action: {action} [{auto_str}]")


def _send_to_channel(chat_id: str, text: str,
                     disable_notification: bool = False) -> bool:
    """Send message to a specific Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        log.warning("Telegram not configured for channel %s", chat_id)
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": disable_notification,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Failed to send to channel %s: %s", chat_id, e)
        return False
