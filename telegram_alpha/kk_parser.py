"""Krypto King Telegram Parser.

Monitors KK's Telegram channel for token calls.
Detects: Solana contract addresses (base58, 32-44 chars), cashtags ($TOKEN), buy language.
On detection: DexScreener lookup -> Safety Gate -> alert/auto-enter.

REQUIRES: Telethon library + user session
  pip install telethon

REQUIRES in .env:
  TELEGRAM_API_ID=<your telegram api id>
  TELEGRAM_API_HASH=<your telegram api hash>
  TELEGRAM_PHONE=<your phone number>
  KK_CHANNEL_ID=<krypto king channel/group id>

First-time setup:
  python -m telegram_alpha.kk_parser
  (Will prompt for phone verification code interactively)
"""

import re
import asyncio
import threading
from config import get_logger
from db.connection import execute

log = get_logger("telegram_alpha.kk_parser")

# Solana address regex: base58 characters, 32-44 chars
SOLANA_ADDRESS_RE = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')

# Cashtag regex: $TOKEN
CASHTAG_RE = re.compile(r'\$([A-Z]{2,10})\b')

# Buy language indicators
BUY_KEYWORDS = {'buy', 'ape', 'aped', 'aping', 'send it', 'sending', 'loading',
                'loaded', 'entry', 'entered', 'buying', 'long', 'bullish',
                'accumulate', 'accumulating', 'bid', 'bidding', 'scooping'}


def parse_message(message_text: str) -> list[str]:
    """Extract Solana contract addresses from a message.

    Looks for:
    - Base58 addresses (32-44 chars)
    - $CASHTAG mentions (resolved via DexScreener)
    - Buy language context

    Returns: list of token addresses found.
    """
    if not message_text:
        return []

    addresses = []

    # Find base58 addresses
    for match in SOLANA_ADDRESS_RE.finditer(message_text):
        addr = match.group(1)
        # Filter out common non-token addresses (too short, known programs)
        if len(addr) >= 32 and _looks_like_token_address(addr):
            addresses.append(addr)

    # Find cashtags and resolve to addresses
    for match in CASHTAG_RE.finditer(message_text):
        symbol = match.group(1)
        resolved = _resolve_cashtag(symbol)
        if resolved and resolved not in addresses:
            addresses.append(resolved)

    return addresses


def _looks_like_token_address(addr: str) -> bool:
    """Filter out known non-token addresses."""
    known_programs = {
        "11111111111111111111111111111111",
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "So11111111111111111111111111111111111111112",
    }
    return addr not in known_programs


def _resolve_cashtag(symbol: str) -> str | None:
    """Resolve a $CASHTAG to a Solana token address via DexScreener."""
    try:
        from quality_gate.helpers import get_json
        data = get_json(f"https://api.dexscreener.com/latest/dex/search?q={symbol}")
        pairs = data.get("pairs", [])
        # Find Solana pair with matching symbol
        for pair in pairs:
            if (pair.get("chainId") == "solana" and
                    pair.get("baseToken", {}).get("symbol", "").upper() == symbol.upper()):
                return pair["baseToken"]["address"]
    except Exception:
        pass
    return None


