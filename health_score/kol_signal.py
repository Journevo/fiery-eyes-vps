"""Whale & KOL Behavior Signal — 20 points.

Source: kol_wallets + kol_transactions tables.

Sub-scores:
  - Triggering KOL status: /8
  - Other smart money:     /6
  - Early holder behavior: /3
  - Dev wallet:            /3

OVERRIDE: if any Tier 1 KOL sells >50% of position -> score = 0/20 AND set kol_exit flag.
"""

from datetime import datetime, timezone, timedelta
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("health_score.kol")


def score_kol(token_address: str) -> tuple[float, str, dict]:
    """Score KOL/whale behavior for a token.

    Returns: (score: float /20, data_state: str, details: dict)
    """
    details = {'kol_exit': False}

    # Check if we have any KOL transaction data for this token
    try:
        rows = execute(
            """SELECT kt.kol_wallet_id, kw.name, kw.tier, kt.action,
                      kt.amount_usd, kt.token_amount, kt.detected_at,
                      kt.is_conviction_buy
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.token_address = %s
               ORDER BY kt.detected_at DESC""",
            (token_address,),
            fetch=True,
        )
    except Exception as e:
        log.debug("KOL query failed (tables may not exist yet): %s", e)
        # No KOL tracking for this token — neutral default
        return 10.0, 'missing', {**details, 'reason': 'no_kol_data'}

    if not rows:
        # No KOL has interacted with this token
        return 10.0, 'missing', {**details, 'reason': 'no_kol_transactions'}

    # Organize by wallet
    wallets = {}
    for wallet_id, name, tier, action, amount_usd, token_amount, detected_at, is_conviction in rows:
        if wallet_id not in wallets:
            wallets[wallet_id] = {
                'name': name, 'tier': tier, 'buys': [], 'sells': [],
                'total_bought': 0, 'total_sold': 0,
            }
        entry = {
            'amount_usd': float(amount_usd or 0),
            'token_amount': float(token_amount or 0),
            'detected_at': detected_at,
            'is_conviction': is_conviction,
        }
        if action == 'buy':
            wallets[wallet_id]['buys'].append(entry)
            wallets[wallet_id]['total_bought'] += entry['token_amount']
        else:
            wallets[wallet_id]['sells'].append(entry)
            wallets[wallet_id]['total_sold'] += entry['token_amount']

    # Check data freshness
    most_recent = max(r[6] for r in rows if r[6])
    if most_recent:
        age_hours = (datetime.now(timezone.utc) - most_recent.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if age_hours > 1:
            data_state = 'stale'
        else:
            data_state = 'live'
    else:
        data_state = 'stale'

    # CHECK OVERRIDE: any Tier 1 KOL sold >50%
    for wid, w in wallets.items():
        if w['tier'] == 1 and w['total_bought'] > 0:
            pct_sold = w['total_sold'] / w['total_bought'] * 100
            if pct_sold > 50:
                details['kol_exit'] = True
                details['exit_kol'] = w['name']
                details['exit_pct_sold'] = round(pct_sold, 1)
                log.warning("KOL EXIT: %s sold %.0f%% of %s",
                            w['name'], pct_sold, token_address[:12])
                return 0.0, data_state, details

    # Sub-score 1: Triggering KOL status (/8)
    # Find the KOL that triggered (first buyer or highest conviction)
    triggering_kol = None
    trigger_status = 'none'
    for wid, w in wallets.items():
        if w['buys']:
            triggering_kol = w['name']
            total_bought = w['total_bought']
            total_sold = w['total_sold']
            if total_bought > 0:
                pct_sold = total_sold / total_bought * 100
            else:
                pct_sold = 0

            if w['sells'] == [] and w['buys']:
                # Still adding or holding
                most_recent_buy = max(b['detected_at'] for b in w['buys'])
                age = (datetime.now(timezone.utc) - most_recent_buy.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if age < 2:
                    trigger_status = 'adding'
                else:
                    trigger_status = 'holding'
            elif pct_sold < 30:
                trigger_status = 'holding'
            elif pct_sold < 50:
                trigger_status = 'partial_sell'
            else:
                trigger_status = 'selling'
            break  # use first found

    status_scores = {
        'adding': 8,
        'holding': 6,
        'partial_sell': 2,
        'selling': 0,
        'none': 4,  # N/A
    }
    sub1 = status_scores.get(trigger_status, 4)
    details['triggering_kol'] = triggering_kol
    details['trigger_status'] = trigger_status
    details['sub1_trigger_kol'] = sub1

    # Sub-score 2: Other smart money (/6)
    unique_buyers = len([w for w in wallets.values() if w['buys']])
    unique_sellers = len([w for w in wallets.values() if w['sells']])

    if unique_buyers >= 3 and unique_sellers == 0:
        sub2 = 6  # accumulating
    elif unique_buyers >= 2:
        sub2 = 4  # stable
    elif unique_sellers > unique_buyers:
        sub2 = 1  # distributing
    else:
        sub2 = 3  # mixed

    details['unique_kol_buyers'] = unique_buyers
    details['unique_kol_sellers'] = unique_sellers
    details['sub2_smart_money'] = sub2

    # Sub-score 3: Early holder behavior (/3)
    # Check if first buyers are still holding
    conviction_buys = [w for w in wallets.values()
                       if any(b['is_conviction'] for b in w['buys'])]
    if conviction_buys:
        holding_count = sum(1 for w in conviction_buys if w['total_sold'] < w['total_bought'] * 0.3)
        holding_pct = holding_count / len(conviction_buys) * 100
        if holding_pct > 70:
            sub3 = 3
        elif holding_pct > 40:
            sub3 = 2
        else:
            sub3 = 0
    else:
        sub3 = 1  # no conviction buy data, neutral-low

    details['sub3_early_holders'] = sub3

    # Sub-score 4: Dev wallet (/3)
    # We don't track dev wallets specifically yet — neutral default
    sub4 = 2
    details['sub4_dev_wallet'] = sub4
    details['dev_wallet_note'] = 'not_tracked'

    total_score = sub1 + sub2 + sub3 + sub4
    total_score = round(min(20, max(0, total_score)), 1)

    details['wallets_holding'] = unique_buyers

    return total_score, data_state, details
