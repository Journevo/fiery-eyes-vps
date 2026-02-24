"""Override Commands — reply to an alert with commands.

Commands (reply to an H-Fire alert):
  buy   — confirm entry (for KK calls with flags, or manual confirms)
  skip  — skip this opportunity
  2x    — double position size
  exit  — force exit entire position
  hold  — override trim/exit recommendation, keep holding
"""

import re
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("telegram_bot.overrides")

# Command patterns
COMMANDS = {
    'buy': re.compile(r'^\s*buy\s*$', re.IGNORECASE),
    'skip': re.compile(r'^\s*skip\s*$', re.IGNORECASE),
    '2x': re.compile(r'^\s*2x\s*$', re.IGNORECASE),
    'exit': re.compile(r'^\s*exit\s*$', re.IGNORECASE),
    'hold': re.compile(r'^\s*hold\s*$', re.IGNORECASE),
}

# Token address pattern to extract from alert text
TOKEN_RE = re.compile(r'\$([A-Z]{2,10})')
ADDRESS_RE = re.compile(r'([1-9A-HJ-NP-Za-km-z]{32,44})')


def parse_override(message_text: str, reply_text: str | None = None) -> dict | None:
    """Parse an override command from a reply message.

    Args:
        message_text: The reply message text (the command)
        reply_text: The original alert message being replied to

    Returns: {command, token_symbol, token_address} or None
    """
    if not message_text:
        return None

    command = None
    for cmd, pattern in COMMANDS.items():
        if pattern.match(message_text.strip()):
            command = cmd
            break

    if not command:
        return None

    # Extract token from the replied-to alert
    token_symbol = None
    token_address = None

    if reply_text:
        sym_match = TOKEN_RE.search(reply_text)
        if sym_match:
            token_symbol = sym_match.group(1)

        addr_match = ADDRESS_RE.search(reply_text)
        if addr_match:
            token_address = addr_match.group(1)

    # Try to resolve symbol to address if we have symbol but not address
    if token_symbol and not token_address:
        try:
            row = execute_one(
                "SELECT contract_address FROM tokens WHERE symbol = %s LIMIT 1",
                (token_symbol,),
            )
            if row:
                token_address = row[0]
        except Exception:
            pass

    return {
        'command': command,
        'token_symbol': token_symbol,
        'token_address': token_address,
    }


def execute_override(command: str, token_address: str,
                     token_symbol: str | None = None) -> str:
    """Execute an override command.

    Returns: status message
    """
    if not token_address:
        return "No token address found in alert"

    if command == 'buy':
        return _handle_buy(token_address, token_symbol)
    elif command == 'skip':
        return _handle_skip(token_address, token_symbol)
    elif command == '2x':
        return _handle_2x(token_address, token_symbol)
    elif command == 'exit':
        return _handle_exit(token_address, token_symbol)
    elif command == 'hold':
        return _handle_hold(token_address, token_symbol)
    else:
        return f"Unknown command: {command}"


def _handle_buy(token_address: str, symbol: str | None) -> str:
    """Confirm entry for a flagged token."""
    try:
        from telegram_alpha.entry_pipeline import execute_entry
        result = execute_entry(token_address, 'kk_call', token_data={'symbol': symbol})
        return f"Entry confirmed for ${symbol or token_address[:12]}: {result.get('status', '?')}"
    except Exception as e:
        log.error("Buy override failed: %s", e)
        return f"Entry failed: {e}"


def _handle_skip(token_address: str, symbol: str | None) -> str:
    """Skip this opportunity."""
    try:
        execute(
            """UPDATE telegram_calls SET action_taken = 'skipped',
                 notes = COALESCE(notes, '') || ' | Manual skip'
               WHERE token_address = %s AND action_taken != 'skipped'
               ORDER BY detected_at DESC LIMIT 1""",
            (token_address,),
        )
    except Exception:
        pass
    return f"Skipped ${symbol or token_address[:12]}"


def _handle_2x(token_address: str, symbol: str | None) -> str:
    """Double position size on open trade."""
    try:
        row = execute_one(
            """SELECT id, position_size_pct FROM shadow_trades
               WHERE token_address = %s AND status = 'open'
               ORDER BY entry_time DESC LIMIT 1""",
            (token_address,),
        )
        if not row:
            return f"No open trade for ${symbol or token_address[:12]}"

        trade_id, current_pct = row
        new_pct = float(current_pct or 0) * 2
        execute(
            """UPDATE shadow_trades SET position_size_pct = %s,
                 notes = COALESCE(notes, '') || ' | 2x override'
               WHERE id = %s""",
            (new_pct, trade_id),
        )
        return f"Doubled ${symbol or '?'}: {float(current_pct):.0f}% -> {new_pct:.0f}%"
    except Exception as e:
        log.error("2x override failed: %s", e)
        return f"2x failed: {e}"


def _handle_exit(token_address: str, symbol: str | None) -> str:
    """Force exit entire position."""
    try:
        row = execute_one(
            """SELECT id FROM shadow_trades
               WHERE token_address = %s AND status = 'open'
               ORDER BY entry_time DESC LIMIT 1""",
            (token_address,),
        )
        if not row:
            return f"No open trade for ${symbol or token_address[:12]}"

        from shadow.tracker import close_shadow_trade
        # Get current price
        try:
            from quality_gate.helpers import get_json
            data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            pairs = data.get("pairs", [])
            exit_price = float(pairs[0].get("priceUsd", 0) or 0) if pairs else 0
        except Exception:
            exit_price = 0

        close_shadow_trade(row[0], 'manual', exit_price)
        return f"Force exited ${symbol or token_address[:12]}"
    except Exception as e:
        log.error("Exit override failed: %s", e)
        return f"Exit failed: {e}"


def _handle_hold(token_address: str, symbol: str | None) -> str:
    """Override trim/exit recommendation."""
    try:
        execute(
            """UPDATE shadow_trades SET
                 notes = COALESCE(notes, '') || ' | HOLD override at ' || NOW()::text
               WHERE token_address = %s AND status = 'open'""",
            (token_address,),
        )
        return f"HOLD override set for ${symbol or token_address[:12]} — will not auto-trim"
    except Exception as e:
        log.error("Hold override failed: %s", e)
        return f"Hold failed: {e}"