def _has_buy_language(text: str) -> bool:
    """Check if message contains buy-related language."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in BUY_KEYWORDS)


def on_kk_call(token_address: str, message_text: str = ""):
    """Entry pipeline triggered when KK calls a token.

    1. DexScreener lookup
    2. Safety Gate check
    3. Log to telegram_calls
    4. If clean -> auto phase 1 entry (shadow mode)
    5. If flags -> alert only
    6. Schedule confirmation check at +5min
    """
    log.info("KK CALL detected: %s", token_address[:16])

    # DexScreener lookup
    token_data = _dexscreener_lookup(token_address)
    symbol = token_data.get('symbol', '???')

    # Safety Gate (quick check)
    safety_result, safety_flags = _quick_safety_check(token_address)

    # Health score
    health_score_val = None
    try:
        from health_score.engine import score_token
        hs = score_token(token_address, symbol)
        health_score_val = hs.get('scaled_score')
    except Exception as e:
        log.error("Health score failed during KK call: %s", e)

    # Log to telegram_calls
    action_taken = 'skipped'
    try:
        if safety_result == 'clean':
            action_taken = 'auto_enter'
        elif safety_result == 'flags':
            action_taken = 'alert_only'

        execute(
            """INSERT INTO telegram_calls
               (source, message_text, token_address, token_symbol, detected_at,
                safety_result, safety_flags, action_taken, health_score_at_call)
               VALUES (%s,%s,%s,%s,NOW(),%s,%s,%s,%s)""",
            ('krypto_king', message_text[:500] if message_text else None,
             token_address, symbol, safety_result,
             ','.join(safety_flags) if safety_flags else None,
             action_taken, health_score_val),
        )
    except Exception as e:
        log.error("Failed to log telegram call: %s", e)

    if safety_result == 'clean':
        # Auto phase 1 entry (shadow mode)
        try:
            from telegram_alpha.entry_pipeline import execute_entry
            execute_entry(token_address, 'kk_call', token_data={
                'symbol': symbol,
                **token_data,
            })
        except Exception as e:
            log.error("Entry pipeline failed: %s", e)

        # Schedule confirmation check at +5min
        _schedule_confirmation(token_address, symbol, delay_sec=300)

    elif safety_result == 'flags':
        # Alert only
        try:
            from telegram_bot.severity import route_alert
            flags_str = ', '.join(safety_flags) if safety_flags else 'unknown'
            msg = (f"🟡 <b>KK CALL — FLAGS</b>\n"
                   f"🪙 ${symbol}\n"
                   f"⚠️ {flags_str}\n"
                   f"Reply 'buy' to override")
            route_alert(2, msg)
        except Exception as e:
            log.error("Failed to send KK alert: %s", e)

    else:
        log.info("KK call for %s failed safety — skipped", symbol)

    return {'token_address': token_address, 'symbol': symbol,
            'safety': safety_result, 'action': action_taken}


def check_kk_confirmation(token_address: str, token_symbol: str | None = None):
    """Called 5min after KK call to check for confirmation signals.

    Checks:
    - Did any other KOL wallet buy?
    - Volume accelerating?
    - X mentions rising?

    YES -> phase 2 (25%) + schedule phase 3
    NO  -> hold at 15%, set tight stop, re-evaluate at 30min
    """
    log.info("Checking KK confirmation for %s", token_symbol or token_address[:12])

    confirmed = False
    reasons = []

    # Check 1: KOL wallet buys
    try:
        from kol_tracking.monitor import get_kol_status
        kol = get_kol_status(token_address)
        if kol['wallets_holding'] > 0:
            confirmed = True
            reasons.append(f"KOL {kol['triggering_kol']} holding")
    except Exception:
        pass

    # Check 2: Volume acceleration
    try:
        from health_score.volume_signal import score_volume
        vol_score, vol_state, vol_details = score_volume(token_address)
        if vol_details.get('vol_growth_ratio', 0) > 1.3:
            confirmed = True
            reasons.append("volume accelerating")
    except Exception:
        pass

    # Check 3: Health score holding up
    try:
        from health_score.engine import score_token
        hs = score_token(token_address, token_symbol)
        if hs.get('scaled_score', 0) > 65:
            confirmed = True
            reasons.append(f"health={hs['scaled_score']:.0f}")
    except Exception:
        pass

    if confirmed:
        log.info("KK confirmation POSITIVE for %s: %s",
                 token_symbol or token_address[:12], ', '.join(reasons))
        try:
            from telegram_alpha.entry_pipeline import execute_phase2
            from db.connection import execute_one
            row = execute_one(
                """SELECT id FROM shadow_trades
                   WHERE token_address = %s AND status = 'open'
                   ORDER BY entry_time DESC LIMIT 1""",
                (token_address,),
            )
            if row:
                execute_phase2(row[0])
        except Exception as e:
            log.error("Phase 2 execution failed: %s", e)
    else:
        log.info("KK confirmation NEGATIVE for %s — holding position",
                 token_symbol or token_address[:12])
        # Schedule re-evaluation at 30min
        _schedule_confirmation(token_address, token_symbol, delay_sec=1500)


def _dexscreener_lookup(token_address: str) -> dict:
    """Quick DexScreener lookup for token data."""
    try:
        from quality_gate.helpers import get_json
        data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs", [])
        if pairs:
            pair = pairs[0]
            return {
                'symbol': pair.get("baseToken", {}).get("symbol", "???"),
                'name': pair.get("baseToken", {}).get("name", "Unknown"),
                'price': float(pair.get("priceUsd", 0) or 0),
                'mcap': float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0),
                'liquidity': float(pair.get("liquidity", {}).get("usd", 0) or 0),
                'volume_h24': float(pair.get("volume", {}).get("h24", 0) or 0),
            }
    except Exception as e:
        log.error("DexScreener lookup failed: %s", e)
    return {}


def _quick_safety_check(token_address: str) -> tuple[str, list[str]]:
    """Quick safety gate check. Returns (result, flags).

    result: 'clean', 'flags', 'failed'
    """
    flags = []

    try:
        from quality_gate.gate import run_gate
        result = run_gate(token_address, category="meme")
        gate_status = result.get("gate_status", "rejected")

        if gate_status == "passed":
            return 'clean', []

        failures = result.get("failures", [])
        if gate_status == "watching":
            return 'flags', failures

        # Check specific failures
        checks = result.get("checks", {})
        contract = checks.get("contract_safety", {})
        if not contract.get("pass"):
            return 'failed', ['contract_safety_fail']

        # Other failures are flags, not hard fails
        return 'flags', failures

    except Exception as e:
        log.error("Safety check failed: %s", e)
        return 'failed', [str(e)]


def _schedule_confirmation(token_address: str, token_symbol: str | None,
                           delay_sec: int):
    """Schedule a confirmation check after a delay."""
    def _run():
        import time
        time.sleep(delay_sec)
        check_kk_confirmation(token_address, token_symbol)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    log.info("Scheduled confirmation check for %s in %ds",
             token_symbol or token_address[:12], delay_sec)


def start_listener():
    """Connect to Telegram via Telethon and listen to KK channel.

    Requires interactive first-time login for session creation.
    """
    import os

    api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('TELEGRAM_API_HASH')
    phone = os.getenv('TELEGRAM_PHONE')
    channel_id = os.getenv('KK_CHANNEL_ID')

    if not all([api_id, api_hash, phone, channel_id]):
        log.error("Missing Telegram credentials. Set TELEGRAM_API_ID, "
                   "TELEGRAM_API_HASH, TELEGRAM_PHONE, KK_CHANNEL_ID in .env")
        return

    try:
        from telethon import TelegramClient, events
    except ImportError:
        log.error("Telethon not installed. Run: pip install telethon")
        return

    session_path = os.path.join(os.path.dirname(__file__), '..', 'kk_session')
    client = TelegramClient(session_path, int(api_id), api_hash)

    @client.on(events.NewMessage(chats=int(channel_id)))
    async def handler(event):
        text = event.message.text or ""
        log.info("KK message: %s", text[:100])

        addresses = parse_message(text)
        if addresses:
            for addr in addresses:
                log.info("KK call detected: %s", addr[:16])
                # Run in thread to avoid blocking the event loop
                threading.Thread(
                    target=on_kk_call, args=(addr, text), daemon=True
                ).start()
        elif _has_buy_language(text):
            log.debug("Buy language detected but no address: %s", text[:80])

    async def main():
        await client.start(phone=phone)
        log.info("KK Telegram listener started for channel %s", channel_id)

        try:
            from telegram_bot.severity import route_alert
            route_alert(4, "KK Telegram listener started")
        except Exception:
            pass

        await client.run_until_disconnected()

    asyncio.run(main())


if __name__ == "__main__":
    start_listener()
